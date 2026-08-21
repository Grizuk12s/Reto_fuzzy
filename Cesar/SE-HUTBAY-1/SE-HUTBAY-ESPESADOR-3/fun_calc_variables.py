# -*- coding: utf-8 -*-
"""Shim de compatibilidad — fun_calc_variables.py

Re-exporta la lógica genérica de cálculo desde core.variables.calculator
y las definiciones del proceso desde processes.espesador.variables.

Call sites existentes:
    from fun_calc_variables import calcular_variable, calcular_variables_df, detectar_dt_s
    -> Siguen funcionando sin cambio.
"""
from __future__ import annotations

from core.variables.calculator import (
    calcular_variable,
    calcular_variables_df,
    detectar_dt_s,
)
from processes.espesador.variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS

__all__ = [
    "calcular_variable",
    "calcular_variables_df",
    "detectar_dt_s",
    "DEFINICIONES_CALCULADAS",
    "VARIABLES_CRUDAS",
]
