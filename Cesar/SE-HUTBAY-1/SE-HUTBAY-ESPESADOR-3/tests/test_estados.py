# -*- coding: utf-8 -*-
"""Estados y subestados: anidamiento, referencias y copias.

Cubre lo agregado el 2026-09-01 (ver AFINACION_PENDIENTE.md A24). El hilo
comun de todos estos tests es el mismo criterio del proyecto: **nada cambia
en silencio lo que el motor termina evaluando**.

Todos los archivos se apuntan a temporales, por la leccion de A17/A19: el
suite prueba el cableado y no puede depender de lo que el operador tenga
cargado en la planta hoy.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web.state as st
import web.api.config as C
from app import app


@pytest.fixture
def cli(monkeypatch, tmp_path):
    estados = tmp_path / "estados.json"
    reglas = tmp_path / "reglas.json"
    estados.write_text("{}", encoding="utf-8")
    reglas.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(st, "ESTADOS_JSON_PATH", str(estados))
    monkeypatch.setattr(st, "REGLAS_JSON", str(reglas))
    monkeypatch.setattr(C, "REGLAS_JSON", str(reglas))

    # Catalogos fijos: un estado se valida contra las variables y etiquetas
    # disponibles, y esas salen de la config viva de la planta.
    monkeypatch.setattr(st, "variables_validas", lambda: {"pv_a", "pv_b"})
    monkeypatch.setattr(C, "variables_validas", lambda: {"pv_a", "pv_b"})
    monkeypatch.setattr(st, "etiquetas_validas", lambda: {"LOW", "OK", "HIGH"})
    monkeypatch.setattr(C, "etiquetas_validas", lambda: {"LOW", "OK", "HIGH"})
    # Desde A27 el validador pregunta por VARIABLE, no contra la union: sin
    # esto los tests validarian contra el fuzzy.json de la planta.
    monkeypatch.setattr(C, "etiquetas_validas_de", lambda v: {"LOW", "OK", "HIGH"})
    monkeypatch.setattr(C, "acciones_validas", lambda: {"SUBIR"})
    return app.test_client()


def _estado(cli, nombre, condicion, tipo="estado"):
    return cli.post("/api/estados",
                    json={"nombre": nombre, "tipo": tipo, "condicion": {"AND": condicion}})


def _regla(cli, rid, condiciones):
    return cli.post("/api/reglas", json={
        "id": rid, "bloque": "estabilidad", "priority": 50, "weight": 1.0,
        "if": condiciones,
        "then": [{"accion": "SUBIR", "waits": [], "reiniciar_waits": []}],
    })


# ============================================================
# Validacion del alta (antes no habia NINGUNA)
# ============================================================
@pytest.mark.parametrize("condicion, esperado", [
    ([["no_existe", "OK"]], "variable invalida"),
    ([["pv_a", "VERDE"]],   "etiqueta invalida"),   # sigue diciendo esto: VERDE no existe en ninguna parte
    ([],                    "al menos una condicion"),
])
def test_un_estado_invalido_no_se_guarda(cli, condicion, esperado):
    """`api_create_estado` guardaba el payload TAL CUAL, sin validar nada.

    Se podia crear un estado con una variable inexistente; el error recien
    aparecia --callado-- cuando una regla lo usaba y quedaba `no_evaluable`.
    """
    r = _estado(cli, "X", condicion)
    assert r.status_code == 400
    assert esperado in r.get_json()["error"]
    assert json.loads(open(st.ESTADOS_JSON_PATH).read()) == {}


# ============================================================
# Anidamiento: UN solo nivel, sin ciclos posibles
# ============================================================
def test_un_estado_puede_anidar_otro_y_la_copia_se_regenera(cli):
    """La copia guardada sale de la definicion VIGENTE, no de lo que mande el navegador."""
    _estado(cli, "VEL_OK", [["pv_b", "OK"]], tipo="subestado")
    # Se manda el AND vacio a proposito: el backend lo rellena.
    r = _estado(cli, "COMPUESTO", [["pv_a", "HIGH"], {"ref_estado": "VEL_OK", "AND": []}])
    assert r.status_code == 201
    cond = r.get_json()["estado"]["condicion"]["AND"]
    assert cond[1]["ref_estado"] == "VEL_OK"
    assert cond[1]["AND"] == [["pv_b", "OK"]]


def test_el_segundo_nivel_de_anidamiento_se_rechaza(cli):
    _estado(cli, "VEL_OK", [["pv_b", "OK"]], tipo="subestado")
    _estado(cli, "COMPUESTO", [["pv_a", "HIGH"], {"ref_estado": "VEL_OK", "AND": []}])
    r = _estado(cli, "NIVEL3", [{"ref_estado": "COMPUESTO", "AND": []}])
    assert r.status_code == 400
    assert "UN solo nivel" in r.get_json()["error"]


def test_un_estado_ya_referenciado_no_puede_ganar_referencias(cli):
    """La otra direccion de la misma regla. Es lo que hace imposible un ciclo."""
    _estado(cli, "VEL_OK", [["pv_b", "OK"]], tipo="subestado")
    _estado(cli, "COMPUESTO", [["pv_a", "HIGH"], {"ref_estado": "VEL_OK", "AND": []}])
    _estado(cli, "OTRO", [["pv_a", "LOW"]])
    r = cli.put("/api/estados/VEL_OK", json={"tipo": "subestado", "condicion": {"AND": [
        ["pv_b", "OK"], {"ref_estado": "OTRO", "AND": []}]}})
    assert r.status_code == 400
    assert "UN solo nivel" in r.get_json()["error"]


def test_un_estado_no_se_referencia_a_si_mismo(cli):
    _estado(cli, "YO", [["pv_a", "OK"]])
    r = cli.put("/api/estados/YO", json={"tipo": "estado", "condicion": {"AND": [
        {"ref_estado": "YO", "AND": []}]}})
    assert r.status_code == 400
    assert "si mismo" in r.get_json()["error"]


# ============================================================
# La copia dentro de la regla y su etiqueta
# ============================================================
def test_una_regla_con_un_solo_estado_conserva_la_etiqueta(cli):
    """El `if` de un solo grupo AND se desenvolvia SIEMPRE.

    Con eso, una regla cuya unica condicion era un estado se guardaba como
    hojas sueltas: al reabrirla el estado ya no se reconocia. Ahora el
    desenvoltorio se saltea cuando el grupo trae `ref_estado`.
    """
    _estado(cli, "LLENO", [["pv_a", "HIGH"], ["pv_b", "OK"]])
    copia = cli.get("/api/estados/LLENO").get_json()["condicion"]
    assert _regla(cli, "R1", [dict(copia, ref_estado="LLENO")]).status_code == 201

    guardada = [r for r in cli.get("/api/reglas").get_json() if r["id"] == "R1"][0]
    assert len(guardada["if"]) == 1
    assert guardada["if"][0]["ref_estado"] == "LLENO"


def test_editar_un_estado_no_toca_las_reglas_pero_las_reporta(cli):
    """El nucleo de la opcion elegida: se avisa, no se propaga solo."""
    _estado(cli, "LLENO", [["pv_a", "HIGH"], ["pv_b", "OK"]])
    copia = cli.get("/api/estados/LLENO").get_json()["condicion"]
    _regla(cli, "R1", [dict(copia, ref_estado="LLENO")])

    r = cli.put("/api/estados/LLENO", json={"tipo": "estado", "condicion": {"AND": [
        ["pv_a", "HIGH"], ["pv_b", "LOW"]]}})
    d = r.get_json()
    assert d["condicion_cambio"] is True
    assert d["usos"]["reglas"] == [{"id": "R1", "desactualizada": True}]

    # Y la regla NO se movio.
    guardada = [x for x in cli.get("/api/reglas").get_json() if x["id"] == "R1"][0]
    assert guardada["if"][0]["AND"][1] == ["pv_b", "OK"]


def test_con_propagar_true_la_regla_se_actualiza(cli):
    _estado(cli, "LLENO", [["pv_a", "HIGH"], ["pv_b", "OK"]])
    copia = cli.get("/api/estados/LLENO").get_json()["condicion"]
    _regla(cli, "R1", [dict(copia, ref_estado="LLENO")])

    r = cli.put("/api/estados/LLENO", json={"tipo": "estado", "propagar": True,
                "condicion": {"AND": [["pv_a", "HIGH"], ["pv_b", "LOW"]]}})
    assert r.get_json()["propagado"]["reglas"] == ["R1"]
    guardada = [x for x in cli.get("/api/reglas").get_json() if x["id"] == "R1"][0]
    assert guardada["if"][0]["AND"][1] == ["pv_b", "LOW"]


def test_borrar_un_estado_anidado_en_otro_se_niega(cli):
    _estado(cli, "VEL_OK", [["pv_b", "OK"]], tipo="subestado")
    _estado(cli, "COMPUESTO", [["pv_a", "HIGH"], {"ref_estado": "VEL_OK", "AND": []}])
    r = cli.delete("/api/estados/VEL_OK")
    assert r.status_code == 409
    assert r.get_json()["estados_afectados"] == ["COMPUESTO"]
    assert "VEL_OK" in cli.get("/api/estados").get_json()


def test_borrar_un_estado_usado_por_reglas_avisa_pero_no_las_rompe(cli):
    """La copia sigue siendo evaluable palabra por palabra: el motor no cambia."""
    _estado(cli, "LLENO", [["pv_a", "HIGH"]])
    copia = cli.get("/api/estados/LLENO").get_json()["condicion"]
    _regla(cli, "R1", [dict(copia, ref_estado="LLENO")])

    r = cli.delete("/api/estados/LLENO")
    assert r.status_code == 200
    assert r.get_json()["reglas_afectadas"] == ["R1"]
    guardada = [x for x in cli.get("/api/reglas").get_json() if x["id"] == "R1"][0]
    assert guardada["if"][0]["AND"] == [["pv_a", "HIGH"]]


# ============================================================
# Grupo OR mixto y evaluacion en el motor
# ============================================================
def test_un_grupo_or_acepta_hojas_y_estados(cli):
    """El validador solo aceptaba hojas dentro de un OR: un OR con un estado
    adentro se rechazaba, aunque el motor sabe evaluarlo desde siempre."""
    _estado(cli, "LLENO", [["pv_a", "HIGH"], ["pv_b", "OK"]])
    copia = cli.get("/api/estados/LLENO").get_json()["condicion"]
    r = _regla(cli, "R_OR", [{"OR": [["pv_a", "LOW"], dict(copia, ref_estado="LLENO")]}])
    assert r.status_code == 201, r.get_json()


def test_el_motor_evalua_el_or_mixto_como_max_de_min(cli):
    """Que el JSON se guarde no alcanza: tiene que EVALUAR bien."""
    from runner import cargar_reglas_json
    from core.engine.motor import evaluar_condicion

    _estado(cli, "LLENO", [["pv_a", "HIGH"], ["pv_b", "OK"]])
    copia = cli.get("/api/estados/LLENO").get_json()["condicion"]
    _regla(cli, "R_OR", [{"OR": [["pv_a", "LOW"], dict(copia, ref_estado="LLENO")]}])

    regla = [r for r in cargar_reglas_json(st.REGLAS_JSON) if r["id"] == "R_OR"][0]
    fuzzy_out = {"pv_a": {"pert": {"LOW": 0.0, "HIGH": 0.9}},
                 "pv_b": {"pert": {"OK": 0.4}}}
    # max(hoja 0.0, estado min(0.9, 0.4)) = 0.4
    assert evaluar_condicion(regla["if"][0], fuzzy_out) == pytest.approx(0.4)


def test_el_motor_evalua_un_estado_anidado(cli):
    from runner import cargar_reglas_json
    from core.engine.motor import evaluar_condicion

    _estado(cli, "VEL_OK", [["pv_b", "OK"]], tipo="subestado")
    _estado(cli, "COMPUESTO", [["pv_a", "HIGH"], {"ref_estado": "VEL_OK", "AND": []}])
    copia = cli.get("/api/estados/COMPUESTO").get_json()["condicion"]
    _regla(cli, "R_AN", [dict(copia, ref_estado="COMPUESTO")])

    regla = [r for r in cargar_reglas_json(st.REGLAS_JSON) if r["id"] == "R_AN"][0]
    fuzzy_out = {"pv_a": {"pert": {"HIGH": 0.8}}, "pv_b": {"pert": {"OK": 0.3}}}
    assert evaluar_condicion(regla["if"][0], fuzzy_out) == pytest.approx(0.3)


# ============================================================
# Nada de plantillas del espesador (A20)
# ============================================================
def test_sin_estados_json_no_se_siembra_ninguna_plantilla():
    assert st._defaults_estados() == {}


def test_reglas_json_ilegible_no_carga_las_reglas_de_otra_planta(tmp_path):
    """Caia a las 28 reglas del espesador: un archivo corrupto no detenia el
    motor, lo ponia a operar con las reglas de otra planta."""
    from runner import cargar_reglas_json
    roto = tmp_path / "reglas.json"
    roto.write_text("{no es json", encoding="utf-8")
    assert cargar_reglas_json(str(roto)) == []
    assert cargar_reglas_json(str(tmp_path / "ni_existe.json")) == []


def test_el_catalogo_de_permisivos_sale_del_json(monkeypatch, tmp_path):
    """Salia del dict hardcodeado del espesador: se ofrecian cinco `__PERM_*`
    que el motor nunca producia (A20)."""
    p = tmp_path / "permisivos.json"
    p.write_text(json.dumps({"MI_PERMISIVO": []}), encoding="utf-8")
    monkeypatch.setattr(st, "PERMISIVOS_JSON", str(p))
    assert st.nombres_permisivos() == ["MI_PERMISIVO"]

    p.write_text("{}", encoding="utf-8")
    assert st.nombres_permisivos() == []


# ============================================================
# Etiquetas POR VARIABLE (A27)
# ============================================================
# La union de etiquetas servia para validar "existe en alguna parte", y se
# ofrecia entera. Con eso se podia pedir LOW a una pendiente cuyas filas son
# INC/DEC/STABLE: la regla se guardaba, no daba error, y evaluaba 0 PARA
# SIEMPRE. Estos tests fijan que el catalogo sea por variable y que salga de la
# MISMA regla que usa el motor.

@pytest.fixture
def cat(monkeypatch, tmp_path):
    fuzzy = tmp_path / "fuzzy.json"
    pend = tmp_path / "pendientes.json"
    perm = tmp_path / "permisivos.json"
    fuzzy.write_text(json.dumps({
        "nivel": {"type": "norm", "offset": [0, 1],
                  "labels": {"LOW": [1, 0], "OK": [0, 1], "HIGH": [0, 1]}},
        "nivel_es": {"type": "norm", "offset": [0, 1],
                     "labels": {"BAJO": [1, 0], "NORMAL": [0, 1], "ALTO": [0, 1]}},
    }), encoding="utf-8")
    pend.write_text(json.dumps({
        "pend_nivel": {"variable": "nivel", "ventana_min": 5, "x": [-1, 0, 1],
                       "labels": {"DEC": [1, 0, 0], "INC": [0, 0, 1],
                                  "STABLE": [0, 1, 0]}},
    }), encoding="utf-8")
    perm.write_text(json.dumps({"PERMITIR": []}), encoding="utf-8")
    monkeypatch.setattr(st, "FUZZY_JSON", str(fuzzy))
    monkeypatch.setattr(st, "PENDIENTES_JSON", str(pend))
    monkeypatch.setattr(st, "PERMISIVOS_JSON", str(perm))
    return st.etiquetas_por_variable()


def test_cada_variable_ofrece_solo_sus_etiquetas(cat):
    assert set(cat["nivel"]) == {"LOW", "OK", "HIGH", "NO-LOW", "NO-OK", "NO-HIGH",
                                 "CERCA_ALTO", "CERCA_BAJO"}
    assert set(cat["pend_nivel"]) == {"DEC", "INC", "STABLE",
                                      "NO-DEC", "NO-INC", "NO-STABLE"}
    assert set(cat["__PERM_PERMITIR"]) == {"ON", "OFF", "NO-ON", "NO-OFF"}


def test_una_pendiente_no_ofrece_low_ni_cerca_alto(cat):
    """El error mudo concreto que aparecio en la config de la planta."""
    assert "LOW" not in cat["pend_nivel"]
    assert "CERCA_ALTO" not in cat["pend_nivel"]


def test_un_nivel_no_ofrece_inc(cat):
    assert "INC" not in cat["nivel"]


def test_cerca_alto_solo_donde_el_motor_lo_calcula(cat):
    """`expandir_etiquetas_compuestas` genera CERCA_ALTO solo si hay OK y HIGH.

    Un fuzzy con las filas en espanol no las tiene, asi que el motor no lo
    calcula — y por lo tanto no se ofrece. Antes se ofrecia igual y la regla
    evaluaba 0 en silencio.
    """
    assert "CERCA_ALTO" in cat["nivel"]
    assert "CERCA_ALTO" not in cat["nivel_es"]
    assert set(cat["nivel_es"]) == {"BAJO", "NORMAL", "ALTO",
                                    "NO-BAJO", "NO-NORMAL", "NO-ALTO"}


def test_el_catalogo_y_el_motor_no_pueden_divergir():
    """Los dos consultan `etiquetas_derivadas`: es la unica declaracion."""
    from core.fuzzy.evaluator import etiquetas_derivadas, expandir_etiquetas_compuestas

    for base in (["LOW", "OK", "HIGH"], ["DEC", "INC", "STABLE"],
                 ["BAJO", "NORMAL", "ALTO"], ["ON", "OFF"]):
        pert = {k: 0.5 for k in base}
        salida = expandir_etiquetas_compuestas({"v": {"pert": pert}})["v"]["pert"]
        esperado = set(base) | set(etiquetas_derivadas(base))
        assert set(salida) == esperado, base


def test_una_variable_sin_fuzzy_no_ofrece_nada(cli, monkeypatch, tmp_path):
    vacio = tmp_path / "fuzzy.json"
    vacio.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(st, "FUZZY_JSON", str(vacio))
    monkeypatch.setattr(st, "PENDIENTES_JSON", str(vacio))
    monkeypatch.setattr(st, "variables_disponibles", lambda: ["huerfana"])
    assert st.etiquetas_por_variable()["huerfana"] == []


def test_una_regla_con_etiqueta_de_otra_variable_se_rechaza(cli, monkeypatch, tmp_path):
    """La prueba de fuego: la combinacion que antes se guardaba callada."""
    monkeypatch.setattr(C, "etiquetas_validas_de",
                        lambda v: {"DEC", "INC", "STABLE"} if v == "pv_b" else {"LOW", "OK", "HIGH"})
    r = cli.post("/api/reglas", json={
        "id": "R", "bloque": "estabilidad", "priority": 50, "weight": 1.0,
        "if": [["pv_b", "LOW"]],
        "then": [{"accion": "SUBIR", "waits": [], "reiniciar_waits": []}]})
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert "no tiene la etiqueta 'LOW'" in err
    assert "DEC" in err          # dice cuales SI valen


def test_un_estado_con_etiqueta_de_otra_variable_se_rechaza(cli, monkeypatch):
    monkeypatch.setattr(C, "etiquetas_validas_de",
                        lambda v: {"DEC", "INC", "STABLE"} if v == "pv_b" else {"LOW", "OK", "HIGH"})
    r = _estado(cli, "SSSS", [["pv_b", "LOW"]], tipo="subestado")
    assert r.status_code == 400
    assert "no tiene la etiqueta 'LOW'" in r.get_json()["error"]
