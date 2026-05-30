# -*- coding: utf-8 -*-
"""Reglas del sistema experto Espesador.

Fuente: hoja "Estrategia" del Excel.

Organizacion por bloque (decision C):
  - "critico"      : Estados 1-5 -- alertas que requieren accion inmediata
  - "estabilidad"  : Estados 6-11 -- control fino dentro de rangos OK
  - "optimizacion" : Estado 12 -- arquitectura lista pero sin reglas (decision E)

Jerarquia: si un bloque critico dispara, estabilidad queda bloqueado en este
tick. Optimizacion es INDEPENDIENTE y siempre evalua (segun config.BLOQUES).

Sintaxis de conectores (decision C):
  - En estas reglas se usa conector explicito en todos los `if`.
  - Cada item puede ser una tupla (var, label) o un dict {"OR": [...]} /
    {"AND": [...]} / {"NOT": ...}
  - los OR/AND/NOT son recursivos

Cooldowns (decision D):
  - NO se definen en las reglas
  - se aplican por FAMILIA de SP segun config.COOLDOWN_FAMILIA_S
"""

from __future__ import annotations


# ============================================================
# BLOQUE CRITICO -- Estados 1 a 5
# ============================================================

REGLAS_CRITICO: list[dict] = [

    # ----------------------------------------------------------
    # Estado 1: Presion Diferencial Baja (Bba - Sello)
    # 4 sub-estados por (bed_level, torque)
    # ----------------------------------------------------------
    {
        "id": "E1.S1", "bloque": "critico", "priority": 100.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_level", "LOW"),
            ("torque",    "HIGH"),
            ("presion_diferencial",      "LOW"),
            ("pend_presion_diferencial", "NO-INC"),
            ("torque_bomba",             "HIGH"),
            ("pend_torque_bomba",        "NO-DEC"),
            ("potencia_bomba",           "HIGH"),
            ("pend_potencia_bomba",      "NO-DEC"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA_FUERTE", "DISMINUIR_TONELAJE_FUERTE"],
    },
    {
        "id": "E1.S2", "bloque": "critico", "priority": 100.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_level", "LOW"),
            ("torque",    "NO-HIGH"),
            ("presion_diferencial",      "LOW"),
            ("pend_presion_diferencial", "NO-INC"),
            ("torque_bomba",             "HIGH"),
            ("pend_torque_bomba",        "NO-DEC"),
            ("potencia_bomba",           "HIGH"),
            ("pend_potencia_bomba",      "NO-DEC"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA_FUERTE", "DISMINUIR_TONELAJE"],
    },
    {
        "id": "E1.S3", "bloque": "critico", "priority": 100.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_level", "NO-LOW"),
            ("torque",    "HIGH"),
            ("presion_diferencial",      "LOW"),
            ("pend_presion_diferencial", "NO-INC"),
            ("torque_bomba",             "HIGH"),
            ("pend_torque_bomba",        "NO-DEC"),
            ("potencia_bomba",           "HIGH"),
            ("pend_potencia_bomba",      "NO-DEC"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA", "DISMINUIR_TONELAJE_FUERTE"],
    },
    {
        "id": "E1.S4", "bloque": "critico", "priority": 100.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_level", "NO-LOW"),
            ("torque",    "NO-HIGH"),
            ("presion_diferencial",      "LOW"),
            ("pend_presion_diferencial", "NO-INC"),
            ("torque_bomba",             "HIGH"),
            ("pend_torque_bomba",        "NO-DEC"),
            ("potencia_bomba",           "HIGH"),
            ("pend_potencia_bomba",      "NO-DEC"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA", "DISMINUIR_TONELAJE"],
    },

    # ----------------------------------------------------------
    # Estado 2: Presion Descarga Alta
    # ----------------------------------------------------------
    {
        "id": "E2.S1", "bloque": "critico", "priority": 99.0, "weight": 1.0,
        "if": [{"AND": [
            ("presion_descarga",      "HIGH"),
            ("pend_presion_descarga", "NO-DEC"),
            ("bed_level",             "LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA", "DISMINUIR_TONELAJE"],
    },
    {
        "id": "E2.S2", "bloque": "critico", "priority": 99.0, "weight": 1.0,
        "if": [{"AND": [
            ("presion_descarga",      "HIGH"),
            ("pend_presion_descarga", "NO-DEC"),
            ("bed_level",             "NO-LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA"],
    },

    # ----------------------------------------------------------
    # Estado 3: Nivel Rastra Alta
    # ----------------------------------------------------------
    {
        "id": "E3.S1", "bloque": "critico", "priority": 98.0, "weight": 1.0,
        "if": [{"AND": [
            ("nivel_rastra",      "HIGH"),
            ("pend_nivel_rastra", "NO-DEC"),
            ("bed_level",         "LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA", "DISMINUIR_TONELAJE"],
    },
    {
        "id": "E3.S2", "bloque": "critico", "priority": 98.0, "weight": 1.0,
        "if": [{"AND": [
            ("nivel_rastra",      "HIGH"),
            ("pend_nivel_rastra", "NO-DEC"),
            ("bed_level",         "NO-LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA"],
    },

    # ----------------------------------------------------------
    # Estado 4: Torque Alto
    # ----------------------------------------------------------
    {
        "id": "E4.S1", "bloque": "critico", "priority": 97.0, "weight": 1.0,
        "if": [{"AND": [
            ("torque",      "HIGH"),
            ("pend_torque", "NO-DEC"),
            ("bed_level",   "LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA"],
    },
    {
        "id": "E4.S2", "bloque": "critico", "priority": 97.0, "weight": 1.0,
        "if": [{"AND": [
            ("torque",      "HIGH"),
            ("pend_torque", "NO-DEC"),
            ("bed_level",   "NO-LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA", "DISMINUIR_FLOCULANTE"],
    },

    # ----------------------------------------------------------
    # Estado 5: Bed Mass Alto
    # ----------------------------------------------------------
    {
        "id": "E5.S1", "bloque": "critico", "priority": 96.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_mass",      "HIGH"),
            ("pend_bed_mass", "NO-DEC"),
            ("bed_level",     "LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA", "AUMENTAR_FLOCULANTE_SUAVE"],
    },
    {
        "id": "E5.S2", "bloque": "critico", "priority": 96.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_mass",      "HIGH"),
            ("pend_bed_mass", "NO-DEC"),
            ("bed_level",     "NO-LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA"],
    },
]


# ============================================================
# BLOQUE ESTABILIDAD -- Estados 6 a 11
# ============================================================

REGLAS_ESTABILIDAD: list[dict] = [

    # ----------------------------------------------------------
    # Estado 6: Control Presion Diferencial (cerca bajo + DEC)
    # ----------------------------------------------------------
    {
        "id": "E6.S1", "bloque": "estabilidad", "priority": 89.0, "weight": 1.0,
        "if": [{"AND": [
            ("presion_diferencial",      "CERCA_BAJO"),
            ("pend_presion_diferencial", "DEC"),
            ("bed_level",                "LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA", "AUMENTAR_FLOCULANTE"],
    },
    {
        "id": "E6.S2", "bloque": "estabilidad", "priority": 89.0, "weight": 1.0,
        "if": [{"AND": [
            ("presion_diferencial",      "CERCA_BAJO"),
            ("pend_presion_diferencial", "DEC"),
            ("bed_level",                "NO-LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA"],
    },

    # ----------------------------------------------------------
    # Estado 7: Control Presion Descarga (cerca alto + INC)
    # ----------------------------------------------------------
    {
        "id": "E7.S1", "bloque": "estabilidad", "priority": 88.0, "weight": 1.0,
        "if": [{"AND": [
            ("presion_descarga",      "CERCA_ALTO"),
            ("pend_presion_descarga", "INC"),
            ("bed_level",             "LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA", "AUMENTAR_FLOCULANTE"],
    },
    {
        "id": "E7.S2", "bloque": "estabilidad", "priority": 88.0, "weight": 1.0,
        "if": [{"AND": [
            ("presion_descarga",      "CERCA_ALTO"),
            ("pend_presion_descarga", "INC"),
            ("bed_level",             "NO-LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA"],
    },

    # ----------------------------------------------------------
    # Estado 8: Control Torque (cerca alto + INC)
    # ----------------------------------------------------------
    {
        "id": "E8.S1", "bloque": "estabilidad", "priority": 87.0, "weight": 1.0,
        "if": [{"AND": [
            ("torque",      "CERCA_ALTO"),
            ("pend_torque", "INC"),
            ("bed_level",   "LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA"],
    },
    {
        "id": "E8.S2", "bloque": "estabilidad", "priority": 87.0, "weight": 1.0,
        "if": [{"AND": [
            ("torque",      "CERCA_ALTO"),
            ("pend_torque", "INC"),
            ("bed_level",   "NO-LOW"),
        ]}],
        "then": ["DISMINUIR_FLOCULANTE", "AUMENTAR_VEL_BOMBA"],
    },

    # ----------------------------------------------------------
    # Estado 9: Control Bed Mass (cerca alto + INC)
    # ----------------------------------------------------------
    {
        "id": "E9.S1", "bloque": "estabilidad", "priority": 86.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_mass",      "CERCA_ALTO"),
            ("pend_bed_mass", "INC"),
            ("bed_level",     "LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA", "AUMENTAR_FLOCULANTE_SUAVE"],
    },
    {
        "id": "E9.S2", "bloque": "estabilidad", "priority": 86.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_mass",      "CERCA_ALTO"),
            ("pend_bed_mass", "INC"),
            ("bed_level",     "NO-LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA"],
    },

    # ----------------------------------------------------------
    # Estado 10: Control Densidad (con permisivos)
    # ----------------------------------------------------------
    # 10.A: densidad HIGH + permisivo "soltar descarga" OFF (= no podemos
    #       seguir soltando porque algo lo bloquea aguas arriba/abajo)
    {
        "id": "E10.A.S1", "bloque": "estabilidad", "priority": 85.0, "weight": 1.0,
        "if": [{"AND": [
            ("densidad",                       "HIGH"),
            ("__PERM_PERMITIR_SOLTAR_DESCARGA", "OFF"),
            ("bed_level",                       "LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA", "DISMINUIR_FLOCULANTE_SUAVE"],
    },
    {
        "id": "E10.A.S2", "bloque": "estabilidad", "priority": 85.0, "weight": 1.0,
        "if": [{"AND": [
            ("densidad",                       "HIGH"),
            ("__PERM_PERMITIR_SOLTAR_DESCARGA", "OFF"),
            ("bed_level",                       "NO-LOW"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA"],
    },
    # 10.B: densidad LOW + permisivo "frenar descarga" OFF
    {
        "id": "E10.B.S1", "bloque": "estabilidad", "priority": 85.0, "weight": 1.0,
        "if": [{"AND": [
            ("densidad",                       "LOW"),
            ("__PERM_PERMITIR_FRENAR_DESCARGA", "OFF"),
            ("bed_level",                       "LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA", "AUMENTAR_FLOCULANTE_SUAVE"],
    },
    {
        "id": "E10.B.S2", "bloque": "estabilidad", "priority": 85.0, "weight": 1.0,
        "if": [{"AND": [
            ("densidad",                       "LOW"),
            ("__PERM_PERMITIR_FRENAR_DESCARGA", "OFF"),
            ("bed_level",                       "NO-LOW"),
        ]}],
        "then": ["DISMINUIR_VEL_BOMBA"],
    },

    # ----------------------------------------------------------
    # Estado 11: Control Bed Level (bajo + permisivo soltar OFF)
    # Sub-estados por estado de densidad.
    # ----------------------------------------------------------
    {
        "id": "E11.S3", "bloque": "estabilidad", "priority": 84.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_level",                       "LOW"),
            ("__PERM_PERMITIR_SOLTAR_DESCARGA", "OFF"),
            ("densidad",                        "OK"),
            ("pend_densidad",                   "DEC"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA", "AUMENTAR_FLOCULANTE_SUAVE"],
    },
    {
        "id": "E11.S4", "bloque": "estabilidad", "priority": 84.0, "weight": 1.0,
        "if": [{"AND": [
            ("bed_level",                       "LOW"),
            ("__PERM_PERMITIR_SOLTAR_DESCARGA", "OFF"),
            ("densidad",                        "OK"),
            ("pend_densidad",                   "INC"),
        ]}],
        "then": ["AUMENTAR_VEL_BOMBA"],
    },
]


# ============================================================
# BLOQUE OPTIMIZACION -- Estado 12 (decision E)
# Arquitectura lista, sin reglas implementadas. Se llenara cuando se
# tengan los defuzzy/acciones SUBIR_OBJETIVO_DENSIDAD / BAJAR_OBJETIVO_DENSIDAD.
# Las reglas DEBEN llevar bloque="optimizacion" y van a usar los
# permisivos OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD / OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD.
# ============================================================

REGLAS_OPTIMIZACION: list[dict] = [
    # TODO Estado 12.A: subir objetivo densidad cuando OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD == ON
    # {
    #     "id": "E12.A", "bloque": "optimizacion", "priority": 50.0, "weight": 1.0,
    #     "if": [{"AND": [
    #         ("__PERM_OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD", "ON"),
    #     ]}],
    #     "then": ["SUBIR_OBJETIVO_DENSIDAD"],
    # },
    # TODO Estado 12.B: bajar objetivo densidad cuando OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD == ON
    # {
    #     "id": "E12.B", "bloque": "optimizacion", "priority": 50.0, "weight": 1.0,
    #     "if": [{"AND": [
    #         ("__PERM_OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD", "ON"),
    #     ]}],
    #     "then": ["BAJAR_OBJETIVO_DENSIDAD"],
    # },
]


# ============================================================
# LISTA UNIFICADA -- la que consume el motor
# ============================================================
REGLAS_ESPESADOR: list[dict] = (
    REGLAS_CRITICO
    + REGLAS_ESTABILIDAD
    + REGLAS_OPTIMIZACION
)
