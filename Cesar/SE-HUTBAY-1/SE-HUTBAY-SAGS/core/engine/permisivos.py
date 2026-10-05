# -*- coding: utf-8 -*-
"""Motor de evaluación de permisivos operacionales — lógica genérica pura.

Este módulo no contiene definiciones de permisivos de ningún proceso.
El dict de permisivos se pasa siempre como parámetro.

Sintaxis de cada permisivo (AND implícito al top-level):

    "NOMBRE": [
        condicion_dict,                      # plain (var/op/value o fuzzy_var/label/min_mu)
        {"OR":  [condicion, ...]},
        {"AND": [condicion, ...]},
        {"NOT": condicion},
    ]

Tipos de condición primitiva:
    {"var": "nombre_variable", "op": ">", "value": 300}
    {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5}

Los permisivos se exponen en fuzzy_out como:
    "__PERM_<NOMBRE>" con etiquetas ON / OFF.
"""
from __future__ import annotations

import operator
from typing import Any


# ============================================================
# OPERADORES NUMÉRICOS
# ============================================================
_OPERADORES = {
    "<":  operator.lt,
    "<=": operator.le,
    ">":  operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "=":  operator.eq,
    "!=": operator.ne,
}


def nombre_variable_permisivo(nombre: str) -> str:
    limpio = str(nombre).strip().upper()
    if limpio.startswith("__PERM_"):
        return limpio
    return f"__PERM_{limpio}"


# ============================================================
# Resolución de valores numéricos
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
        "Revisar setpoints, inputs, row o columnas_entrada."
    )


# ============================================================
# Evaluación recursiva de condición
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
    """Evalúa una condición (primitiva o compuesta) a True/False."""
    # Listas planas = AND implícito
    if isinstance(condicion, list):
        return all(
            _evaluar_condicion(
                c,
                fuzzy_out=fuzzy_out, row=row, inputs=inputs,
                setpoints=setpoints, columnas_entrada=columnas_entrada,
                min_mu_default=min_mu_default,
            )
            for c in condicion
        )

    if isinstance(condicion, dict):
        if "OR" in condicion:
            return any(
                _evaluar_condicion(
                    c,
                    fuzzy_out=fuzzy_out, row=row, inputs=inputs,
                    setpoints=setpoints, columnas_entrada=columnas_entrada,
                    min_mu_default=min_mu_default,
                )
                for c in condicion["OR"]
            )
        if "AND" in condicion:
            return all(
                _evaluar_condicion(
                    c,
                    fuzzy_out=fuzzy_out, row=row, inputs=inputs,
                    setpoints=setpoints, columnas_entrada=columnas_entrada,
                    min_mu_default=min_mu_default,
                )
                for c in condicion["AND"]
            )
        if "NOT" in condicion:
            return not _evaluar_condicion(
                condicion["NOT"],
                fuzzy_out=fuzzy_out, row=row, inputs=inputs,
                setpoints=setpoints, columnas_entrada=columnas_entrada,
                min_mu_default=min_mu_default,
            )

        # Primitiva fuzzy
        if "fuzzy_var" in condicion:
            var = str(condicion["fuzzy_var"])
            label = str(condicion["label"]).upper()
            min_mu = float(condicion.get("min_mu", min_mu_default))
            return _mu_fuzzy(fuzzy_out, var, label) >= min_mu

        # Primitiva numérica
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
                return False  # Variable no disponible → condición no satisfecha
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
    """Evalúa todos los permisivos. Retorna {NOMBRE: bool} (ON=True / OFF=False)."""
    if not permisivos_config:
        return {}

    estados: dict[str, bool] = {}
    for nombre, condiciones in permisivos_config.items():
        if not condiciones:
            estados[str(nombre).strip().upper()] = False
            continue
        activo = _evaluar_condicion(
            condiciones,
            fuzzy_out=fuzzy_out, row=row, inputs=inputs,
            setpoints=setpoints, columnas_entrada=columnas_entrada,
            min_mu_default=min_mu_default,
        )
        estados[str(nombre).strip().upper()] = bool(activo)

    return estados


def inyectar_permisivos_en_fuzzy_out(
    fuzzy_out: dict,
    estados_permisivos: dict[str, bool],
) -> dict:
    """Inyecta cada permisivo como pseudo-variable __PERM_<NOMBRE> con ON/OFF."""
    for nombre, activo in (estados_permisivos or {}).items():
        var_perm = nombre_variable_permisivo(nombre)
        fuzzy_out[var_perm] = {
            "pert": {
                "ON":  1.0 if activo else 0.0,
                "OFF": 0.0 if activo else 1.0,
            }
        }
    return fuzzy_out
