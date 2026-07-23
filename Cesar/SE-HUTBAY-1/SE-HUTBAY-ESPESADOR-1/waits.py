# -*- coding: utf-8 -*-
"""Helpers para usar waits definidos en catalogo y asignarles duracion."""

from __future__ import annotations

from waits_catalogo import (
    variable_controlada_de_accion,
)


def usar_wait(wait_ref: dict, duracion_s: float) -> dict:
    if not isinstance(wait_ref, dict) or "wait_id" not in wait_ref:
        raise TypeError(f"Referencia de wait no soportada: {wait_ref!r}")
    spec = dict(wait_ref)
    spec["duracion_s"] = float(duracion_s)
    return spec


def _normalizar_wait(accion: str, wait) -> dict:
    variable = variable_controlada_de_accion(accion)

    if isinstance(wait, tuple):
        if len(wait) != 2:
            raise ValueError(f"Wait en formato tupla mal formado: {wait!r}")
        wait_ref, duracion_s = wait
        wait = usar_wait(wait_ref, duracion_s)

    if not isinstance(wait, dict):
        raise TypeError(f"Wait no soportado: {wait!r}")

    spec = dict(wait)
    if "wait" in spec:
        if "duracion_s" not in spec:
            raise ValueError(f"Falta 'duracion_s' para el wait {spec!r}")
        spec = usar_wait(spec["wait"], spec["duracion_s"])

    if "duracion_s" not in spec:
        raise ValueError(
            f"El wait {spec.get('wait_id', spec)!r} no tiene duracion. "
            "La duracion debe especificarse en la regla."
        )

    spec.setdefault("accion", str(accion))
    spec.setdefault("variable_controlada", variable)
    spec["duracion_s"] = float(spec.get("duracion_s", 0.0))

    if variable is not None and spec.get("variable_controlada") not in (None, variable):
        raise ValueError(
            f"El wait {spec.get('wait_id')!r} no corresponde a la variable controlada de {accion!r}."
        )

    return spec


def _normalizar_lista_waits(accion: str, waits) -> list[dict]:
    if waits is None:
        return []
    if isinstance(waits, dict):
        waits = [waits]
    return [_normalizar_wait(accion, wait) for wait in waits]


def accion_con_waits(
    accion: str,
    waits=None,
    reiniciar_waits=None,
) -> dict:
    return {
        "accion": str(accion),
        "waits": _normalizar_lista_waits(accion, waits),
        "reiniciar_waits": _normalizar_lista_waits(accion, reiniciar_waits),
    }


def accion_con_tipos_wait(
    *,
    regla_id: str,
    accion: str,
    waits: list[tuple[str, float]] | None = None,
    reiniciar_waits: list[tuple[str, float]] | None = None,
) -> dict:
    """Helper retrocompatible para pruebas o migracion."""
    waits_norm = []
    for tipo, duracion_s in waits or []:
        tipo = str(tipo).strip().lower()
        if tipo == "particular":
            from .waits_catalogo import crear_wait_desde_accion
            waits_norm.append(usar_wait(crear_wait_desde_accion(f"{regla_id}_{accion}_particular", accion), duracion_s))
        elif tipo == "general_accion":
            from .waits_catalogo import crear_wait_desde_accion
            waits_norm.append(usar_wait(crear_wait_desde_accion(f"{accion}_general", accion), duracion_s))
        elif tipo == "general_variable":
            from .waits_catalogo import crear_wait_desde_accion
            waits_norm.append(usar_wait(crear_wait_desde_accion(f"{accion}_variable", accion), duracion_s))
        else:
            raise ValueError(f"Tipo de wait no soportado: {tipo!r}")

    reinicios_norm = []
    for tipo, duracion_s in reiniciar_waits or []:
        tipo = str(tipo).strip().lower()
        if tipo == "particular":
            from .waits_catalogo import crear_wait_desde_accion
            reinicios_norm.append(usar_wait(crear_wait_desde_accion(f"{regla_id}_{accion}_reinicio", accion), duracion_s))
        elif tipo == "general_accion":
            from .waits_catalogo import crear_wait_desde_accion
            reinicios_norm.append(usar_wait(crear_wait_desde_accion(f"{accion}_general", accion), duracion_s))
        elif tipo == "general_variable":
            from .waits_catalogo import crear_wait_desde_accion
            reinicios_norm.append(usar_wait(crear_wait_desde_accion(f"{accion}_variable", accion), duracion_s))
        else:
            raise ValueError(f"Tipo de wait no soportado: {tipo!r}")

    return accion_con_waits(
        accion=accion,
        waits=waits_norm,
        reiniciar_waits=reinicios_norm,
    )


def normalizar_accion_regla(item, regla_id: str | None = None) -> dict:
    if isinstance(item, str):
        return accion_con_waits(item, waits=[], reiniciar_waits=[])
    if not isinstance(item, dict):
        raise TypeError(f"Accion de regla no soportada: {item!r}")

    accion = item.get("accion")
    if not accion:
        raise ValueError(f"Falta 'accion' en la definicion de accion de regla: {item!r}")

    waits = _normalizar_lista_waits(accion, item.get("waits"))
    reiniciar_waits = _normalizar_lista_waits(accion, item.get("reiniciar_waits"))

    if regla_id is not None:
        for wait in waits:
            wait.setdefault("regla_id", str(regla_id))
        for wait in reiniciar_waits:
            wait.setdefault("regla_id", str(regla_id))

    return {
        "accion": str(accion),
        "waits": waits,
        "reiniciar_waits": reiniciar_waits,
    }
