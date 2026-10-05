# -*- coding: utf-8 -*-
"""Una pendiente puede ratificarse con una aceleracion de ventana corta.

El problema que resuelve: una pendiente mide sobre minutos. Eso es lo que la
hace robusta al ruido y tambien lo que la hace llegar tarde — sigue diciendo
"sube" un rato despues de que la variable dejo de subir, porque la mayor parte
de su ventana todavia esta subiendo.

La aceleracion mira 5-10 s y produce `rate`: la derivada EN EL EXTREMO DERECHO,
o sea lo que la variable esta haciendo ahora. Ratificar es contrastar una
contra la otra.
"""
from __future__ import annotations

import pytest

from core.fuzzy.pendientes import PendientesOnline, ratificar


EJE = [-1.0, -0.25, 0.0, 0.25, 1.0]
LABELS = {"DEC":    [1.0, 0.5, 0.0, 0.0, 0.0],
          "STABLE": [0.0, 0.5, 1.0, 0.5, 0.0],
          "INC":    [0.0, 0.0, 0.0, 0.5, 1.0]}


# ------------------------------------------------------------
# La decision, aislada
# ------------------------------------------------------------

def _acel(rate, banda=0.005):
    return {"rate": rate, "umbral_rate": banda}


def test_coherente_pasa_intacta():
    valor, motivo = ratificar(0.5, _acel(0.02))
    assert valor == 0.5 and motivo == "ratificada"


def test_contraria_se_evalua_como_pendiente_nula():
    """Cero y no una etiqueta fija: las etiquetas las nombra el operador, asi
    que no hay una 'neutra' que el nucleo pueda dar por sentada."""
    valor, motivo = ratificar(0.5, _acel(-0.02))
    assert valor == 0.0 and "desmentida" in motivo


def test_rate_en_banda_muerta_no_desmiente():
    """Tratarlo como 'no confirmado' dejaria la pendiente muda en operacion
    normal y tranquila, que es cuando mas se la necesita."""
    valor, motivo = ratificar(0.5, _acel(-0.001))
    assert valor == 0.5 and motivo == ""


def test_pendiente_nula_no_tiene_nada_que_ratificar():
    assert ratificar(0.0, _acel(0.02)) == (0.0, "")


@pytest.mark.parametrize("acel", [None, {}, {"rate": "x"}, {"rate": None}])
def test_sin_datos_de_aceleracion_la_decision_no_rompe(acel):
    """Quien decide si falta el ratificador es `actualizar`, no esta funcion:
    aca un dato ilegible no puede convertirse en una excepcion en el tick."""
    assert ratificar(0.5, acel) == (0.5, "")


@pytest.mark.parametrize("pend,rate,esperado", [
    (-0.5, -0.02, -0.5),   # baja y sigue bajando
    (-0.5,  0.02,  0.0),   # baja pero ya esta subiendo -> desmentida
    ( 0.5,  0.02,  0.5),
    ( 0.5, -0.02,  0.0),
])
def test_los_cuatro_cuadrantes(pend, rate, esperado):
    assert ratificar(pend, _acel(rate))[0] == pytest.approx(esperado)


# ------------------------------------------------------------
# En el pipeline
# ------------------------------------------------------------

def _correr(aceleraciones, ratifica_con="acel5", subida_por_min=0.5):
    cfg = {"pend": {"variable": "nivel", "ventana_s": 60.0,
                    "ratifica_con": ratifica_con,
                    "x": list(EJE), "labels": {k: list(v) for k, v in LABELS.items()}}}
    p = PendientesOnline(cfg)
    out, omitidas = {}, []
    for i in range(200):
        t = i * 0.5
        out, omitidas = p.actualizar({"nivel": 50.0 + subida_por_min * t / 60.0},
                                     t, aceleraciones=aceleraciones)
    return out, omitidas


def test_una_subida_confirmada_sigue_siendo_INC():
    out, _ = _correr({"acel5": _acel(0.02)})
    assert out["pend"]["dom"] == "INC"
    assert out["pend"]["slope_per_min"] == pytest.approx(out["pend"]["slope_medido"])


def test_una_subida_desmentida_deja_de_afirmarse():
    out, _ = _correr({"acel5": _acel(-0.02)})
    assert out["pend"]["dom"] == "STABLE"
    assert out["pend"]["slope_per_min"] == 0.0
    # Lo MEDIDO se conserva: es lo que permite entender en la traza por que el
    # SE no actuo, en vez de ver una pendiente nula sin explicacion.
    assert out["pend"]["slope_medido"] == pytest.approx(0.5, abs=0.05)
    assert "desmentida" in out["pend"]["ratificacion"]


def test_sin_ratificador_disponible_la_pendiente_no_se_emite():
    """Fail-closed: emitirla sin ratificar seria afirmar justo lo que el
    operador pidio comprobar. Mismo criterio que el NOT del motor."""
    out, omitidas = _correr({})
    assert "pend" not in out
    assert any("no disponible" in o for o in omitidas)


def test_sin_ratificador_configurado_nada_cambia():
    out, _ = _correr({}, ratifica_con="")
    assert out["pend"]["dom"] == "INC"
    assert out["pend"]["ratificacion"] == ""


# ------------------------------------------------------------
# Validacion de la configuracion
# ------------------------------------------------------------

VAR = "hopper_nvl_pv_a"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    import web.api.config as cfgapi
    monkeypatch.setattr(cfgapi, "PENDIENTES_JSON", str(tmp_path / "pendientes.json"))
    monkeypatch.setattr(cfgapi, "ACELERACIONES_JSON", str(tmp_path / "aceleraciones.json"))
    monkeypatch.setattr(cfgapi, "FILTROS_JSON", str(tmp_path / "filtros.json"))
    cfgapi._save_filtros({VAR: {"q": 0.15, "ventana_s": 10.0}})
    cfgapi._save_aceleraciones({
        "acel_ok":   {"variable": VAR, "ventana_s": 5.0, "umbral_estable": 0.01,
                      "umbral_rate": None, "min_puntos": 3, "habilitado": True},
        "acel_off":  {"variable": VAR, "ventana_s": 5.0, "umbral_estable": 0.01,
                      "umbral_rate": None, "min_puntos": 3, "habilitado": False},
        "acel_otra": {"variable": "corriente", "ventana_s": 5.0, "umbral_estable": 0.01,
                      "umbral_rate": None, "min_puntos": 3, "habilitado": True},
        "acel_larga": {"variable": VAR, "ventana_s": 600.0, "umbral_estable": 0.01,
                       "umbral_rate": None, "min_puntos": 3, "habilitado": True},
    })
    return cfgapi


def _spec(ratifica_con="", ventana_s=30):
    return {"variable": VAR, "ventana_s": ventana_s, "ratifica_con": ratifica_con,
            "x": list(EJE), "labels": {k: list(v) for k, v in LABELS.items()}}


def test_un_ratificador_valido_se_guarda(api):
    norm, error = api._normalizar_pendientes_payload({"p": _spec("acel_ok")})
    assert error is None and norm["p"]["ratifica_con"] == "acel_ok"


def test_un_ratificador_inexistente_se_rechaza(api):
    _, error = api._normalizar_pendientes_payload({"p": _spec("fantasma")})
    assert error is not None and "no existe" in error


def test_una_aceleracion_deshabilitada_no_puede_ratificar(api):
    _, error = api._normalizar_pendientes_payload({"p": _spec("acel_off")})
    assert error is not None and "deshabilitada" in error


def test_tiene_que_ser_de_la_misma_variable(api):
    """Ratificar es contrastar dos medidas de LA MISMA senal a dos escalas de
    tiempo. Con otra variable no se esta ratificando nada."""
    _, error = api._normalizar_pendientes_payload({"p": _spec("acel_otra")})
    assert error is not None and "MISMA variable" in error


def test_el_ratificador_no_puede_mirar_mas_lejos_que_la_pendiente(api):
    _, error = api._normalizar_pendientes_payload({"p": _spec("acel_larga", ventana_s=30)})
    assert error is not None and "mas corta" in error


def test_no_se_borra_una_aceleracion_que_esta_ratificando(api):
    """Sin ratificador la pendiente no se emite y las reglas que la nombran
    quedan mudas. El motivo estaria a dos pantallas de distancia."""
    norm, _ = api._normalizar_pendientes_payload({"p": _spec("acel_ok")})
    api._save_pendientes(norm)
    assert api._pendientes_que_ratifican_con(["acel_ok"])
    assert not api._pendientes_que_ratifican_con(["acel_otra"])
