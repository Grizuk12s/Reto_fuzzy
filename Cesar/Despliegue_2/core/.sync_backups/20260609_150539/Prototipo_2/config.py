# -*- coding: utf-8 -*-
"""Contrato del sistema experto Espesador.

Define:
- Variables medidas (PV) que evaluan los fuzzys
- Variables crudas de sensores (inputs del calculo de variables derivadas)
- Variables externas (calculadas por variables_calculadas.py, NO pre-calculadas aguas arriba)
- Setpoints manipulados
- Mapeo de roles a columnas del DataFrame
- Cooldowns por familia de SP (NO se ponen en cada regla, ver decision D)
- Definicion de bloques jerarquicos
"""

from __future__ import annotations

# ============================================================
# VARIABLES DE PROCESO (PV) -- las que se fuzzifican y reciben pendiente
# ============================================================
VARIABLES_PROCESO = [
    "torque",               # Torque Espesador (%)
    "bed_mass",             # Bed Mass
    "bed_level",            # Bed Level (mts)
    "densidad",             # Densidad Descarga (%)
    "torque_bomba",         # Torque Bomba (%)
    "potencia_bomba",       # Potencia Bomba (kW)
    "presion_descarga",     # Presion Descarga
    "presion_diferencial",  # Presion Diferencial (impulsion - sello)
    "nivel_rastra",         # Nivel Rastra (%) -- usado como trigger en Estado 3
]

# ============================================================
# VARIABLES CRUDAS DE SENSORES
# ------------------------------------------------------------
# Son las variables brutas que el proceso o el SCADA entrega directamente.
# El modulo variables_calculadas.py las consume para producir las
# VARIABLES_EXTERNAS. El DataFrame de entrada debe incluir estas columnas.
#
# Detalle completo en variables_calculadas.VARIABLES_CRUDAS.
# ============================================================
VARIABLES_CRUDAS_REQUERIDAS = [
    "tonelaje_sag_1",   # Tonelaje SAG Mill 1 (t/h)
    "tonelaje_sag_2",   # Tonelaje SAG Mill 2 (t/h)
    "tonelaje_relave",  # Tonelaje de relave (t/h)
    "presion_bomba_1",  # Presion impulsion bomba descarga 1 (bar)
    "presion_bomba_2",  # Presion impulsion bomba descarga 2 (bar)
    "turbiedad_agua",   # Turbiedad agua recuperada (NTU) -- sensor directo
]

# ============================================================
# VARIABLES EXTERNAS -- calculadas por variables_calculadas.py
# ------------------------------------------------------------
# Ya NO se asumen pre-calculadas aguas arriba.
# El runner llama a calcular_variables_df() ANTES del pipeline
# principal, lo que agrega estas columnas al DataFrame.
# Los permisivos las leen directamente desde el row del DataFrame.
# ============================================================
VARIABLES_EXTERNAS = [
    "tonelaje_sag_delta_30min",    # delta de tonelaje SAG 1+2 en ultimos 30 min
    "tonelaje_sag_desv_est_30min", # desv. estandar de tonelaje SAG 1+2 en 30 min
    "turbiedad_agua",              # turbiedad del agua recuperada (sensor directo)
    "diferencial_ton_sag_relave",  # diferencial Ton (SAG total - relave)
    "diferencial_presion_bbas",    # diferencial de presion entre bombas (impulsion)
]

# ============================================================
# SETPOINTS MANIPULADOS
# ============================================================
SETPOINT_KEYS = ["sp_tonelaje", "sp_floculante", "sp_vel_bomba"]

# Campo temporal minimo esperado por los runners
TIME_KEY = "t_s"

# ============================================================
# COLUMNAS_ENTRADA: mapeo rol -> nombre de columna esperado en el DataFrame
# ============================================================
COLUMNAS_ENTRADA: dict = {
    TIME_KEY: TIME_KEY,
}

# PV canonicas
for _var in VARIABLES_PROCESO:
    COLUMNAS_ENTRADA[_var] = _var

# Limites de fuzzificacion por variable (lmin / lmax)
for _var in VARIABLES_PROCESO:
    COLUMNAS_ENTRADA[f"{_var}_lmin"] = f"{_var}_lmin"
    COLUMNAS_ENTRADA[f"{_var}_lmax"] = f"{_var}_lmax"

# Variables crudas de sensores
for _var in VARIABLES_CRUDAS_REQUERIDAS:
    COLUMNAS_ENTRADA[_var] = _var

# Variables externas calculadas (presentes en el DF despues de calcular_variables_df)
for _var in VARIABLES_EXTERNAS:
    COLUMNAS_ENTRADA[_var] = _var

# Setpoints actuales (estado del actuador)
for _sp in SETPOINT_KEYS:
    COLUMNAS_ENTRADA[_sp] = _sp


# Relacion estructural entre variable y columnas de limites
LIMITES_FUZZY_POR_VARIABLE = {
    var: {"lmin": f"{var}_lmin", "lmax": f"{var}_lmax"}
    for var in VARIABLES_PROCESO
}

ROLES_REQUERIDOS = [TIME_KEY, *VARIABLES_PROCESO, *SETPOINT_KEYS]
ROLES_LIMITES_REQUERIDOS = [
    limite for meta in LIMITES_FUZZY_POR_VARIABLE.values() for limite in meta.values()
]


# ============================================================
# COOLDOWNS POR FAMILIA DE SP (decision D)
# ============================================================
COOLDOWN_FAMILIA_S = {
    "sp_vel_bomba":  15 * 60,   # 15 min
    "sp_tonelaje":   45 * 60,   # 45 min
    "sp_floculante": 30 * 60,   # 30 min
}

SP_FAMILIA_A_KEY = {
    "sp_vel_bomba":  "sp_vel_bomba",
    "sp_tonelaje":   "sp_tonelaje",
    "sp_floculante": "sp_floculante",
}


# ============================================================
# BLOQUES (decision C)
# ============================================================
BLOQUES = {
    "critico": {
        "label": "Estados Criticos",
        "level": 1,
        "independent": False,
    },
    "estabilidad": {
        "label": "Estabilidad",
        "level": 2,
        "independent": False,
    },
    "optimizacion": {
        "label": "Optimizacion",
        "level": 99,
        "independent": True,
    },
}


# ============================================================
# Metadatos opcionales
# ============================================================
DESCRIPCION_ROLES = {
    "torque":              "Torque del espesador (%)",
    "bed_mass":            "Masa de la cama del espesador",
    "bed_level":           "Nivel de la cama (mts)",
    "densidad":            "Densidad de descarga (%)",
    "torque_bomba":        "Torque bomba descarga (%)",
    "potencia_bomba":      "Potencia bomba descarga (kW)",
    "presion_descarga":    "Presion de descarga",
    "presion_diferencial": "Presion diferencial (impulsion - sello)",
    "nivel_rastra":        "Nivel rastra (%)",
    "sp_tonelaje":         "Setpoint tonelaje",
    "sp_floculante":       "Setpoint flujo de floculante",
    "sp_vel_bomba":        "Setpoint % velocidad de bomba (descarga)",
    "tonelaje_sag_1":      "Tonelaje SAG Mill 1 (t/h)",
    "tonelaje_sag_2":      "Tonelaje SAG Mill 2 (t/h)",
    "tonelaje_relave":     "Tonelaje de relave (t/h)",
    "presion_bomba_1":     "Presion impulsion bomba descarga 1 (bar)",
    "presion_bomba_2":     "Presion impulsion bomba descarga 2 (bar)",
    "turbiedad_agua":      "Turbiedad del agua recuperada (NTU)",
    "tonelaje_sag_total":          "Tonelaje SAG total (SAG1+SAG2) (t/h)",
    "tonelaje_sag_delta_30min":    "Delta tonelaje SAG total en 30 min (t/h)",
    "tonelaje_sag_desv_est_30min": "Desv. estandar tonelaje SAG total en 30 min",
    "diferencial_ton_sag_relave":  "Diferencial tonelaje SAG total - relave (t/h)",
    "diferencial_presion_bbas":    "Diferencial presion impulsion entre bombas (bar)",
}
