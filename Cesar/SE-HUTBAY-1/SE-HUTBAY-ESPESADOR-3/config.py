# -*- coding: utf-8 -*-
"""Contrato del sistema experto Espesador.

Define:
- Variables medidas (PV) que evaluan los fuzzys
- Variables crudas de sensores (inputs del calculo de variables derivadas)
- Variables externas (calculadas por variables_calculadas.py, NO pre-calculadas aguas arriba)
- Setpoints manipulados
- Mapeo de roles a columnas del DataFrame
- Waits base por variable controlada (las reglas v3 asignan los waits por accion)
- Definicion de bloques jerarquicos
"""

from __future__ import annotations

import json as _json
import os.path as _os_path

# ============================================================
# VARIABLES DE PROCESO (PV) -- las que se fuzzifican y reciben pendiente
# ============================================================
# Contrato POR DEFECTO del espesador (plantilla estandar del producto).
# Es el punto de partida; el contrato vigente se lee de contrato.json y
# se ajusta por cliente desde la interfaz (seccion Contrato de Variables).
VARIABLES_PROCESO_DEFAULT = [
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

SETPOINT_KEYS_DEFAULT = ["sp_tonelaje", "sp_floculante", "sp_vel_bomba"]

# ------------------------------------------------------------
# Contrato vigente — se lee de config/espesador/contrato.json
# ------------------------------------------------------------
# Este proyecto es un estandar que se moldea a cada cliente: las
# variables que el SE controla cambian segun que instrumentacion
# exista en planta. Por eso la lista NO puede vivir en el codigo.
#
# Si el archivo no existe o esta corrupto, se usan los defaults de
# arriba: el sistema siempre arranca con la plantilla completa.
CONTRATO_JSON = _os_path.join(
    _os_path.dirname(_os_path.abspath(__file__)),
    "config", "espesador", "contrato.json",
)


def _cargar_contrato() -> tuple[list, list]:
    try:
        with open(CONTRATO_JSON, encoding="utf-8") as f:
            data = _json.load(f)
        pv = data.get("variables_proceso")
        sp = data.get("setpoints")
        if not isinstance(pv, list) or not pv:
            pv = list(VARIABLES_PROCESO_DEFAULT)
        if not isinstance(sp, list) or not sp:
            sp = list(SETPOINT_KEYS_DEFAULT)
        # Solo nombres validos y sin duplicar, conservando el orden.
        limpiar = lambda xs: list(dict.fromkeys(
            str(x).strip() for x in xs if isinstance(x, str) and str(x).strip()))
        return limpiar(pv), limpiar(sp)
    except (OSError, ValueError, AttributeError):
        return list(VARIABLES_PROCESO_DEFAULT), list(SETPOINT_KEYS_DEFAULT)


VARIABLES_PROCESO, _SETPOINTS_CONTRATO = _cargar_contrato()


def _cargar_limites_sp() -> dict:
    """Limites de ingenieria por setpoint, leidos de contrato.json.

    Antes vivian en `simulacion.LIMITES_SP`, hardcodeados con los 3 SP del
    espesador. Eso no es cosmetico: `apply_actions_tabla` solo clipea las
    familias presentes en este dict, asi que un SP de otro cliente quedaba
    SIN CLIPEO y el SE podia escribir cualquier valor al DCS.

    Vacio es un estado valido y significa "sin limites declarados"; el motor
    se niega a arrancar en ese caso en vez de escribir sin tope.
    """
    try:
        with open(CONTRATO_JSON, encoding="utf-8") as f:
            data = _json.load(f)
        crudo = data.get("limites_sp")
        if not isinstance(crudo, dict):
            return {}
    except (OSError, ValueError, AttributeError):
        return {}

    out = {}
    for sp, par in crudo.items():
        if not isinstance(par, (list, tuple)) or len(par) != 2:
            continue
        try:
            lo, hi = float(par[0]), float(par[1])
        except (TypeError, ValueError):
            continue
        if lo >= hi:
            continue
        out[str(sp)] = (lo, hi)
    return out


LIMITES_SP_CONTRATO = _cargar_limites_sp()

# ============================================================
# VARIABLES CRUDAS DE SENSORES
# ------------------------------------------------------------
# Son las variables brutas que el proceso o el SCADA entrega directamente.
# El modulo variables_calculadas.py las consume para producir las
# VARIABLES_EXTERNAS. El DataFrame de entrada debe incluir estas columnas.
#
# Detalle completo en variables_calculadas.VARIABLES_CRUDAS.
# ============================================================
VARIABLES_CRUDAS_DEFAULT = [
    "tonelaje_sag_1",   # Tonelaje SAG Mill 1 (t/h)
    "tonelaje_sag_2",   # Tonelaje SAG Mill 2 (t/h)
    "tonelaje_relave",  # Tonelaje de relave (t/h)
    "presion_bomba_1",  # Presion impulsion bomba descarga 1 (bar)
    "presion_bomba_2",  # Presion impulsion bomba descarga 2 (bar)
    "turbiedad_agua",   # Turbiedad agua recuperada (NTU) -- sensor directo
]

# Las crudas vigentes salen de variables.json, que ya es editable desde la
# interfaz (/api/variables). Antes esta lista estaba duplicada aqui y editar
# las crudas en la UI no cambiaba el contrato: eso quedaba desincronizado.
_VARIABLES_JSON = _os_path.join(
    _os_path.dirname(_os_path.abspath(__file__)),
    "config", "espesador", "variables.json",
)


def _cargar_crudas() -> list:
    """Lee las crudas de variables.json.

    OJO: una lista vacia es un estado VALIDO, no un error. Si el archivo
    existe y dice `"crudas": {}`, el contrato no tiene crudas y punto.
    Solo se cae a la plantilla si el archivo falta o esta corrupto; si no,
    blanquear la seccion no serviria de nada porque volveria sola.
    """
    try:
        with open(_VARIABLES_JSON, encoding="utf-8") as f:
            data = _json.load(f)
        crudas = data.get("crudas")
        if isinstance(crudas, dict):
            return list(crudas.keys())
    except (OSError, ValueError, AttributeError):
        pass
    return list(VARIABLES_CRUDAS_DEFAULT)


VARIABLES_CRUDAS_REQUERIDAS = _cargar_crudas()

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
SETPOINT_KEYS = _SETPOINTS_CONTRATO

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
# Recarga en caliente del contrato
# ============================================================
def recargar_contrato() -> dict:
    """Relee contrato.json y variables.json SIN reiniciar el servicio.

    El truco es que todo se muta **en el mismo objeto**. Medio proyecto hace
    `from config import VARIABLES_PROCESO`, asi que reasignar el nombre aqui
    no cambiaria nada para quien ya lo importo: cada modulo seguiria mirando
    la lista vieja. Mutando la lista/dict que ya tienen en la mano, el cambio
    llega a todos a la vez.

    Lo que NO hace: tocar al motor que este corriendo. Cambiar el contrato
    bajo un lazo de control en marcha significaria fuzzificar contra otra
    escala y escribir a otro setpoint a mitad de tick. El motor toma el
    contrato nuevo en su proximo `start()`, y la API avisa si estaba corriendo.

    Devuelve el contrato vigente tras la recarga.
    """
    pv, sp = _cargar_contrato()
    VARIABLES_PROCESO[:] = pv
    SETPOINT_KEYS[:] = sp

    LIMITES_SP_CONTRATO.clear()
    LIMITES_SP_CONTRATO.update(_cargar_limites_sp())

    VARIABLES_CRUDAS_REQUERIDAS[:] = _cargar_crudas()

    LIMITES_FUZZY_POR_VARIABLE.clear()
    LIMITES_FUZZY_POR_VARIABLE.update({
        var: {"lmin": f"{var}_lmin", "lmax": f"{var}_lmax"}
        for var in VARIABLES_PROCESO
    })

    COLUMNAS_ENTRADA.clear()
    COLUMNAS_ENTRADA[TIME_KEY] = TIME_KEY
    for _v in VARIABLES_PROCESO:
        COLUMNAS_ENTRADA[_v] = _v
        COLUMNAS_ENTRADA[f"{_v}_lmin"] = f"{_v}_lmin"
        COLUMNAS_ENTRADA[f"{_v}_lmax"] = f"{_v}_lmax"
    for _v in VARIABLES_CRUDAS_REQUERIDAS:
        COLUMNAS_ENTRADA[_v] = _v
    for _v in VARIABLES_EXTERNAS:
        COLUMNAS_ENTRADA[_v] = _v
    for _s in SETPOINT_KEYS:
        COLUMNAS_ENTRADA[_s] = _s

    ROLES_REQUERIDOS[:] = [TIME_KEY, *VARIABLES_PROCESO, *SETPOINT_KEYS]
    ROLES_LIMITES_REQUERIDOS[:] = [
        limite for meta in LIMITES_FUZZY_POR_VARIABLE.values() for limite in meta.values()
    ]

    return {"variables_proceso": list(VARIABLES_PROCESO),
            "setpoints": list(SETPOINT_KEYS),
            "crudas": list(VARIABLES_CRUDAS_REQUERIDAS),
            "limites_sp": {k: list(v) for k, v in LIMITES_SP_CONTRATO.items()}}


# ============================================================
# WAITS BASE POR VARIABLE CONTROLADA
# ------------------------------------------------------------
# Se conservan estos valores como referencia base para construir los waits
# declarativos de v3. Las reglas ya no dependen de un cooldown implicito
# global; ahora seleccionan uno o mas waits por accion y con la duracion que
# necesiten.
# ============================================================
COOLDOWN_FAMILIA_S = {
    "sp_vel_bomba":  15 * 60,   # 15 min
    "sp_tonelaje":   45 * 60,   # 45 min
    "sp_floculante": 30 * 60,   # 30 min
}

WAIT_BASE_S = dict(COOLDOWN_FAMILIA_S)

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
