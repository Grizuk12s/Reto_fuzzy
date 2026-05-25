# ============================================================
# reglas_estrategia_correcta.py
# ------------------------------------------------------------
# Estrategia hardcodeada para el experto.
#
# Importante:
# - Las reglas compuestas (ej. 2.1 + 2.1_b) se representan aquí
#   como UNA sola regla con múltiples acciones en `then`.
# - Eso preserva la semántica correcta de una única condición que
#   dispara una maniobra compuesta sobre más de un setpoint.
# ============================================================

from __future__ import annotations


REGLAS = [
    # 1.x
    {"id": "1.1", "if": [("potencia", "LOW"), ("pend_potencia", "DEC"), ("nivel", "HIGH"), ("p80", "HIGH")],
     "then": ["DISMINUIR_TONELAJE_MUY_FUERTE"], "weight": 1.0, "priority": 100.0, "cooldown_s": 90},
    {"id": "1.2", "if": [("potencia", "LOW"), ("pend_potencia", "STABLE"), ("nivel", "HIGH"), ("p80", "HIGH")],
     "then": ["DISMINUIR_TONELAJE_FUERTE"], "weight": 1.0, "priority": 99.0, "cooldown_s": 90},

    # 2.x (una condición, dos acciones)
    {"id": "2.1", "if": [("potencia", "LOW"), ("pend_potencia", "DEC"), ("nivel", "HIGH"), ("p80", "NO-HIGH")],
     "then": ["DISMINUIR_TONELAJE_FUERTE", "DISMINUIR_AGUA_CAJON_FUERTE"], "weight": 1.0, "priority": 98.0,
     "cooldown_s": {"sp_tonelaje": 90, "sp_agua_cajon": 60}},
    {"id": "2.2", "if": [("potencia", "LOW"), ("pend_potencia", "STABLE"), ("nivel", "HIGH"), ("p80", "NO-HIGH")],
     "then": ["DISMINUIR_TONELAJE", "DISMINUIR_AGUA_CAJON"], "weight": 1.0, "priority": 96.0,
     "cooldown_s": {"sp_tonelaje": 90, "sp_agua_cajon": 60}},

    # 3.x
    {"id": "3.1", "if": [("potencia", "LOW"), ("pend_potencia", "DEC"), ("nivel", "HIGH"), ("p80", "HIGH")],
     "then": ["DISMINUIR_TONELAJE_FUERTE"], "weight": 1.0, "priority": 94.0, "cooldown_s": 90},
    {"id": "3.2", "if": [("potencia", "LOW"), ("pend_potencia", "STABLE"), ("nivel", "HIGH"), ("p80", "HIGH")],
     "then": ["DISMINUIR_TONELAJE"], "weight": 1.0, "priority": 93.0, "cooldown_s": 90},

    # 4.x (una condición, dos acciones)
    {"id": "4.1", "if": [("potencia", "LOW"), ("pend_potencia", "DEC"), ("nivel", "NO-HIGH"), ("p80", "NO-HIGH")],
     "then": ["AUMENTAR_AGUA_MOLINO_FUERTE", "DISMINUIR_AGUA_CAJON_FUERTE"], "weight": 1.0, "priority": 92.0,
     "cooldown_s": {"sp_agua_molino": 60, "sp_agua_cajon": 60}},
    {"id": "4.2", "if": [("potencia", "LOW"), ("pend_potencia", "STABLE"), ("nivel", "NO-HIGH"), ("p80", "NO-HIGH")],
     "then": ["AUMENTAR_AGUA_MOLINO", "DISMINUIR_AGUA_CAJON"], "weight": 1.0, "priority": 90.0,
     "cooldown_s": {"sp_agua_molino": 60, "sp_agua_cajon": 60}},

    # Cajón alto
    {"id": "5.0", "if": [("nivel", "HIGH"), ("pend_nivel", "NO-DEC"), ("presion", "HIGH"), ("p80", "HIGH")],
     "then": ["DISMINUIR_TONELAJE"], "weight": 1.0, "priority": 88.0, "cooldown_s": 90},
    {"id": "6.0", "if": [("nivel", "HIGH"), ("pend_nivel", "NO-DEC"), ("presion", "HIGH"), ("p80", "NO-HIGH")],
     "then": ["DISMINUIR_AGUA_CAJON"], "weight": 1.0, "priority": 87.0, "cooldown_s": 30},
    {"id": "7.0", "if": [("nivel", "HIGH"), ("pend_nivel", "NO-DEC"), ("presion", "NO-HIGH")],
     "then": ["AUMENTAR_RPM_BOMBA"], "weight": 1.0, "priority": 86.0, "cooldown_s": 30},

    # P80 alto
    {"id": "8.0", "if": [("p80", "HIGH"), ("pend_p80", "NO-DEC"), ("presion", "NO-HIGH")],
     "then": ["AUMENTAR_AGUA_CAJON"], "weight": 1.0, "priority": 85.0, "cooldown_s": 45},
    {"id": "9.1", "if": [("p80", "HIGH"), ("pend_p80", "STABLE"), ("presion", "HIGH")],
     "then": ["DISMINUIR_TONELAJE_SUAVE"], "weight": 1.0, "priority": 84.0, "cooldown_s": 90},
    {"id": "9.2", "if": [("p80", "HIGH"), ("pend_p80", "INC"), ("presion", "HIGH")],
     "then": ["DISMINUIR_AGUA_MOLINO"], "weight": 1.0, "priority": 83.0, "cooldown_s": 60},

    # Optimizar potencia
    {"id": "10.0", "if": [("potencia", "CERCA_BAJO"), ("pend_potencia", "DEC")],
     "then": ["DISMINUIR_TONELAJE"], "weight": 1.0, "priority": 82.0, "cooldown_s": 90},

    # 11.0 sin acción en fuente original

    # No Regla 15
    {"id": "12.1", "if": [("__R15", "OFF"), ("potencia", "OK"), ("pend_potencia", "STABLE")],
     "then": ["AUMENTAR_TONELAJE"], "weight": 1.0, "priority": 80.0, "cooldown_s": 90},
    {"id": "12.2", "if": [("__R15", "OFF"), ("potencia", "OK"), ("pend_potencia", "INC")],
     "then": ["AUMENTAR_TONELAJE_FUERTE"], "weight": 1.0, "priority": 79.0, "cooldown_s": 90},
    {"id": "12.3", "if": [("__R15", "OFF"), ("potencia", "HIGH"), ("pend_potencia", "NO-DEC")],
     "then": ["AUMENTAR_TONELAJE_MUY_FUERTE"], "weight": 1.0, "priority": 78.0, "cooldown_s": 90},

    # P80 no alto
    {"id": "13.1", "if": [("p80", "OK"), ("pend_p80", "STABLE")],
     "then": ["DISMINUIR_AGUA_CAJON_SUAVE"], "weight": 1.0, "priority": 77.0, "cooldown_s": 30},
    {"id": "13.2", "if": [("p80", "OK"), ("pend_p80", "DEC")],
     "then": ["DISMINUIR_AGUA_CAJON"], "weight": 1.0, "priority": 76.0, "cooldown_s": 30},
    {"id": "13.3", "if": [("p80", "LOW"), ("pend_p80", "NO-INC")],
     "then": ["DISMINUIR_AGUA_CAJON_FUERTE"], "weight": 1.0, "priority": 75.0, "cooldown_s": 30},

    # Control densidad
    {"id": "14.0", "if": [("densidad", "HIGH"), ("pend_densidad", "NO-DEC"), ("presion", "NO-HIGH")],
     "then": ["AUMENTAR_AGUA_CAJON"], "weight": 1.0, "priority": 74.0, "cooldown_s": 30},
    {"id": "15.0", "if": [("densidad", "HIGH"), ("pend_densidad", "NO-DEC"), ("presion", "HIGH")],
     "then": ["DISMINUIR_TONELAJE"], "weight": 1.0, "priority": 73.0, "cooldown_s": 120},
    {"id": "16.0", "if": [("densidad", "LOW"), ("pend_densidad", "NO-INC")],
     "then": ["DISMINUIR_AGUA_CAJON"], "weight": 1.0, "priority": 72.0, "cooldown_s": 45},

    # Control presión
    {"id": "17.0", "if": [("presion", "HIGH"), ("pend_presion", "NO-DEC")],
     "then": ["DISMINUIR_RPM_BOMBA"], "weight": 1.0, "priority": 71.0, "cooldown_s": 30},
    {"id": "18.0", "if": [("presion", "LOW"), ("pend_presion", "NO-INC"), ("nivel", "NO-LOW")],
     "then": ["AUMENTAR_RPM_BOMBA"], "weight": 1.0, "priority": 70.0, "cooldown_s": 30},

    # Control nivel bajo
    {"id": "19.0", "if": [("nivel", "LOW"), ("pend_nivel", "NO-INC")],
     "then": ["DISMINUIR_RPM_BOMBA"], "weight": 1.0, "priority": 69.0, "cooldown_s": 30},
    {"id": "20.0", "if": [("nivel", "LOW"), ("pend_nivel", "NO-INC")],
     "then": ["AUMENTAR_AGUA_CAJON"], "weight": 1.0, "priority": 68.0, "cooldown_s": 30},
]


def _acciones_regla(regla: dict) -> list[str]:
    acciones = regla.get("then", [])
    if isinstance(acciones, str):
        return [acciones]
    return [str(a) for a in acciones]


ACTION_COOLDOWN: dict[str, int] = {}
for _regla in REGLAS:
    for _accion in _acciones_regla(_regla):
        duracion = _regla.get("cooldown_s", 0)
        if isinstance(duracion, dict):
            valor = max(int(v) for v in duracion.values()) if duracion else 0
        else:
            valor = int(duracion or 0)
        ACTION_COOLDOWN[_accion] = max(valor, int(ACTION_COOLDOWN.get(_accion, 0)))
