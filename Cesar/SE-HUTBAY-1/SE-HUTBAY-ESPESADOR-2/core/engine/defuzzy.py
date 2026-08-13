# -*- coding: utf-8 -*-
"""Motor de defuzzificación estilo Sugeno — lógica genérica pura.

Este módulo no contiene datos de ningún proceso.
Las tablas Sugeno se pasan como parámetro explícito `defuzzy_por_familia`.

Estructura esperada de `defuzzy_por_familia`:
    {
        "sp_<familia>": {
            "belief_axis": [b0, b1, ...],
            "steps_por_accion": {
                "AUMENTAR_FUERTE": [s_at_b0, s_at_b1, ...],
                "AUMENTAR":        [...],
                "AUMENTAR_SUAVE":  [...],
                "DISMINUIR_SUAVE": [...],
                "DISMINUIR":       [...],
                "DISMINUIR_FUERTE":[...],
            }
        }
    }

Naming convention para acciones:
    {DIRECCION}_{FAMILIA}[_{INTENSIDAD}]
      DIRECCION  : AUMENTAR | DISMINUIR
      FAMILIA    : VEL_BOMBA | TONELAJE | FLOCULANTE | ...
      INTENSIDAD : FUERTE | (vacío = Normal) | SUAVE
"""
from __future__ import annotations

import numpy as np


def _clip(x: float, ll: float, hl: float) -> float:
    return float(max(ll, min(hl, x)))


# Mapping de segmento FAMILIA -> clave de setpoint
# Puede extenderse para nuevos procesos sin modificar este módulo.
_FAMILIAS_NOMBRE_SP: dict[str, str] = {
    "VEL_BOMBA":  "sp_vel_bomba",
    "TONELAJE":   "sp_tonelaje",
    "FLOCULANTE": "sp_floculante",
}

_INTENSIDADES = ("FUERTE", "SUAVE")  # vacío = Normal


def parsear_accion(accion: str) -> dict:
    """Parsea 'AUMENTAR_VEL_BOMBA_FUERTE' -> {direccion, familia_sp, intensidad, key_defuzzy}.

    Retorna:
        {
            "direccion":   "AUMENTAR" | "DISMINUIR",
            "familia_sp":  "sp_vel_bomba" | ...,
            "intensidad":  "FUERTE" | "SUAVE" | "" (Normal),
            "key_defuzzy": "AUMENTAR_FUERTE" | "AUMENTAR" | "AUMENTAR_SUAVE" |
                           "DISMINUIR_SUAVE" | "DISMINUIR" | "DISMINUIR_FUERTE",
        }
    """
    a = str(accion).upper().strip()

    if a.startswith("AUMENTAR_"):
        direccion = "AUMENTAR"
        resto = a[len("AUMENTAR_"):]
    elif a.startswith("DISMINUIR_"):
        direccion = "DISMINUIR"
        resto = a[len("DISMINUIR_"):]
    else:
        raise ValueError(
            f"Accion no reconocida: {accion!r} (debe empezar con AUMENTAR_ o DISMINUIR_)"
        )

    # Detectar intensidad al final
    intensidad = ""
    for ints in _INTENSIDADES:
        sufijo = "_" + ints
        if resto.endswith(sufijo):
            intensidad = ints
            resto = resto[: -len(sufijo)]
            break

    familia_nombre = resto  # ej: "VEL_BOMBA"
    if familia_nombre not in _FAMILIAS_NOMBRE_SP:
        raise ValueError(
            f"Familia no reconocida en accion {accion!r}: {familia_nombre!r}. "
            f"Familias validas: {sorted(_FAMILIAS_NOMBRE_SP)}"
        )

    familia_sp = _FAMILIAS_NOMBRE_SP[familia_nombre]
    key_defuzzy = direccion if not intensidad else f"{direccion}_{intensidad}"

    return {
        "direccion":   direccion,
        "familia_sp":  familia_sp,
        "intensidad":  intensidad,
        "key_defuzzy": key_defuzzy,
    }


def step_por_accion_tabla(
    accion: str,
    belief: float,
    defuzzy_por_familia: dict,
) -> tuple[str, float]:
    """Retorna (familia_sp, step) interpolando sobre la tabla dada.

    Parámetros
    ----------
    accion : str
        Nombre de la acción (ej. 'AUMENTAR_VEL_BOMBA_FUERTE').
    belief : float
        Grado de activación de la regla [0.0, 1.0].
    defuzzy_por_familia : dict
        Tablas Sugeno indexadas por familia de setpoint.
    """
    info = parsear_accion(accion)
    familia_sp = info["familia_sp"]
    key = info["key_defuzzy"]

    tabla = defuzzy_por_familia.get(familia_sp)
    if tabla is None:
        raise ValueError(f"No hay tabla defuzzy para familia {familia_sp!r}")

    if key not in tabla["steps_por_accion"]:
        raise ValueError(
            f"Tabla defuzzy de {familia_sp} no tiene la columna {key!r}. "
            f"Columnas disponibles: {sorted(tabla['steps_por_accion'])}"
        )

    bx = np.array(tabla["belief_axis"], dtype=float)
    sy = np.array(tabla["steps_por_accion"][key], dtype=float)
    b = float(max(0.0, min(1.0, float(belief))))
    step = float(np.interp(b, bx, sy))
    return familia_sp, step


def apply_actions_tabla(
    acciones_con_belief: list[tuple[str, float]],
    setpoints: dict,
    limites_sp: dict,
    defuzzy_por_familia: dict,
) -> dict:
    """Aplica una lista [(accion, belief), ...] sobre los setpoints.

    Parámetros
    ----------
    acciones_con_belief : list[(str, float)]
    setpoints : dict — estado actual de SPs.
    limites_sp : dict — {familia_sp: (LL, HL)}.
    defuzzy_por_familia : dict — tablas Sugeno.

    Retorna
    -------
    dict con los setpoints actualizados (clipeados a límites).
    """
    nuevos = dict(setpoints)
    for accion, belief in acciones_con_belief:
        familia_sp, step = step_por_accion_tabla(accion, belief, defuzzy_por_familia)
        if familia_sp not in nuevos:
            raise KeyError(
                f"Familia {familia_sp!r} no está en setpoints (claves: {sorted(nuevos)})"
            )
        nuevos[familia_sp] = float(nuevos[familia_sp]) + step

    for familia_sp, lims in (limites_sp or {}).items():
        if familia_sp not in nuevos:
            continue
        ll, hl = float(lims[0]), float(lims[1])
        nuevos[familia_sp] = _clip(nuevos[familia_sp], ll, hl)

    return nuevos
