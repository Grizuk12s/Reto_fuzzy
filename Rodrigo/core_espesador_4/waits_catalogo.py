# -*- coding: utf-8 -*-
"""Catalogo de waits reutilizables, sin duracion asociada.

Idea:
- Aqui solo se define la identidad de cada wait.
- El tiempo no existe en este modulo.
- La regla decide que wait usa para su accion y que waits quiere reiniciar.
"""

from __future__ import annotations

from .defuzzy_actions import parsear_accion


def _slug(texto: str) -> str:
    limpio = str(texto).strip().lower()
    for origen, destino in (
        (" ", "_"),
        (".", "_"),
        ("-", "_"),
        ("|", "_"),
        (":", "_"),
        ("/", "_"),
        ("\\", "_"),
    ):
        limpio = limpio.replace(origen, destino)
    while "__" in limpio:
        limpio = limpio.replace("__", "_")
    return limpio.strip("_")


def variable_controlada_de_accion(accion: str) -> str | None:
    try:
        return str(parsear_accion(accion)["familia_sp"])
    except ValueError:
        return None


def crear_wait(
    nombre: str,
    *,
    variable_controlada: str | None = None,
    descripcion: str = "",
) -> dict:
    return {
        "wait_id": f"wait::{_slug(nombre)}",
        "nombre": str(nombre),
        "tipo": "custom",
        "variable_controlada": None if variable_controlada is None else str(variable_controlada),
        "descripcion": str(descripcion),
    }


def crear_wait_desde_accion(
    nombre: str,
    accion: str,
    *,
    descripcion: str = "",
) -> dict:
    return crear_wait(
        nombre,
        variable_controlada=variable_controlada_de_accion(accion),
        descripcion=descripcion or f"Wait catalogado para la accion {accion}",
    )


# ============================================================
# WAITS DE OPERACION USADOS POR LAS REGLAS REALES
# ------------------------------------------------------------
# Cada wait se define explicitamente por variable controlada.
# Sirven tanto para aumentar como para disminuir, porque bloquean el
# control sobre la misma variable, no una direccion particular.
# ============================================================
WAIT_VEL_BOMBA_CRITICO = crear_wait(
    "vel_bomba_critico",
    variable_controlada="sp_vel_bomba",
    descripcion="Wait compartido para control critico de velocidad de bomba",
)

WAIT_VEL_BOMBA_ESTABILIDAD = crear_wait(
    "vel_bomba_estabilidad",
    variable_controlada="sp_vel_bomba",
    descripcion="Wait compartido para control de estabilidad de velocidad de bomba",
)

WAIT_TONELAJE_CRITICO = crear_wait(
    "tonelaje_critico",
    variable_controlada="sp_tonelaje",
    descripcion="Wait compartido para control critico de tonelaje",
)

WAIT_TONELAJE_ESTABILIDAD = crear_wait(
    "tonelaje_estabilidad",
    variable_controlada="sp_tonelaje",
    descripcion="Wait compartido para control de estabilidad de tonelaje",
)

WAIT_FLOCULANTE_CRITICO = crear_wait(
    "floculante_critico",
    variable_controlada="sp_floculante",
    descripcion="Wait compartido para control critico de floculante",
)

WAIT_FLOCULANTE_ESTABILIDAD = crear_wait(
    "floculante_estabilidad",
    variable_controlada="sp_floculante",
    descripcion="Wait compartido para control de estabilidad de floculante",
)


# ============================================================
# WAITS DE EJEMPLO PEDIDOS POR EL USUARIO
# ------------------------------------------------------------
# Caso:
# - un wait cuando controlo velocidad para solidos
# - otro wait cuando controlo velocidad para presion de cama
# - el wait de presion de cama puede reiniciar el de solidos
# ============================================================
WAIT_VEL_SOLIDOS = crear_wait(
    "velocidad_para_solidos",
    variable_controlada="sp_vel_bomba",
    descripcion="Wait para control de velocidad enfocado en solidos",
)

WAIT_VEL_PRESION_CAMA = crear_wait(
    "velocidad_para_presion_cama",
    variable_controlada="sp_vel_bomba",
    descripcion="Wait para control de velocidad enfocado en presion de cama",
)
