# -*- coding: utf-8 -*-
"""Variables del proceso Espesador — definiciones declarativas.

Contiene únicamente las definiciones de datos específicas del Espesador:
- VARIABLES_CRUDAS : sensores directos que deben existir en el DataFrame.
- DEFINICIONES_CALCULADAS : variables derivadas que el runner calcula
  automáticamente usando core.variables.calculator.calcular_variables_df().

La lógica de cálculo (rolling, aritmética, etc.) vive en
core.variables.calculator — este módulo solo declara qué calcular.
"""
from __future__ import annotations


# ============================================================
# VARIABLES CRUDAS (sensores directos)
# ============================================================
VARIABLES_CRUDAS: dict[str, str] = {
    "tonelaje_sag_1":  "Tonelaje SAG Mill 1 (t/h)",
    "tonelaje_sag_2":  "Tonelaje SAG Mill 2 (t/h)",
    "tonelaje_relave": "Tonelaje de relave (t/h)",
    "presion_bomba_1": "Presión de impulsión bomba descarga 1 (bar)",
    "presion_bomba_2": "Presión de impulsión bomba descarga 2 (bar)",
    "turbiedad_agua":  "Turbidez del agua recuperada (NTU)",
}


# ============================================================
# DEFINICIONES CALCULADAS (procesadas en orden — chaining OK)
# ============================================================
DEFINICIONES_CALCULADAS: dict[str, dict] = {

    # Intermedia: tonelaje SAG total (se calcula antes de las derivadas)
    "tonelaje_sag_total": {
        "descripcion": "Tonelaje total SAG (Mill 1 + Mill 2) en t/h",
        "tipo": "aritmetica",
        "operacion": "suma",
        "args": ["tonelaje_sag_1", "tonelaje_sag_2"],
    },

    "tonelaje_sag_delta_30min": {
        "descripcion": "Cambio del tonelaje SAG total en los últimos 30 min (t/h)",
        "tipo": "rolling_delta",
        "arg": "tonelaje_sag_total",
        "ventana_min": 30.0,
    },

    "tonelaje_sag_desv_est_30min": {
        "descripcion": "Desv. estándar del tonelaje SAG total en ventana de 30 min",
        "tipo": "rolling_std",
        "arg": "tonelaje_sag_total",
        "ventana_min": 30.0,
    },

    "diferencial_ton_sag_relave": {
        "descripcion": "Diferencial de tonelaje: SAG total - relave (t/h)",
        "tipo": "aritmetica",
        "operacion": "resta",
        "args": ["tonelaje_sag_total", "tonelaje_relave"],
    },

    "diferencial_presion_bbas": {
        "descripcion": "Diferencial de presión de impulsión: bomba_1 - bomba_2 (bar)",
        "tipo": "aritmetica",
        "operacion": "resta",
        "args": ["presion_bomba_1", "presion_bomba_2"],
    },
}
