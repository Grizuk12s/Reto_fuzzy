# -*- coding: utf-8 -*-
"""El ritmo del lazo (`piso_s`) se configura, persiste y se aplica en vivo.

Antes el piso viajaba hardcodeado desde el navegador (`startSystem()` mandaba
0.05 fijo): no habia forma de cambiarlo sin editar la plantilla, y nadie veia
el ritmo real que el motor estaba logrando. Estos tests cubren las tres
propiedades que eso rompia.
"""
from __future__ import annotations

import json
import os

import pytest

import web.state as st
from app import app


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    """Cliente Flask con motor.json aislado: no se toca la config de la planta."""
    ruta = str(tmp_path / "motor.json")
    monkeypatch.setattr(st, "MOTOR_JSON", ruta)
    import web.api.se as api_se
    # `cargar_motor_cfg` se importo por nombre en el blueprint: hay que
    # repuntarlo o seguiria leyendo el modulo con la ruta vieja.
    monkeypatch.setattr(api_se, "cargar_motor_cfg", st.cargar_motor_cfg)
    with app.test_client() as c:
        yield c, ruta


def test_sin_archivo_cae_al_default_y_no_explota(cliente):
    c, ruta = cliente
    assert not os.path.exists(ruta)
    d = c.get("/api/se/piso").get_json()
    assert d["piso_s"] == pytest.approx(st.SEEngine.PISO_S_DEFAULT)


def test_guardar_persiste_en_disco_y_se_relee(cliente):
    c, ruta = cliente
    r = c.put("/api/se/piso", json={"piso_ms": 250})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert json.load(open(ruta, encoding="utf-8"))["piso_s"] == pytest.approx(0.25)
    assert c.get("/api/se/piso").get_json()["piso_s"] == pytest.approx(0.25)


def test_se_aplica_en_vivo_sin_reiniciar(cliente, monkeypatch):
    """Cambiar el piso NO puede pasar por un reinicio: vaciaria las ventanas."""
    c, _ = cliente
    llamadas = []
    monkeypatch.setattr(st._se_engine, "reiniciar",
                        lambda *a, **k: llamadas.append(1) or {"ok": True})
    c.put("/api/se/piso", json={"piso_ms": 120})
    assert st._se_engine._piso_s == pytest.approx(0.12)
    assert llamadas == [], "el piso se aplico reiniciando el motor"


@pytest.mark.parametrize("body", [{"piso_ms": 999999}, {"piso_s": -1}, {}, {"piso_s": "x"}])
def test_valores_invalidos_se_rechazan(cliente, body):
    c, _ = cliente
    r = c.put("/api/se/piso", json=body)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_un_archivo_corrupto_no_deja_al_motor_sin_ritmo(cliente):
    c, ruta = cliente
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("{ esto no es json")
    assert c.get("/api/se/piso").get_json()["piso_s"] == pytest.approx(
        st.SEEngine.PISO_S_DEFAULT)


def test_el_status_expone_las_metricas_del_lazo(cliente):
    """Son las que dicen si el piso limita o si manda la duracion del tick."""
    c, _ = cliente
    d = c.get("/api/se/piso").get_json()
    for k in ("periodo_ms", "dur_tick_ms", "dur_tick_max_ms", "dormido_ms", "ticks_por_s"):
        assert k in d
