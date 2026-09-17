# -*- coding: utf-8 -*-
"""Shim de compatibilidad — permisivos.py

Re-exporta la logica del motor desde core.engine.permisivos.

Imports existentes en el resto del proyecto siguen funcionando sin cambio:
    from permisivos import nombre_variable_permisivo
    from permisivos import evaluar_permisivos, inyectar_permisivos_en_fuzzy_out

NOTA (2026-09-01): ya no se exporta el dict `PERMISIVOS` del espesador.
Los permisivos salen de `config/<contrato>/permisivos.json`, que es lo que
el motor evalua (`cargar_permisivos_json`). El dict hardcodeado se usaba solo
como "default" y como catalogo de la interfaz, y en ese segundo papel ofrecia
cinco pseudo-variables `__PERM_*` de otra planta que el motor nunca producia:
la condicion se podia escribir, se guardaba, y quedaba `no_evaluable` para
siempre. Ver AFINACION_PENDIENTE.md A20.
"""
from __future__ import annotations

from core.engine.permisivos import (
    _OPERADORES,
    nombre_variable_permisivo,
    _mu_fuzzy,
    _resolver_valor,
    _evaluar_condicion,
    evaluar_permisivos,
    inyectar_permisivos_en_fuzzy_out,
)

__all__ = [
    "_OPERADORES",
    "nombre_variable_permisivo",
    "_mu_fuzzy",
    "_resolver_valor",
    "_evaluar_condicion",
    "evaluar_permisivos",
    "inyectar_permisivos_en_fuzzy_out",
]
