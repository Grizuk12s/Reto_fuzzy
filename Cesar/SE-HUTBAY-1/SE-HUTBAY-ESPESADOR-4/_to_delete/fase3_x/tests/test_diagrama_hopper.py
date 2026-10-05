# -*- coding: utf-8 -*-
"""Pagina Diagrama: el endpoint del Hopper y su comportamiento con el motor.

- Motor ENCENDIDO: usa la ultima lectura del propio motor (`_last_read`), sin
  abrir otra sesion OPC-UA por refresco.
- Motor APAGADO: no toca el KEPserver; devuelve el ultimo valor del historial,
  marcado `en_vivo=false`.
"""
from __future__ import annotations

import pytest

import web.state as st
import web.api.se as api_se
from app import app


def _tags():
    def t(name, rol, cat, unidad="%"):
        return {"id": abs(hash(name)) % 10000, "name": name, "rol": rol,
                "categoria": cat, "enabled": True, "pseudonimo": name,
                "unidad_ing": unidad, "data_type": "Float"}
    return [
        t("H.NIVEL", "hopper_nvl_pv_a", "pv"),
        t("H.MIN", "hopper_nvl_pv_a_lmin", "lim"),
        t("H.MAX", "hopper_nvl_pv_a_lmax", "lim"),
        t("P.VEL", "velocidad_pv", "pv"),
        t("P.COR", "corriente", "pv", "Amp"),
        t("P.SPD", "velocidad_sp_del_dcs_referencia_de_arranque", "sp"),
        t("P.SPS", "velocidad_salida_del_se", "sp"),
    ]


class _Motor:
    def __init__(self, running, lectura=None):
        self._running = running
        self._last_read = lectura or {}

    def status(self):
        return {"running": self._running, "tick": 7, "t_s": 3.0, "generacion": 1,
                "periodo_ms": 50.0, "setpoints": {},
                "handshake": {"enabled": True, "ultimo": {"ok": True, "motivo": None}}}


@pytest.fixture()
def cliente(monkeypatch):
    monkeypatch.setattr(api_se, "_load_tags", lambda: {"tags": _tags()})
    with app.test_client() as c:
        yield c


def _lectura(valor, calidad="Good"):
    return {"connected": True, "exists": True, "value": valor, "quality": calidad}


def test_motor_encendido_usa_la_lectura_del_motor_sin_ir_al_kepserver(cliente, monkeypatch):
    lectura = {"H.NIVEL": _lectura(72.4), "H.MIN": _lectura(60.0), "H.MAX": _lectura(90.0),
               "P.VEL": _lectura(70.0), "P.COR": _lectura(78.0),
               "P.SPD": _lectura(75.0), "P.SPS": _lectura(72.5)}
    monkeypatch.setattr(api_se, "_se_engine", _Motor(True, lectura))

    def _no_debe_llamarse(_):
        raise AssertionError("no debe abrir otra lectura OPC con el motor encendido")
    monkeypatch.setattr(api_se, "_read_kepserver_tags_batch", _no_debe_llamarse)

    d = cliente.get("/api/diagrama/hopper").get_json()
    assert d["running"] is True and d["en_vivo"] is True and d["tick"] == 7
    v = d["variables"]
    assert v["nivel"]["valor"] == pytest.approx(72.4)
    assert v["nivel_min"]["valor"] == 60.0 and v["nivel_max"]["valor"] == 90.0
    assert v["vel_pv"]["valor"] == 70.0 and v["corriente"]["valor"] == 78.0
    assert v["vel_sp_dcs"]["valor"] == 75.0 and v["vel_sp_se"]["valor"] == 72.5
    assert v["corriente"]["unidad"] == "Amp"


def test_tag_que_el_motor_no_leyo_se_pide_al_kepserver(cliente, monkeypatch):
    monkeypatch.setattr(api_se, "_se_engine", _Motor(True, {"H.NIVEL": _lectura(50.0)}))
    pedidos = []

    def _batch(nombres):
        pedidos.extend(nombres)
        return {n: _lectura(1.0) for n in nombres}
    monkeypatch.setattr(api_se, "_read_kepserver_tags_batch", _batch)

    d = cliente.get("/api/diagrama/hopper").get_json()
    assert "H.NIVEL" not in pedidos and "P.COR" in pedidos
    assert d["variables"]["nivel"]["valor"] == 50.0
    assert d["variables"]["corriente"]["valor"] == 1.0


def test_calidad_mala_no_viaja_como_dato(cliente, monkeypatch):
    monkeypatch.setattr(api_se, "_se_engine",
                        _Motor(True, {"H.NIVEL": _lectura(50.0, "Bad")}))
    monkeypatch.setattr(api_se, "_read_kepserver_tags_batch",
                        lambda n: {x: _lectura(1.0) for x in n})
    d = cliente.get("/api/diagrama/hopper").get_json()
    assert d["variables"]["nivel"]["ok"] is False
    assert d["variables"]["nivel"]["valor"] is None


def test_motor_apagado_no_toca_el_kepserver_y_marca_no_en_vivo(cliente, monkeypatch):
    monkeypatch.setattr(api_se, "_se_engine", _Motor(False))

    def _no_debe_llamarse(_):
        raise AssertionError("con el motor apagado no se lee el KEPserver")
    monkeypatch.setattr(api_se, "_read_kepserver_tags_batch", _no_debe_llamarse)
    monkeypatch.setattr(api_se, "_ultimo_de_historial",
                        lambda nombres: {"H.NIVEL": {"value": 65.5, "t": 1.0}})

    d = cliente.get("/api/diagrama/hopper").get_json()
    assert d["running"] is False and d["en_vivo"] is False
    assert d["variables"]["nivel"]["valor"] == 65.5
    assert d["variables"]["corriente"]["valor"] is None      # sin historial: "--"


def test_rol_sin_tag_viaja_como_null(cliente, monkeypatch):
    monkeypatch.setattr(api_se, "_load_tags",
                        lambda: {"tags": [t for t in _tags() if t["rol"] != "corriente"]})
    monkeypatch.setattr(api_se, "_se_engine", _Motor(True, {}))
    monkeypatch.setattr(api_se, "_read_kepserver_tags_batch",
                        lambda n: {x: _lectura(1.0) for x in n})
    d = cliente.get("/api/diagrama/hopper").get_json()
    assert d["variables"]["corriente"] is None


def test_la_pagina_se_llama_diagrama_y_refresca_cada_segundo(cliente):
    html = cliente.get("/espesador/diagrama").get_data(as_text=True)
    assert "<h1>Diagrama</h1>" in html
    assert "/api/diagrama/hopper" in html
    assert "setInterval(poll, 1000)" in html
    for pag in ("/espesador/graficos", "/espesador/entrada"):
        assert ">Diagrama</a>" in cliente.get(pag).get_data(as_text=True)
    # PostgreSQL y Export / Import llevan el menu lateral comun (2026-10-02).
    for pag in ("/espesador/postgres", "/espesador/export-import", "/espesador"):
        assert 'href="/espesador/diagrama"' in cliente.get(pag).get_data(as_text=True), pag


def test_menu_lateral_comun(cliente):
    """Editor, PostgreSQL y Export / Import comparten el menu lateral (2026-10-02)."""
    for pag in ("/espesador", "/espesador/postgres", "/espesador/export-import"):
        html = cliente.get(pag).get_data(as_text=True)
        assert html.count('id="main-sidebar"') == 1, pag
        assert "<!--SE_SIDEBAR" not in html and "__SB_BASE__" not in html, pag
    assert 'href="#reglas"' in cliente.get("/espesador").get_data(as_text=True)
    assert 'href="/espesador#reglas"' in cliente.get("/espesador/postgres").get_data(as_text=True)


def test_motor_en_su_propia_pagina(cliente):
    """Motor, handshake, heartbeat y generador viven en /espesador/motor, con el
    menu lateral (Motor en Conexion); en Tags KEPserver quedan ocultos (2026-10-02)."""
    html = cliente.get("/espesador/motor").get_data(as_text=True)
    assert html.count('id="main-sidebar"') == 1
    for ruta in ("/api/se/start", "/api/se/stop", "/api/tags/handshake", "/api/tags/heartbeat",
                 "/api/tags/generator", "/api/se/auto-aplicar", "/api/se/restart"):
        assert ruta in html, ruta
    assert "/gestion/api/sags" not in html, "el Hopper no tiene router de SAG"
    menu = html[html.index('id="main-sidebar"'):]
    conexion = menu.split(">Conexion<", 1)[1].split("section-label", 1)[0]
    assert 'href="/espesador/motor"' in conexion
    editor = cliente.get("/espesador").get_data(as_text=True)
    assert "#system-control-box,#se-live-panel,#gen-panel,#hs-panel,#hb-panel{display:none!important}" in editor
    assert 'href="/espesador/motor"' in editor
