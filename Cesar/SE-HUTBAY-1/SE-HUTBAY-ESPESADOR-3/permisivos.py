# -*- coding: utf-8 -*-
"""Shim de compatibilidad — permisivos.py

Re-exporta la lógica del motor desde core.engine.permisivos y el dict
de permisivos del Espesador desde processes.espesador.permisivos_config.

Imports existentes en el resto del proyecto siguen funcionando sin cambio:
    from permisivos import PERMISIVOS, nombre_variable_permisivo
    from permisivos import evaluar_permisivos, inyectar_permisivos_en_fuzzy_out
"""
from __future__ import annotations

# ---------- Engine genérico ----------
from core.engine.permisivos import (
    _OPERADORES,
    nombre_variable_permisivo,
    _mu_fuzzy,
    _resolver_valor,
    _evaluar_condicion,
    evaluar_permisivos,
    inyectar_permisivos_en_fuzzy_out,
)

# ---------- Permisivos del Espesador ----------
from processes.espesador.permisivos_config import PERMISIVOS

__all__ = [
    "_OPERADORES",
    "nombre_variable_permisivo",
    "_mu_fuzzy",
    "_resolver_valor",
    "_evaluar_condicion",
    "evaluar_permisivos",
    "inyectar_permisivos_en_fuzzy_out",
    "PERMISIVOS",
]
