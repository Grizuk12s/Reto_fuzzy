# ============================================================
# defuzzy_actions.py
# ------------------------------------------------------------
# Defuzzy estilo Sugeno: cada accion mapea a un step segun el
# `belief` con el que la regla disparo, usando tablas por familia.
#
# Tablas tomadas de la hoja "Defuzzy" del Excel:
#   - sp_floculante  : calibrado
#   - sp_vel_bomba   : calibrado
#   - sp_tonelaje    : PLACEHOLDER (decision 5; tabla con valores tipicos
#                      para que el usuario edite)
#
# Notas sobre la tabla calibrada (revisar):
#   - "Disminuir Lento" del floculante y de vel_bomba tienen step
#     POSITIVO en el Excel original (+0.012, +0.03 y +0.15, +0.3).
#     Se respeta tal cual; revisar con operador si era intencion.
#   - "Aumentar Lento" de vel_bomba decrece con belief mas alto
#     (0.3 -> 0.15). Tambien se respeta.
#
# Naming convention para acciones:
#   {DIRECCION}_{FAMILIA}[_{INTENSIDAD}]
#     DIRECCION  : AUMENTAR | DISMINUIR
#     FAMILIA    : VEL_BOMBA | TONELAJE | FLOCULANTE
#     INTENSIDAD : FUERTE | (vacio = Normal) | SUAVE
#
#   ej: AUMENTAR_VEL_BOMBA_FUERTE, DISMINUIR_TONELAJE, AUMENTAR_FLOCULANTE_SUAVE
# ============================================================

from __future__ import annotations

import numpy as np


def _clip(x: float, ll: float, hl: float) -> float:
    return float(max(ll, min(hl, x)))


# ============================================================
# TABLAS DEFUZZY POR FAMILIA
# Estructura:
#   FAMILIA -> {
#      "belief_axis": [b0, b1, ...],
#      "steps_por_accion": {
#          "AUMENTAR_FUERTE": [s_at_b0, s_at_b1, ...],
#          "AUMENTAR":        [...],
#          "AUMENTAR_SUAVE":  [...],
#          "DISMINUIR_SUAVE": [...],
#          "DISMINUIR":       [...],
#          "DISMINUIR_FUERTE":[...],
#      }
#   }
# Mapping de nombres del Excel a claves:
#   "Aumentar Rapido"  -> AUMENTAR_FUERTE
#   "Aumentar"         -> AUMENTAR
#   "Aumentar Lento"   -> AUMENTAR_SUAVE
#   "Disminuir Lento"  -> DISMINUIR_SUAVE
#   "Disminuir"        -> DISMINUIR
#   "Disminuir Rapido" -> DISMINUIR_FUERTE
# ============================================================

# ----- FLOCULANTE -- calibrado del Excel -----
DEFUZZY_FLOCULANTE = {
    "belief_axis": [0.0, 0.5, 1.0],
    "steps_por_accion": {
        "AUMENTAR_FUERTE":   [0.0,  0.1,   0.1  ],
        "AUMENTAR":          [0.0,  0.06,  0.06 ],
        "AUMENTAR_SUAVE":    [0.0,  0.04,  0.04 ],
        # Atencion: signos positivos en "Disminuir Lento" del Excel original
        "DISMINUIR_SUAVE":   [0.0,  0.012, 0.03 ],
        "DISMINUIR":         [0.0, -0.025, -0.05],
        "DISMINUIR_FUERTE":  [0.0, -0.05,  -0.06],
    },
}

# ----- VEL BOMBA -- calibrado del Excel -----
DEFUZZY_VEL_BOMBA = {
    "belief_axis": [0.0, 0.5, 1.0],
    "steps_por_accion": {
        "AUMENTAR_FUERTE":   [0.0,  0.4,   0.5 ],
        "AUMENTAR":          [0.0,  0.3,   0.4 ],
        # Atencion: "Aumentar Lento" decrece al subir belief en el Excel
        "AUMENTAR_SUAVE":    [0.0,  0.3,   0.15],
        # Atencion: signos positivos en "Disminuir Lento" del Excel original
        "DISMINUIR_SUAVE":   [0.0,  0.15,  0.3 ],
        "DISMINUIR":         [0.0, -0.3,  -0.4 ],
        "DISMINUIR_FUERTE":  [0.0, -0.4,  -0.5 ],
    },
}

# ----- TONELAJE -- PLACEHOLDER (decision 5: usuario edita despues) -----
DEFUZZY_TONELAJE = {
    "belief_axis": [0.0, 0.5, 1.0],
    "steps_por_accion": {
        # TODO calibrar con el equipo de proceso. Valores tipicos:
        "AUMENTAR_FUERTE":   [0.0,  2.0,   3.0 ],
        "AUMENTAR":          [0.0,  1.0,   2.0 ],
        "AUMENTAR_SUAVE":    [0.0,  0.5,   1.0 ],
        "DISMINUIR_SUAVE":   [0.0, -0.5,  -1.0 ],
        "DISMINUIR":         [0.0, -1.0,  -2.0 ],
        "DISMINUIR_FUERTE":  [0.0, -2.0,  -3.0 ],
    },
}


DEFUZZY_POR_FAMILIA = {
    "sp_floculante": DEFUZZY_FLOCULANTE,
    "sp_vel_bomba":  DEFUZZY_VEL_BOMBA,
    "sp_tonelaje":   DEFUZZY_TONELAJE,
}


# ============================================================
# Parser de nombre de accion
# ============================================================
_FAMILIAS_NOMBRE_SP = {
    "VEL_BOMBA":  "sp_vel_bomba",
    "TONELAJE":   "sp_tonelaje",
    "FLOCULANTE": "sp_floculante",
}

_INTENSIDADES = ("FUERTE", "SUAVE")  # vacio = Normal


def parsear_accion(accion: str) -> dict:
    """Parsea 'AUMENTAR_VEL_BOMBA_FUERTE' -> {direccion, familia_sp, key_defuzzy}.

    Retorna:
        {
            "direccion": "AUMENTAR" | "DISMINUIR",
            "familia_sp": "sp_vel_bomba" | ...,
            "intensidad": "FUERTE" | "SUAVE" | "" (Normal),
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
        raise ValueError(f"Accion no reconocida: {accion!r} (debe empezar con AUMENTAR_ o DISMINUIR_)")

    # Detectar intensidad al final
    intensidad = ""
    for ints in _INTENSIDADES:
        sufijo = "_" + ints
        if resto.endswith(sufijo):
            intensidad = ints
            resto = resto[:-len(sufijo)]
            break

    # Lo que queda es el nombre de la familia
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


# ============================================================
# Calculo del step por accion + belief
# ============================================================
def step_por_accion(accion: str, belief: float) -> tuple[str, float]:
    """Retorna (familia_sp, step) para una accion y belief dados.

    El step se interpola linealmente sobre el `belief_axis` de la tabla
    de la familia. Si el belief sale del rango (0..1), se clipea.
    """
    info = parsear_accion(accion)
    familia_sp = info["familia_sp"]
    key = info["key_defuzzy"]

    tabla = DEFUZZY_POR_FAMILIA.get(familia_sp)
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


# ============================================================
# Aplicar acciones disparadas a los setpoints
# ------------------------------------------------------------
# Reemplaza la apply_action(...) de v4. El step ya viene en unidades
# absolutas del setpoint (el Excel ya define magnitudes reales), asi
# que no se multiplica por (HL-LL).
# ============================================================
def apply_actions(
    acciones_con_belief: list[tuple[str, float]],
    setpoints: dict,
    limites_sp: dict,
) -> dict:
    """Aplica una lista [(accion, belief), ...] sobre los setpoints.

    Parametros
    ----------
    acciones_con_belief : list[(str, float)]
        Cada item es (nombre_accion, belief).
    setpoints : dict
        Estado actual de SPs. Debe tener al menos las claves listadas en
        SETPOINT_KEYS (sp_tonelaje, sp_floculante, sp_vel_bomba).
    limites_sp : dict
        Limites (LL, HL) por familia. Ejemplo:
            {"sp_tonelaje": (LL, HL), "sp_floculante": (LL, HL), "sp_vel_bomba": (LL, HL)}

    Retorna
    -------
    dict con los setpoints actualizados (clipeados a limites).
    """
    nuevos = dict(setpoints)
    for accion, belief in acciones_con_belief:
        familia_sp, step = step_por_accion(accion, belief)
        if familia_sp not in nuevos:
            raise KeyError(
                f"Familia {familia_sp!r} no esta en setpoints (claves: {sorted(nuevos)})"
            )
        nuevos[familia_sp] = float(nuevos[familia_sp]) + step

    # Clip a limites
    for familia_sp, lims in (limites_sp or {}).items():
        if familia_sp not in nuevos:
            continue
        ll, hl = float(lims[0]), float(lims[1])
        nuevos[familia_sp] = _clip(nuevos[familia_sp], ll, hl)

    return nuevos
