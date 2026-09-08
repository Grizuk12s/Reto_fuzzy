# -*- coding: utf-8 -*-
"""Red de seguridad del conector OPC-UA (B1.2).

No prueba que hablemos OPC-UA de verdad — eso lo prueba el simulador de DCS.
Prueba el manejo de la SESION, que es lo que se cambio: que se reuse, que se
cierre cuando se rompe, que un hilo no le pise la sesion a otro, y que una
caida de conexion no se confunda con instrumentos rotos.

El punto de sustitucion es `_conectar`: todo lo que hace el modulo con la red
pasa por ahi.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import types

import pytest
from opcua import ua

import connectors.kepserver as kep


class _DataValueFalso:
    """Lo justo del DataValue de OPC-UA que mira el conector."""

    def __init__(self, valor, status_code, source_ts=None):
        self.Value = types.SimpleNamespace(Value=valor)
        self.StatusCode = status_code
        self.SourceTimestamp = source_ts
        self.ServerTimestamp = None


class _NodoFalso:
    def __init__(self, client, nombre):
        self._client = client
        self._nombre = nombre

    def get_data_value(self):
        self._client.lecturas.append(self._nombre)
        err = self._client.errores.get(self._nombre)
        if err is not None:
            raise err
        return _DataValueFalso(
            self._client.valores.get(self._nombre, 1.0),
            self._client.calidades.get(self._nombre, ua.StatusCode(0)),
            self._client.estampas.get(self._nombre),
        )

    def set_value(self, _dv):
        self._client.escrituras.append(self._nombre)
        err = self._client.errores.get(self._nombre)
        if err is not None:
            raise err


class _ClientFalso:
    """Cliente OPC-UA de mentira, con la superficie que usa el conector."""

    def __init__(self, url):
        self.url = url
        self.conectado = True
        self.lecturas = []
        self.escrituras = []
        self.valores = {}
        self.errores = {}
        self.calidades = {}
        self.estampas = {}
        self.keepalive = None
        self.uaclient = self
        self.cierres = []

    def get_node(self, nodeid):
        return _NodoFalso(self, nodeid.split(";s=", 1)[1])

    def disconnect(self):
        self.conectado = False
        self.cierres.append("ordenado")

    def disconnect_socket(self):
        self.conectado = False
        self.cierres.append("socket")


@pytest.fixture
def opc(monkeypatch):
    """Reemplaza la conexion real. Expone los clientes creados, en orden."""
    estado = {"clientes": [], "falla_al_conectar": None, "valores": {},
              "errores": {}, "calidades": {}, "estampas": {}}

    def _conectar(url, timeout_s):
        if estado["falla_al_conectar"] is not None:
            raise estado["falla_al_conectar"]
        c = _ClientFalso(url)
        c.valores = estado["valores"]
        c.errores = estado["errores"]
        c.calidades = estado["calidades"]
        c.estampas = estado["estampas"]
        estado["clientes"].append(c)
        return c

    monkeypatch.setattr(kep, "_conectar", _conectar)
    monkeypatch.setattr(kep, "get_url", lambda: "opc.tcp://falso:49320")
    kep.close_all_clients()
    kep.reset_estancamiento()
    # La cosecha tiene un piso de 5 s entre pasadas; los tests la necesitan
    # activa en cada llamada.
    monkeypatch.setattr(kep, "_COSECHA_CADA_S", 0.0)
    yield estado
    kep.close_all_clients()
    kep.reset_estancamiento()


@pytest.fixture
def reloj(monkeypatch):
    """Reloj monotonico controlado. Devuelve un `avanzar(segundos)`.

    El estancamiento se mide en segundos reales; sin esto habria que dormir 60 s
    para probar un umbral de 60 s. Se parchea via monkeypatch para que quede
    restaurado aunque el test falle — es el reloj global del proceso.
    """
    t = [1000.0]
    monkeypatch.setattr(kep.time, "monotonic", lambda: t[0])

    def avanzar(segundos):
        t[0] += float(segundos)

    return avanzar


def _error_de_servidor():
    """Un StatusCode del servidor: respondio, y dijo que ese tag no sirve."""
    from opcua.ua.uaerrors import BadNodeIdUnknown
    return BadNodeIdUnknown()


# ---------------------------------------------------------------
# Reuso de sesion
# ---------------------------------------------------------------
def test_la_sesion_se_reusa_entre_lecturas(opc):
    """Es el motivo entero de B1.2: una sesion, no una por llamada."""
    for _ in range(5):
        kep.read_tags_batch(["A", "B"])
    assert len(opc["clientes"]) == 1
    assert kep.pool_status()["abiertas"] == 1


def test_la_sesion_se_reusa_entre_lectura_y_escritura(opc):
    kep.read_tags_batch(["A"])
    kep.write_float_batch({"SP": 1.0})
    kep.write_tag("SP", True, "Boolean")
    assert len(opc["clientes"]) == 1


def test_sin_conexion_la_lectura_no_lanza_y_todo_queda_desconectado(opc):
    opc["falla_al_conectar"] = OSError("timed out")
    res = kep.read_tags_batch(["A", "B"])
    assert all(not v["connected"] for v in res.values())
    assert all(v["value"] is None for v in res.values())
    assert kep.pool_status()["abiertas"] == 0


def test_sin_conexion_la_escritura_de_sp_propaga(opc):
    """`_write_setpoints` cuenta con la excepcion para reportar el fallo."""
    opc["falla_al_conectar"] = OSError("timed out")
    with pytest.raises(OSError):
        kep.write_float_batch({"SP": 1.0})


# ---------------------------------------------------------------
# Reconexion: transporte roto vs. tag malo
# ---------------------------------------------------------------
def test_un_tag_inexistente_no_tira_la_sesion(opc):
    """El servidor respondio: la conexion esta viva y el problema es del tag.

    Si esto cerrara la sesion, un solo tag mal escrito en tags.json haria
    reconectar en cada tick y volveriamos a una sesion por tick — en silencio,
    que es lo peor.
    """
    opc["errores"]["MALO"] = _error_de_servidor()
    res = kep.read_tags_batch(["BUENO", "MALO", "OTRO"])

    assert res["MALO"]["connected"] is True
    assert res["MALO"]["exists"] is False
    assert res["MALO"]["value"] is None
    assert res["MALO"]["quality"] == "Bad"
    # El nombre del status es el dato que manda a alguien a arreglar el tag.
    assert res["MALO"]["status_code"] == "BadNodeIdUnknown"
    assert res["OTRO"]["exists"] is True        # el lote siguio
    assert len(opc["clientes"]) == 1
    assert kep.pool_status()["abiertas"] == 1


def test_una_caida_de_transporte_cierra_la_sesion_y_la_siguiente_reconecta(opc):
    opc["errores"]["A"] = OSError("connection reset")
    kep.read_tags_batch(["A"])
    assert kep.pool_status()["abiertas"] == 0
    assert opc["clientes"][0].cierres == ["socket"]   # corte duro, sin saludar

    opc["errores"].clear()
    res = kep.read_tags_batch(["A"])
    assert res["A"]["exists"] is True
    assert len(opc["clientes"]) == 2


def test_una_caida_a_mitad_de_lote_no_marca_los_demas_como_instrumentos_malos(opc):
    """El hallazgo que motiva la clasificacion de errores.

    Con la sesion persistente, un socket que muere a mitad del lote dejaria a
    los tags que faltaban reportados como `exists=False, quality=Bad`: el SE
    veria "todos los instrumentos rotos" en vez de "me quede sin KEPserver".
    Y no reconectaria.

    Ademas acota el costo: sin cortar, cada tag restante espera su propio
    timeout. Con 27 tags a 2 s son 54 s en UN tick, con el motor imposible de
    parar (`stop()` hace join con 3 s).
    """
    tags = ["T1", "T2", "T3", "T4", "T5"]
    opc["errores"]["T2"] = OSError("connection reset")
    res = kep.read_tags_batch(tags)

    assert res["T1"]["exists"] is True
    for t in ("T2", "T3", "T4", "T5"):
        assert res[t]["connected"] is False, f"{t} deberia decir 'sin conexion'"
        assert res[t]["quality"] == "Unknown"

    # Se corto en T2: a T3..T5 no se les llego a preguntar.
    assert opc["clientes"][0].lecturas == ["T1", "T2"]


def test_una_escritura_interrumpida_no_marca_como_escritos_los_que_faltaban(opc):
    """Marcar como escrito lo que nunca salio rompe el write-on-change.

    `_write_setpoints` solo mueve `_sp_escritos` con lo que figura en
    `escritos`. Si un SP que no se intento apareciera ahi, el DCS se quedaria
    con el valor viejo y el SE convencido de haberlo mandado, para siempre.
    """
    opc["errores"]["SP2"] = OSError("connection reset")
    res = kep.write_float_batch({"SP1": 1.0, "SP2": 2.0, "SP3": 3.0})

    assert res["escritos"] == ["SP1"]
    assert set(res["fallidos"]) == {"SP2", "SP3"}
    assert kep.pool_status()["abiertas"] == 0


def test_un_sp_rechazado_por_el_dcs_no_tira_la_sesion(opc):
    """Un tag de solo lectura es un problema del tag, no de la conexion."""
    opc["errores"]["SP2"] = _error_de_servidor()
    res = kep.write_float_batch({"SP1": 1.0, "SP2": 2.0, "SP3": 3.0})

    assert sorted(res["escritos"]) == ["SP1", "SP3"]
    assert list(res["fallidos"]) == ["SP2"]
    assert kep.pool_status()["abiertas"] == 1


def test_write_tag_reconecta_tras_una_caida(opc):
    opc["errores"]["HB"] = OSError("broken pipe")
    r = kep.write_tag("HB", True, "Boolean")
    assert r["ok"] is False
    assert kep.pool_status()["abiertas"] == 0

    opc["errores"].clear()
    assert kep.write_tag("HB", True, "Boolean")["ok"] is True
    assert len(opc["clientes"]) == 2


# ---------------------------------------------------------------
# Aislamiento entre hilos
# ---------------------------------------------------------------
def test_dos_hilos_no_comparten_la_sesion(opc):
    """El motor y una request HTTP no deben esperarse.

    Es la razon por la que hay una sesion por hilo y no una sola con lock: con
    lock global, abrir la pagina de Tags esperaria al tick del lazo de control.
    """
    kep.read_tags_batch(["A"])
    urls = []

    def _otro_hilo():
        kep.read_tags_batch(["B"])
        urls.append(kep.pool_status()["abiertas"])

    t = threading.Thread(target=_otro_hilo, name="hilo-b")
    t.start(); t.join()

    assert len(opc["clientes"]) == 2
    assert urls == [2]


def test_se_cosecha_la_sesion_de_un_hilo_muerto(opc):
    """El servidor de desarrollo de Flask crea un hilo por request.

    Sin cosecha, cada carga de pagina dejaria una sesion abierta una hora en el
    KEPserver.
    """
    t = threading.Thread(target=lambda: kep.read_tags_batch(["A"]), name="efimero")
    t.start(); t.join()
    assert kep.pool_status()["abiertas"] == 1

    kep.read_tags_batch(["B"])          # cualquier uso dispara la cosecha
    estado = kep.pool_status()
    assert estado["abiertas"] == 1
    assert estado["detalle"][0]["hilo"] != "efimero"
    assert opc["clientes"][0].conectado is False


def test_close_thread_client_cierra_ordenado_y_es_idempotente(opc):
    kep.read_tags_batch(["A"])
    kep.close_thread_client()
    assert kep.pool_status()["abiertas"] == 0
    assert opc["clientes"][0].cierres == ["ordenado"]
    kep.close_thread_client()           # sin sesion: no debe reventar


# ---------------------------------------------------------------
# Cambios de configuracion
# ---------------------------------------------------------------
def test_cambiar_de_host_no_deja_leyendo_del_kepserver_viejo(opc, monkeypatch):
    """Peor que fallar: seguir devolviendo valores buenos del servidor equivocado."""
    kep.read_tags_batch(["A"])
    monkeypatch.setattr(kep, "get_url", lambda: "opc.tcp://otro:49320")
    kep.read_tags_batch(["A"])

    assert [c.url for c in opc["clientes"]] == ["opc.tcp://falso:49320",
                                                "opc.tcp://otro:49320"]
    assert opc["clientes"][0].cierres == ["ordenado"]
    assert kep.pool_status()["abiertas"] == 1


def test_el_timeout_se_acota_a_un_rango_sano(monkeypatch):
    """`timeout_s: 0` significaria no esperar nada: ninguna lectura andaria."""
    monkeypatch.setattr(kep, "_load_config", lambda: {"timeout_s": 0.0})
    assert kep.get_timeout_s() == kep._TIMEOUT_S_MIN
    monkeypatch.setattr(kep, "_load_config", lambda: {"timeout_s": 9999.0})
    assert kep.get_timeout_s() == kep._TIMEOUT_S_MAX
    monkeypatch.setattr(kep, "_load_config", lambda: {"timeout_s": "nada"})
    assert kep.get_timeout_s() == kep._DEFAULT_TIMEOUT_S


def test_el_timeout_configurado_llega_al_cliente(opc, monkeypatch):
    visto = []
    monkeypatch.setattr(kep, "get_timeout_s", lambda: 1.25)
    real = kep._conectar

    def _spy(url, timeout_s):
        visto.append(timeout_s)
        return real(url, timeout_s)

    monkeypatch.setattr(kep, "_conectar", _spy)
    kep.read_tags_batch(["A"])
    assert visto == [1.25]


# ---------------------------------------------------------------
# Calidad de dato real (B1.4)
# ---------------------------------------------------------------
def test_la_calidad_sale_del_status_code_y_no_de_la_ausencia_de_excepcion(opc):
    """El corazon de B1.4.

    Antes se leia con `get_value()`, que descarta el StatusCode, y se rellenaba
    "Good" si no habia excepcion. Un instrumento que el servidor reportaba
    `Uncertain` se veia identico a uno sano.
    """
    opc["calidades"]["SANO"] = ua.StatusCode(0)
    opc["calidades"]["DUDOSO"] = ua.StatusCode(0x40930000)     # UncertainSensorNotAccurate
    opc["calidades"]["ROTO"] = ua.StatusCode(0x80AE0000)       # BadSensorFailure
    res = kep.read_tags_batch(["SANO", "DUDOSO", "ROTO"])

    assert res["SANO"]["quality"] == "Good"
    assert res["DUDOSO"]["quality"] == "Uncertain"
    assert res["ROTO"]["quality"] == "Bad"
    # El nombre exacto sobrevive: "Bad" no dice a donde ir, el nombre si.
    assert res["DUDOSO"]["status_code"] == "UncertainSensorNotAccurate"


def test_un_valor_con_status_bad_no_cuenta_como_existente(opc):
    """Un Bad CON valor es el servidor avisando que no lo tomes.

    Es el caso traicionero: llega un numero, no hay excepcion. Si `exists`
    quedara en True, aguas abajo habria que revisar dos campos — y alguna de las
    tres lecturas del motor se olvidaria de uno.
    """
    opc["valores"]["ROTO"] = 42.0
    opc["calidades"]["ROTO"] = ua.StatusCode(0x80AE0000)
    res = kep.read_tags_batch(["ROTO"])

    assert res["ROTO"]["exists"] is False
    assert res["ROTO"]["value"] == 42.0     # se conserva para diagnostico
    assert res["ROTO"]["quality"] == "Bad"


def test_un_status_code_ilegible_no_pasa_por_bueno(opc):
    """Fail-closed: si no se entiende la calidad, no es Good."""
    opc["calidades"]["RARO"] = object()
    res = kep.read_tags_batch(["RARO"])
    assert res["RARO"]["quality"] == "Unknown"
    assert res["RARO"]["exists"] is True     # Unknown no es Bad; lo juzga state.py


def test_el_estancamiento_mide_el_valor_y_no_el_timestamp(opc, reloj):
    """La detección de congelado, con la razón por la que no usa el SourceTimestamp.

    Verificado contra el servidor real: el SourceTimestamp avanza en CADA
    lectura aunque el valor no se mueva, asi que como detector de congelado da
    siempre "fresco". Este test fija esa decision: la estampa cambia todo el
    tiempo y el estancamiento igual crece.
    """
    import datetime as dt

    opc["valores"]["QUIETO"] = 5.0
    base = dt.datetime(2026, 8, 25, 12, 0, 0)

    for i in range(3):
        opc["estampas"]["QUIETO"] = base + dt.timedelta(seconds=i)
        res = kep.read_tags_batch(["QUIETO"])
        reloj(30.0)

    # Dos intervalos de 30 s con el mismo valor, pese a tres estampas distintas.
    assert res["QUIETO"]["estancado_s"] == pytest.approx(60.0)
    assert res["QUIETO"]["source_ts"] == "2026-08-25T12:00:02"


def test_el_estancamiento_se_reinicia_cuando_el_valor_cambia(opc, reloj):
    opc["valores"]["PV"] = 1.0
    kep.read_tags_batch(["PV"])
    reloj(45.0)
    assert kep.read_tags_batch(["PV"])["PV"]["estancado_s"] == pytest.approx(45.0)

    opc["valores"]["PV"] = 1.5
    reloj(5.0)
    assert kep.read_tags_batch(["PV"])["PV"]["estancado_s"] == 0.0


def test_aceptar_uncertain_es_configurable_y_por_defecto_no(monkeypatch):
    monkeypatch.setattr(kep, "_load_config", lambda: {})
    assert kep.get_aceptar_uncertain() is False
    monkeypatch.setattr(kep, "_load_config", lambda: {"aceptar_uncertain": True})
    assert kep.get_aceptar_uncertain() is True


def test_el_umbral_de_estancamiento_tolera_basura_y_no_admite_negativos(monkeypatch):
    monkeypatch.setattr(kep, "_load_config", lambda: {"estancado_alerta_s": "nada"})
    assert kep.get_estancado_alerta_s() == kep._DEFAULT_ESTANCADO_ALERTA_S
    monkeypatch.setattr(kep, "_load_config", lambda: {"estancado_alerta_s": -5})
    assert kep.get_estancado_alerta_s() == 0.0
