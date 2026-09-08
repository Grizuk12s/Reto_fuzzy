# -*- coding: utf-8 -*-
"""Conector KEPserver — toda la lógica OPC-UA en un solo lugar.

IT-8: extraído de web/state.py.
B1.2 (2026-08-25): la sesión OPC-UA es persistente, con timeout y reconexión.
B1.4 (2026-08-25): la calidad sale del StatusCode real, no del optimismo.

API pública
-----------
URL : str
    Valor inicial de la URL OPC-UA (alias legacy). Preferir `get_url()`.

get_url() -> str
    Devuelve la URL actual (leida de config/espesador/kepserver.json).

get_config() / set_config(host, port) -> dict
    Lee o actualiza host/puerto/timeout persistidos.

read_tags_batch(tag_names) -> dict[str, dict]
    Lee múltiples tags en una sesión OPC-UA. Devuelve, por tag:
    {"connected", "exists", "value", "quality", "status_code", "source_ts",
     "estancado_s"}.

write_tag(tag_name, value, data_type) -> dict
    Escribe un valor a un tag. Devuelve {"ok", "error"}.

write_float_batch(tag_values) -> dict
    Escribe un dict {tag_name: float}. Lanza Exception si no conecta.

enrich_tags(tags) -> list[dict]
    Agrega datos live de KEPserver a una lista de dicts de tags.

check_connection(url=None) -> tuple[bool, str]
    Verifica conectividad. Devuelve (ok, mensaje_error).

close_thread_client() -> None
    Cierra la sesión del hilo actual. La llaman los workers al terminar.

close_all_clients() -> int
    Cierra todas las sesiones. Para apagado y para cambios de configuración.

pool_status() -> dict
    Diagnóstico: cuántas sesiones hay abiertas y de qué hilos.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import time

from core.jsonio import escribir_json_atomico

# Config persistida en JSON. La primera vez se genera con defaults + env.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_JSON = os.path.join(_HERE, "config", "espesador", "kepserver.json")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 49320

# Timeout de la sesión OPC-UA, en segundos. `Client(url, timeout=N)` lo aplica
# tanto al `socket.create_connection` del arranque como a la espera de respuesta
# de CADA request (`future.result(timeout)`), que son los dos lugares donde el
# tick se podia colgar para siempre. 2 s es holgado para una LAN industrial y
# deja al tick por debajo del `join(timeout=3)` que usa `SEEngine.stop()`: si el
# KEPserver deja de responder, el motor se puede parar de verdad.
_DEFAULT_TIMEOUT_S = 2.0
_TIMEOUT_S_MIN = 0.2
_TIMEOUT_S_MAX = 30.0

# Segundos sin que una PV cambie de valor a partir de los cuales se avisa. Es un
# umbral de DIAGNOSTICO: no saca la variable del pipeline (ver B1.4 en
# AFINACION_PENDIENTE.md). 0 lo desactiva.
_DEFAULT_ESTANCADO_ALERTA_S = 60.0

# Cuanto se sigue usando el ultimo valor bueno de un tag cuya calidad se cayo.
# 5 s son ~30 ticks al ritmo real (~6 tick/s): alcanza para cruzar un parpadeo o
# una reconexion, y es corto frente a la ventana del filtro Exp-Q (50 s) y a
# cualquier wait. 0 lo desactiva (la variable desaparece en el mismo tick).
_DEFAULT_RETENCION_S = 5.0

# Precedencia: JSON > env var > default.
_ENV_URL = os.environ.get("KEPSERVER_URL", "").strip()


def _parse_url(url: str) -> tuple[str, int]:
    """opc.tcp://host:port -> (host, port). Devuelve defaults si falla."""
    try:
        raw = url.strip()
        if raw.startswith("opc.tcp://"):
            raw = raw[len("opc.tcp://"):]
        raw = raw.split("/")[0]
        host, _, port_str = raw.partition(":")
        host = (host or _DEFAULT_HOST).strip()
        port = int(port_str) if port_str else _DEFAULT_PORT
        return host, port
    except (ValueError, AttributeError):
        return _DEFAULT_HOST, _DEFAULT_PORT


def build_url(host: str, port: int) -> str:
    return f"opc.tcp://{host}:{int(port)}"


def _defaults() -> dict:
    if _ENV_URL:
        h, p = _parse_url(_ENV_URL)
    else:
        h, p = _DEFAULT_HOST, _DEFAULT_PORT
    return {
        "host": h,
        "port": p,
        "timeout_s": _DEFAULT_TIMEOUT_S,
        "aceptar_uncertain": False,
        "estancado_alerta_s": _DEFAULT_ESTANCADO_ALERTA_S,
        "retencion_s": _DEFAULT_RETENCION_S,
        "last_status": "unconfigured",
        "last_message": "",
    }


def _leer_config_de_disco() -> dict:
    if not os.path.exists(CONFIG_JSON):
        cfg = _defaults()
        try:
            _save_config(cfg)
        except OSError:
            pass
        return cfg
    try:
        with open(CONFIG_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return _defaults()
    if not isinstance(data, dict):
        return _defaults()
    return {**_defaults(), **data}


# Cache con testigo de mtime. `get_url()` pasó a estar en el camino caliente
# (cada uso del cliente compara la URL de su sesión contra la vigente) y sin
# esto serían tantas lecturas de disco por segundo como ticks. El testigo de
# mtime conserva la propiedad de que editar el JSON a mano se nota sin
# reiniciar. Mismo patrón que `_load_pg_config`.
_cfg_cache: dict | None = None
_cfg_mtime: float = -1.0
_cfg_lock = threading.Lock()


def _load_config() -> dict:
    global _cfg_cache, _cfg_mtime
    try:
        mtime = os.path.getmtime(CONFIG_JSON)
    except OSError:
        mtime = -1.0
    with _cfg_lock:
        if _cfg_cache is not None and mtime == _cfg_mtime:
            return dict(_cfg_cache)
    cfg = _leer_config_de_disco()
    with _cfg_lock:
        _cfg_cache = dict(cfg)
        _cfg_mtime = mtime
    return cfg


def _save_config(cfg: dict) -> None:
    global _cfg_cache
    escribir_json_atomico(CONFIG_JSON, cfg)
    with _cfg_lock:
        _cfg_cache = None


def get_config() -> dict:
    return _load_config()


def get_url() -> str:
    cfg = _load_config()
    return build_url(cfg.get("host", _DEFAULT_HOST), cfg.get("port", _DEFAULT_PORT))


def get_timeout_s() -> float:
    """Timeout de sesión vigente, acotado a un rango sano.

    Sin el acote, un `timeout_s: 0` en el JSON significaria "no esperar nada" y
    ninguna lectura funcionaria; un valor enorme devuelve el cuelgue indefinido
    que este parametro existe para evitar.
    """
    try:
        val = float(_load_config().get("timeout_s", _DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S
    return min(max(_TIMEOUT_S_MIN, val), _TIMEOUT_S_MAX)


def get_aceptar_uncertain() -> bool:
    """¿Se le cree a un valor que el servidor marcó `Uncertain`?

    Por defecto NO, en linea con la regla de oro del proyecto: el SE no decide
    sobre datos que no puede sostener. `Uncertain` es el servidor diciendo
    "tomá el numero pero no me hago responsable", y algunos de esos codigos son
    literalmente un dato viejo (`UncertainLastUsableValue` significa que la
    fuente se cayo y esto es lo ultimo que hubo).

    Es configurable porque la alternativa no es gratis: hay codigos benignos
    (`UncertainEngineeringUnitsExceeded` es solo "fuera de rango de ingenieria")
    y en una planta que los emita seguido, rechazarlos deja al experto mudo. Que
    la decision este en un JSON y no hundida en el codigo es a proposito.
    """
    return bool(_load_config().get("aceptar_uncertain", False))


def get_estancado_alerta_s() -> float:
    """Umbral de aviso por valor que no cambia. 0 = desactivado."""
    try:
        val = float(_load_config().get("estancado_alerta_s",
                                       _DEFAULT_ESTANCADO_ALERTA_S))
    except (TypeError, ValueError):
        return _DEFAULT_ESTANCADO_ALERTA_S
    return max(0.0, val)


def get_retencion_s() -> float:
    """Cuántos segundos se sigue usando el último valor bueno. 0 = desactivado.

    Quien lo aplica es `SEEngine._read_tags`, no el conector: el conector
    reporta lo que el servidor dijo en ESTA lectura y no debe mentir sobre eso.
    """
    try:
        val = float(_load_config().get("retencion_s", _DEFAULT_RETENCION_S))
    except (TypeError, ValueError):
        return _DEFAULT_RETENCION_S
    return max(0.0, val)


def set_config(host: str, port: int, last_status: str | None = None,
               last_message: str | None = None,
               timeout_s: float | None = None,
               aceptar_uncertain: bool | None = None,
               estancado_alerta_s: float | None = None,
               retencion_s: float | None = None) -> dict:
    cfg = _load_config()
    cfg["host"] = str(host).strip() or _DEFAULT_HOST
    cfg["port"] = int(port)
    if timeout_s is not None:
        cfg["timeout_s"] = min(max(_TIMEOUT_S_MIN, float(timeout_s)), _TIMEOUT_S_MAX)
    if aceptar_uncertain is not None:
        cfg["aceptar_uncertain"] = bool(aceptar_uncertain)
    if estancado_alerta_s is not None:
        cfg["estancado_alerta_s"] = max(0.0, float(estancado_alerta_s))
    if retencion_s is not None:
        cfg["retencion_s"] = max(0.0, float(retencion_s))
    if last_status is not None:
        cfg["last_status"] = last_status
    if last_message is not None:
        cfg["last_message"] = last_message
    _save_config(cfg)
    # Las sesiones abiertas apuntan al host viejo. `_obtener_sesion` ya las
    # reconecta al detectar el cambio de URL, pero cerrarlas aca hace que el
    # cambio se note en el primer uso y no dependa de que alguien compare.
    close_all_clients()
    return cfg


# Alias legacy: algunas partes del codigo leen `URL` como constante. Se refresca
# en cada acceso via get_url() cuando lo necesitan; este valor es solo el inicial.
URL = get_url()

_TYPE_MAP = {
    "Float":   "Float",
    "Int":     "Int32",
    "Boolean": "Boolean",
    "String":  "String",
}


# ============================================================
# Sesión OPC-UA persistente, una por hilo
# ============================================================
# B1.2. Antes cada lectura y cada escritura abria su propia sesion: `Client(url)`
# + `connect()` + `disconnect()`. Con el SEEngine en ciclo libre eso son decenas
# de sesiones TCP por segundo contra el KEPserver, y el `connect()` no llevaba
# timeout: un servidor lento colgaba el tick de forma indefinida y con el el
# `join(timeout=3)` de `stop()`, que es lo que dejaba al motor imposible de
# parar (era el escenario que disparaba B1.1).
#
# Por que UNA POR HILO y no una sola con lock: el cliente OPC-UA no es
# reentrante, asi que una sesion compartida obliga a serializar a todos sus
# usuarios. Y los usuarios no son solo el motor — el heartbeat pulsa cada 2 s y
# las rutas HTTP releen todos los tags en cada carga de la pagina de Tags. Con
# un lock global, abrir esa pagina esperaria al tick del lazo de control y el
# tick esperaria a la pagina. Con una sesion por hilo cada uno va a su ritmo.
#
# La cuenta queda acotada porque los hilos que llaman aca son de vida larga:
# los tres workers del SE (motor, heartbeat, generador) y el pool de gunicorn
# (`worker_class = "gthread"`, `threads = 4`, hilos reutilizados, no uno por
# request). Igual se cosechan las sesiones de hilos muertos y las que llevan
# mucho sin usarse, porque el servidor de desarrollo de Flask SI crea un hilo
# por request y cada sesion huerfana vive una hora en el KEPserver.

# Cada cuanto se cosecha una sesion que nadie usa. Una sesion viva consume un
# slot en el KEPserver y dos hilos en este proceso (el socket y el KeepAlive),
# asi que no conviene dejar abiertas las de un hilo HTTP que atendio una carga
# de pagina y no volvio.
#
# La cosecha es OPORTUNISTA: corre cuando alguien pide una sesion, no en un hilo
# de fondo. Es a proposito — un hilo mas para esto no se paga solo. La
# consecuencia es que con el motor parado y nadie mirando la pagina de Tags,
# puede quedar una sesion en pie mas alla de este umbral hasta el proximo uso
# del conector. No se pierde nada: el KeepAlive de la libreria la mantiene sana,
# y con el motor corriendo la cosecha se dispara varias veces por segundo.
_IDLE_CIERRE_S = 120.0

# La cosecha se consulta en cada uso del cliente, o sea decenas de veces por
# segundo. Sin este piso serían otras tantas pasadas sobre el registro por algo
# que solo cambia en escalas de minutos.
_COSECHA_CADA_S = 5.0
_ultima_cosecha = 0.0


class _Sesion:
    """Una conexión OPC-UA y el candado que la protege.

    El candado casi nunca tiene contención: solo lo toma su hilo dueño. Existe
    para que el cosechador pueda cerrar la sesión sin pisar un uso en curso —
    la cosecha corre en un hilo ajeno y solo cierra lo que logra tomar sin
    esperar.
    """
    __slots__ = ("client", "url", "hilo", "lock", "ultimo_uso", "abierta_en")

    def __init__(self, client, url: str, hilo: threading.Thread):
        self.client = client
        self.url = url
        self.hilo = hilo
        self.lock = threading.Lock()
        self.ultimo_uso = time.monotonic()
        self.abierta_en = time.monotonic()


_sesiones: dict[int, _Sesion] = {}
_sesiones_lock = threading.Lock()

# Clases de error de la libreria, resueltas una vez. `opcua` es un import
# opcional (hay entornos sin el modulo), asi que no se puede importar arriba.
# La tupla vacia como respaldo funciona: `isinstance(x, ())` es siempre False,
# de modo que sin la libreria la clasificacion cae a "OSError es transporte",
# que es exactamente lo correcto.
_clases_ua: tuple | None = None


def _clases_error() -> tuple:
    global _clases_ua
    if _clases_ua is None:
        try:
            from opcua.ua.uaerrors import UaError, UaStatusCodeError  # type: ignore
            _clases_ua = (UaStatusCodeError, UaError)
        except Exception:
            _clases_ua = ((), ())
    return _clases_ua


def _es_falla_de_transporte(exc: BaseException) -> bool:
    """¿Se cayó la conexión, o el servidor contestó que ese tag no sirve?

    Distinción que antes no hacía falta y ahora sí. Con una sesión por llamada,
    un socket roto se manifestaba como fallo del `connect()` y el lote entero
    salía con `connected=False`. Ahora el lote comparte la conexión: si se cae
    a mitad de camino, sin esta distinción los tags que faltaban se reportarían
    como `exists=False, quality="Bad"` — o sea, el SE vería "todos los
    instrumentos rotos" en vez de "me quedé sin KEPserver", seguiría usando la
    sesión muerta y no reconectaría nunca.

    El criterio: si el servidor RESPONDIÓ con un StatusCode (tag inexistente,
    sin permiso), la conexión está viva y el problema es de ese tag. Si no hubo
    respuesta —socket, timeout, error de protocolo— es transporte.

    Un error que no es ninguna de las dos cosas (un `ValueError` convirtiendo
    el valor) se trata como problema del tag: cerrar la sesión por eso la haría
    reconectar en cada tick, silenciosamente, y volveríamos a una sesión por
    tick sin que nadie se enterara.
    """
    status_code_error, ua_error = _clases_error()
    if isinstance(exc, status_code_error):
        return False
    return isinstance(exc, (OSError, concurrent.futures.TimeoutError, ua_error))


def _conectar(url: str, timeout_s: float):
    from opcua import Client  # type: ignore  (ImportError se propaga)
    client = Client(url, timeout=timeout_s)
    client.connect()
    return client


def _cerrar(sesion: _Sesion, *, ordenado: bool) -> None:
    """Cierra una sesión. `ordenado=False` corta el socket sin saludar.

    Un `disconnect()` limpio manda `close_session` y `close_secure_channel` y
    espera respuesta de cada uno: sobre una conexión ya rota son dos esperas de
    `timeout_s` que el tick paga para nada. Cuando cerramos PORQUE algo falló,
    se corta directo.
    """
    client = sesion.client
    if not ordenado:
        # El KeepAlive es un hilo aparte que renueva el canal seguro cada
        # ~0.7 * session_timeout. `disconnect()` lo baja; cortando el socket a
        # mano hay que bajarlo explícitamente o queda girando contra una
        # conexión muerta hasta que reviente.
        try:
            ka = getattr(client, "keepalive", None)
            if ka is not None and ka.is_alive():
                ka.stop()
        except Exception:
            pass
    try:
        if ordenado:
            client.disconnect()
        else:
            client.uaclient.disconnect_socket()
    except Exception:
        # Cerrar es best-effort: si el socket ya no está, no hay nada que hacer
        # y propagarlo solo taparía el error real que nos trajo hasta acá.
        pass


def _cosechar(excepto: int) -> None:
    """Cierra las sesiones de hilos muertos y las que llevan mucho sin uso.

    Solo cierra las que puede tomar sin esperar: si el candado está ocupado, su
    dueño la está usando justo ahora y no es basura.

    El cierre va FUERA del candado del registro. Cerrar habla por el socket y
    puede tardar hasta el timeout; haciéndolo con el registro tomado, una
    sesión moribunda dejaría al motor esperando para pedir la suya.
    """
    global _ultima_cosecha
    ahora = time.monotonic()
    if (ahora - _ultima_cosecha) < _COSECHA_CADA_S:
        return
    _ultima_cosecha = ahora

    a_cerrar: list[_Sesion] = []
    with _sesiones_lock:
        for ident, sesion in list(_sesiones.items()):
            if ident == excepto:
                continue
            if sesion.hilo.is_alive() and (ahora - sesion.ultimo_uso) <= _IDLE_CIERRE_S:
                continue
            if not sesion.lock.acquire(blocking=False):
                continue
            _sesiones.pop(ident, None)
            a_cerrar.append(sesion)

    for sesion in a_cerrar:
        try:
            _cerrar(sesion, ordenado=sesion.hilo.is_alive())
        finally:
            sesion.lock.release()


def _obtener_sesion() -> _Sesion:
    """Devuelve la sesión del hilo actual, conectándola si hace falta.

    Reconecta también cuando cambió la URL configurada: la sesión abierta
    apuntaría al host anterior y las lecturas seguirían saliendo bien contra el
    KEPserver equivocado, que es peor que fallar.
    """
    ident = threading.get_ident()
    _cosechar(excepto=ident)

    url = get_url()
    with _sesiones_lock:
        sesion = _sesiones.get(ident)

    if sesion is not None:
        if sesion.url == url:
            return sesion
        with sesion.lock:
            with _sesiones_lock:
                if _sesiones.get(ident) is sesion:
                    del _sesiones[ident]
            _cerrar(sesion, ordenado=True)

    # Conectar va sin candados tomados: es lo único acá que puede tardar
    # (hasta `timeout_s`) y no hay motivo para que un hilo esperando su propia
    # conexión frene a los demás.
    client = _conectar(url, get_timeout_s())
    sesion = _Sesion(client, url, threading.current_thread())
    with _sesiones_lock:
        _sesiones[ident] = sesion
    return sesion


def _descartar(sesion: _Sesion) -> None:
    """Saca la sesión del registro y corta el socket. El próximo uso reconecta.

    Se llama con el candado de la sesión en la mano.
    """
    ident = threading.get_ident()
    with _sesiones_lock:
        if _sesiones.get(ident) is sesion:
            del _sesiones[ident]
    _cerrar(sesion, ordenado=False)


def close_thread_client() -> None:
    """Cierra la sesión del hilo actual, si tiene una.

    La llaman los workers del SE en su `finally`. Sin esto, parar el motor
    dejaría su sesión abierta en el KEPserver hasta que venciera el
    `session_timeout` (una hora), y arrancarlo de nuevo abriría otra.
    """
    ident = threading.get_ident()
    with _sesiones_lock:
        sesion = _sesiones.pop(ident, None)
    if sesion is None:
        return
    with sesion.lock:
        _cerrar(sesion, ordenado=True)


def close_all_clients() -> int:
    """Cierra todas las sesiones registradas. Devuelve cuántas cerró."""
    with _sesiones_lock:
        pendientes = list(_sesiones.items())
        _sesiones.clear()
    for _, sesion in pendientes:
        # Sin esperar el candado: quien lo tenga va a encontrar su sesión fuera
        # del registro y reconectará en el uso siguiente.
        _cerrar(sesion, ordenado=False)
    return len(pendientes)


def pool_status() -> dict:
    """Diagnóstico del pool de sesiones."""
    ahora = time.monotonic()
    with _sesiones_lock:
        sesiones = list(_sesiones.items())
    return {
        "abiertas": len(sesiones),
        "timeout_s": get_timeout_s(),
        "idle_cierre_s": _IDLE_CIERRE_S,
        "detalle": sorted(
            [
                {
                    "hilo": s.hilo.name,
                    "url": s.url,
                    "viva_s": round(ahora - s.abierta_en, 1),
                    "sin_uso_s": round(ahora - s.ultimo_uso, 1),
                    "hilo_vivo": s.hilo.is_alive(),
                }
                for _, s in sesiones
            ],
            key=lambda d: d["hilo"],
        ),
    }


# ============================================================
# Calidad de dato: StatusCode real y estancamiento (B1.4)
# ============================================================
# Antes se leia con `node.get_value()`, que DESCARTA el StatusCode, y se
# rellenaba `"quality": "Good"` si no habia excepcion. O sea: la columna QUALITY
# de la interfaz decia siempre *Good*, dijera lo que dijera el servidor, y un
# instrumento en `Uncertain` era indetectable. El campo era decorativo.
#
# `get_data_value()` trae el DataValue completo. De ahi salen tres cosas:
#
#   quality      severidad OPC-UA: Good / Uncertain / Bad. Es lo que se juzga.
#   status_code  el nombre exacto ("UncertainLastUsableValue"), para diagnostico.
#   source_ts    el SourceTimestamp del servidor, tal como vino.

# Severidad OPC-UA: los dos bits altos del StatusCode.
#   0x00000000 Good · 0x40000000 Uncertain · 0x80000000 Bad
_SEVERIDAD_MASK = 0xC0000000
_SEVERIDAD = {0x00000000: "Good", 0x40000000: "Uncertain", 0x80000000: "Bad"}


def _severidad(status_code) -> str:
    try:
        return _SEVERIDAD.get(int(status_code.value) & _SEVERIDAD_MASK, "Bad")
    except (AttributeError, TypeError, ValueError):
        return "Unknown"


def _nombre_status(status_code) -> str:
    try:
        return str(status_code.name)
    except (AttributeError, TypeError):
        return ""


# Estancamiento: hace cuanto que el valor de un tag no cambia.
#
# Se publica como HECHO, no como juicio: el conector no sabe que tags deberian
# moverse. Un tag LIM vale 90.0 para siempre y eso es correcto; una PV que no se
# mueve en 60 s con el lazo a 6 tick/s es sospechosa. Quien decide es
# `web/state.py`, que si sabe la categoria.
#
# POR QUE NO SE USA EL SourceTimestamp, que era la propuesta del backlog:
# verificado contra este servidor, el SourceTimestamp AVANZA EN CADA LECTURA
# aunque el valor no cambie (mismo 66.906 con estampas 18.509 -> 20.011 ->
# 21.516). O sea, estampa el momento de la lectura, no el del ultimo cambio:
# como detector de congelado da siempre "fresco". El valor si se queda quieto,
# y un float analogico real jitterea en los ultimos bits, asi que la igualdad
# exacta sostenida es la mejor senal disponible aca.
_MAX_TAGS_VIGILADOS = 2000
_estancamiento: dict[str, tuple] = {}
_estancamiento_lock = threading.Lock()


def _registrar_valor(nombre: str, valor) -> float:
    """Devuelve hace cuántos segundos que este tag no cambia de valor."""
    ahora = time.monotonic()
    with _estancamiento_lock:
        previo = _estancamiento.get(nombre)
        if previo is None:
            if len(_estancamiento) >= _MAX_TAGS_VIGILADOS:
                _estancamiento.clear()      # cota dura; se re-puebla solo
            _estancamiento[nombre] = (valor, ahora)
            return 0.0
        valor_previo, t_cambio = previo
        if valor != valor_previo:
            _estancamiento[nombre] = (valor, ahora)
            return 0.0
        return ahora - t_cambio


def reset_estancamiento() -> None:
    """Olvida la historia de estancamiento. La usan los tests y el arranque."""
    with _estancamiento_lock:
        _estancamiento.clear()


def read_tags_batch(tag_names: list[str]) -> dict[str, dict]:
    """Lee múltiples tags del KEPserver reusando la sesión del hilo.

    Nunca lanza. Por tag devuelve:

      connected    hubo conexión con el servidor
      exists       el servidor contestó con un valor para ese tag
      value        el valor, o None
      quality      Good / Uncertain / Bad / Unknown — severidad OPC-UA real
      status_code  nombre exacto del StatusCode, para diagnóstico
      source_ts    SourceTimestamp del servidor, ISO-8601, o None
      estancado_s  hace cuántos segundos que el valor no cambia

    `connected=False` (no hubo conexión) y `exists=False` (el servidor dijo que
    ese tag no sirve) son distintos: `SEEngine._read_tags` los usa para explicar
    en la traza qué faltó exactamente.
    """
    def _default() -> dict:
        return {"connected": False, "exists": False, "value": None,
                "quality": "Unknown", "status_code": "", "source_ts": None,
                "estancado_s": 0.0}

    results: dict[str, dict] = {n: _default() for n in tag_names}
    if not tag_names:
        return results

    try:
        sesion = _obtener_sesion()
    except Exception:
        return results          # sin conexión: todo queda connected=False

    with sesion.lock:
        for name in tag_names:
            try:
                node = sesion.client.get_node(f"ns=2;s={name}")
                dv = node.get_data_value()
                val = dv.Value.Value
                calidad = _severidad(dv.StatusCode)
                src = dv.SourceTimestamp
                results[name] = {
                    "connected":   True,
                    # Un StatusCode Bad CON valor no es un dato: es el servidor
                    # avisando que no lo tome. Se trata igual que un tag que no
                    # contesto, para que aguas abajo no haya que revisar dos
                    # campos y olvidarse de uno.
                    "exists":      calidad != "Bad" and val is not None,
                    "value":       val,
                    "quality":     calidad,
                    "status_code": _nombre_status(dv.StatusCode),
                    "source_ts":   src.isoformat() if src is not None else None,
                    "estancado_s": round(_registrar_valor(name, val), 1),
                }
            except Exception as e:
                if _es_falla_de_transporte(e):
                    # Se cayó la conexión a mitad del lote. Los tags que faltan
                    # NO son instrumentos malos: no se los pudo preguntar.
                    # Quedan con su default (connected=False).
                    _descartar(sesion)
                    return results
                # El servidor respondió con un StatusCode de error. El nombre de
                # la excepción ES el nombre del status (BadNodeIdUnknown,
                # BadUserAccessDenied), que es justo lo que hay que mostrarle a
                # quien tiene que ir a arreglar el tag.
                results[name] = {
                    "connected":   True,
                    "exists":      False,
                    "value":       None,
                    "quality":     "Bad",
                    "status_code": type(e).__name__,
                    "source_ts":   None,
                    "estancado_s": 0.0,
                }
        sesion.ultimo_uso = time.monotonic()

    return results


def write_tag(tag_name: str, value, data_type: str) -> dict:
    """Escribe un valor a un tag KEPserver via OPC-UA.

    Devuelve {"ok": bool, "error": str|None}.
    """
    try:
        sesion = _obtener_sesion()
    except Exception as e:
        return {"ok": False, "error": f"Sin conexion OPC-UA: {e}"}

    from opcua import ua  # type: ignore
    with sesion.lock:
        try:
            node = sesion.client.get_node(f"ns=2;s={tag_name}")
            vtype_name = _TYPE_MAP.get(data_type, "Float")
            vtype = getattr(ua.VariantType, vtype_name)
            node.set_value(ua.DataValue(ua.Variant(value, vtype)))
            sesion.ultimo_uso = time.monotonic()
            return {"ok": True, "error": None}
        except Exception as e:
            if _es_falla_de_transporte(e):
                _descartar(sesion)
            return {"ok": False, "error": str(e)}


def write_float_batch(tag_values: dict[str, float]) -> dict:
    """Escribe múltiples tags Float reusando la sesión del hilo.

    Lanza Exception si no puede conectar.

    Devuelve {"escritos": [tags que aceptaron el valor],
              "fallidos": {tag: motivo}} — el fallo por tag NO se traga: quien
    llama necesita saber que ese setpoint no llego al DCS para reintentarlo.
    """
    if not tag_values:
        return {"escritos": [], "fallidos": {}}

    from opcua import ua  # type: ignore  (ImportError se propaga)

    sesion = _obtener_sesion()      # si no conecta, propaga (contrato vigente)
    escritos: list[str] = []
    fallidos: dict[str, str] = {}
    with sesion.lock:
        for tag_name, val in tag_values.items():
            try:
                node = sesion.client.get_node(f"ns=2;s={tag_name}")
                node.set_value(
                    ua.DataValue(ua.Variant(float(val), ua.VariantType.Float))
                )
                escritos.append(tag_name)
            except Exception as e:
                # Antes esto era `except Exception: pass`. Un tag de solo
                # lectura, mal escrito o rechazado por el DCS se descartaba en
                # silencio: quien llama marcaba el SP como escrito y el
                # write-on-change no volvia a intentarlo NUNCA. El SE se creia
                # en control sin haber escrito nada.
                fallidos[tag_name] = f"{type(e).__name__}: {e}"
                if _es_falla_de_transporte(e):
                    # Se cayo la conexion. Los SP que faltan NO se intentaron:
                    # marcarlos como escritos dejaria al DCS con un valor viejo
                    # y al write-on-change convencido de haberlo mandado.
                    _descartar(sesion)
                    for restante in tag_values:
                        if restante not in escritos and restante not in fallidos:
                            fallidos[restante] = "Conexion OPC-UA caida antes de escribirlo"
                    return {"escritos": escritos, "fallidos": fallidos}
        sesion.ultimo_uso = time.monotonic()
    return {"escritos": escritos, "fallidos": fallidos}


def enrich_tags(tags: list[dict]) -> list[dict]:
    """Agrega datos live de KEPserver a cada tag dict.

    Tags con enabled=False reciben quality='Suspended' sin consultar al servidor.
    """
    enabled = {t["name"]: t for t in tags if t.get("enabled", True)}
    live = read_tags_batch(list(enabled.keys()))

    results = []
    for tag in tags:
        if tag.get("enabled", True) and tag["name"] in live:
            results.append({**tag, **live[tag["name"]]})
        else:
            results.append({**tag, "connected": False, "exists": False,
                            "value": None, "quality": "Suspended",
                            "status_code": "", "source_ts": None,
                            "estancado_s": 0.0})
    return results


def check_connection(url: str | None = None, timeout_s: float | None = None) -> tuple[bool, str]:
    """Verifica conectividad con el KEPserver.

    Si `url` viene, se usa esa URL sin persistir (util para probar antes de
    guardar). Abre y cierra una sesión propia a proposito: el objetivo es
    probar un endpoint que quizas no es el configurado, y reusar la sesión del
    hilo mediria otra cosa.

    Devuelve (True, "") si OK, o (False, mensaje_error) si falla.
    """
    target = url or get_url()
    tmo = get_timeout_s() if timeout_s is None else min(
        max(_TIMEOUT_S_MIN, float(timeout_s)), _TIMEOUT_S_MAX)
    try:
        from opcua import Client  # type: ignore
        # El timeout va en el constructor. Antes se ajustaba `session_timeout`,
        # que es cuanto el SERVIDOR conserva la sesion (en ms) y no tiene nada
        # que ver con cuanto esperamos nosotros: el `connect()` quedaba sin
        # limite y esta funcion podia colgar la request HTTP que la llamo.
        c = Client(target, timeout=tmo)
        c.connect()
        c.disconnect()
        return True, ""
    except ImportError:
        return False, "Modulo 'opcua' no instalado. Ejecuta: pip install opcua"
    except Exception as e:
        return False, str(e)
