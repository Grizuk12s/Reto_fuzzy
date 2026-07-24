# -*- coding: utf-8 -*-
"""Tracking PV-SP para las variables manipuladas.

El tracking evita emitir una nueva accion sobre una familia mientras su PV
no haya alcanzado el SP objetivo. Se considera alcanzado cuando el PV entra
en la banda inclusiva ``SP - rango <= PV <= SP + rango``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from .config import SETPOINT_KEYS, TRACKING_CONFIG_DEFAULT


def normalizar_config_tracking(config: dict | None = None) -> dict:
    """Combina una configuracion parcial con los valores por defecto."""
    base = {familia: dict(spec) for familia, spec in TRACKING_CONFIG_DEFAULT.items()}
    if config is not None:
        for familia, spec in config.items():
            if familia not in SETPOINT_KEYS:
                raise KeyError(
                    f"Familia de tracking desconocida {familia!r}. "
                    f"Familias validas: {SETPOINT_KEYS}"
                )
            if isinstance(spec, (int, float)):
                spec = {"rango": float(spec)}
            if not isinstance(spec, Mapping):
                raise TypeError(
                    f"La configuracion de tracking de {familia!r} debe ser un "
                    "dict o un numero usado como rango."
                )
            base.setdefault(familia, {}).update(dict(spec))

    resultado = {}
    for familia in SETPOINT_KEYS:
        spec = dict(base.get(familia, {}))
        spec.setdefault("habilitado", True)
        spec.setdefault("pv_key", f"pv_{familia.removeprefix('sp_')}")
        spec.setdefault("rango", 0.0)
        spec["habilitado"] = bool(spec["habilitado"])
        spec["pv_key"] = str(spec["pv_key"])
        spec["rango"] = float(spec["rango"])
        if not math.isfinite(spec["rango"]) or spec["rango"] < 0.0:
            raise ValueError(
                f"El rango de tracking de {familia!r} debe ser finito y >= 0; "
                f"se recibio {spec['rango']!r}."
            )
        resultado[familia] = spec
    return resultado


def evaluar_tracking(
    pv_actuales: Mapping,
    setpoints: Mapping,
    config: dict | None = None,
) -> dict:
    """Evalua el bloqueo de cada familia manipulada."""
    config_norm = normalizar_config_tracking(config)
    estados = {}

    for familia, spec in config_norm.items():
        if familia not in setpoints:
            raise KeyError(f"Falta el SP objetivo {familia!r} para evaluar tracking.")

        pv_key = spec["pv_key"]
        habilitado = bool(spec["habilitado"])
        if pv_key not in pv_actuales and not habilitado:
            estados[familia] = {
                "familia_sp": familia,
                "pv_key": pv_key,
                "pv": None,
                "sp": float(setpoints[familia]),
                "error": None,
                "error_abs": None,
                "rango": float(spec["rango"]),
                "limite_inferior": float(setpoints[familia]) - float(spec["rango"]),
                "limite_superior": float(setpoints[familia]) + float(spec["rango"]),
                "en_rango": True,
                "habilitado": False,
                "bloqueada": False,
                "motivo": "tracking_deshabilitado",
            }
            continue
        if pv_key not in pv_actuales:
            raise KeyError(
                f"Falta el PV de tracking {pv_key!r} para {familia!r}. "
                "Agregue la columna/configuracion correspondiente o ejecute "
                "el runner con usar_tracking=False."
            )

        pv = float(pv_actuales[pv_key])
        sp = float(setpoints[familia])
        if not math.isfinite(pv) or not math.isfinite(sp):
            raise ValueError(
                f"PV y SP de tracking deben ser finitos para {familia!r}; "
                f"PV={pv!r}, SP={sp!r}."
            )

        error = pv - sp
        error_abs = abs(error)
        en_rango = error_abs <= float(spec["rango"])
        bloqueada = habilitado and not en_rango

        estados[familia] = {
            "familia_sp": familia,
            "pv_key": pv_key,
            "pv": pv,
            "sp": sp,
            "error": error,
            "error_abs": error_abs,
            "rango": float(spec["rango"]),
            "limite_inferior": sp - float(spec["rango"]),
            "limite_superior": sp + float(spec["rango"]),
            "en_rango": en_rango,
            "habilitado": habilitado,
            "bloqueada": bloqueada,
            "motivo": (
                "tracking_deshabilitado"
                if not habilitado
                else "pv_en_rango"
                if en_rango
                else "pv_fuera_de_rango"
            ),
        }

    return estados


def columnas_pv_tracking(config: dict | None = None) -> list[str]:
    """Retorna las claves de PV habilitadas requeridas por el tracking."""
    return [
        spec["pv_key"]
        for spec in normalizar_config_tracking(config).values()
        if spec["habilitado"]
    ]
