# -*- coding: utf-8 -*-
"""Aceleracion por ajuste cuadratico: el calculo, el estado y el cableado.

Tres capas, y las tres importan por motivos distintos:

* **El ajuste** — que `2a` y `2a*t+b` sean de verdad la aceleracion y el rate.
  Se prueba contra parabolas de coeficientes conocidos: si el ajuste esta mal,
  todo lo de arriba decide sobre un numero inventado.
* **El estado** — que ACELERANDO signifique "la magnitud del cambio crece" y
  no "la aceleracion es positiva". Es el error facil de cometer y el que
  haria que una variable escapandose hacia abajo se reportara como que frena.
* **El cableado** — que la variable llegue al `fuzzy_out` con sus `NO-<X>` y
  que los catalogos la ofrezcan, que es lo que la hace usable en CONDICIONES,
  Estados y Subestados.

Todos los archivos se apuntan a temporales (leccion A17/A19): el suite prueba
el cableado y no puede caerse porque alguien afine la planta.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.variables.aceleracion import (
    AceleracionesOnline,
    ETIQUETA_ACELERANDO,
    ETIQUETA_ACEL_NEGATIVA,
    ETIQUETA_ACEL_NULA,
    ETIQUETA_ACEL_POSITIVA,
    ETIQUETA_DESACELERANDO,
    ETIQUETA_ESTABLE,
    ETIQUETAS_ACELERACION,
    ajustar_cuadratica,
    construir_registry_aceleraciones,
    estado_aceleracion,
    nombre_sugerido,
    signo_aceleracion,
)


# ============================================================
# El ajuste cuadratico
# ============================================================

def _parabola(a, b, c, n=21, dt=0.25):
    """Muestras exactas de a*t^2 + b*t + c, a partir de t=100 s.

    Arranca en 100 y no en 0 a proposito: el `t_s` del motor es
    `time.monotonic() - t0` y crece sin parar. Un ajuste que solo funciona
    cerca del origen se rompe a la media hora de operacion.
    """
    return [(100.0 + i * dt, a * (100.0 + i * dt) ** 2 + b * (100.0 + i * dt) + c)
            for i in range(n)]


def test_el_ajuste_recupera_los_coeficientes_de_una_parabola_exacta():
    a, b, c = 0.5, -3.0, 12.0
    muestras = _parabola(a, b, c)
    rate, acel = ajustar_cuadratica(muestras)
    t_fin = muestras[-1][0]
    assert acel == pytest.approx(2 * a, rel=1e-6)
    assert rate == pytest.approx(2 * a * t_fin + b, rel=1e-6)


def test_una_recta_tiene_aceleracion_cero_y_rate_igual_a_su_pendiente():
    rate, acel = ajustar_cuadratica(_parabola(0.0, 2.5, 4.0))
    assert acel == pytest.approx(0.0, abs=1e-6)
    assert rate == pytest.approx(2.5, rel=1e-6)


def test_muestras_a_intervalos_IRREGULARES_dan_el_mismo_resultado():
    """El motor corre en ciclo libre: el `dt` cambia tick a tick.

    Minimos cuadrados no supone muestreo uniforme, pero si alguien lo
    reimplementara con diferencias finitas esto se caeria.
    """
    a, b, c = -0.25, 1.5, 3.0
    ts = [100.0, 100.03, 100.4, 100.41, 101.9, 102.0, 103.7, 104.9]
    muestras = [(t, a * t * t + b * t + c) for t in ts]
    rate, acel = ajustar_cuadratica(muestras)
    assert acel == pytest.approx(2 * a, rel=1e-5)
    assert rate == pytest.approx(2 * a * ts[-1] + b, rel=1e-5)


def test_sin_variacion_de_tiempo_el_ajuste_NO_se_puede_determinar():
    """No devuelve 0.0: "no se puede saber" y "no se mueve" son distintos."""
    rate, acel = ajustar_cuadratica([(5.0, 1.0), (5.0, 2.0), (5.0, 3.0)])
    assert rate is None and acel is None


def test_con_menos_de_tres_puntos_no_hay_cuadratica():
    assert ajustar_cuadratica([(1.0, 1.0), (2.0, 2.0)]) == (None, None)


# ============================================================
# El estado: magnitud del cambio, no signo de la aceleracion
# ============================================================

@pytest.mark.parametrize("rate,acel,esperado", [
    # Sube cada vez mas rapido.
    (2.0, 1.0, ETIQUETA_ACELERANDO),
    # BAJA cada vez mas rapido: aceleracion NEGATIVA y esta ACELERANDO.
    # Es el caso que un `acel > 0` se llevaria puesto.
    (-2.0, -1.0, ETIQUETA_ACELERANDO),
    # Sube y frena.
    (2.0, -1.0, ETIQUETA_DESACELERANDO),
    # Baja y frena: aceleracion positiva, esta DESACELERANDO.
    (-2.0, 1.0, ETIQUETA_DESACELERANDO),
])
def test_el_estado_mira_la_MAGNITUD_del_cambio(rate, acel, esperado):
    assert estado_aceleracion(rate, acel, umbral_estable=0.1,
                              umbral_rate=0.05) == esperado


def test_ESTABLE_exige_las_DOS_magnitudes_en_banda_muerta():
    """Cambio de contrato del 2026-09-14, no una regresion.

    Antes bastaba `|accel| < umbral` para decir ESTABLE, asi que una variable
    subiendo a velocidad constante salia ESTABLE: correcto como "la magnitud
    del cambio no cambia", pero se leia —y se usaba en reglas— como "el
    proceso esta quieto". Ahora ESTABLE exige que el rate tambien este en su
    banda.
    """
    assert estado_aceleracion(0.01, 0.05, umbral_estable=0.1,
                              umbral_rate=0.05) == ETIQUETA_ESTABLE


def test_velocidad_constante_no_lleva_etiqueta_dinamica():
    """El caso que el cambio de arriba destapa: accel en banda, rate afuera.

    No esta acelerando ni desacelerando, y afirmar ESTABLE seria decir que el
    proceso esta quieto mientras se escapa parejo. Se describe solo con el
    signo, y las tres NO-<dinamica> quedan en 1.
    """
    assert estado_aceleracion(5.0, 0.05, umbral_estable=0.1,
                              umbral_rate=0.05) == ""


# ============================================================
# El signo: la pregunta que la dinamica no deja hacer
# ============================================================

@pytest.mark.parametrize("acel,esperado", [
    (1.0,   ETIQUETA_ACEL_POSITIVA),
    (-1.0,  ETIQUETA_ACEL_NEGATIVA),
    (0.05,  ETIQUETA_ACEL_NULA),
    (-0.05, ETIQUETA_ACEL_NULA),
    (0.0,   ETIQUETA_ACEL_NULA),
])
def test_el_signo_se_mide_contra_la_misma_banda_muerta(acel, esperado):
    assert signo_aceleracion(acel, umbral_estable=0.1) == esperado


@pytest.mark.parametrize("rate,acel,dinamica,signo", [
    (2.0,  1.0,  ETIQUETA_ACELERANDO,    ETIQUETA_ACEL_POSITIVA),
    (2.0,  -1.0, ETIQUETA_DESACELERANDO, ETIQUETA_ACEL_NEGATIVA),
    (-2.0, -1.0, ETIQUETA_ACELERANDO,    ETIQUETA_ACEL_NEGATIVA),
    (-2.0, 1.0,  ETIQUETA_DESACELERANDO, ETIQUETA_ACEL_POSITIVA),
])
def test_las_dos_familias_son_independientes(rate, acel, dinamica, signo):
    """La tabla textual del estandar: cada combinacion da UNA de cada familia.

    Es lo que justifica que existan las dos: ACELERANDO no dice para que lado
    y ACELERACION_NEGATIVA no dice si el movimiento se agranda.
    """
    assert estado_aceleracion(rate, acel, umbral_estable=0.1,
                              umbral_rate=0.05) == dinamica
    assert signo_aceleracion(acel, umbral_estable=0.1) == signo


def test_arrancar_a_moverse_desde_rate_cero_es_ACELERANDO():
    """Con rate ~0 el producto de signos da 0.

    Sin el caso explicito caeria en DESACELERANDO justo cuando la variable
    empieza a moverse, que es lo contrario de lo que pasa.
    """
    assert estado_aceleracion(0.0, 1.0, umbral_estable=0.1,
                              umbral_rate=0.05) == ETIQUETA_ACELERANDO


# ============================================================
# El evaluador online
# ============================================================

def _correr(acel, valores_por_t):
    ultimo = ({}, [])
    for t, v in valores_por_t:
        ultimo = acel.actualizar({"nivel": v}, t_s=t)
    return ultimo


def test_no_produce_nada_hasta_cubrir_la_ventana():
    """Mismo criterio que las pendientes: sin historia no se afirma nada.

    Reportar ESTABLE con dos segundos de datos y una ventana de cinco seria
    afirmar algo que el motor no puede sostener, con reglas actuando encima.
    """
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0}})
    out, omitidas = _correr(ac, [(t / 10.0, t / 10.0) for t in range(0, 30)])
    assert out == {}
    assert any("ventana incompleta" in m for m in omitidas)

    out, _ = _correr(ac, [(t / 10.0, t / 10.0) for t in range(30, 60)])
    assert "a5" in out


def test_produce_el_estado_y_los_dos_numeros():
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    # x(t) = 0.5*t^2  ->  aceleracion 1.0, rate = t
    out, _ = _correr(ac, [(t / 10.0, 0.5 * (t / 10.0) ** 2) for t in range(0, 101)])
    fila = out["a5"]
    assert fila["aceleracion"] == pytest.approx(1.0, rel=1e-3)
    assert fila["rate"] == pytest.approx(10.0, rel=1e-2)
    assert fila["dom"] == ETIQUETA_ACELERANDO
    assert fila["es_aceleracion"] is True
    assert fila["variable"] == "nivel"


def test_una_variable_que_BAJA_cada_vez_mas_rapido_esta_ACELERANDO():
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    out, _ = _correr(ac, [(t / 10.0, -0.5 * (t / 10.0) ** 2) for t in range(0, 101)])
    assert out["a5"]["aceleracion"] < 0
    assert out["a5"]["rate"] < 0
    assert out["a5"]["dom"] == ETIQUETA_ACELERANDO


def test_una_rampa_limpia_no_lleva_dinamica_y_es_ACELERACION_NULA():
    """Una rampa sube a velocidad constante: 2a ~ 0 y el rate bien lejos de 0.

    Hasta el 2026-09-14 esto salia ESTABLE. Dejo de salir porque ESTABLE paso
    a exigir las dos magnitudes en banda muerta; el caso se describe con el
    signo, que es lo unico que se puede afirmar.
    """
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    out, _ = _correr(ac, [(t / 10.0, 3.0 * (t / 10.0)) for t in range(0, 101)])
    assert out["a5"]["dom"] == ""
    assert out["a5"]["signo"] == ETIQUETA_ACEL_NULA
    assert out["a5"]["rate"] == pytest.approx(3.0, rel=1e-3)


def test_una_variable_quieta_de_verdad_es_ESTABLE():
    """Sin rate y sin aceleracion: las dos en banda, la unica ESTABLE real."""
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    out, _ = _correr(ac, [(t / 10.0, 42.0) for t in range(0, 101)])
    assert out["a5"]["dom"] == ETIQUETA_ESTABLE
    assert out["a5"]["signo"] == ETIQUETA_ACEL_NULA


def test_las_pertenencias_son_NITIDAS_una_por_familia():
    """Es lo que hace que `expandir_etiquetas_compuestas` genere los NO-<X>.

    Las SEIS etiquetas estan siempre presentes en `pert` (las que no aplican,
    en 0.0): si una faltara, su `NO-<X>` no se generaria y una regla que la
    nombrara quedaria no evaluable.
    """
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    # Sube cada vez mas rapido: ACELERANDO + ACELERACION_POSITIVA.
    out, _ = _correr(ac, [(t / 10.0, 0.5 * (t / 10.0) ** 2) for t in range(0, 101)])
    pert = out["a5"]["pert"]
    assert sorted(pert) == sorted(ETIQUETAS_ACELERACION)
    assert pert[ETIQUETA_ACELERANDO] == 1.0
    assert pert[ETIQUETA_ACEL_POSITIVA] == 1.0
    # Una de cada familia, ni una mas.
    assert sum(pert.values()) == pytest.approx(2.0)


def test_con_velocidad_constante_solo_se_activa_el_signo():
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    out, _ = _correr(ac, [(t / 10.0, 3.0 * (t / 10.0)) for t in range(0, 101)])
    pert = out["a5"]["pert"]
    assert pert[ETIQUETA_ACEL_NULA] == 1.0
    assert sum(pert[e] for e in ("ACELERANDO", "DESACELERANDO", "ESTABLE")) == 0.0


def test_sin_dato_de_la_fuente_la_variable_no_se_produce():
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 1.0}})
    out, omitidas = ac.actualizar({"otra": 1.0}, t_s=10.0)
    assert out == {}
    assert omitidas == ["a5 (sin dato de nivel)"]


def test_reset_olvida_la_ventana():
    """Arrancar el motor con la historia de hace media hora es justo lo que
    el arranque bumpless existe para evitar."""
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0}})
    out, _ = _correr(ac, [(t / 10.0, t / 10.0) for t in range(0, 101)])
    assert "a5" in out
    ac.reset()
    out, omitidas = ac.actualizar({"nivel": 10.0}, t_s=10.1)
    assert out == {}
    assert any("ventana incompleta" in m for m in omitidas)


def test_una_ventana_de_5s_con_5_datos_alcanza():
    """El ejemplo textual del estandar: 5 s, 5 muestras, se usan las 5."""
    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    out = {}
    for t in range(0, 11):
        out, _ = ac.actualizar({"nivel": 0.5 * t * t}, t_s=float(t))
    assert out["a5"]["n_puntos"] >= 5
    assert out["a5"]["aceleracion"] == pytest.approx(1.0, rel=1e-6)


# ============================================================
# El registry: una entrada mal formada se omite, no rompe el arranque
# ============================================================

def test_una_entrada_mal_formada_se_omite_y_las_demas_sobreviven():
    reg = construir_registry_aceleraciones({
        "buena":       {"variable": "nivel", "ventana_s": 5.0},
        "sin_var":     {"ventana_s": 5.0},
        "ventana_mala": {"variable": "nivel", "ventana_s": 0.0},
        "no_es_dict":  "cualquier cosa",
        "apagada":     {"variable": "nivel", "ventana_s": 5.0, "habilitado": False},
    })
    assert sorted(reg) == ["buena"]


def test_min_puntos_nunca_baja_de_tres():
    reg = construir_registry_aceleraciones(
        {"a": {"variable": "nivel", "ventana_s": 5.0, "min_puntos": 1}})
    assert reg["a"]["min_puntos"] == 3


def test_el_umbral_de_rate_se_deriva_del_de_aceleracion():
    reg = construir_registry_aceleraciones(
        {"a": {"variable": "nivel", "ventana_s": 5.0, "umbral_estable": 0.02}})
    assert reg["a"]["umbral_rate"] == pytest.approx(0.02 * 5.0)


def test_el_nombre_sugerido_sigue_el_estandar():
    assert nombre_sugerido("PU009.Nivel", 5.0) == "PU009.Nivel_Acceleration_5s"


# ============================================================
# Cableado: catalogos y expansion de NO-<X>
# ============================================================

@pytest.fixture
def cfg_acel(monkeypatch, tmp_path):
    """Apunta aceleraciones.json a un temporal con una entrada declarada."""
    import web.state as st
    ruta = tmp_path / "aceleraciones.json"
    ruta.write_text(json.dumps(
        {"nivel_Acceleration_5s": {"variable": "nivel", "ventana_s": 5.0}}),
        encoding="utf-8")
    monkeypatch.setattr(st, "ACELERACIONES_JSON", str(ruta))
    return st


def test_la_aceleracion_se_ofrece_como_variable(cfg_acel):
    assert "nivel_Acceleration_5s" in cfg_acel.variables_disponibles()
    assert "nivel_Acceleration_5s" in cfg_acel.variables_validas()


def test_ofrece_SOLO_sus_seis_etiquetas_mas_las_derivadas(cfg_acel):
    """A27: ofrecer la union dejaba pedir LOW a una pendiente.

    Una regla asi se guardaba, no daba error, y evaluaba 0 PARA SIEMPRE. Las
    seis (tres dinamicas + tres de signo) se ofrecen sobre la MISMA variable:
    es lo que deja escribir una regla por signo y otra por dinamica.
    """
    etqs = cfg_acel.etiquetas_validas_de("nivel_Acceleration_5s")
    assert {"ACELERANDO", "DESACELERANDO", "ESTABLE"} <= etqs
    assert {"NO-ACELERANDO", "NO-DESACELERANDO", "NO-ESTABLE"} <= etqs
    assert {"ACELERACION_POSITIVA", "ACELERACION_NEGATIVA",
            "ACELERACION_NULA"} <= etqs
    assert {"NO-ACELERACION_POSITIVA", "NO-ACELERACION_NEGATIVA",
            "NO-ACELERACION_NULA"} <= etqs
    assert "LOW" not in etqs and "INC" not in etqs


def test_las_etiquetas_de_aceleracion_estan_en_el_catalogo_global(cfg_acel):
    """El catalogo global es el que alimenta reglas, estados y subestados."""
    disponibles = cfg_acel.etiquetas_disponibles()
    for e in ("ACELERANDO", "DESACELERANDO", "ESTABLE",
              "NO-ACELERANDO", "NO-DESACELERANDO",
              "ACELERACION_POSITIVA", "ACELERACION_NEGATIVA",
              "ACELERACION_NULA", "NO-ACELERACION_POSITIVA"):
        assert e in disponibles


def test_la_negacion_la_genera_el_expansor_sin_caso_especial():
    """El estandar pide No-Acelerando y No-Desacelerando.

    No hay codigo propio para eso: salen de `expandir_etiquetas_compuestas`
    porque las pertenencias son nitidas. Este test es el que avisa si alguien
    cambia esa propiedad.
    """
    from core.fuzzy.evaluator import expandir_etiquetas_compuestas

    ac = AceleracionesOnline({"a5": {"variable": "nivel", "ventana_s": 5.0,
                                     "umbral_estable": 0.01}})
    out, _ = _correr(ac, [(t / 10.0, 0.5 * (t / 10.0) ** 2) for t in range(0, 101)])
    expandido = expandir_etiquetas_compuestas(out)
    pert = expandido["a5"]["pert"]
    assert pert["ACELERANDO"] == pytest.approx(1.0)
    assert pert["NO-ACELERANDO"] == pytest.approx(0.0)
    assert pert["NO-DESACELERANDO"] == pytest.approx(1.0)
    # Y lo mismo para el signo, sin una linea de codigo propia.
    assert pert["ACELERACION_POSITIVA"] == pytest.approx(1.0)
    assert pert["NO-ACELERACION_POSITIVA"] == pytest.approx(0.0)
    assert pert["NO-ACELERACION_NEGATIVA"] == pytest.approx(1.0)


def test_una_regla_puede_evaluar_una_condicion_de_aceleracion():
    """La prueba de que sirve en CONDICIONES: el motor la evalua de verdad."""
    from core.engine.motor import evaluar_condicion
    from core.fuzzy.evaluator import expandir_etiquetas_compuestas

    ac = AceleracionesOnline({"nivel_Acceleration_5s":
                              {"variable": "nivel", "ventana_s": 5.0,
                               "umbral_estable": 0.01}})
    out, _ = _correr(ac, [(t / 10.0, 0.5 * (t / 10.0) ** 2) for t in range(0, 101)])
    fuzzy_out = expandir_etiquetas_compuestas(out)

    assert evaluar_condicion(
        {"AND": [("nivel_Acceleration_5s", "ACELERANDO")]}, fuzzy_out) > 0.5
    assert evaluar_condicion(
        {"AND": [("nivel_Acceleration_5s", "NO-ACELERANDO")]}, fuzzy_out) < 0.5


def test_una_aceleracion_en_uso_arrastra_a_su_variable_de_origen(monkeypatch, tmp_path):
    """Sin `nivel` no hay aceleracion de `nivel`, aunque nadie la nombre.

    Es el mismo criterio con el que una pendiente y una calculada arrastran a
    sus fuentes en `roles_en_uso`.
    """
    import web.state as st
    from config import VARIABLES_PROCESO

    if not VARIABLES_PROCESO:
        pytest.skip("el contrato vigente no declara ninguna PV")
    pv = VARIABLES_PROCESO[0]

    acel = tmp_path / "aceleraciones.json"
    acel.write_text(json.dumps(
        {"acc": {"variable": pv, "ventana_s": 5.0}}), encoding="utf-8")
    reglas = tmp_path / "reglas.json"
    reglas.write_text(json.dumps([{
        "id": "R_ACC", "enabled": True, "bloque": "estabilidad",
        "priority": 50, "weight": 1.0,
        # `if` es una LISTA de condiciones, no una condicion suelta:
        # `pares_de_regla` itera sobre ella y con un dict no encuentra nada.
        "if": [{"AND": [["acc", "ACELERANDO"]]}],
        "then": [],
    }]), encoding="utf-8")

    for nombre, ruta in (("ACELERACIONES_JSON", acel), ("REGLAS_JSON", reglas)):
        monkeypatch.setattr(st, nombre, str(ruta))
    for nombre, contenido in (("PENDIENTES_JSON", "{}"), ("PERMISIVOS_JSON", "{}"),
                              ("VARIABLES_JSON", "{}"), ("DEFUZZY_JSON", "{}"),
                              ("TRACKING_JSON", "{}")):
        vacio = tmp_path / f"{nombre.lower()}.json"
        vacio.write_text(contenido, encoding="utf-8")
        monkeypatch.setattr(st, nombre, str(vacio))

    en_uso = st.roles_en_uso(fuzzy_cfg={})
    assert "acc" in en_uso["aceleracion"]
    assert pv in en_uso["pv"]


# ============================================================
# API — /api/aceleraciones
# ============================================================

@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Cliente con `aceleraciones.json` y `reglas.json` en temporales.

    Leccion A17/A19 otra vez: sin esto los tests escribirian en la config viva
    de la planta y su resultado dependeria de lo que el operador tenga cargado.
    """
    import web.state as st
    import web.api.config as C

    acel = tmp_path / "aceleraciones.json"
    acel.write_text("{}", encoding="utf-8")
    reglas = tmp_path / "reglas.json"
    reglas.write_text("[]", encoding="utf-8")
    for mod in (st, C):
        monkeypatch.setattr(mod, "ACELERACIONES_JSON", str(acel))
    monkeypatch.setattr(st, "REGLAS_JSON", str(reglas))
    monkeypatch.setattr(C, "REGLAS_JSON", str(reglas))

    from app import app
    cliente = app.test_client()
    fuentes = cliente.get("/api/aceleraciones").get_json()["fuentes"]
    if not fuentes:
        pytest.skip("el contrato vigente no ofrece ninguna fuente")
    cliente._pv = fuentes[0]["identificador"]
    cliente._reglas = reglas
    return cliente


def test_get_devuelve_catalogo_y_plantilla(cli):
    d = cli.get("/api/aceleraciones").get_json()
    assert d["actual"] == {}
    # Las seis del estandar, en el orden del modulo: primero las dinamicas y
    # despues las de signo. La pagina pinta dos pastillas y ese orden decide
    # cual va primero.
    assert d["etiquetas"] == ["ACELERANDO", "DESACELERANDO", "ESTABLE",
                              "ACELERACION_POSITIVA", "ACELERACION_NEGATIVA",
                              "ACELERACION_NULA"]
    # Las dos familias por separado: la pagina pinta una pastilla de cada una
    # y derivarlas en el navegador seria una segunda definicion del catalogo.
    assert d["etiquetas_dinamicas"] == ["ACELERANDO", "DESACELERANDO", "ESTABLE"]
    assert d["etiquetas_signo"] == ["ACELERACION_POSITIVA",
                                    "ACELERACION_NEGATIVA", "ACELERACION_NULA"]
    assert d["etiquetas_dinamicas"] + d["etiquetas_signo"] == d["etiquetas"]
    assert d["plantilla"]["min_puntos"] == 3
    assert d["limites"]["min_puntos_min"] == 3


def test_crear_normaliza_y_persiste(cli):
    r = cli.post(f"/api/aceleraciones/acc_test", json={"variable": cli._pv})
    assert r.status_code == 201
    spec = r.get_json()["actual"]["acc_test"]
    assert spec["variable"] == cli._pv
    assert spec["ventana_s"] == 5.0
    assert spec["umbral_rate"] is None          # se deriva en el nucleo
    assert spec["habilitado"] is True


def test_el_nombre_sugerido_esquiva_colisiones(cli):
    base = cli.get(f"/api/aceleraciones/sugerir-nombre?variable={cli._pv}&ventana_s=5")
    nombre = base.get_json()["nombre"]
    assert nombre.endswith("_Acceleration_5s")
    cli.post(f"/api/aceleraciones/{nombre}", json={"variable": cli._pv})
    otra = cli.get(f"/api/aceleraciones/sugerir-nombre?variable={cli._pv}&ventana_s=5")
    assert otra.get_json()["nombre"] == nombre + "_2"


@pytest.mark.parametrize("cfg,fragmento", [
    ({"variable": "", "ventana_s": 5}, "falta 'variable'"),
    ({"variable": "no_existe_jamas", "ventana_s": 5}, "no es una PV"),
    ({"ventana_s": 0}, "debe ser > 0"),
    ({"ventana_s": 99999}, "no puede pasar"),
    ({"ventana_s": 5, "umbral_estable": -1}, "no puede ser negativo"),
    ({"ventana_s": 5, "min_puntos": 2}, "no puede bajar de 3"),
])
def test_el_validador_rechaza_con_un_motivo_legible(cli, cfg, fragmento):
    payload = dict(cfg)
    payload.setdefault("variable", cli._pv)
    r = cli.put("/api/aceleraciones", json={"acc": payload})
    assert r.status_code == 400
    assert fragmento in r.get_json()["error"]


def test_un_nombre_que_choca_con_una_PV_se_rechaza(cli):
    r = cli.put("/api/aceleraciones",
                json={cli._pv: {"variable": cli._pv, "ventana_s": 5}})
    assert r.status_code == 400
    assert "ya existe como PV" in r.get_json()["error"]


def _declarar_regla(cli, variable):
    import json as _json
    cli._reglas.write_text(_json.dumps([{
        "id": "R_ACC", "enabled": True, "bloque": "estabilidad",
        "priority": 50, "weight": 1.0,
        "if": [{"AND": [[variable, "ACELERANDO"]]}],
        "then": [],
    }]), encoding="utf-8")


def test_borrar_una_aceleracion_que_una_regla_usa_se_RECHAZA(cli):
    """Fallar ruidoso: borrar en caliente lo que una regla nombra la deja muda."""
    cli.post("/api/aceleraciones/acc", json={"variable": cli._pv})
    _declarar_regla(cli, "acc")

    r = cli.delete("/api/aceleraciones/acc")
    assert r.status_code == 409
    assert "R_ACC" in r.get_json()["error"]
    assert "acc" in cli.get("/api/aceleraciones").get_json()["actual"]

    # El escape existe, pero hay que pedirlo.
    assert cli.delete("/api/aceleraciones/acc?forzar=1").status_code == 200
    assert cli.get("/api/aceleraciones").get_json()["actual"] == {}


def test_un_PUT_que_deja_la_regla_sin_su_variable_se_RECHAZA(cli):
    """Desde el JSON un renombre es un borrado + un alta: rompe igual."""
    cli.post("/api/aceleraciones/acc", json={"variable": cli._pv})
    _declarar_regla(cli, "acc")

    renombrada = {"acc_nueva": {"variable": cli._pv, "ventana_s": 5}}
    r = cli.put("/api/aceleraciones", json=renombrada)
    assert r.status_code == 409
    assert "R_ACC" in r.get_json()["error"]

    r = cli.put("/api/aceleraciones", json={"__forzar__": True, **renombrada})
    assert r.status_code == 200


def test_vaciar_deja_el_archivo_en_vacio(cli):
    cli.post("/api/aceleraciones/acc", json={"variable": cli._pv})
    assert cli.post("/api/aceleraciones/vaciar").get_json()["actual"] == {}


def test_la_ruta_vaciar_no_la_captura_el_creador_por_nombre(cli):
    """`/vaciar` y `/<nombre>` son las dos POST: el orden de matcheo importa."""
    r = cli.post("/api/aceleraciones/vaciar")
    assert r.status_code == 200 and r.get_json() == {"ok": True, "actual": {}}
