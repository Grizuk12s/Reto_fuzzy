# -*- coding: utf-8 -*-
"""Shim de compatibilidad — defuzzy_actions.py

Re-exporta la lógica del motor desde core.engine.defuzzy y las tablas
del proceso Espesador desde processes.espesador.defuzzy_tables.

DEFUZZY_POR_FAMILIA es un dict mutable en este módulo para que app.py
pueda actualizarlo en caliente con .clear() + .update() (monkey-patch
de configuración en vivo desde defuzzy.json).

Imports existentes en el resto del proyecto siguen funcionando sin cambio:
    from defuzzy_actions import DEFUZZY_POR_FAMILIA
    from defuzzy_actions import apply_actions
    from defuzzy_actions import parsear_accion
    import defuzzy_actions as _dfz_mod   # <- monkey-patch vivo en app.py
"""
from __future__ import annotations

# ---------- Engine genérico ----------
from core.engine.defuzzy import (
    _clip,
    _FAMILIAS_NOMBRE_SP,
    _INTENSIDADES,
    parsear_accion,
    step_por_accion_tabla,
    apply_actions_tabla,
)

# ---------- Tablas del Espesador ----------
from processes.espesador.defuzzy_tables import (
    DEFUZZY_FLOCULANTE,
    DEFUZZY_VEL_BOMBA,
    DEFUZZY_TONELAJE,
    DEFUZZY_POR_FAMILIA_DEFAULT,
)

# ---------- Dict mutable (monkey-patcheable en vivo) ----------
# app.py hace: _dfz_mod.DEFUZZY_POR_FAMILIA.clear(); .update(defuzzy_cfg)
DEFUZZY_POR_FAMILIA: dict = dict(DEFUZZY_POR_FAMILIA_DEFAULT)


# ---------- Wrappers que usan el dict mutable local ----------
def step_por_accion(accion: str, belief: float) -> tuple[str, float]:
    """Retorna (familia_sp, step) usando la tabla activa DEFUZZY_POR_FAMILIA."""
    return step_por_accion_tabla(accion, belief, DEFUZZY_POR_FAMILIA)


def apply_actions(
    acciones_con_belief: list[tuple[str, float]],
    setpoints: dict,
    limites_sp: dict,
) -> dict:
    """Aplica acciones sobre setpoints usando la tabla activa DEFUZZY_POR_FAMILIA."""
    return apply_actions_tabla(
        acciones_con_belief, setpoints, limites_sp, DEFUZZY_POR_FAMILIA
    )
