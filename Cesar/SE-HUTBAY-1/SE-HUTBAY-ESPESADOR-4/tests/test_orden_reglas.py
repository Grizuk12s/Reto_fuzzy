# -*- coding: utf-8 -*-
"""Orden entre reglas de la misma prioridad (flechas de la pagina de reglas).

A igual prioridad el motor evalua en el orden del archivo, y ese orden importa:
si dos reglas comparten un wait, en un mismo tick gana la primera.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.state as st
import web.api.config as C


def _r(i, prio):
    return {"id": i, "priority": prio, "bloque": "estabilidad",
            "if": [], "then": [], "enabled": True}


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    ruta = tmp_path / "reglas.json"
    ruta.write_text(json.dumps([_r("A", 50), _r("X", 90), _r("B", 50),
                                _r("C", 50), _r("Z", 10)]), encoding="utf-8")
    monkeypatch.setattr(st, "REGLAS_JSON", str(ruta))
    monkeypatch.setattr(C, "REGLAS_JSON", str(ruta))
    from app import app
    with app.test_client() as c:
        c.ruta = ruta
        yield c


def _ids(c):
    return [r["id"] for r in json.loads(c.ruta.read_text(encoding="utf-8"))]


def _visible(c):
    reglas = json.loads(c.ruta.read_text(encoding="utf-8"))
    return [reglas[i]["id"] for i in C.orden_visible_reglas(reglas)]


def test_el_orden_visible_es_el_del_motor(cliente):
    assert _visible(cliente) == ["X", "A", "B", "C", "Z"]


def test_bajar_intercambia_solo_con_la_vecina_de_igual_prioridad(cliente):
    r = cliente.post("/api/reglas/A/mover", json={"direccion": "abajo"})
    assert r.status_code == 200 and r.get_json()["con"] == "B"
    assert _visible(cliente) == ["X", "B", "A", "C", "Z"]
    # Las de otras prioridades no se mueven en el archivo.
    assert _ids(cliente) == ["B", "X", "A", "C", "Z"]


def test_subir(cliente):
    r = cliente.post("/api/reglas/C/mover", json={"direccion": "arriba"})
    assert r.status_code == 200
    assert _visible(cliente) == ["X", "A", "C", "B", "Z"]


def test_no_cruza_a_otra_prioridad(cliente):
    antes = _ids(cliente)
    assert cliente.post("/api/reglas/A/mover", json={"direccion": "arriba"}).status_code == 409
    assert cliente.post("/api/reglas/C/mover", json={"direccion": "abajo"}).status_code == 409
    assert _ids(cliente) == antes


def test_extremos_y_errores(cliente):
    assert cliente.post("/api/reglas/X/mover", json={"direccion": "arriba"}).status_code == 409
    assert cliente.post("/api/reglas/Z/mover", json={"direccion": "abajo"}).status_code == 409
    assert cliente.post("/api/reglas/NO/mover", json={"direccion": "abajo"}).status_code == 404
    assert cliente.post("/api/reglas/A/mover", json={"direccion": "lado"}).status_code == 400


def test_el_motor_evalua_primero_la_de_arriba(cliente):
    """El orden que se ve es el que usa `_evaluar_set_reglas`."""
    cliente.post("/api/reglas/C/mover", json={"direccion": "arriba"})
    reglas = json.loads(cliente.ruta.read_text(encoding="utf-8"))
    motor = sorted(reglas, key=lambda r: float(r.get("priority", 0.0)), reverse=True)
    assert [r["id"] for r in motor] == _visible(cliente)
