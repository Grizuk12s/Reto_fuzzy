# -*- coding: utf-8 -*-
"""Shim de compatibilidad — calculos_variables.py

Re-exporta las definiciones declarativas del Espesador desde
processes.espesador.variables.

Call sites existentes:
    from calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS
    -> Siguen funcionando sin cambio.
"""
from __future__ import annotations

from processes.espesador.variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS

__all__ = ["DEFINICIONES_CALCULADAS", "VARIABLES_CRUDAS"]
