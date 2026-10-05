# -*- coding: utf-8 -*-
"""La ventana de una pendiente se declara en SEGUNDOS y se sintoniza contra el filtro.

Dos cosas que antes no existian:

* La ventana venia en MINUTOS (`ventana_min`). Pedir 20 s obligaba a escribir
  0.33, que es justo el rango util cuando la pendiente se sintoniza contra el
  filtro. Ahora se declara en segundos y `ventana_min` se sigue LEYENDO para no
  romper los archivos que ya estan en disco.
* No habia ninguna comprobacion de la relacion 2x-4x contra la ventana del
  filtro de la variable fuente. Bajar el filtro a 10 s y dejar la pendiente en
  2 min pasaba inadvertido, y eso se ve en planta como "el SE reacciona tarde",
  no como un error de configuracion.
"""
from __future__ import annotations

import pytest

from core.fuzzy.pendientes import ventana_s_de_spec, construir_registry_pendientes


# ------------------------------------------------------------
# Nucleo: de donde sale la ventana
# ------------------------------------------------------------

def test_ventana_s_manda_sobre_ventana_min():
    """Si estan las dos, gana la nueva. Un archivo a medio migrar no cambia
    de comportamiento segun el orden de las claves."""
    assert ventana_s_de_spec({"ventana_s": 30, "ventana_min": 5}) == 30.0


def test_ventana_min_se_sigue_leyendo_en_segundos():
    assert ventana_s_de_spec({"ventana_min": 2}) == 120.0


@pytest.mark.parametrize("spec", [{}, {"ventana_s": 0}, {"ventana_s": -1},
                                  {"ventana_s": "x"}, {"ventana_min": 0}, None])
def test_una_ventana_invalida_no_inventa_un_default(spec):
    """Sin ventana valida la pendiente NO se construye. Sembrarle un default
    daria una tendencia que nadie pidio, medida sobre una ventana inventada."""
    assert ventana_s_de_spec(spec) is None


def test_el_registry_usa_los_segundos_declarados():
    reg = construir_registry_pendientes({
        "pend_a": {"variable": "nivel", "ventana_s": 20,
                   "x": [-1.0, 0.0, 1.0],
                   "labels": {"DEC": [1.0, 0.0, 0.0], "INC": [0.0, 0.0, 1.0]}},
        "pend_b": {"variable": "nivel", "ventana_min": 3,
                   "x": [-1.0, 0.0, 1.0],
                   "labels": {"DEC": [1.0, 0.0, 0.0], "INC": [0.0, 0.0, 1.0]}},
    })
    assert reg["pend_a"]["ventana_s"] == 20.0
    assert reg["pend_b"]["ventana_s"] == 180.0


# ------------------------------------------------------------
# API: migracion al leer y relacion con el filtro
# ------------------------------------------------------------

VAR = "hopper_nvl_pv_a"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    import web.api.config as cfgapi
    monkeypatch.setattr(cfgapi, "PENDIENTES_JSON", str(tmp_path / "pendientes.json"))
    monkeypatch.setattr(cfgapi, "FILTROS_JSON", str(tmp_path / "filtros.json"))
    # La fuente tiene que ser una PV real del contrato de esta planta: el
    # validador rechaza una variable que no exista.
    cfgapi._save_filtros({VAR: {"q": 0.15, "ventana_s": 10.0}})
    return cfgapi


def _spec(ventana, clave="ventana_s"):
    return {"variable": VAR, clave: ventana, "x": [-1.0, 0.0, 1.0],
            "labels": {"DEC": [1.0, 0.0, 0.0], "INC": [0.0, 0.0, 1.0]}}


def test_leer_un_archivo_viejo_lo_devuelve_en_segundos(api):
    api._save_pendientes({"pend_a": _spec(2, "ventana_min")})
    leido = api._load_pendientes()["pend_a"]
    assert leido["ventana_s"] == 120.0
    assert "ventana_min" not in leido


def test_la_migracion_no_reescribe_el_archivo(api):
    """Igual que en los filtros: se persiste recien cuando el operador guarda,
    para que un rollback del codigo encuentre el archivo que dejo."""
    import json
    api._save_pendientes({"pend_a": _spec(2, "ventana_min")})
    api._load_pendientes()
    crudo = json.load(open(api.PENDIENTES_JSON, encoding="utf-8"))
    assert "ventana_min" in crudo["pend_a"]


def test_guardar_normaliza_a_segundos(api):
    norm, error = api._normalizar_pendientes_payload({"pend_a": _spec(2, "ventana_min")})
    assert error is None
    assert norm["pend_a"]["ventana_s"] == 120.0
    assert "ventana_min" not in norm["pend_a"]


@pytest.mark.parametrize("ventana,estado", [
    (10, "corta"),      # 1x  el filtro
    (19, "corta"),      # 1.9x
    (20, "ok"),         # 2x  justo en el borde
    (30, "ok"),         # 3x
    (40, "ok"),         # 4x  justo en el borde
    (41, "larga"),      # 4.1x
    (300, "larga"),     # 30x
])
def test_la_relacion_con_el_filtro_clasifica_los_bordes(api, ventana, estado):
    rel = api._relacion_filtro(VAR, ventana)
    assert rel["estado"] == estado
    assert rel["factor"] == pytest.approx(ventana / 10.0, abs=0.01)
    assert rel["recomendado_s"] == [20.0, 40.0]


def test_una_variable_sin_filtro_no_se_juzga(api):
    """Una calculada no tiene filtro propio: se filtran sus insumos. Inventar
    una relacion ahi seria un aviso fantasma."""
    rel = api._relacion_filtro("promedio_niveles", 30)
    assert rel["estado"] == "sin_filtro"
    assert rel["factor"] is None


def test_el_aviso_no_bloquea_el_guardado(api):
    """La relacion es una OPINION sobre la calibracion, no un error: una deriva
    de turno se mira con ventanas largas a proposito."""
    norm, error = api._normalizar_pendientes_payload({"pend_a": _spec(600)})
    assert error is None and norm["pend_a"]["ventana_s"] == 600.0
    assert api._relacion_filtro(VAR, 600)["estado"] == "larga"


def test_una_ventana_absurda_si_se_rechaza(api):
    _, error = api._normalizar_pendientes_payload({"pend_a": _spec(999999)})
    assert error is not None and "ventana_s" in error


def test_falta_la_ventana_y_el_error_lo_dice_en_segundos(api):
    spec = _spec(1)
    del spec["ventana_s"]
    _, error = api._normalizar_pendientes_payload({"pend_a": spec})
    assert error is not None and "ventana_s" in error
