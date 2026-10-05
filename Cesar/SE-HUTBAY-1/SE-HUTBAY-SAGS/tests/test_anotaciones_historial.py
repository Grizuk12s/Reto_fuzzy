# -*- coding: utf-8 -*-
"""Dominio, pendiente y aceleracion POR INSTANTE para el Explorador de Series.

Antes el tooltip mostraba siempre los del ultimo tick, aunque el cursor
estuviera sobre un punto de hace 20 minutos.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.state as st

MAPEO = {"tag_to_pv": {"T.NIVEL": "nivel"}, "tag_to_cruda": {},
         "sp_to_tag": {"vel_sp": "T.SP"}}


def _tz(dom, slope, acel, signo="ACELERACION_POSITIVA"):
    return {"fuzzy": [
        {"var": "nivel", "valor": 60.0, "dom": dom, "lmin": 0, "lmax": 100},
        {"var": "pend_nivel", "es_pendiente": True, "fuente": "nivel",
         "slope_per_min": slope, "ventana_s": 60, "dom": "INC"},
        {"var": "acel_nivel", "es_aceleracion": True, "fuente": "nivel",
         "rate": 0.1, "aceleracion": acel, "ventana_s": 5,
         "dom": "ACELERANDO", "signo": signo},
    ]}


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    monkeypatch.setattr(st, "_anot_hist", st.deque(maxlen=st._ANOT_HIST_SIZE))


def test_cada_instante_conserva_su_propia_anotacion():
    st.registrar_anotaciones(_tz("LOW", 0.1, 0.01), MAPEO, ts=1000.0)
    st.registrar_anotaciones(_tz("HIGH", 0.9, 0.05), MAPEO, ts=1001.0)
    h = st.historial_anotaciones(0, 2e6, ["T.NIVEL"])
    assert [p[0] for p in h] == [1000000, 1001000]
    assert h[0][1]["T.NIVEL"]["dom"] == "LOW"
    assert h[0][1]["T.NIVEL"]["pendiente"]["slope_per_min"] == 0.1
    assert h[1][1]["T.NIVEL"]["dom"] == "HIGH"
    assert h[1][1]["T.NIVEL"]["aceleracion"]["aceleracion"] == 0.05
    assert h[1][1]["T.NIVEL"]["aceleracion"]["signo"] == "ACELERACION_POSITIVA"


def test_se_ralea_a_una_muestra_por_segundo():
    assert st.registrar_anotaciones(_tz("OK", 0, 0), MAPEO, ts=10.0)
    assert not st.registrar_anotaciones(_tz("OK", 0, 0), MAPEO, ts=10.4)
    assert st.registrar_anotaciones(_tz("OK", 0, 0), MAPEO, ts=11.0)
    assert len(st.historial_anotaciones(0, 1e9)) == 2


def test_filtra_por_rango_y_por_tag():
    for i in range(5):
        st.registrar_anotaciones(_tz("OK", i, 0), MAPEO, ts=100.0 + i)
    h = st.historial_anotaciones(101000, 103000, ["T.NIVEL"])
    assert [p[0] for p in h] == [101000, 102000, 103000]
    assert st.historial_anotaciones(0, 1e9, ["OTRO"])[0][1] == {}


def test_tags_sin_nada_que_decir_no_ocupan_memoria():
    st.registrar_anotaciones(_tz("OK", 0, 0), MAPEO, ts=5.0)
    _, comp = st._anot_hist[-1]
    assert "T.SP" not in comp            # el SP no tiene fuzzy
    assert "T.NIVEL" in comp


def test_el_formato_coincide_con_el_de_la_anotacion_actual():
    """El grafico usa las mismas funciones para 'ahora' y para el historial."""
    actual = st.anotaciones_por_tag(_tz("HIGH", 0.5, 0.02), MAPEO)["T.NIVEL"]
    st.registrar_anotaciones(_tz("HIGH", 0.5, 0.02), MAPEO, ts=7.0)
    hist = st.historial_anotaciones(0, 1e9, ["T.NIVEL"])[0][1]["T.NIVEL"]
    assert hist["dom"] == actual["dom"]
    assert hist["pendiente"]["slope_per_min"] == actual["pendiente"]["slope_per_min"]
    assert hist["pendiente"]["dom"] == actual["pendiente"]["dom"]
    for k in ("aceleracion", "dom", "signo"):
        assert hist["aceleracion"][k] == actual["aceleracion"][k]
