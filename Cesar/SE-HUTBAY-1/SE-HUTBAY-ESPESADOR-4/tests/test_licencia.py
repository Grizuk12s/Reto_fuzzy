"""Licencia vencida: que se bloquea, que sigue andando y como se avisa.

Politica (2026-09-16, pedida por Cesar):

- El SE NO escribe setpoints al DCS (ni la escritura normal ni el espejo).
- El generador de datos de tags NO funciona: ni genera, ni graba, ni escribe.
- El heartbeat SI funciona: es la senal de vida que vigila el DCS.
- El SE SUELTA el control externo (ENABLE_EXT=False) una vez si la licencia
  vence con el motor corriendo, y no lo pide al arrancar sin licencia.
- La alerta "no hay licencia activa" esta SIEMPRE en la barra de alertas, aunque
  el operador la marque resuelta.

Los tests usan un `licencia.json` REAL firmado en un temporal y el
`_license_check` de produccion. La fixture `kep` de test_pipeline lo reemplaza
por un "siempre valida"; aca se restituye el verdadero.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime as _dt

import pytest

import web.state as st
from test_pipeline import (  # noqa: F401  (fixtures)
    kep, store, config_completa, _motor_ref_dcs, _tag_ref_del_dcs,
)

_LICENSE_CHECK_REAL = st._license_check


def _escribir_licencia(ruta, activated_at, expires_at, last_seen=None):
    last_seen = last_seen or _dt.now().replace(microsecond=0).isoformat()
    a = _dt.strptime(activated_at, "%Y-%m-%d")
    e = _dt.strptime(expires_at, "%Y-%m-%d")
    cfg = {
        "active": True, "months": 6, "type": "Licencia prototipo 6 meses",
        "activated_at": activated_at, "expires_at": expires_at,
        "capped": False, "history": [],
        "duration_seconds": float((e - a).days) * 86400.0 * 10,  # manda la fecha
        "consumed_seconds": 0.0, "last_seen_at": last_seen,
    }
    cfg["signature"] = st._license_sign(cfg)
    ruta.write_text(json.dumps(cfg), encoding="utf-8")


@pytest.fixture
def licencia(monkeypatch, tmp_path):
    """Devuelve `poner(estado)` con estado 'vencida' | 'vigente' | 'ausente'."""
    ruta = tmp_path / "licencia.json"
    monkeypatch.setattr(st, "LICENCIA_JSON", str(ruta))
    monkeypatch.setattr(st, "_license_check", _LICENSE_CHECK_REAL)
    monkeypatch.setattr(st, "_license_last_persist_ts", 0.0)

    def poner(estado):
        if ruta.exists():
            ruta.unlink()
        if estado == "vencida":
            _escribir_licencia(ruta, "2026-01-01", "2026-02-01")
        elif estado == "vigente":
            _escribir_licencia(ruta, "2026-01-01", "2099-01-01")
        return ruta

    return poner


def _alertas_licencia(coleccion):
    return [a for a in coleccion.get_active() if a["category"] == "licencia"]


# ---------------------------------------------------------------
# Verificacion de la fecha: vale el 12-oct, deja de valer el 13-oct
# ---------------------------------------------------------------

@pytest.mark.parametrize("ahora, valida", [
    ("2026-10-12T23:59:00", True),
    ("2026-10-13T00:00:30", False),
])
def test_la_licencia_de_planta_vence_el_13_de_octubre(licencia, monkeypatch, tmp_path,
                                                     ahora, valida):
    """Mismo formato que el licencia.json de la planta (tope 2026-10-12)."""
    ruta = tmp_path / "licencia.json"
    _escribir_licencia(ruta, "2026-07-20", "2026-10-12",
                       last_seen="2026-09-15T03:38:34")

    fijo = _dt.fromisoformat(ahora)

    class _Reloj(_dt):
        @classmethod
        def now(cls, tz=None):
            return fijo

    monkeypatch.setattr(st, "datetime", _Reloj)
    lic = st._license_check()
    assert lic["valid"] is valida
    assert lic["expired"] is (not valida)
    assert lic["tampered"] is False


# ---------------------------------------------------------------
# Setpoints
# ---------------------------------------------------------------

def test_con_licencia_vencida_el_SE_no_escribe_ningun_SP(store, kep, config_completa,
                                                         licencia, monkeypatch):
    col = st.AlertCollector()
    monkeypatch.setattr(st, "_alerts", col)
    licencia("vencida")
    eng = st.SEEngine()
    eng._init_state()

    for _ in range(5):
        eng._run_tick()

    assert kep["escrituras"] == [], "sin licencia no sale ningun SP hacia el DCS"
    assert eng._historial_escrituras == [] or len(eng._historial_escrituras) == 0
    assert "SE bloqueado" in (eng._last_error or "")
    alertas = _alertas_licencia(col)
    assert len(alertas) == 1, "una sola alerta, deduplicada"
    assert alertas[0]["message"] == st.LICENCIA_ALERTA_MSG


def test_control_con_licencia_vigente_el_SE_si_escribe(store, kep, config_completa,
                                                       licencia, monkeypatch):
    """Contraprueba: el bloqueo de arriba es la licencia y no otra cosa."""
    col = st.AlertCollector()
    monkeypatch.setattr(st, "_alerts", col)
    licencia("vigente")
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    assert len(kep["escrituras"]) == 1
    assert _alertas_licencia(col) == []


def test_con_licencia_vencida_el_espejo_tampoco_escribe(store, kep, config_completa,
                                                        licencia, monkeypatch, tmp_path):
    st._reset_trazas()
    tag_ref = _tag_ref_del_dcs(kep, 72.0)
    motor = _motor_ref_dcs(monkeypatch, tmp_path, kep, tag_ref, permiso=False)
    # Despues de armar el motor: el helper instala su propio AlertCollector.
    col = st.AlertCollector()
    monkeypatch.setattr(st, "_alerts", col)
    licencia("vencida")

    for _ in range(3):
        motor._run_tick()

    assert motor._sp_espejo, "habia un espejo pendiente de escribir"
    assert kep["escrituras"] == []
    assert _alertas_licencia(col)


def test_control_con_licencia_vigente_el_espejo_si_escribe(store, kep, config_completa,
                                                          licencia, monkeypatch, tmp_path):
    st._reset_trazas()
    tag_ref = _tag_ref_del_dcs(kep, 72.0)
    motor = _motor_ref_dcs(monkeypatch, tmp_path, kep, tag_ref, permiso=False)
    licencia("vigente")
    motor._run_tick()
    assert kep["escrituras"], "con licencia el espejo escribe"


def test_si_la_licencia_vence_con_el_motor_corriendo_deja_de_escribir(
        store, kep, config_completa, licencia, monkeypatch):
    from config import SETPOINT_KEYS
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    licencia("vigente")
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    n = len(kep["escrituras"])
    assert n == 1

    licencia("vencida")
    sp0 = SETPOINT_KEYS[0]
    eng._setpoints[sp0] = eng._setpoints[sp0] + 5.0    # el SE quiere mover el SP
    for _ in range(3):
        eng._run_tick()
    assert len(kep["escrituras"]) == n, "vencida a mitad de corrida: no escribe mas"


# ---------------------------------------------------------------
# Generador
# ---------------------------------------------------------------

def test_con_licencia_vencida_el_generador_no_genera_ni_graba_ni_escribe(
        licencia, monkeypatch):
    col = st.AlertCollector()
    monkeypatch.setattr(st, "_alerts", col)
    escritos, grabados = [], []
    monkeypatch.setattr(st._kep, "write_float_batch", lambda v: escritos.append(v))
    monkeypatch.setattr(st, "_record_tag_values", lambda v: grabados.append(v))
    licencia("vencida")

    gen = st.TagGenerator()
    gen._ranges = {"PLANTA.PV_x": {"min": 0.0, "max": 10.0, "noise": 0.0,
                                   "enabled": True, "vigente": True}}
    gen._last_values = {}
    for _ in range(3):
        gen._write_tick()

    assert escritos == [] and grabados == []
    assert gen._last_values == {}
    assert "Generador bloqueado" in (gen._last_error or "")
    assert _alertas_licencia(col)

    licencia("vigente")         # contraprueba
    gen._write_tick()
    assert len(escritos) == 1 and len(grabados) == 1


# ---------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------

def test_con_licencia_vencida_el_heartbeat_sigue_pulsando(licencia, monkeypatch):
    licencia("vencida")
    pulsos = []
    monkeypatch.setattr(st._kep, "write_tag",
                        lambda tag, val, dt: pulsos.append((tag, val)) or {"ok": True})
    monkeypatch.setattr(st._kep, "close_thread_client", lambda: None)

    hb = st.HeartbeatManager()
    monkeypatch.setattr(hb, "_load_config", lambda: None)
    hb._cfg = {**hb._cfg, "tag_out": "PLANTA.HB_OUT", "tag_in": "",
               "intervalo_s": 0.2, "value_a": 1, "value_b": 0}
    hb.start()
    try:
        limite = time.time() + 5
        while len(pulsos) < 3 and time.time() < limite:
            time.sleep(0.05)
    finally:
        hb.stop()

    assert len(pulsos) >= 3, f"el heartbeat tiene que pulsar sin licencia, pulso {pulsos}"
    assert {v for _, v in pulsos} == {0, 1}, "y alternar"
    assert hb._last_write_ok is True


# ---------------------------------------------------------------
# API: alerta constante, heartbeat habilitado, generador bloqueado
# ---------------------------------------------------------------

@pytest.fixture
def cliente(licencia):
    from app import app
    return app.test_client()


def test_la_alerta_de_licencia_vuelve_aunque_se_marque_resuelta(cliente, licencia):
    licencia("vencida")
    st._alerts.resolve_category("licencia")

    lista = cliente.get("/api/alerts").get_json()["alerts"]
    lic = [a for a in lista if a["category"] == "licencia"]
    assert len(lic) == 1 and lic[0]["message"] == st.LICENCIA_ALERTA_MSG

    cliente.post(f"/api/alerts/{lic[0]['id']}/resolve")
    lista = cliente.get("/api/alerts").get_json()["alerts"]
    assert [a for a in lista if a["category"] == "licencia"], (
        "sin licencia la alerta tiene que reaparecer en la proxima consulta")

    for _ in range(3):
        cliente.get("/api/alerts")
    lista = cliente.get("/api/alerts").get_json()["alerts"]
    assert len([a for a in lista if a["category"] == "licencia"]) == 1, "sin duplicados"


def test_la_alerta_de_licencia_se_cierra_al_activar(cliente, licencia):
    licencia("vencida")
    cliente.get("/api/alerts")
    licencia("vigente")
    lista = cliente.get("/api/alerts").get_json()["alerts"]
    assert not [a for a in lista if a["category"] == "licencia"]


def test_sin_archivo_de_licencia_tambien_alerta(cliente, licencia):
    licencia("ausente")
    lista = cliente.get("/api/alerts").get_json()["alerts"]
    assert [a for a in lista if a["category"] == "licencia"]
    st._alerts.resolve_category("licencia")


def test_api_sin_licencia_heartbeat_permitido_generador_bloqueado(cliente, licencia,
                                                                  monkeypatch):
    licencia("vencida")
    arrancados = []
    monkeypatch.setattr(st._heartbeat, "start", lambda: arrancados.append("hb"))
    monkeypatch.setattr(st._heartbeat, "_save_config", lambda: None)
    monkeypatch.setattr(st._tag_generator, "start", lambda: arrancados.append("gen"))

    r = cliente.post("/api/tags/heartbeat/start")
    assert r.status_code == 200 and arrancados == ["hb"]

    r = cliente.put("/api/tags/heartbeat", json={"intervalo_s": 2.0})
    assert r.status_code == 200

    r = cliente.post("/api/tags/generator/start")
    assert r.status_code == 403 and r.get_json()["license_expired"] is True
    assert arrancados == ["hb"], "el generador no arranca sin licencia"

    r = cliente.post("/api/tags/1/write", json={"value": 1.0})
    assert r.status_code == 403, "ni escritura manual de tags"
    st._alerts.resolve_category("licencia")


# ---------------------------------------------------------------
# Control externo (ENABLE_EXT): sin licencia el SE lo suelta
# ---------------------------------------------------------------

EXT = "PLANTA.HS_ENABLE_EXT"


@pytest.fixture
def ext(monkeypatch):
    """Registra las escrituras a ENABLE_EXT. `falla=True` simula un write rechazado."""
    estado = {"escrituras": [], "falla": False}

    def _write_tag(tag, valor, tipo):
        estado["escrituras"].append((tag, valor))
        return {"ok": not estado["falla"], "error": "rechazado" if estado["falla"] else None}

    monkeypatch.setattr(st._kep, "write_tag", _write_tag)
    return estado


def _motor_con_ext(monkeypatch):
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._handshake_enabled = True
    eng._handshake_fbk_tag = "PLANTA.HS_ENABLE_FBK"
    eng._handshake_ext_tag = EXT
    monkeypatch.setattr(eng, "LICENCIA_VIGILANCIA_S", 0.0)   # revisar en cada tick
    return eng


def test_si_la_licencia_vence_con_el_motor_corriendo_suelta_el_control_una_vez(
        store, kep, config_completa, licencia, ext, monkeypatch):
    licencia("vigente")
    eng = _motor_con_ext(monkeypatch)
    eng._control_pedido = True             # como lo deja start() con licencia

    for _ in range(3):
        eng._run_tick()
    assert ext["escrituras"] == [], "con licencia no se toca ENABLE_EXT"

    licencia("vencida")
    for _ in range(5):
        eng._run_tick()
    assert ext["escrituras"] == [(EXT, False)], "se suelta UNA vez, no en cada tick"
    assert eng._control_pedido is False
    assert any("solto el control" in a["message"] for a in st._alerts.get_active())

    licencia("vigente")
    for _ in range(3):
        eng._run_tick()
    assert ext["escrituras"] == [(EXT, False)], (
        "al reactivar NO se vuelve a pedir el control solo: se reinicia el motor")


def test_si_soltar_falla_se_reintenta(store, kep, config_completa, licencia, ext,
                                     monkeypatch):
    licencia("vencida")
    eng = _motor_con_ext(monkeypatch)
    eng._control_pedido = True
    ext["falla"] = True
    eng._run_tick()
    eng._run_tick()
    assert ext["escrituras"] == [(EXT, False), (EXT, False)]
    assert eng._control_pedido is True, "si el DCS no lo recibio, sigue pendiente"

    ext["falla"] = False
    eng._run_tick()
    eng._run_tick()
    assert len(ext["escrituras"]) == 3 and eng._control_pedido is False


def test_la_vigilancia_no_lee_la_licencia_en_cada_tick(store, kep, config_completa,
                                                      licencia, ext, monkeypatch):
    """En ciclo libre son ~20 tick/s: la revision va a 1 Hz."""
    licencia("vigente")
    eng = _motor_con_ext(monkeypatch)
    monkeypatch.setattr(eng, "LICENCIA_VIGILANCIA_S", 60.0)
    eng._control_pedido = True
    llamadas = []
    real = st._license_check
    monkeypatch.setattr(st, "_license_check", lambda: llamadas.append(1) or real())
    for _ in range(5):
        eng._soltar_control_si_no_hay_licencia()
    assert len(llamadas) == 1


@pytest.mark.parametrize("estado, pide", [("vigente", True), ("vencida", False)])
def test_start_solo_pide_el_control_con_licencia(store, kep, config_completa, licencia,
                                                 ext, monkeypatch, estado, pide):
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    monkeypatch.setattr(st, "_load_tags", lambda: {
        **store, "handshake": {"enabled": True, "enable_fbk_tag": "PLANTA.HS_ENABLE_FBK",
                               "enable_ext_tag": EXT}})
    licencia(estado)
    eng = st.SEEngine()
    monkeypatch.setattr(eng, "_worker", lambda: None)     # sin hilo de verdad
    assert eng.start()["ok"] is True
    try:
        assert ((EXT, True) in ext["escrituras"]) is pide
        assert eng._control_pedido is pide
        if not pide:
            assert [a for a in st._alerts.get_active() if a["category"] == "licencia"]
    finally:
        eng.stop()
    assert ext["escrituras"][-1] == (EXT, False), "detener sigue soltando, como siempre"
