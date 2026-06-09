# -*- coding: utf-8 -*-
"""Definiciones declarativas de variables calculadas.

Este modulo contiene la configuracion de los calculos: que variables crudas
deben existir y que variables derivadas se deben producir. La logica que
ejecuta estos calculos vive en ``variables_calculadas.py``.
"""

from __future__ import annotations


# ============================================================
# VARIABLES CRUDAS (sensores directos que deben estar en el DF)
# ============================================================
VARIABLES_CRUDAS: dict[str, str] = {
    "tonelaje_sag_1":  "Tonelaje SAG Mill 1 (t/h)",
    "tonelaje_sag_2":  "Tonelaje SAG Mill 2 (t/h)",
    "tonelaje_relave": "Tonelaje de relave (t/h)",
    "presion_bomba_1": "Presion de impulsion bomba descarga 1 (bar)",
    "presion_bomba_2": "Presion de impulsion bomba descarga 2 (bar)",
    "turbiedad_agua":  "Turbiedad del agua recuperada (NTU) -- sensor directo",
}


# ============================================================
# DEFINICIONES DECLARATIVAS DE VARIABLES CALCULADAS
# ============================================================
# Las entradas se procesan en orden. Una variable calculada puede
# servir de argumento a la siguiente (chaining).
#
# Campos comunes:
#   "descripcion" : texto libre de documentacion.
#   "tipo"        : 'aritmetica' | 'rolling_delta' | 'rolling_std'
#
# Campos para tipo "aritmetica":
#   "operacion"   : 'suma' | 'resta' | 'multiplicacion' | 'division'
#   "args"        : [nombre_col_a, nombre_col_b]  (exactamente 2)
#
# Campos para tipo "rolling_delta" | "rolling_std":
#   "arg"         : nombre de la columna fuente
#   "ventana_min" : duracion de la ventana en minutos
# ============================================================
DEFINICIONES_CALCULADAS: dict[str, dict] = {

    # -- INTERMEDIA: tonelaje SAG total (SAG1 + SAG2) ---------
    # Se calcula primero porque otras definiciones la usan.
    "tonelaje_sag_total": {
        "descripcion": "Tonelaje total SAG (Mill 1 + Mill 2) en t/h",
        "tipo": "aritmetica",
        "operacion": "suma",
        "args": ["tonelaje_sag_1", "tonelaje_sag_2"],
    },

    # -- Delta de tonelaje SAG en ventana de 30 min -----------
    "tonelaje_sag_delta_30min": {
        "descripcion": "Cambio del tonelaje SAG total en los ultimos 30 min (t/h)",
        "tipo": "rolling_delta",
        "arg": "tonelaje_sag_total",
        "ventana_min": 30.0,
    },

    # -- Desviacion estandar de tonelaje SAG en 30 min --------
    "tonelaje_sag_desv_est_30min": {
        "descripcion": "Desv. estandar del tonelaje SAG total en ventana de 30 min",
        "tipo": "rolling_std",
        "arg": "tonelaje_sag_total",
        "ventana_min": 30.0,
    },

    # -- Diferencial tonelaje SAG - relave --------------------
    "diferencial_ton_sag_relave": {
        "descripcion": "Diferencial de tonelaje: SAG total - relave (t/h)",
        "tipo": "aritmetica",
        "operacion": "resta",
        "args": ["tonelaje_sag_total", "tonelaje_relave"],
    },

    # -- Diferencial de presion entre bombas (impulsion) ------
    "diferencial_presion_bbas": {
        "descripcion": "Diferencial de presion de impulsion: bomba_1 - bomba_2 (bar)",
        "tipo": "aritmetica",
        "operacion": "resta",
        "args": ["presion_bomba_1", "presion_bomba_2"],
    },
}
