# -*- coding: utf-8 -*-
"""Export/Import: orden de filas y reglas, y el bloque `motor` (2026-09-24)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.state as st
from web.api import export_import as ei


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    fuzzy = tmp_path / "fuzzy.json"
    reglas = tmp_path / "reglas.json"
    motor = tmp_path / "motor.json"
    fuzzy.write_text(json.dumps({"pv_a": {"type": "norm", "offset": [0, 0.5, 1],
        "labels": {"OK": [0, 1, 0], "HIGH": [0, 0, 1], "LOW": [1, 0, 0]}}}), encoding="utf-8")
    reglas.write_text(json.dumps([
        {"id": "A", "priority": 50}, {"id": "X", "priority": 10},
        {"id": "B", "priority": 50}, {"id": "C", "priority": 50}]), encoding="utf-8")
    motor.write_text(json.dumps({"piso_s": 0.2, "auto_aplicar": False}), encoding="utf-8")
    for mod in (st, ei):
        monkeypatch.setattr(mod, "FUZZY_JSON", str(fuzzy))
        monkeypatch.setattr(mod, "REGLAS_JSON", str(reglas))
        monkeypatch.setattr(mod, "MOTOR_JSON", str(motor))
    from app import app
    with app.test_client() as c:
        c.p = {"fuzzy": fuzzy, "reglas": reglas, "motor": motor}
        yield c


def test_el_export_conserva_el_orden_de_las_filas_del_fuzzy(entorno):
    r = entorno.post("/api/export-import/export", json={"selected": {"fuzzy": True}})
    assert r.status_code == 200
    texto = r.get_data(as_text=True)
    assert texto.index('"OK"') < texto.index('"HIGH"') < texto.index('"LOW"')
    pkg = r.get_json()["package"]
    assert list(pkg["data"]["fuzzy"]["pv_a"]["labels"]) == ["OK", "HIGH", "LOW"]


def test_el_export_incluye_el_motor(entorno):
    pkg = entorno.post("/api/export-import/export",
                       json={"selected": {"motor": True}}).get_json()["package"]
    assert pkg["data"]["motor"] == {"piso_s": 0.2, "auto_aplicar": False}


def test_importar_motor_reemplazar_y_agregar(entorno):
    assert ei._aplicar_motor({"piso_s": 0.5, "auto_aplicar": True}, "agregar")["aplicados"] == []
    res = ei._aplicar_motor({"piso_s": 0.5, "auto_aplicar": True}, "reemplazar")
    assert res["aplicados"] == ["piso_s", "auto_aplicar"]
    assert json.loads(entorno.p["motor"].read_text()) == {"piso_s": 0.5, "auto_aplicar": True}


def test_reemplazar_reglas_aplica_el_orden_del_paquete(entorno):
    pkg = [{"id": "C", "priority": 50}, {"id": "B", "priority": 50}, {"id": "A", "priority": 50}]
    ei._aplicar_reglas(pkg, "reemplazar")
    ids = [r["id"] for r in json.loads(entorno.p["reglas"].read_text())]
    # X no venia en el paquete: no se mueve de su lugar.
    assert ids == ["C", "X", "B", "A"]


def test_agregar_reglas_no_reordena_las_existentes(entorno):
    ei._aplicar_reglas([{"id": "C", "priority": 50}, {"id": "N", "priority": 1}], "agregar")
    ids = [r["id"] for r in json.loads(entorno.p["reglas"].read_text())]
    assert ids == ["A", "X", "B", "C", "N"]


def test_el_motor_aparece_en_el_catalogo_de_modulos(entorno):
    ids = [m["id"] for m in entorno.get("/api/export-import/modulos").get_json()["modulos"]]
    assert "motor" in ids
