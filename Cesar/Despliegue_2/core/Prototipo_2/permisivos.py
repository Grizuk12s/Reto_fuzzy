# -*- coding: utf-8 -*-
"""Permisivos operacionales del Espesador.

Extiende el engine v4 para soportar operadores logicos OR / AND / NOT.
Mantiene retrocompatibilidad: una lista plana de condiciones sigue
siendo un AND.

Sintaxis de cada permisivo:

    "NOMBRE_PERMISIVO": [
        # AND implicito al top-level. Cada item puede ser:
        condicion_dict,                          # plain (var/op/value o fuzzy_var/label/min_mu)
        {"OR":  [condicion, ...]},               # OR de subcondiciones
        {"AND": [condicion, ...]},               # AND de subcondiciones
        {"NOT": condicion},                      # negacion
    ]

Tipos de condicion primitiva:
    {"var": "tonelaje_sag_delta_30min", "op": ">", "value": 300}
    {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5}

El nombre del permisivo se expone en fuzzy_out como pseudo-variable:
    "__PERM_<NOMBRE>" con etiquetas ON / OFF.
"""

from __future__ import annotations

import operator
from typing import Any


# ============================================================
# OPERADORES NUMERICOS
# ============================================================
_OPERADORES = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "=": operator.eq,
    "!=": operator.ne,
}


def nombre_variable_permisivo(nombre: str) -> str:
    limpio = str(nombre).strip().upper()
    if limpio.startswith("__PERM_"):
        return limpio
    return f"__PERM_{limpio}"


# ============================================================
# Resolucion de valores numericos (setpoints / inputs / row)
# ============================================================
def _mu_fuzzy(fuzzy_out: dict, var: str, label: str) -> float:
    info_var = fuzzy_out.get(str(var), {}) or {}
    pert = info_var.get("pert", {}) or {}
    return float(pert.get(str(label).upper(), 0.0))


def _resolver_valor(
    nombre: str,
    *,
    row: Any | None = None,
    inputs: dict | None = None,
    setpoints: dict | None = None,
    columnas_entrada: dict | None = None,
) -> float:
    nombre = str(nombre)

    if setpoints is not None and nombre in setpoints:
        return float(setpoints[nombre])

    if inputs is not None and nombre in inputs:
        return float(inputs[nombre])

    if row is not None:
        if nombre in row:
            return float(row[nombre])
        if columnas_entrada is not None:
            col = columnas_entrada.get(nombre, nombre)
            if col in row:
                return float(row[col])

    raise KeyError(
        f"No se pudo resolver la variable '{nombre}' para evaluar permisivos. "
        "Revisar setpoints, inputs, row o COLUMNAS_ENTRADA."
    )


# ============================================================
# Evaluacion recursiva de una condicion (puede ser compuesta)
# ============================================================
def _evaluar_condicion(
    condicion: Any,
    *,
    fuzzy_out: dict,
    row: Any | None = None,
    inputs: dict | None = None,
    setpoints: dict | None = None,
    columnas_entrada: dict | None = None,
    min_mu_default: float = 0.50,
) -> bool:
    """Evalua una condicion (primitiva o compuesta) a True/False.

    Soporta operadores logicos OR / AND / NOT y dos primitivas:
      - numerical : {"var": ..., "op": ..., "value": ...}
      - fuzzy     : {"fuzzy_var": ..., "label": ..., "min_mu": ...}
    """
    # Listas planas = AND implicito
    if isinstance(condicion, list):
        return all(
            _evaluar_condicion(
                c,
                fuzzy_out=fuzzy_out, row=row, inputs=inputs, setpoints=setpoints,
                columnas_entrada=columnas_entrada, min_mu_default=min_mu_default,
            )
            for c in condicion
        )

    if isinstance(condicion, dict):
        # Operadores logicos
        if "OR" in condicion:
            return any(
                _evaluar_condicion(
                    c,
                    fuzzy_out=fuzzy_out, row=row, inputs=inputs, setpoints=setpoints,
                    columnas_entrada=columnas_entrada, min_mu_default=min_mu_default,
                )
                for c in condicion["OR"]
            )
        if "AND" in condicion:
            return all(
                _evaluar_condicion(
                    c,
                    fuzzy_out=fuzzy_out, row=row, inputs=inputs, setpoints=setpoints,
                    columnas_entrada=columnas_entrada, min_mu_default=min_mu_default,
                )
                for c in condicion["AND"]
            )
        if "NOT" in condicion:
            return not _evaluar_condicion(
                condicion["NOT"],
                fuzzy_out=fuzzy_out, row=row, inputs=inputs, setpoints=setpoints,
                columnas_entrada=columnas_entrada, min_mu_default=min_mu_default,
            )

        # Primitivas
        if "fuzzy_var" in condicion:
            var = str(condicion["fuzzy_var"])
            label = str(condicion["label"]).upper()
            min_mu = float(condicion.get("min_mu", min_mu_default))
            return _mu_fuzzy(fuzzy_out, var, label) >= min_mu

        if "var" in condicion:
            var = str(condicion["var"])
            op = str(condicion.get("op", "==")).strip()
            if op not in _OPERADORES:
                raise ValueError(
                    f"Operador no soportado: {op!r}. Validos: {sorted(_OPERADORES)}"
                )
            try:
                valor_actual = _resolver_valor(
                    var,
                    row=row, inputs=inputs, setpoints=setpoints,
                    columnas_entrada=columnas_entrada,
                )
            except KeyError:
                # Variable no presente en el df -> condicion no satisfecha.
                # Esto permite que el permisivo siga funcionando sin variables
                # externas que aun no estan integradas.
                return False
            valor_ref = float(condicion["value"])
            return bool(_OPERADORES[op](valor_actual, valor_ref))

    raise ValueError(
        f"Condicion invalida: {condicion!r}. "
        "Usa primitivas (var/op/value, fuzzy_var/label/min_mu) o operadores OR/AND/NOT."
    )


def evaluar_permisivos(
    permisivos_config: dict[str, Any] | None,
    *,
    fuzzy_out: dict,
    row: Any | None = None,
    inputs: dict | None = None,
    setpoints: dict | None = None,
    columnas_entrada: dict | None = None,
    min_mu_default: float = 0.50,
) -> dict[str, bool]:
    """Evalua todos los permisivos. Retorna {NOMBRE: bool} (ON=True / OFF=False)."""
    if not permisivos_config:
        return {}

    estados: dict[str, bool] = {}
    for nombre, condiciones in permisivos_config.items():
        if not condiciones:
            estados[str(nombre).strip().upper()] = False
            continue
        # Top-level AND
        activo = _evaluar_condicion(
            condiciones,
            fuzzy_out=fuzzy_out, row=row, inputs=inputs, setpoints=setpoints,
            columnas_entrada=columnas_entrada, min_mu_default=min_mu_default,
        )
        estados[str(nombre).strip().upper()] = bool(activo)

    return estados


def inyectar_permisivos_en_fuzzy_out(fuzzy_out: dict, estados_permisivos: dict[str, bool]) -> dict:
    """Inyecta cada permisivo como pseudo-variable __PERM_<NOMBRE> con ON/OFF."""
    for nombre, activo in (estados_permisivos or {}).items():
        var_perm = nombre_variable_permisivo(nombre)
        fuzzy_out[var_perm] = {
            "pert": {
                "ON": 1.0 if activo else 0.0,
                "OFF": 0.0 if activo else 1.0,
            }
        }
    return fuzzy_out


# ============================================================
# PERMISIVOS DEL ESPESADOR (hoja "Permisivos" del Excel)
# ============================================================
# Cada permisivo es una lista AND implicita.
# Donde se usa "NOT(AND(...))" se modela el caso: "el permisivo es ON
# mientras NO se cumpla la condicion de alerta (compuesta)".
#
# NOTA: las variables externas (tonelaje_sag_*, nivel_rastra, turbiedad_agua,
# diferencial_*) son placeholders. Si esas columnas no estan en el df, la
# condicion individual queda en False (no satisfecha) y se ignora con
# seguridad (decision F1).
# ============================================================

PERMISIVOS: dict[str, list] = {

    # ----------------------------------------------------------
    # PERMITIR_FRENAR_DESCARGA
    # ON cuando NO hay alertas. Cualquiera de las alertas listadas
    # apaga el permisivo (= no se debe frenar la descarga).
    # ----------------------------------------------------------
    "PERMITIR_FRENAR_DESCARGA": [
        # alerta 1: tonelaje SAG creciendo fuerte en 30 min
        {"NOT": {"var": "tonelaje_sag_delta_30min", "op": ">", "value": 300.0}},

        # alerta 2: variabilidad de tonelaje SAG alta en 30 min
        {"NOT": {"var": "tonelaje_sag_desv_est_30min", "op": ">", "value": 50.0}},  # umbral placeholder

        # alerta 3: torque OK pero subiendo
        {"NOT": {"AND": [
            {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5},
            {"fuzzy_var": "pend_torque", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 4: torque LP OK y subiendo
        # (mismo concepto pero filtrado de largo plazo -- no disponible aun)
        # Por ahora, equivalente al short plazo.
        {"NOT": {"AND": [
            {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5},
            {"fuzzy_var": "pend_torque", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 5: Bed Level OK (cerca bajo) y bajando
        {"NOT": {"AND": [
            {"fuzzy_var": "bed_level", "label": "CERCA_BAJO", "min_mu": 0.4},
            {"fuzzy_var": "pend_bed_level", "label": "DEC", "min_mu": 0.5},
        ]}},

        # alerta 6: Bed Mass OK (cerca alto) y subiendo
        {"NOT": {"AND": [
            {"fuzzy_var": "bed_mass", "label": "CERCA_ALTO", "min_mu": 0.4},
            {"fuzzy_var": "pend_bed_mass", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 7: Presion descarga OK (cerca alto) y subiendo
        {"NOT": {"AND": [
            {"fuzzy_var": "presion_descarga", "label": "CERCA_ALTO", "min_mu": 0.4},
            {"fuzzy_var": "pend_presion_descarga", "label": "INC", "min_mu": 0.5},
        ]}},

        # alerta 8: Nivel Rastra > 15% (variable externa)
        {"NOT": {"var": "nivel_rastra", "op": ">", "value": 15.0}},

        # alerta 9: Turbiedad agua OK y subiendo (variable externa con fuzzy si existe;
        #          aqui se modela como umbral simple sobre el numerico)
        {"NOT": {"var": "turbiedad_agua", "op": ">", "value": 50.0}},  # umbral placeholder

        # alerta 10: Diferencial Ton (SAG - relave) aumentando y Ton SAG > 3500
        {"NOT": {"AND": [
            {"var": "diferencial_ton_sag_relave", "op": ">", "value": 0.0},   # aumentando = positivo
            {"var": "tonelaje_sag_delta_30min",  "op": ">", "value": 3500.0},
        ]}},
    ],

    # ----------------------------------------------------------
    # PERMITIR_SOLTAR_DESCARGA
    # ON cuando NO hay las alertas listadas (= se permite aumentar
    # descarga).
    # ----------------------------------------------------------
    "PERMITIR_SOLTAR_DESCARGA": [
        # alerta 1: Diferencial de Presion entre bombas aumentando
        {"NOT": {"var": "diferencial_presion_bbas", "op": ">", "value": 0.0}},  # aumentando = positivo

        # alerta 2: Presion diferencial (impulsion entre bbas) alta
        # Como esta variable representa "entre bombas", se usa la externa.
        {"NOT": {"var": "diferencial_presion_bbas", "op": ">", "value": 5.0}},  # umbral placeholder

        # alerta 3: Presion diferencial (impulsion - sello) baja
        {"NOT": {"fuzzy_var": "presion_diferencial", "label": "LOW", "min_mu": 0.5}},

        # alerta 4: Torque bombas alto
        {"NOT": {"fuzzy_var": "torque_bomba", "label": "HIGH", "min_mu": 0.5}},
    ],

    # ----------------------------------------------------------
    # OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD (Aum Objetivo)
    # ON cuando se cumplen TODAS las condiciones para subir el objetivo
    # de densidad (operacion favorable, holgura).
    # ----------------------------------------------------------
    "OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD": [
        # tonelaje SAG bajo y poco variable
        {"OR": [
            {"var": "tonelaje_sag_delta_30min", "op": "<", "value": -200.0},
            {"var": "tonelaje_sag_desv_est_30min", "op": "<", "value": 20.0},  # placeholder
        ]},
        # torque OK y estable LP
        {"AND": [
            {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.6},
            {"fuzzy_var": "pend_torque", "label": "STABLE", "min_mu": 0.5},
        ]},
        # Bed Level OK y estable LP
        {"AND": [
            {"fuzzy_var": "bed_level", "label": "OK", "min_mu": 0.6},
            {"fuzzy_var": "pend_bed_level", "label": "STABLE", "min_mu": 0.5},
        ]},
        # Presion Cama OK y No Aumentando (presion_descarga proxy)
        # En el Excel literal dice "Presion Cama"; mientras no haya un
        # tag dedicado, se proxy-modela con presion_descarga.
        {"AND": [
            {"fuzzy_var": "presion_descarga", "label": "OK", "min_mu": 0.5},
            {"NOT": {"fuzzy_var": "pend_presion_descarga", "label": "INC", "min_mu": 0.5}},
        ]},
        # Presion descarga < 400 (variable externa o limite del fuzzy --
        # aqui se permite que el usuario lo deje como fuzzy por simplicidad)
        {"NOT": {"fuzzy_var": "presion_descarga", "label": "HIGH", "min_mu": 0.5}},
    ],

    # ----------------------------------------------------------
    # OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD (Dism Objetivo)
    # ON cuando se cumplen las condiciones para bajar el objetivo
    # (operacion menos favorable, alta variabilidad).
    # ----------------------------------------------------------
    "OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD": [
        # tonelaje SAG alto o muy variable
        {"OR": [
            {"var": "tonelaje_sag_delta_30min", "op": ">", "value": 200.0},
            {"var": "tonelaje_sag_desv_est_30min", "op": ">", "value": 30.0},
        ]},
        # torque OK y aumentando LP
        {"AND": [
            {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5},
            {"fuzzy_var": "pend_torque", "label": "INC", "min_mu": 0.5},
        ]},
        # Bed Level OK y disminuyendo
        {"AND": [
            {"fuzzy_var": "bed_level", "label": "OK", "min_mu": 0.5},
            {"fuzzy_var": "pend_bed_level", "label": "DEC", "min_mu": 0.5},
        ]},
        # Presion descarga aumentando
        {"fuzzy_var": "pend_presion_descarga", "label": "INC", "min_mu": 0.5},
    ],
}
