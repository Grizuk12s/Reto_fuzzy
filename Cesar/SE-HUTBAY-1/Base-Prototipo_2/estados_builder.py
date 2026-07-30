"""Helpers declarativos para construir condiciones, estados y reglas."""

from __future__ import annotations


_LABELS_PERTENENCIA = {
    "ALTO": "HIGH",
    "HIGH": "HIGH",
    "OK": "OK",
    "BAJO": "LOW",
    "LOW": "LOW",
    "NO_ALTO": "NO-HIGH",
    "NO-HIGH": "NO-HIGH",
    "NO_BAJO": "NO-LOW",
    "NO-LOW": "NO-LOW",
    "NO_OK": "NO-OK",
    "NO-OK": "NO-OK",
    "CERCA_ALTO": "CERCA_ALTO",
    "CERCA_BAJO": "CERCA_BAJO",
}

_LABELS_TENDENCIA = {
    "SUBIENDO": "INC",
    "INC": "INC",
    "ESTABLE": "STABLE",
    "STABLE": "STABLE",
    "BAJANDO": "DEC",
    "DEC": "DEC",
    "NO_SUBIENDO": "NO-INC",
    "NO-INC": "NO-INC",
    "NO_BAJANDO": "NO-DEC",
    "NO-DEC": "NO-DEC",
}


def _normalizar_label(label: str, tabla: dict[str, str]) -> str:
    key = str(label).strip().upper().replace(" ", "_")
    if key not in tabla:
        raise ValueError(f"Label no soportado: {label!r}")
    return tabla[key]


def pertenencia(variable: str, label: str) -> tuple[str, str]:
    return (str(variable), _normalizar_label(label, _LABELS_PERTENENCIA))


def tendencia(variable: str, label: str) -> tuple[str, str]:
    return (f"pend_{variable}", _normalizar_label(label, _LABELS_TENDENCIA))


def alto(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "ALTO")


def ok(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "OK")


def bajo(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "BAJO")


def no_alto(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "NO_ALTO")


def no_bajo(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "NO_BAJO")


def no_ok(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "NO_OK")


def cerca_alto(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "CERCA_ALTO")


def cerca_bajo(variable: str) -> tuple[str, str]:
    return pertenencia(variable, "CERCA_BAJO")


def subiendo(variable: str) -> tuple[str, str]:
    return tendencia(variable, "SUBIENDO")


def estable(variable: str) -> tuple[str, str]:
    return tendencia(variable, "ESTABLE")


def bajando(variable: str) -> tuple[str, str]:
    return tendencia(variable, "BAJANDO")


def no_subiendo(variable: str) -> tuple[str, str]:
    return tendencia(variable, "NO_SUBIENDO")


def no_bajando(variable: str) -> tuple[str, str]:
    return tendencia(variable, "NO_BAJANDO")


def _resolver_condicion(condicion):
    if (
        isinstance(condicion, dict)
        and "condicion" in condicion
        and condicion.get("tipo") in {"estado", "subestado", "fuerza"}
    ):
        return _resolver_condicion(condicion["condicion"])

    if isinstance(condicion, list):
        return [_resolver_condicion(item) for item in condicion]

    if isinstance(condicion, dict):
        if "AND" in condicion:
            return {"AND": [_resolver_condicion(item) for item in condicion["AND"]]}
        if "OR" in condicion:
            return {"OR": [_resolver_condicion(item) for item in condicion["OR"]]}
        if "NOT" in condicion:
            return {"NOT": _resolver_condicion(condicion["NOT"])}

    return condicion


def _combinar(conector: str, *condiciones):
    items = [_resolver_condicion(c) for c in condiciones if c is not None]
    nombre = str(conector).upper()
    if not items:
        return [] if nombre == "TOP" else {nombre: []}
    if nombre == "TOP":
        return items
    if len(items) == 1:
        return items[0]
    if nombre not in {"AND", "OR"}:
        raise ValueError(f"Conector no soportado: {conector!r}")
    return {nombre: items}


def AND(*condiciones):
    return _combinar("AND", *condiciones)


def OR(*condiciones):
    return _combinar("OR", *condiciones)


def NOT(condicion):
    return {"NOT": _resolver_condicion(condicion)}


def crear_estado(nombre: str, *condiciones, conector: str = "AND") -> dict:
    return {
        "tipo": "estado",
        "nombre": str(nombre),
        "condicion": _combinar(conector, *condiciones),
    }


def crear_subestado(nombre: str, *condiciones, conector: str = "AND") -> dict:
    return {
        "tipo": "subestado",
        "nombre": str(nombre),
        "condicion": _combinar(conector, *condiciones),
    }


def crear_fuerza(*condiciones, conector: str = "OR"):
    if not condiciones:
        return None
    return {
        "tipo": "fuerza",
        "nombre": "fuerza",
        "condicion": _combinar(conector, *condiciones),
    }


def crear_regla(
    regla_id: str,
    bloque: str,
    priority: float,
    activacion,
    then,
    fuerza=None,
    weight: float = 1.0,
) -> dict:
    activacion_norm = _combinar("TOP", *(activacion or []))
    fuerza_norm = _resolver_condicion(fuerza["condicion"]) if isinstance(fuerza, dict) else _resolver_condicion(fuerza)

    return {
        "id": str(regla_id),
        "bloque": str(bloque),
        "priority": float(priority),
        "weight": float(weight),
        "fuerza": fuerza_norm,
        "if": activacion_norm,
        "then": list(then),
    }
