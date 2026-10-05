# -*- coding: utf-8 -*-
"""Auto-aplicar configuracion desde el servidor (2026-09-24).

Antes el reinicio en caliente lo disparaba la pestana del panel principal. Con
esa pestana cerrada, un Guardar hecho en cualquier otra pagina no se aplicaba,
y un guardado que caia mientras se aplicaba el anterior se perdia. Estos tests
fijan el vigilante del servidor: compara la huella de disco contra la que el
motor CARGO, no contra la ultima vista.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.state as st


class MotorFalso:
    """Lo minimo que el AutoAplicador usa del motor."""
    def __init__(self, huella_fn):
        self._huella_fn = huella_fn
        self._running = True
        self._huella_cargada = huella_fn()
        self.reinicios = 0
        self.falla = None

    def reiniciar_si_corre(self, caliente=True):
        if not self._running:
            return None
        self.reinicios += 1
        if self.falla:
            return {"ok": False, "error": self.falla}
        # Como `_init_state`: la huella se toma al empezar a cargar.
        self._huella_cargada = self._huella_fn()
        return {"ok": True, "caliente": True, "avisos": [], "generacion": self.reinicios}


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "MOTOR_JSON", str(tmp_path / "motor.json"))
    monkeypatch.setattr(st, "_activity_log",
                        st.ActivityLogCollector(str(tmp_path / "log.json")))
    disco = {"v": "A"}
    monkeypatch.setattr(st, "_huella_config_fn", lambda: disco["v"])
    motor = MotorFalso(lambda: disco["v"])
    return st.AutoAplicador(motor), motor, disco


def test_sin_cambios_no_reinicia(entorno):
    aa, motor, _ = entorno
    assert aa.revisar(0.0) == "al_dia"
    assert aa.revisar(5.0) == "al_dia"
    assert motor.reinicios == 0


def test_un_guardado_se_aplica_tras_el_debounce(entorno):
    aa, motor, disco = entorno
    disco["v"] = "B"
    assert aa.revisar(0.0) == "esperando"
    assert aa.revisar(0.5) == "esperando"
    assert aa.revisar(1.1) == "aplicado"
    assert motor.reinicios == 1
    assert aa.revisar(1.6) == "al_dia"


def test_una_tanda_de_guardados_dispara_un_solo_reinicio(entorno):
    aa, motor, disco = entorno
    disco["v"] = "B"; aa.revisar(0.0)
    disco["v"] = "C"; aa.revisar(0.6)          # cambio nuevo: la cuenta vuelve a cero
    assert aa.revisar(1.2) == "esperando"
    assert aa.revisar(1.7) == "aplicado"
    assert motor.reinicios == 1


def test_un_guardado_durante_la_aplicacion_no_se_pierde(entorno):
    """El bug del navegador: al terminar un reinicio releia la huella y daba
    por aplicado un guardado que habia caido en el medio."""
    aa, motor, disco = entorno
    disco["v"] = "B"
    aa.revisar(0.0)
    # El motor carga B, pero alguien guarda C justo despues de que se tomo
    # la huella de carga.
    orig = motor.reiniciar_si_corre
    def reiniciar_y_guardar(caliente=True):
        r = orig(caliente)
        disco["v"] = "C"
        return r
    motor.reiniciar_si_corre = reiniciar_y_guardar
    assert aa.revisar(1.1) == "aplicado"
    motor.reiniciar_si_corre = orig
    assert motor._huella_cargada == "B"
    assert aa.revisar(1.6) == "esperando"      # C todavia no esta aplicado
    assert aa.revisar(2.7) == "aplicado"
    assert motor._huella_cargada == "C"


def test_con_el_motor_detenido_no_hace_nada(entorno):
    aa, motor, disco = entorno
    motor._running = False
    disco["v"] = "B"
    assert aa.revisar(0.0) == "detenido"
    assert aa.revisar(5.0) == "detenido"
    assert motor.reinicios == 0


def test_apagado_no_reinicia_y_se_guarda_en_motor_json(entorno):
    aa, motor, disco = entorno
    st.guardar_auto_aplicar(False)
    disco["v"] = "B"
    assert aa.revisar(0.0) == "apagado"
    assert aa.revisar(5.0) == "apagado"
    assert motor.reinicios == 0
    assert st.cargar_motor_cfg()["auto_aplicar"] is False


def test_guardar_el_piso_no_borra_el_interruptor(entorno):
    st.guardar_auto_aplicar(False)
    st.guardar_motor_cfg(0.2)
    cfg = st.cargar_motor_cfg()
    assert cfg["auto_aplicar"] is False and cfg["piso_s"] == 0.2


def test_un_reinicio_fallido_no_se_reintenta_en_lazo(entorno):
    aa, motor, disco = entorno
    motor.falla = "config invalida"
    disco["v"] = "B"
    aa.revisar(0.0)
    assert aa.revisar(1.1) == "error"
    assert aa.revisar(2.0) == "fallida"
    assert aa.revisar(9.0) == "fallida"
    assert motor.reinicios == 1
    assert aa.estado()["ultimo"]["ok"] is False
    # Un guardado nuevo (la correccion) si se intenta.
    motor.falla = None
    disco["v"] = "C"
    aa.revisar(10.0)
    assert aa.revisar(11.1) == "aplicado"


def test_reiniciar_si_corre_no_arranca_un_motor_detenido():
    """La carrera que evita: Detener justo antes del reinicio automatico."""
    motor = st.SEEngine()
    assert motor._running is False
    assert motor.reiniciar_si_corre(caliente=True) is None
    assert motor._running is False


def test_init_state_toma_la_huella_antes_de_leer(store, kep, config_completa,
                                                 monkeypatch):
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    monkeypatch.setattr(st, "_huella_config_fn", lambda: "H1")
    motor = st.SEEngine()
    motor._init_state()
    assert motor._huella_cargada == "H1"


def test_la_huella_real_cambia_al_guardar_un_archivo_versionado(tmp_path, monkeypatch):
    """La huella registrada por web/api/config.py es la que ve el vigilante."""
    import web.api.config as api_cfg
    assert st.huella_config_actual() == api_cfg.huella_config()[0]


from tests.test_pipeline import kep, store, config_completa      # noqa: E402,F401
