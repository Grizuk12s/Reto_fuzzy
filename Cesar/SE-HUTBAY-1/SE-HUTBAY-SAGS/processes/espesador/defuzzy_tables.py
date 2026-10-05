# -*- coding: utf-8 -*-
"""Tablas de defuzzificación Sugeno — Proceso Espesador.

Calibradas a partir de la hoja "Defuzzy" del Excel del proyecto.

Notas sobre valores del Excel original:
  - "Disminuir Lento" de floculante y vel_bomba tienen step POSITIVO
    (+0.012, +0.03 y +0.15, +0.3). Se respeta tal cual.
  - "Aumentar Lento" de vel_bomba decrece con belief más alto (0.3 -> 0.15).

Las tablas se referencian desde defuzzy_actions.py (shim) a través de
DEFUZZY_POR_FAMILIA_DEFAULT y DEFUZZY_POR_FAMILIA (mutable, para monkey-patch
en vivo desde app.py).
"""
from __future__ import annotations

# ----- FLOCULANTE — calibrado del Excel -----
DEFUZZY_FLOCULANTE: dict = {
    "belief_axis": [0.0, 0.5, 1.0],
    "steps_por_accion": {
        "AUMENTAR_FUERTE":   [0.0,  0.1,   0.1  ],
        "AUMENTAR":          [0.0,  0.06,  0.06 ],
        "AUMENTAR_SUAVE":    [0.0,  0.04,  0.04 ],
        # Atención: signos positivos en "Disminuir Lento" del Excel original
        "DISMINUIR_SUAVE":   [0.0,  0.012, 0.03 ],
        "DISMINUIR":         [0.0, -0.025, -0.05],
        "DISMINUIR_FUERTE":  [0.0, -0.05,  -0.06],
    },
}

# ----- VEL BOMBA — calibrado del Excel -----
DEFUZZY_VEL_BOMBA: dict = {
    "belief_axis": [0.0, 0.5, 1.0],
    "steps_por_accion": {
        "AUMENTAR_FUERTE":   [0.0,  0.4,   0.5 ],
        "AUMENTAR":          [0.0,  0.3,   0.4 ],
        # Atención: "Aumentar Lento" decrece al subir belief en el Excel
        "AUMENTAR_SUAVE":    [0.0,  0.3,   0.15],
        # Atención: signos positivos en "Disminuir Lento" del Excel original
        "DISMINUIR_SUAVE":   [0.0,  0.15,  0.3 ],
        "DISMINUIR":         [0.0, -0.3,  -0.4 ],
        "DISMINUIR_FUERTE":  [0.0, -0.4,  -0.5 ],
    },
}

# ----- TONELAJE — PLACEHOLDER (calibrar con el equipo de proceso) -----
DEFUZZY_TONELAJE: dict = {
    "belief_axis": [0.0, 0.5, 1.0],
    "steps_por_accion": {
        # TODO: calibrar con el equipo de proceso. Valores típicos:
        "AUMENTAR_FUERTE":   [0.0,  2.0,   3.0 ],
        "AUMENTAR":          [0.0,  1.0,   2.0 ],
        "AUMENTAR_SUAVE":    [0.0,  0.5,   1.0 ],
        "DISMINUIR_SUAVE":   [0.0, -0.5,  -1.0 ],
        "DISMINUIR":         [0.0, -1.0,  -2.0 ],
        "DISMINUIR_FUERTE":  [0.0, -2.0,  -3.0 ],
    },
}

# Dict maestro — indexado por familia de setpoint
DEFUZZY_POR_FAMILIA_DEFAULT: dict = {
    "sp_floculante": DEFUZZY_FLOCULANTE,
    "sp_vel_bomba":  DEFUZZY_VEL_BOMBA,
    "sp_tonelaje":   DEFUZZY_TONELAJE,
}
