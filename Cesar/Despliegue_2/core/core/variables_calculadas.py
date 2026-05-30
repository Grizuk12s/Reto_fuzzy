# -*- coding: utf-8 -*-
"""Shim de compatibilidad.

El nucleo divide el calculo de variables derivadas en dos modulos:
- ``calculos_variables.py``: definiciones declarativas (VARIABLES_CRUDAS,
  DEFINICIONES_CALCULADAS).
- ``fun_calc_variables.py``: funciones que ejecutan los calculos
  (``calcular_variable``, ``calcular_variables_df``, ``detectar_dt_s``).

El ``runner.py`` y los docstrings internos del nucleo siguen referenciando el
nombre historico ``variables_calculadas`` para el modulo de funciones.
Este archivo mantiene esa fachada re-exportando los simbolos relevantes para
que los imports no se rompan ni en el nucleo ni en los despliegues standalone.
"""

from __future__ import annotations

from .calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS
from .fun_calc_variables import (
    calcular_variable,
    calcular_variables_df,
    detectar_dt_s,
)

__all__ = [
    "DEFINICIONES_CALCULADAS",
    "VARIABLES_CRUDAS",
    "calcular_variable",
    "calcular_variables_df",
    "detectar_dt_s",
]
