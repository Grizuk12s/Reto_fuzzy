# -*- coding: utf-8 -*-
"""API estable para variables calculadas en v3.

Este modulo deja un nombre consistente con la documentacion y reexporta la
implementacion existente en `fun_calc_variables.py`.
"""

from __future__ import annotations

from calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS
from fun_calc_variables import calcular_variable, calcular_variables_df, detectar_dt_s

__all__ = [
    "VARIABLES_CRUDAS",
    "DEFINICIONES_CALCULADAS",
    "calcular_variable",
    "calcular_variables_df",
    "detectar_dt_s",
]
