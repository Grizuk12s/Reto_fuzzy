# -*- coding: utf-8 -*-
"""Generador senoidal + promedio / desviacion estandar del nivel del Hopper."""
from __future__ import annotations

import json
import math
import statistics

import pytest

import web.state as st
import web.api.se as api_se
from app import app
from core.variables.estadistica import (
    EstadisticaNivel, estadistica_de, normalizar_cfg,
)


# --------------------------------------------------------------------------
# Onda senoidal (dentro del Min/Max del random, no perfecta)
# --------------------------------------------------------------------------
LO, HI = 62.0, 78.0


def _serie(periodo, desde=0.0, hasta=600.0, paso=0.5, semilla="NIVEL", ruido=0.0):
    t, out = desde, []
    while t < hasta:
        out.append((t, st._sinusoidal_valor(t, periodo, LO, HI, semilla, ruido)))
        t += paso
    return out


@pytest.mark.parametrize("periodo", [30.0, 45.0])
def test_la_onda_nunca_sale_del_rango_del_random(periodo):
    for ruido in (0.0, 1.5, 6.0):
        vals = [v for _, v in _serie(periodo, ruido=ruido)]
        assert min(vals) >= LO - 1e-9 and max(vals) <= HI + 1e-9


def test_las_crestas_y_los_valles_cambian_de_un_ciclo_a_otro():
    """No es una onda perfecta: unas crestas son mas altas que otras."""
    crestas, valles = [], []
    for ciclo in range(40):
        base = ciclo * 30.0
        valles.append(st._sinusoidal_valor(base, 30, LO, HI, "NIVEL"))
        crestas.append(st._sinusoidal_valor(base + 15, 30, LO, HI, "NIVEL"))
    assert len(set(round(c, 3) for c in crestas)) > 20
    assert len(set(round(v, 3) for v in valles)) > 20
    assert max(crestas) - min(crestas) > 2.0 and max(valles) - min(valles) > 2.0
    mitad = (LO + HI) / 2
    assert all(c > mitad for c in crestas) and all(v < mitad for v in valles)


def test_es_una_onda_suave_que_sube_y_baja_en_cada_periodo():
    serie = _serie(30.0, 0.0, 30.0, 0.25)
    vals = [v for _, v in serie]
    assert vals[0] < vals[len(vals) // 2] > vals[-1]            # valle -> cresta -> valle
    saltos = [abs(b - a) for a, b in zip(vals, vals[1:])]
    assert max(saltos) < 1.2                                     # sin saltos bruscos


def test_es_reproducible_y_cada_tag_oscila_distinto():
    a = st._sinusoidal_valor(1234.5, 30, LO, HI, "NIVEL")
    assert a == st._sinusoidal_valor(1234.5, 30, LO, HI, "NIVEL")
    assert a != st._sinusoidal_valor(1234.5, 30, LO, HI, "OTRO")


def test_rango_invertido_y_periodo_absurdo():
    v = st._sinusoidal_valor(7.0, 30, HI, LO, "X")
    assert LO <= v <= HI
    c = st._sanear_sinusoidal({"sin_periodo_s": 0.01, "sin_min": 80, "sin_max": 70})
    assert c["sin_periodo_s"] == st.SIN_PERIODO_MIN_S
    assert "sin_min" not in c and "sin_max" not in c           # el rango es el del random
    assert st._sanear_sinusoidal({"sin_periodo_s": "x"})["sin_periodo_s"] == st.SIN_PERIODO_DEFAULT_S
    assert st._sinusoidal_valor(5.0, 30, 70.0, 70.0, "X") == 70.0   # rango sin ancho


@pytest.fixture()
def generador(monkeypatch):
    escritos = []
    monkeypatch.setattr(st, "_license_check", lambda: {"valid": True, "reason": ""})
    monkeypatch.setattr(st, "_load_tags", lambda: {"tags": [], "generator": {}})
    monkeypatch.setattr(st, "_save_tags", lambda d: None)
    monkeypatch.setattr(st, "_record_tag_values", lambda d: None)
    monkeypatch.setattr(st._kep, "write_float_batch", lambda d: escritos.append(dict(d)))
    g = st.TagGenerator()
    g._ranges = {
        "NIVEL": {"min": 62.0, "max": 78.0, "noise": 1.5, "enabled": True, "vigente": True,
                  "categoria": "pv", "sin_enabled": True, "sin_periodo_s": 30.0},
        "OTRO":  {"min": 10.0, "max": 20.0, "noise": 0.0, "enabled": True, "vigente": True,
                  "categoria": "pv"},
    }
    return g, escritos


def test_el_generador_usa_la_senoidal_solo_en_el_tag_marcado_y_dentro_de_min_max(generador, monkeypatch):
    g, escritos = generador
    for t in range(0, 300, 3):
        monkeypatch.setattr(st.time, "time", lambda t=t: float(t))
        g._write_tick()
    niveles = [e["NIVEL"] for e in escritos]
    assert all(62.0 <= v <= 78.0 for v in niveles)
    assert max(niveles) - min(niveles) > 8.0                  # oscila de verdad
    assert all(10.0 <= e["OTRO"] <= 20.0 for e in escritos)   # el resto sigue al azar


def test_cambiar_min_max_mueve_la_onda_y_el_periodo_se_edita(generador, monkeypatch):
    g, escritos = generador
    g.update_config(ranges={"NIVEL": {"min": 80, "max": 100, "sin_periodo_s": 45}})
    assert g._ranges["NIVEL"]["sin_periodo_s"] == 45.0
    for t in range(0, 200, 2):
        monkeypatch.setattr(st.time, "time", lambda t=t: float(t))
        g._write_tick()
    assert all(80.0 <= e["NIVEL"] <= 100.0 for e in escritos)


def test_sin_marcar_la_casilla_el_tag_vuelve_al_azar(generador, monkeypatch):
    g, escritos = generador
    g.update_config(ranges={"NIVEL": {"sin_enabled": False}})
    monkeypatch.setattr(st.time, "time", lambda: 15.0)
    g._write_tick()
    assert 62.0 <= escritos[-1]["NIVEL"] <= 78.0


# --------------------------------------------------------------------------
# Estadistica del nivel
# --------------------------------------------------------------------------
class _Reloj:
    t = 1_000_000.0

    def __call__(self):
        return self.t


def _est(tmp_path, valores):
    reloj = _Reloj()
    estado = {"corre": True, "v": 50.0}
    e = EstadisticaNivel(str(tmp_path / "estadistica.json"),
                         leer_valor=lambda: estado["v"],
                         esta_corriendo=lambda: estado["corre"], reloj=reloj)
    return e, reloj, estado


def test_estadistica_de_coincide_con_la_libreria_estandar():
    v = [70.0, 72.5, 68.1, 75.0, 71.2]
    r = estadistica_de(v)
    assert r["promedio"] == pytest.approx(statistics.mean(v))
    assert r["desv"] == pytest.approx(statistics.stdev(v))      # muestral
    assert estadistica_de([5.0])["desv"] is None
    assert estadistica_de([])["promedio"] is None


def test_calculo_periodico_cada_30_min_sobre_la_ventana(tmp_path):
    e, reloj, est = _est(tmp_path, None)
    onda = [60 + 10 * math.sin(i / 20) for i in range(30 * 60 + 5)]
    for i, v in enumerate(onda):
        est["v"] = v
        e.muestrear()
        if i < 30 * 60 - 1:
            assert e.estado()["calculo"] is None        # todavia no toca
        reloj.t += 1
    c = e.estado()["calculo"]
    assert c is not None and c["ventana_min"] == 30
    assert c["n"] >= 1790
    assert 55 < c["promedio"] < 65 and c["desv"] > 0


def test_el_rango_editable_cambia_la_ventana_del_calculo(tmp_path):
    e, reloj, est = _est(tmp_path, None)
    for i in range(600):                     # 10 min: 0..299 s en 40, resto en 80
        est["v"] = 40.0 if i < 300 else 80.0
        e.muestrear(); reloj.t += 1
    e.configurar(ventana_min=2)
    c = e.calcular_ahora()
    assert c["ventana_min"] == 2
    assert c["promedio"] == pytest.approx(80.0) and c["desv"] == pytest.approx(0.0)
    e.configurar(ventana_min=10)
    c = e.calcular_ahora()
    assert 55 < c["promedio"] < 65 and c["desv"] > 15


def test_motor_apagado_no_muestrea(tmp_path):
    e, reloj, est = _est(tmp_path, None)
    est["corre"] = False
    for _ in range(10):
        e.muestrear(); reloj.t += 1
    assert e.estado()["n_muestras"] == 0 and e.estado()["proximo_ts"] is None


def test_lectura_mala_no_entra_a_la_muestra(tmp_path):
    e, reloj, est = _est(tmp_path, None)
    for v in (50.0, None, float("nan"), True, 52.0):
        est["v"] = v
        e.muestrear(); reloj.t += 1
    assert e.estado()["n_muestras"] == 2


def test_la_config_se_acota_y_persiste(tmp_path):
    e, _, _ = _est(tmp_path, None)
    assert e.config() == {"ventana_min": 30, "recalculo_min": 30, "ventana_max_min": 1440}
    e.configurar(ventana_min=90, recalculo_min=15)
    assert json.load(open(tmp_path / "estadistica.json", encoding="utf-8"))["ventana_min"] == 90
    assert normalizar_cfg({"ventana_min": 99999})["ventana_min"] == 1440     # tope duro
    assert normalizar_cfg({"ventana_max_min": 60, "ventana_min": 120})["ventana_min"] == 60
    assert normalizar_cfg("basura") == {"ventana_min": 30, "recalculo_min": 30, "ventana_max_min": 1440}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    e, reloj, est = _est(tmp_path, None)
    monkeypatch.setattr(api_se, "_estadistica_nivel", e)
    with app.test_client() as c:
        yield c, e


def test_api_edita_el_rango_y_rechaza_lo_invalido(cliente):
    c, e = cliente
    r = c.put("/api/diagrama/estadistica", json={"ventana_min": 45, "recalculo_min": 10})
    assert r.status_code == 200 and r.get_json()["config"]["ventana_min"] == 45
    assert e.config()["recalculo_min"] == 10
    for malo in ({"ventana_min": 0}, {"ventana_min": 5000}, {"ventana_min": "abc"}, {"ventana_min": 2.5}):
        r = c.put("/api/diagrama/estadistica", json=malo)
        assert r.status_code == 400 and r.get_json()["ok"] is False
    assert e.config()["ventana_min"] == 45          # lo invalido no cambio nada


def test_api_calcular_ahora_y_estado(cliente):
    c, e = cliente
    d = c.post("/api/diagrama/estadistica/calcular").get_json()
    assert d["ok"] and d["calculo"] is not None
    assert c.get("/api/diagrama/estadistica").get_json()["config"]["ventana_min"] == 30


# --------------------------------------------------------------------------
# Siembra por defecto del nivel del Hopper A
# --------------------------------------------------------------------------
def test_el_nivel_del_hopper_a_nace_senoidal_y_respeta_la_decision_del_operador(monkeypatch):
    tags = [
        {"name": "H.NIVEL", "rol": "hopper_nvl_pv_a", "categoria": "pv", "enabled": True},
        {"name": "P.COR", "rol": "corriente", "categoria": "pv", "enabled": True},
    ]
    monkeypatch.setattr(st, "_load_tags", lambda: {"tags": tags, "generator": {}})
    monkeypatch.setattr(st, "_save_tags", lambda d: None)
    g = st.TagGenerator()
    g._ranges = {"H.NIVEL": {"min": 62.0, "max": 78.0, "noise": 1.5, "enabled": True}}
    g.sincronizar_con_tags()
    r = g._ranges["H.NIVEL"]
    assert r["sin_enabled"] is True and r["sin_periodo_s"] == 30.0
    assert "sin_min" not in r and "sin_max" not in r
    assert g._ranges["P.COR"]["sin_enabled"] is False        # el resto, al azar
    # El operador la apaga: no se vuelve a sembrar.
    g.update_config(ranges={"H.NIVEL": {"sin_enabled": False}})
    g.sincronizar_con_tags()
    assert g._ranges["H.NIVEL"]["sin_enabled"] is False
