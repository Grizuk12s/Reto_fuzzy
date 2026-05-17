# -*- coding: utf-8 -*-
"""Contrato mínimo del núcleo para despliegue en planta.

Este módulo NO contiene valores operacionales de prueba como setpoints
base, límites numéricos fijos, frecuencias de simulación o rutas de salida.
Solo define el contrato estructural que debe cumplir la data que entra al
sistema experto.

La idea es simple:
- el core conoce *qué roles* necesita;
- cada integración o prueba decide *en qué columnas reales* vienen esos roles.

Además, como los fuzzy requieren límites, esos límites también se consideran
parte de la data de entrada esperada por el experto.
"""

from __future__ import annotations

# Roles canónicos de proceso evaluados por el sistema experto.
VARIABLES_PROCESO = ["potencia", "nivel", "presion", "p80", "densidad"]

# Variables derivadas que pueden aparecer en reglas sobre tendencia.
VARIABLES_PENDIENTE = [f"pend_{var}" for var in VARIABLES_PROCESO]

# Flags meta que el motor puede expandir como variables booleanas de regla.
META_FLAGS_DISPONIBLES = ["__R15"]

# Variables válidas para editores de reglas y capas UI.
VARIABLES_REGLAS_DISPONIBLES = [*VARIABLES_PROCESO, *VARIABLES_PENDIENTE, *META_FLAGS_DISPONIBLES]

# Etiquetas válidas para reglas.
ETIQUETAS_ESTADO = ["LOW", "OK", "HIGH"]
ETIQUETAS_ESTADO_NEGADAS = ["NO-LOW", "NO-HIGH"]
ETIQUETAS_PENDIENTE = ["DEC", "STABLE", "INC"]
ETIQUETAS_PENDIENTE_NEGADAS = ["NO-DEC", "NO-INC"]
ETIQUETAS_ESPECIALES = ["CERCA_BAJO", "ON", "OFF"]
ETIQUETAS_REGLAS_DISPONIBLES = [
    *ETIQUETAS_ESTADO,
    *ETIQUETAS_ESTADO_NEGADAS,
    *ETIQUETAS_PENDIENTE,
    *ETIQUETAS_PENDIENTE_NEGADAS,
    *ETIQUETAS_ESPECIALES,
]

# Setpoints manipulados por las acciones del experto.
SETPOINT_KEYS = ["sp_ton", "sp_am", "sp_ac", "sp_rpm"]

# Campo temporal mínimo esperado por los runners.
TIME_KEY = "t_s"

# Mapeo rol -> nombre de columna esperado por defecto.
# En planta este diccionario puede sobreescribirse por la capa integradora.
COLUMNAS_ENTRADA = {
    # tiempo
    TIME_KEY: TIME_KEY,

    # variables de proceso
    "potencia": "potencia",
    "nivel": "nivel",
    "presion": "presion",
    "p80": "p80",
    "densidad": "densidad",

    # setpoints actuales
    "sp_ton": "sp_ton",
    "sp_am": "sp_am",
    "sp_ac": "sp_ac",
    "sp_rpm": "sp_rpm",

    # límites requeridos por los fuzzys; también entran como data de proceso
    "potencia_lmin": "potencia_lmin",
    "potencia_lmax": "potencia_lmax",
    "nivel_lmin": "nivel_lmin",
    "nivel_lmax": "nivel_lmax",
    "presion_lmin": "presion_lmin",
    "presion_lmax": "presion_lmax",
    "p80_lmin": "p80_lmin",
    "p80_lmax": "p80_lmax",
    "densidad_lmin": "densidad_lmin",
    "densidad_lmax": "densidad_lmax",
}

# Relación estructural entre cada variable y sus columnas de límites fuzzy.
LIMITES_FUZZY_POR_VARIABLE = {
    "potencia": {"lmin": "potencia_lmin", "lmax": "potencia_lmax"},
    "nivel": {"lmin": "nivel_lmin", "lmax": "nivel_lmax"},
    "presion": {"lmin": "presion_lmin", "lmax": "presion_lmax"},
    "p80": {"lmin": "p80_lmin", "lmax": "p80_lmax"},
    "densidad": {"lmin": "densidad_lmin", "lmax": "densidad_lmax"},
}

# Roles obligatorios para una corrida completa del experto.
ROLES_REQUERIDOS = [TIME_KEY, *VARIABLES_PROCESO, *SETPOINT_KEYS]

# Columnas de límites obligatorias para poder fuzzificar correctamente.
ROLES_LIMITES_REQUERIDOS = [
    limite_role
    for meta in LIMITES_FUZZY_POR_VARIABLE.values()
    for limite_role in meta.values()
]

# Relación entre familia lógica del motor y clave de setpoint en la data.
SP_FAMILIA_A_KEY = {
    "sp_tonelaje": "sp_ton",
    "sp_agua_molino": "sp_am",
    "sp_agua_cajon": "sp_ac",
    "sp_agua_ciclones": "sp_ac",
    "sp_rpm_bomba": "sp_rpm",
}

# Metadatos opcionales útiles para capas superiores.
DESCRIPCION_ROLES = {
    "potencia": "Variable de proceso: potencia del molino",
    "nivel": "Variable de proceso: nivel de cajón/sump",
    "presion": "Variable de proceso: presión",
    "p80": "Variable de proceso: granulometría P80",
    "densidad": "Variable de proceso: densidad",
    "sp_ton": "Setpoint de tonelaje",
    "sp_am": "Setpoint de agua molino",
    "sp_ac": "Setpoint de agua cajón/ciclones",
    "sp_rpm": "Setpoint de RPM de bomba",
}
