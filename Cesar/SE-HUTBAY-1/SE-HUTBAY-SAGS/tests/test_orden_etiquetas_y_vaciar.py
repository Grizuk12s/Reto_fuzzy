# -*- coding: utf-8 -*-
"""Orden de filas del fuzzy/pendientes y botones Vaciar (2026-09-24).

Flask ordenaba alfabeticamente las claves al serializar: las filas de un fuzzy
(OK, HIGH, LOW) volvian como HIGH, LOW, OK y al guardar ese orden quedaba en el
archivo. Ahora las rutas de fuzzy y pendientes responden en el orden del
archivo, que es el que el operador elige con las flechas.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.state as st
import web.api.config as C


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    rutas = {}
    for attr in ("FUZZY_JSON", "PENDIENTES_JSON", "VARIABLES_JSON",
                 "PERMISIVOS_JSON", "REGLAS_JSON"):
        ruta = str(tmp_path / (attr.lower() + ".json"))
        rutas[attr] = ruta
        monkeypatch.setattr(C, attr, ruta, raising=False)
        monkeypatch.setattr(st, attr, ruta, raising=False)
    waits = str(tmp_path / "waits.json")
    monkeypatch.setattr(st, "WAITS_JSON_PATH", waits)
    rutas["WAITS"] = waits
    from app import app
    with app.test_client() as c:
        c.rutas = rutas
        yield c


def _escribir(ruta, data):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def test_get_fuzzy_respeta_el_orden_de_las_filas(cliente):
    _escribir(cliente.rutas["FUZZY_JSON"], {"pv_x": {
        "type": "norm", "offset": [0, 0.5, 1],
        "labels": {"OK": [0, 1, 0], "HIGH": [0, 0, 1], "LOW": [1, 0, 0]}}})
    r = cliente.get("/api/fuzzy")
    assert r.status_code == 200
    # Se mira el TEXTO: json.loads de Python conservaria cualquier orden.
    texto = r.get_data(as_text=True)
    texto = texto[texto.index('"actual"'):]
    i_ok, i_high, i_low = (texto.index('"OK"'), texto.index('"HIGH"'),
                           texto.index('"LOW"'))
    assert i_ok < i_high < i_low
    assert list(r.get_json()["actual"]["pv_x"]["labels"]) == ["OK", "HIGH", "LOW"]


def test_get_pendientes_respeta_el_orden_de_las_filas(cliente):
    _escribir(cliente.rutas["PENDIENTES_JSON"], {"pend_x": {
        "variable": "pv_x", "ventana_s": 120, "x": [-1, 0, 1],
        "labels": {"STABLE": [0, 1, 0], "INC": [0, 0, 1], "DEC": [1, 0, 0]}}})
    texto = cliente.get("/api/pendientes").get_data(as_text=True)
    texto = texto[texto.index('"actual"'):texto.index('"fuentes"')]
    assert texto.index('"STABLE"') < texto.index('"INC"') < texto.index('"DEC"')


def test_las_demas_rutas_siguen_como_antes(cliente):
    """El cambio es acotado: el resto de la API no cambia su serializacion."""
    r = cliente.get("/api/reglas")
    assert r.status_code == 200


def test_vaciar_reglas(cliente):
    _escribir(cliente.rutas["REGLAS_JSON"], [{"id": "A", "priority": 1, "if": [], "then": []}])
    r = cliente.post("/api/reglas/reset")
    assert r.status_code == 200 and r.get_json()["borradas"] == 1
    assert _leer(cliente.rutas["REGLAS_JSON"]) == []


def test_vaciar_waits_informa_las_reglas_que_los_usan(cliente):
    _escribir(cliente.rutas["WAITS"], [{"wait_id": "wait::alto", "nombre": "Alto"}])
    _escribir(cliente.rutas["REGLAS_JSON"], [
        {"id": "R1", "priority": 1, "if": [],
         "then": [{"accion": "AUM", "waits": [{"wait_id": "wait::alto", "duracion_s": 5}]}]},
        {"id": "R2", "priority": 1, "if": [], "then": [{"accion": "AUM"}]},
    ])
    r = cliente.post("/api/waits/reset")
    assert r.status_code == 200
    assert r.get_json()["reglas_afectadas"] == ["R1"]
    assert _leer(cliente.rutas["WAITS"]) == []


def test_vaciar_permisivos(cliente):
    _escribir(cliente.rutas["PERMISIVOS_JSON"], {"BOMBA_OK": [{"var": "x", "op": ">", "value": 1}]})
    r = cliente.post("/api/permisivos/reset")
    assert r.status_code == 200
    assert _leer(cliente.rutas["PERMISIVOS_JSON"]) == {}


def test_vaciar_variables_deja_la_estructura_vacia(cliente):
    _escribir(cliente.rutas["VARIABLES_JSON"], {"crudas": {"a": {}}, "definiciones": [
        {"nombre": "calc_1", "tipo": "aritmetica"}]})
    r = cliente.post("/api/variables/reset")
    assert r.status_code == 200
    assert _leer(cliente.rutas["VARIABLES_JSON"]) == {"crudas": {}, "definiciones": []}
