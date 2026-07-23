# -*- coding: utf-8 -*-
"""Permisivos operacionales del Espesador.

Define el dict PERMISIVOS con las condiciones específicas del proceso
Espesador, calibradas según la hoja "Permisivos" del Excel del proyecto.

La evaluación la realiza core.engine.permisivos.evaluar_permisivos().

Notas:
  Las variables externas (tonelaje_sag_*, nivel_rastra, turbiedad_agua,
  diferencial_*) son placeholders. Si no están en el DataFrame, la
  condición individual queda en False (no satisfecha) y se ignora con
  seguridad (decisión F1).
"""
from __future__ import annotations

PERMISIVOS: dict[str, list] = {

    # ----------------------------------------------------------
    # PERMITIR_FRENAR_DESCARGA
    # ON cuando NO hay alertas. Cualquiera de las alertas apaga el
    # permisivo (= no se debe frenar la descarga).
    # ----------------------------------------------------------
    "PERMITIR_FRENAR_DESCARGA": [
        # alerta 1: tonelaje SAG creciendo fuerte en 30 min
        {"NOT": {"var": "tonelaje_sag_delta_30min", "op": ">", "value": 300.0}},

        # alerta 2: variabilidad de tonelaje SAG alta en 30 min
        {"NOT": {"var": "tonelaje_sag_desv_est_30min", "op": ">", "value": 50.0}},

        # alerta 3: torque OK pero subiendo
        {"NOT": {"AND": [
            {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5},
            {"fuzzy_var": "pend_torque", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 4: torque LP OK y subiendo (mismo proxy que alerta 3)
        {"NOT": {"AND": [
            {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5},
            {"fuzzy_var": "pend_torque", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 5: Bed Level OK (cerca bajo) y bajando
        {"NOT": {"AND": [
            {"fuzzy_var": "bed_level", "label": "CERCA_BAJO", "min_mu": 0.4},
            {"fuzzy_var": "pend_bed_level", "label": "DEC", "min_mu": 0.5},
        ]}},

        # alerta 6: Bed Mass OK (cerca alto) y subiendo
        {"NOT": {"AND": [
            {"fuzzy_var": "bed_mass", "label": "CERCA_ALTO", "min_mu": 0.4},
            {"fuzzy_var": "pend_bed_mass", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 7: Presión descarga OK (cerca alto) y subiendo
        {"NOT": {"AND": [
            {"fuzzy_var": "presion_descarga", "label": "CERCA_ALTO", "min_mu": 0.4},
            {"fuzzy_var": "pend_presion_descarga", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 8: Nivel Rastra > 15% (variable externa)
        {"NOT": {"var": "nivel_rastra", "op": ">", "value": 15.0}},

        # alerta 9: Turbiedad agua > umbral (variable externa)
        {"NOT": {"var": "turbiedad_agua", "op": ">", "value": 50.0}},

        # alerta 10: Diferencial Ton SAG creciendo y alto
        {"NOT": {"AND": [
            {"var": "diferencial_ton_sag_relave", "op": ">", "value": 0.0},
            {"var": "tonelaje_sag_delta_30min",   "op": ">", "value": 3500.0},
        ]}},
    ],

    # ----------------------------------------------------------
    # PERMITIR_SOLTAR_DESCARGA
    # ON cuando NO hay las alertas listadas.
    # ----------------------------------------------------------
    "PERMITIR_SOLTAR_DESCARGA": [
        # alerta 1: Diferencial de presión entre bombas aumentando
        {"NOT": {"var": "diferencial_presion_bbas", "op": ">", "value": 0.0}},

        # alerta 2: Diferencial presión alto
        {"NOT": {"var": "diferencial_presion_bbas", "op": ">", "value": 5.0}},

        # alerta 3: Presión diferencial (impulsión - sello) baja
        {"NOT": {"fuzzy_var": "presion_diferencial", "label": "LOW", "min_mu": 0.5}},

        # alerta 4: Torque bombas alto
        {"NOT": {"fuzzy_var": "torque_bomba", "label": "HIGH", "min_mu": 0.5}},
    ],

    # ----------------------------------------------------------
    # OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD
    # ON cuando condiciones son favorables para subir el objetivo.
    # ----------------------------------------------------------
    "OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD": [
        # tonelaje SAG bajo o poco variable
        {"OR": [
            {"var": "tonelaje_sag_delta_30min",   "op": "<", "value": -200.0},
            {"var": "tonelaje_sag_desv_est_30min", "op": "<", "value": 20.0},
        ]},
        # torque OK y estable LP
        {"AND": [
            {"fuzzy_var": "torque",      "label": "OK",     "min_mu": 0.6},
            {"fuzzy_var": "pend_torque", "label": "STABLE", "min_mu": 0.5},
        ]},
        # Bed Level OK y estable LP
        {"AND": [
            {"fuzzy_var": "bed_level",      "label": "OK",     "min_mu": 0.6},
            {"fuzzy_var": "pend_bed_level", "label": "STABLE", "min_mu": 0.5},
        ]},
        # Presión Cama OK y No Aumentando (proxy: presion_descarga)
        {"AND": [
            {"fuzzy_var": "presion_descarga",      "label": "OK",  "min_mu": 0.5},
            {"NOT": {"fuzzy_var": "pend_presion_descarga", "label": "INC", "min_mu": 0.5}},
        ]},
        # Presión descarga no alta
        {"NOT": {"fuzzy_var": "presion_descarga", "label": "HIGH", "min_mu": 0.5}},
    ],

    # ----------------------------------------------------------
    # OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD
    # ON cuando operación es menos favorable / alta variabilidad.
    # ----------------------------------------------------------
    "OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD": [
        # tonelaje SAG alto o muy variable
        {"OR": [
            {"var": "tonelaje_sag_delta_30min",   "op": ">", "value": 200.0},
            {"var": "tonelaje_sag_desv_est_30min", "op": ">", "value": 30.0},
        ]},
        # torque OK y aumentando LP
        {"AND": [
            {"fuzzy_var": "torque",      "label": "OK",  "min_mu": 0.5},
            {"fuzzy_var": "pend_torque", "label": "INC", "min_mu": 0.5},
        ]},
        # Bed Level OK y disminuyendo
        {"AND": [
            {"fuzzy_var": "bed_level",      "label": "OK",  "min_mu": 0.5},
            {"fuzzy_var": "pend_bed_level", "label": "DEC", "min_mu": 0.5},
        ]},
        # Presión descarga aumentando
        {"fuzzy_var": "pend_presion_descarga", "label": "INC", "min_mu": 0.5},
    ],
}
