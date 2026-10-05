# -*- coding: utf-8 -*-
"""Un estado puede declarar alternativas (OR), no solo un AND de condiciones.

El escenario que esto habilita es el que pide el experto de planta:

    Alto = (Alto y NO bajando)  o  (OK y subiendo)
    Bajo = (Bajo y NO subiendo) o  (OK y bajando)

Antes habia que partirlo en dos estados y rearmarlo con un grupo OR DENTRO DE
CADA REGLA: el escenario no existia como objeto con nombre, cada regla tenia su
copia y no habia un lugar donde cambiarlo.

El motor ya evaluaba OR (maximo) y las etiquetas negadas (NO-DEC = 1 - mu_DEC)
desde siempre; lo que faltaba era poder declararlo.
"""
from __future__ import annotations

import pytest

import web.api.config as cfgapi
from core.engine.motor import evaluar_condicion


VAR = "hopper_nvl_pv_a"
PEND = "pend_hopper_nvl_pv_a_5min"


def _coerce(node):
    """Lo mismo que hace `cargar_reglas_json` al leer del disco: las hojas son
    tuplas para el motor y listas en el JSON."""
    if isinstance(node, list):
        if len(node) == 2 and all(isinstance(x, str) for x in node):
            return (node[0], node[1])
        return [_coerce(x) for x in node]
    if isinstance(node, dict):
        return {k: _coerce(v) for k, v in node.items()}
    return node


def _norm(nombre, items, estados=None, tipo="estado"):
    return cfgapi._normalizar_estado_payload(
        nombre, {"nombre": nombre, "tipo": tipo, "condicion": {"AND": items}},
        estados or {}, es_edicion=False)


def _ref(nombre):
    return {"AND": [], cfgapi.REF_ESTADO: nombre}


# ------------------------------------------------------------
# Validacion
# ------------------------------------------------------------

def test_un_or_de_hojas_se_acepta():
    norm, error = _norm("E", [[VAR, "HIGH"], {"OR": [[PEND, "NO-DEC"], [PEND, "INC"]]}])
    assert error is None
    assert norm["condicion"]["AND"][1] == {"OR": [[PEND, "NO-DEC"], [PEND, "INC"]]}


def test_un_or_de_una_sola_opcion_se_rechaza():
    """Con una sola opcion el OR no dice nada que la condicion suelta no diga,
    y esconde la intencion de quien lo lee despues."""
    _, error = _norm("E", [{"OR": [[VAR, "HIGH"]]}])
    assert error is not None and "2" in error


def test_no_se_anida_un_or_dentro_de_otro():
    """'(A o B) o C' es 'A o B o C' escrito raro. Anidar alternativas hace la
    condicion ilegible, que es justo lo que un estado con nombre evita."""
    _, error = _norm("E", [{"OR": [[VAR, "HIGH"],
                                   {"OR": [[VAR, "OK"], [PEND, "INC"]]}]}])
    assert error is not None and "OR dentro de otro" in error


def test_una_hoja_invalida_dentro_del_or_se_caza():
    _, error = _norm("E", [{"OR": [[VAR, "HIGH"], [VAR, "NO_EXISTE"]]}])
    assert error is not None and "NO_EXISTE" in error


def test_una_variable_inexistente_dentro_del_or_se_caza():
    _, error = _norm("E", [{"OR": [[VAR, "HIGH"], ["fantasma", "HIGH"]]}])
    assert error is not None and "fantasma" in error


def test_un_estado_referenciado_dentro_del_or_se_copia_vigente():
    """La copia se REGENERA desde la definicion de hoy, nunca desde lo que
    mande el navegador: ahi podria haber algo cacheado."""
    estados = {"A": {"nombre": "A", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "HIGH"], [PEND, "NO-DEC"]]}},
               "B": {"nombre": "B", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "OK"], [PEND, "INC"]]}}}
    norm, error = _norm("Alto", [{"OR": [_ref("A"), _ref("B")]}], estados)
    assert error is None
    opciones = norm["condicion"]["AND"][0]["OR"]
    assert opciones[0]["AND"] == [[VAR, "HIGH"], [PEND, "NO-DEC"]]
    assert opciones[0][cfgapi.REF_ESTADO] == "A"
    assert opciones[1]["AND"] == [[VAR, "OK"], [PEND, "INC"]]


def test_el_mismo_estado_dos_veces_dentro_del_or_se_rechaza():
    estados = {"A": {"nombre": "A", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "HIGH"]]}}}
    _, error = _norm("E", [{"OR": [_ref("A"), _ref("A")]}], estados)
    assert error is not None and "dos veces" in error


def test_no_se_puede_referenciar_a_si_mismo_dentro_de_un_or():
    estados = {"E": {"nombre": "E", "tipo": "estado",
                     "condicion": {"AND": [[VAR, "HIGH"]]}},
               "A": {"nombre": "A", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "OK"]]}}}
    _, error = _norm("E", [{"OR": [_ref("E"), _ref("A")]}], estados)
    assert error is not None and "si mismo" in error


def test_la_regla_de_un_solo_nivel_no_se_saltea_escondiendose_en_un_or():
    """Si el anidamiento dentro de un OR no contara como referencia, bastaba
    con meter la cadena ahi para tener estados de tres niveles."""
    estados = {
        "HOJA": {"nombre": "HOJA", "tipo": "subestado",
                 "condicion": {"AND": [[VAR, "HIGH"]]}},
        # COMPUESTO referencia a HOJA, pero DENTRO de un OR
        "COMPUESTO": {"nombre": "COMPUESTO", "tipo": "subestado",
                      "condicion": {"AND": [{"OR": [_ref("HOJA"), [VAR, "OK"]]}]}},
        "OTRO": {"nombre": "OTRO", "tipo": "subestado",
                 "condicion": {"AND": [[VAR, "LOW"]]}},
    }
    assert "HOJA" in cfgapi._refs_de_estado(estados["COMPUESTO"])
    _, error = _norm("NUEVO", [_ref("COMPUESTO")], estados)
    assert error is not None and "UN solo nivel" in error


# ------------------------------------------------------------
# Lo que el motor hace con eso
# ------------------------------------------------------------

def _fz(high, ok, no_dec, inc):
    return {VAR:  {"pert": {"HIGH": high, "OK": ok, "LOW": 0.0}},
            PEND: {"pert": {"NO-DEC": no_dec, "INC": inc, "DEC": 0.0}}}


@pytest.mark.parametrize("high,ok,no_dec,inc,esperado", [
    (1.0, 0.0, 1.0, 0.0, 1.0),   # Alto y no bajando  -> se cumple
    (0.0, 1.0, 0.0, 1.0, 1.0),   # OK y subiendo      -> se cumple
    (1.0, 0.0, 0.0, 0.0, 0.0),   # Alto pero bajando  -> no
    (0.0, 1.0, 1.0, 0.0, 0.0),   # OK y no subiendo   -> no
    (0.6, 0.0, 0.8, 0.0, 0.6),   # parcial: manda el minimo de la rama
])
def test_el_motor_evalua_alto_como_lo_pide_el_experto(high, ok, no_dec, inc, esperado):
    estados = {"A": {"nombre": "A", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "HIGH"], [PEND, "NO-DEC"]]}},
               "B": {"nombre": "B", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "OK"], [PEND, "INC"]]}}}
    norm, error = _norm("Alto", [{"OR": [_ref("A"), _ref("B")]}], estados)
    assert error is None
    cond = _coerce(norm["condicion"])
    assert evaluar_condicion(cond, _fz(high, ok, no_dec, inc)) == pytest.approx(esperado)


def test_bajo_es_el_espejo_de_alto():
    estados = {"A": {"nombre": "A", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "LOW"], [PEND, "NO-INC"]]}},
               "B": {"nombre": "B", "tipo": "subestado",
                     "condicion": {"AND": [[VAR, "OK"], [PEND, "DEC"]]}}}
    norm, error = _norm("Bajo", [{"OR": [_ref("A"), _ref("B")]}], estados)
    assert error is None
    cond = _coerce(norm["condicion"])
    bajo_no_sube = {VAR: {"pert": {"LOW": 1.0, "OK": 0.0}},
                    PEND: {"pert": {"NO-INC": 1.0, "DEC": 0.0}}}
    ok_bajando = {VAR: {"pert": {"LOW": 0.0, "OK": 1.0}},
                  PEND: {"pert": {"NO-INC": 0.0, "DEC": 1.0}}}
    assert evaluar_condicion(cond, bajo_no_sube) == pytest.approx(1.0)
    assert evaluar_condicion(cond, ok_bajando) == pytest.approx(1.0)


def test_una_copia_con_or_se_compara_bien_para_el_aviso_de_copia_vieja():
    """`_copia_igual` tiene que mirar dentro del OR: si no, un estado con
    alternativas se veria siempre como 'copia vieja'."""
    cond = {"AND": [{"OR": [[VAR, "HIGH"], [PEND, "INC"]]}]}
    igual = {"AND": [{"OR": [[VAR, "high"], [PEND, "INC"]]}]}   # case distinto
    distinta = {"AND": [{"OR": [[VAR, "OK"], [PEND, "INC"]]}]}
    assert cfgapi._copia_igual(cond, igual)
    assert not cfgapi._copia_igual(cond, distinta)
