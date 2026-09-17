# -*- coding: utf-8 -*-
"""API estable para variables calculadas — re-exportación consolidada.

Combina lógica (core.variables.calculator) y datos del Espesador
(processes.espesador.variables) en un único punto de importación.

Call sites existentes:
    from variables_calculadas import calcular_variables_df, detectar_dt_s, DEFINICIONES_CALCULADAS
    from variables_calculadas import VARIABLES_CRUDAS
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
    "VARIABLES_CRUDAS",
    "DEFINICIONES_CALCULADAS",
    "calcular_variable",
    "calcular_variables_df",
    "detectar_dt_s",
]
