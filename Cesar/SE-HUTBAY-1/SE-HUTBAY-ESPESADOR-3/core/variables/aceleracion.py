# -*- coding: utf-8 -*-
"""Aceleracion de una variable — ajuste cuadratico por minimos cuadrados.

Una PENDIENTE dice si la variable sube o baja. Una ACELERACION dice si ese
movimiento se esta agrandando o achicando. Son preguntas distintas y el
experto de planta necesita las dos: un nivel que sube 2 %/min y frena no pide
la misma accion que uno que sube 2 %/min y se esta escapando.

Sobre todas las muestras de la ventana se ajusta

    x(t) = a*t^2 + b*t + c

por minimos cuadrados, y de ahi salen las dos magnitudes:

    aceleracion = 2*a                    [u/s^2]
    rate        = 2*a*t_final + b        [u/s]   (la tasa AL FINAL de la ventana)

`rate` es la derivada en el extremo derecho, no el promedio de la ventana: es
lo que la variable esta haciendo AHORA, que es sobre lo que decide el experto.

Este modulo es hermano de `core/fuzzy/pendientes.py` y comparte sus decisiones
de fondo (ventana en segundos contra el `t_s` real, buffer decimado, no
afirmar nada hasta cubrir la ventana). Lo que NO comparte es el fuzzy: la
aceleracion produce estados NITIDOS, porque las preguntas que contesta son
categoricas y no admiten una escala de pertenencia calibrable.

Son DOS preguntas distintas sobre el mismo par (rate, aceleracion), y desde
2026-09-14 el modulo contesta las dos por separado:

**Dinamica** — que le esta pasando a la MAGNITUD del cambio:

    ACELERANDO     la variable esta AUMENTANDO la magnitud de su cambio
    DESACELERANDO  la esta DISMINUYENDO, suba o baje
    ESTABLE        ni el rate ni la aceleracion salen de su banda muerta

**Signo** — para que lado apunta la aceleracion, sin mirar el rate:

    ACELERACION_POSITIVA   2a > banda muerta
    ACELERACION_NEGATIVA   2a < -banda muerta
    ACELERACION_NULA       |2a| dentro de la banda muerta

Ojo con el criterio de la DINAMICA: **no** es el signo de la aceleracion. Una
variable que baja cada vez mas rapido tiene aceleracion negativa y esta
ACELERANDO. Lo que decide es si `rate` y `aceleracion` apuntan al mismo lado —
o sea si |rate| esta creciendo. Justamente por eso hacen falta las dos
familias: la dinamica no deja preguntar por el signo, y el signo no deja
preguntar si el movimiento se agranda.

    rate > 0, accel > 0  ->  ACELERANDO    + ACELERACION_POSITIVA
    rate > 0, accel < 0  ->  DESACELERANDO + ACELERACION_NEGATIVA
    rate < 0, accel < 0  ->  ACELERANDO    + ACELERACION_NEGATIVA
    rate < 0, accel > 0  ->  DESACELERANDO + ACELERACION_POSITIVA

Hay un caso sin etiqueta dinamica, y es deliberado: con la aceleracion dentro
de su banda muerta pero el rate FUERA de la suya, la variable se mueve a
velocidad practicamente constante. No esta acelerando ni desacelerando, y
tampoco esta ESTABLE — ESTABLE exige que las DOS magnitudes esten quietas. Se
reporta entonces solo con `ACELERACION_NULA`, y las tres `NO-<dinamica>`
quedan en 1. Decir ESTABLE ahi seria afirmar que el proceso esta quieto
mientras se escapa parejo.
"""
from __future__ import annotations

from collections import deque

import numpy as np


# Igual que en pendientes y en las variables calculadas: el buffer se decima
# para que el costo por tick no dependa de la velocidad del lazo. Con una
# ventana de 5 s el paso de decimacion queda en 0,02 s, o sea que en la
# practica entran TODAS las muestras disponibles, que es lo que se pidio.
MAX_MUESTRAS = 240

# Un cuadratico tiene tres incognitas: con menos de tres puntos el ajuste no
# esta determinado. No es un default configurable a la baja.
MIN_PUNTOS = 3

# Ventana por defecto, en segundos.
VENTANA_S_DEFAULT = 5.0

# Umbral de ESTABLE por defecto, en unidades de ingenieria por segundo al
# cuadrado. Es una PLANTILLA NEUTRA y hay que calibrarla mirando la variable
# real: la segunda derivada amplifica el ruido, asi que un umbral demasiado
# chico deja el estado saltando entre ACELERANDO y DESACELERANDO sin que el
# proceso haga nada.
UMBRAL_ESTABLE_DEFAULT = 0.01

ETIQUETA_ACELERANDO = "ACELERANDO"
ETIQUETA_DESACELERANDO = "DESACELERANDO"
ETIQUETA_ESTABLE = "ESTABLE"

ETIQUETA_ACEL_POSITIVA = "ACELERACION_POSITIVA"
ETIQUETA_ACEL_NEGATIVA = "ACELERACION_NEGATIVA"
ETIQUETA_ACEL_NULA = "ACELERACION_NULA"

# Las dos familias van SEPARADAS y no en una sola tupla plana porque la
# diferencia importa aguas arriba: de cada aceleracion se activa como mucho
# una dinamica y exactamente una de signo. Quien pinta el estado o arma las
# pertenencias necesita saber cual es cual.
ETIQUETAS_DINAMICAS = (ETIQUETA_ACELERANDO,
                       ETIQUETA_DESACELERANDO,
                       ETIQUETA_ESTABLE)

ETIQUETAS_SIGNO = (ETIQUETA_ACEL_POSITIVA,
                   ETIQUETA_ACEL_NEGATIVA,
                   ETIQUETA_ACEL_NULA)

# El catalogo completo. `web/state.py` lo recorre para ofrecer las etiquetas
# en reglas, estados y subestados, asi que agregar una etiqueta nueva sigue
# siendo un solo cambio en un solo lugar.
ETIQUETAS_ACELERACION = ETIQUETAS_DINAMICAS + ETIQUETAS_SIGNO


def nombre_sugerido(variable: str, ventana_s: float) -> str:
    """`<variable>_Acceleration_<n>s`, como pide el estandar.

    Es solo una SUGERENCIA para la interfaz. Igual que con las pendientes, el
    nombre lo elige el operador y queda congelado: derivarlo de la ventana
    haria que cambiar de 5 s a 10 s renombrara la variable y dejara mudas en
    silencio todas las reglas que la nombran.
    """
    v = float(ventana_s)
    n = int(v) if abs(v - int(v)) < 1e-9 else v
    return f"{variable}_Acceleration_{n}s"


def construir_registry_aceleraciones(cfg: dict) -> dict:
    """Arma las definiciones desde `aceleraciones.json`.

    cfg: {<nombre>: {"variable": str, "ventana_s": float,
                     "umbral_estable": float, "umbral_rate": float | None,
                     "min_puntos": int, "habilitado": bool}}

    Igual que `construir_registry_pendientes` y `construir_registry_fuzzy`,
    una entrada mal formada se OMITE en vez de romper el arranque: una config
    a medio escribir no puede dejar a la planta sin experto. Quien llama
    compara `cfg` contra `nombres` para avisar cuales se ignoraron.
    """
    registry: dict = {}
    for nombre, spec in (cfg or {}).items():
        if not isinstance(spec, dict):
            continue
        if not spec.get("habilitado", True):
            continue
        variable = str(spec.get("variable") or "").strip()
        if not variable:
            continue
        try:
            ventana_s = float(spec.get("ventana_s", VENTANA_S_DEFAULT))
            umbral = abs(float(spec.get("umbral_estable", UMBRAL_ESTABLE_DEFAULT)))
            min_puntos = int(spec.get("min_puntos", MIN_PUNTOS))
        except (TypeError, ValueError):
            continue
        if ventana_s <= 0.0:
            continue

        # Umbral de "rate practicamente nulo". Sirve para el arranque de un
        # movimiento: con rate 0 y aceleracion apreciable, el producto de
        # signos da 0 y sin esto el estado saldria DESACELERANDO justo cuando
        # la variable empieza a moverse. El default se DERIVA del umbral de
        # aceleracion (el rate que esa aceleracion produce en una ventana),
        # asi no hay un segundo numero que calibrar salvo que se quiera.
        umbral_rate = spec.get("umbral_rate")
        try:
            umbral_rate = (abs(float(umbral_rate)) if umbral_rate is not None
                           else umbral * ventana_s)
        except (TypeError, ValueError):
            umbral_rate = umbral * ventana_s

        registry[str(nombre)] = {
            "variable":       variable,
            "ventana_s":      ventana_s,
            "umbral_estable": umbral,
            "umbral_rate":    umbral_rate,
            "min_puntos":     max(MIN_PUNTOS, min_puntos),
        }
    return registry


def signo_aceleracion(aceleracion: float, umbral_estable: float) -> str:
    """Signo de la aceleracion contra su banda muerta. SIEMPRE devuelve una.

    Es la pregunta que la dinamica no deja hacer: ACELERANDO no dice para que
    lado, porque una variable que baja cada vez mas rapido y una que sube cada
    vez mas rapido son las dos ACELERANDO. La banda muerta es la misma que
    separa ESTABLE, para que no haya un tercer numero que calibrar.
    """
    u = abs(float(umbral_estable))
    a = float(aceleracion)
    if a > u:
        return ETIQUETA_ACEL_POSITIVA
    if a < -u:
        return ETIQUETA_ACEL_NEGATIVA
    return ETIQUETA_ACEL_NULA


def estado_aceleracion(rate: float, aceleracion: float,
                       umbral_estable: float,
                       umbral_rate: float) -> str:
    """Traduce (rate, aceleracion) al estado DINAMICO. `''` = ninguno.

    El criterio es la MAGNITUD del cambio, no el signo de la aceleracion:

        |accel| y |rate| los dos en banda        -> ESTABLE
        |accel| en banda y |rate| fuera          -> ''  (velocidad constante)
        rate y accel al mismo lado               -> ACELERANDO   (|rate| crece)
        rate y accel en sentidos opuestos        -> DESACELERANDO (|rate| baja)
        |rate| ~ 0 con accel apreciable          -> ACELERANDO   (arranca a moverse)

    **ESTABLE exige las DOS magnitudes quietas** (2026-09-14). Antes bastaba
    con `|accel| < umbral`, asi que una variable subiendo a velocidad constante
    salia ESTABLE: correcto como "la magnitud del cambio no cambia", pero se
    leia —y se usaba en reglas— como "el proceso esta quieto", que es lo
    contrario de lo que pasa. Ese caso ya no lleva etiqueta dinamica: se
    reporta con `ACELERACION_NULA`, que lo describe sin afirmar de mas.

    Devolver `''` y no una cuarta etiqueta es a proposito: las tres `NO-<X>`
    quedan en 1, que es exactamente lo que una regla necesita preguntar, y no
    aparece en el catalogo una etiqueta mas que calibrar.
    """
    if abs(float(aceleracion)) < float(umbral_estable):
        if abs(float(rate)) <= float(umbral_rate):
            return ETIQUETA_ESTABLE
        return ""
    if abs(rate) <= float(umbral_rate):
        # El movimiento esta empezando: la magnitud del cambio crece desde
        # cero. Sin este caso, el producto de signos da 0 y caeria en
        # DESACELERANDO, que es lo contrario de lo que pasa.
        return ETIQUETA_ACELERANDO
    return (ETIQUETA_ACELERANDO if rate * aceleracion > 0.0
            else ETIQUETA_DESACELERANDO)


def ajustar_cuadratica(muestras: list[tuple[float, float]]) -> tuple[float, float]:
    """(rate al final de la ventana, aceleracion) por minimos cuadrados.

    `muestras` es [(t_s, valor), ...] en orden. Devuelve `(None, None)` si el
    ajuste no esta determinado — todas las muestras en el mismo instante, o
    un sistema mal condicionado. No se devuelve 0.0: "no se puede saber" y
    "no se esta moviendo" son respuestas distintas, y confundirlas es
    exactamente lo que la regla de oro del proyecto prohibe.

    El tiempo se centra en la media de la ventana antes de ajustar. Con `t`
    crudo (o incluso con `t - t[0]`) las columnas [t^2, t, 1] quedan casi
    colineales y el sistema se mal condiciona: `a` — que es justo lo que se
    quiere — sale con error grande. Centrar es gratis y no cambia `a`.
    """
    t_vals = np.asarray([t for t, _ in muestras], dtype=float)
    y_vals = np.asarray([y for _, y in muestras], dtype=float)
    if t_vals.size < MIN_PUNTOS:
        return None, None

    t_fin = float(t_vals[-1])
    t_c = t_vals - float(t_vals.mean())
    if float(t_c.max() - t_c.min()) <= 0.0:
        return None, None

    A = np.vstack([t_c ** 2, t_c, np.ones_like(t_c)]).T
    try:
        coef, _res, rango, _sv = np.linalg.lstsq(A, y_vals, rcond=None)
    except np.linalg.LinAlgError:
        return None, None
    if rango < 3:
        # Muestras degeneradas (todas en dos instantes, por ejemplo): el
        # cuadratico no queda determinado.
        return None, None

    a, b, _c = (float(coef[0]), float(coef[1]), float(coef[2]))
    if not (np.isfinite(a) and np.isfinite(b)):
        return None, None

    aceleracion = 2.0 * a
    # `rate` en el extremo derecho de la ventana. Con el tiempo centrado, el
    # t_final del ajuste es (t_fin - t_medio).
    rate = aceleracion * (t_fin - float(t_vals.mean())) + b
    return float(rate), float(aceleracion)


class AceleracionesOnline:
    """Calcula el estado de aceleracion de un tick.

    Uso:
        ac = AceleracionesOnline(cfg)
        entradas, omitidas = ac.actualizar({"nivel": 71.2, ...}, t_s=12.3)

    `entradas` tiene la misma forma que una entrada de `fuzzy_out`, asi que se
    mezcla directamente y las reglas, los estados y los subestados la nombran
    como a cualquier otra variable. Las pertenencias son NITIDAS (1.0 / 0.0):
    el estado activo vale 1 y los otros dos 0, de modo que
    `expandir_etiquetas_compuestas` genere `NO-ACELERANDO` y
    `NO-DESACELERANDO` sin ningun caso especial.
    """

    def __init__(self, cfg: dict | None = None, max_muestras: int = MAX_MUESTRAS):
        self._registry = construir_registry_aceleraciones(cfg or {})
        self._max = max(4, int(max_muestras))
        self._hist: dict[str, deque] = {}
        self._t_inicio: dict[str, float] = {}

    @property
    def nombres(self) -> list[str]:
        return sorted(self._registry)

    def variables_fuente(self) -> set[str]:
        return {m["variable"] for m in self._registry.values()}

    def reset(self) -> None:
        """Olvida las ventanas. Se llama al arrancar el motor.

        Igual que la retencion del ultimo valor bueno: arrancar con la
        historia de hace media hora es exactamente lo que el arranque
        bumpless existe para evitar.
        """
        self._hist.clear()
        self._t_inicio.clear()

    # --------------------------------------------------------
    def _muestras(self, nombre: str, valor: float, t_s: float,
                  ventana_s: float) -> list[tuple[float, float]]:
        """Mismo esquema de decimacion que pendientes y calculadas.

        La muestra ACTUAL siempre cuenta aunque no se haya guardado: es la que
        define el extremo derecho, y `rate` se evalua justo ahi.
        """
        buf = self._hist.setdefault(nombre, deque())
        paso_min = float(ventana_s) / float(self._max)
        if not buf or (float(t_s) - buf[-1][0]) >= paso_min:
            buf.append((float(t_s), float(valor)))
        corte = float(t_s) - float(ventana_s)
        while buf and buf[0][0] < corte:
            buf.popleft()
        muestras = list(buf)
        if not muestras or muestras[-1][0] < float(t_s):
            muestras.append((float(t_s), float(valor)))
        return muestras

    # --------------------------------------------------------
    def actualizar(self, valores: dict, t_s: float) -> tuple[dict, list[str]]:
        """Devuelve ({nombre: entrada_fuzzy}, omitidas).

        Una aceleracion NO se produce hasta que la ventana este cubierta,
        mismo criterio que las pendientes: con dos segundos de historia una
        ventana de 5 s no puede decir si el proceso esta estable, y reportar
        ESTABLE mientras tanto seria afirmar algo que el motor no puede
        sostener — con reglas actuando sobre esa afirmacion. La variable no
        existe, las reglas que la nombran quedan `no_evaluable` y la traza
        dice cuantos segundos faltan.
        """
        out: dict = {}
        omitidas: list[str] = []

        for nombre, meta in self._registry.items():
            var = meta["variable"]
            if var not in (valores or {}):
                omitidas.append(f"{nombre} (sin dato de {var})")
                continue

            try:
                valor = float(valores[var])
            except (TypeError, ValueError):
                omitidas.append(f"{nombre} (valor no numerico de {var})")
                continue

            ventana_s = float(meta["ventana_s"])
            t0 = self._t_inicio.setdefault(nombre, float(t_s))
            muestras = self._muestras(nombre, valor, t_s, ventana_s)

            edad = float(t_s) - float(t0)
            if edad < ventana_s:
                faltan = max(0.0, ventana_s - edad)
                omitidas.append(
                    f"{nombre} (ventana incompleta: faltan {faltan:.0f} s)")
                continue
            if len(muestras) < int(meta["min_puntos"]):
                omitidas.append(
                    f"{nombre} (faltan muestras: {len(muestras)} de "
                    f"{meta['min_puntos']})")
                continue

            rate, aceleracion = ajustar_cuadratica(muestras)
            if rate is None:
                omitidas.append(f"{nombre} (ajuste no determinado)")
                continue

            estado = estado_aceleracion(rate, aceleracion,
                                        meta["umbral_estable"],
                                        meta["umbral_rate"])
            signo = signo_aceleracion(aceleracion, meta["umbral_estable"])
            # Dos etiquetas activas de las seis: una dinamica (o ninguna, con
            # el rate constante) y una de signo. Las dos familias conviven en
            # el MISMO `pert` a proposito: asi una regla puede pedir
            # `ACELERANDO` y otra `ACELERACION_NEGATIVA` sobre la misma
            # variable, y `expandir_etiquetas_compuestas` genera las seis
            # `NO-<X>` sin ningun caso especial.
            pert = {e: (1.0 if e in (estado, signo) else 0.0)
                    for e in ETIQUETAS_ACELERACION}

            out[nombre] = {
                # `dom` sigue siendo la DINAMICA y nada mas: es lo que ya leen
                # la traza, el tooltip del grafico y el CSS de las tarjetas.
                # El signo viaja aparte, en su propia clave.
                "dom": estado,
                "signo": signo,
                "val": 1.0,
                # `offset` es lo que el resto del pipeline lee como "el numero
                # de esta variable". Se pone la aceleracion, que es la
                # magnitud que le da nombre.
                "offset": float(aceleracion),
                "pert": pert,
                # Metadatos para la traza: sin esto una aceleracion quieta y
                # una mal calibrada se ven identicas.
                "aceleracion": float(aceleracion),
                "rate": float(rate),
                "variable": var,
                "ventana_s": ventana_s,
                "umbral_estable": float(meta["umbral_estable"]),
                "n_puntos": len(muestras),
                "es_aceleracion": True,
            }

        return out, omitidas
