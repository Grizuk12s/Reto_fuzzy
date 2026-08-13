# -*- coding: utf-8 -*-
"""Shim de compatibilidad — motor.py

Re-exporta el motor genérico desde core.engine.motor e inyecta
BLOQUES del Espesador como default para que todos los call sites
existentes (app.py, runner.py) funcionen sin cambios.

Call sites existentes:
    import motor
    motor.evaluar_reglas(reglas, fuzzy_out, t_s, estado_waits, min_belief)
    -> El shim inyecta `bloques=BLOQUES` automáticamente.
"""
from __future__ import annotations

from config import BLOQUES

# Importar todo el engine genérico y re-exportar
from core.engine.motor import (
    mu_condicion,
    evaluar_condicion,
    fuerza_activacion,
    fuerza_regla,
    _normalizar_acciones,
    _evaluar_set_reglas,
    motor_reglas as _motor_reglas_core,
)


def evaluar_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    estado_waits: dict | None = None,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
    bloques: dict | None = None,
) -> dict:
    """Wrapper del motor genérico que inyecta BLOQUES del Espesador por defecto.

    El parámetro `bloques` puede sobreescribirse para pruebas u otros procesos.
    """
    return _motor_reglas_core(
        reglas=reglas,
        fuzzy_out=fuzzy_out,
        t_s=t_s,
        bloques=bloques if bloques is not None else BLOQUES,
        estado_waits=estado_waits,
        last_action_time=last_action_time,
        min_belief=min_belief,
    )


def motor_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    estado_waits: dict | None = None,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
    bloques: dict | None = None,
) -> dict:
    """Alias retrocompatible — delega a evaluar_reglas."""
    return evaluar_reglas(
        reglas=reglas,
        fuzzy_out=fuzzy_out,
        t_s=t_s,
        estado_waits=estado_waits,
        last_action_time=last_action_time,
        min_belief=min_belief,
        bloques=bloques,
    )
