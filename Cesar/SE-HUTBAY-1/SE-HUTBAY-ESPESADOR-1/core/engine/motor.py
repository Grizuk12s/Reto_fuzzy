# -*- coding: utf-8 -*-
"""Motor de reglas difusas — lógica genérica pura.

Soporta:
- Operadores lógicos OR / AND / NOT en condiciones de reglas
- Jerarquía de bloques configurable (no independientes y independientes)
- Waits declarativos por acción/regla
- Múltiples acciones en `then`

El dict `bloques` define la jerarquía del proceso y se pasa siempre
como parámetro — este módulo no importa nada específico de ningún proceso.

Estructura de `bloques`:
    {
        "nombre_bloque": {
            "level": int,          # orden de evaluación (menor = más crítico)
            "independent": bool,   # True = siempre evalúa, no bloquea ni es bloqueado
        },
        ...
    }
"""
from __future__ import annotations

from waits import normalizar_accion_regla


# ============================================================
# Helpers de waits
# ============================================================
def _wait_esta_activo(wait_spec: dict, estado_waits: dict, t_s: float) -> bool:
    estado = estado_waits.get(str(wait_spec["wait_id"]))
    if not estado:
        return False
    duracion = float(estado.get("duracion_s", 0.0))
    t_inicio = float(estado.get("t_ultima_activacion_s", -1e18))
    return (float(t_s) - t_inicio) < duracion


def _activar_waits(wait_specs: list[dict], estado_waits: dict, t_s: float, regla_id: str) -> None:
    for wait in wait_specs:
        estado_waits[str(wait["wait_id"])] = {
            "wait_id": str(wait["wait_id"]),
            "tipo": str(wait.get("tipo", "")),
            "accion": str(wait.get("accion", "")),
            "accion_referencia": wait.get("accion_referencia"),
            "variable_controlada": wait.get("variable_controlada"),
            "descripcion": str(wait.get("descripcion", "")),
            "duracion_s": float(wait.get("duracion_s", 0.0)),
            "t_ultima_activacion_s": float(t_s),
            "regla_id": str(regla_id),
        }


def _deduplicar_waits(wait_specs: list[dict]) -> list[dict]:
    unicos: dict[str, dict] = {}
    for wait in wait_specs:
        unicos[str(wait["wait_id"])] = dict(wait)
    return list(unicos.values())


# ============================================================
# Evaluación de condiciones con operadores lógicos
# ============================================================
def mu_condicion(fuzzy_out: dict, var: str, label: str) -> float:
    pert = (fuzzy_out.get(str(var), {}) or {}).get("pert", {}) or {}
    return float(pert.get(str(label).upper(), 0.0))


def _resolver_condicion_declarativa(condicion):
    if (
        isinstance(condicion, dict)
        and "condicion" in condicion
        and condicion.get("tipo") in {"estado", "subestado", "fuerza"}
    ):
        return condicion["condicion"]
    return condicion


def evaluar_condicion(condicion, fuzzy_out: dict) -> float:
    condicion = _resolver_condicion_declarativa(condicion)

    if isinstance(condicion, tuple):
        if len(condicion) != 2:
            raise ValueError(f"Tupla de condicion mal formada: {condicion}")
        var, label = condicion
        return mu_condicion(fuzzy_out, var, label)

    if isinstance(condicion, dict):
        if "OR" in condicion:
            items = condicion["OR"]
            if not items:
                return 0.0
            return float(max(evaluar_condicion(c, fuzzy_out) for c in items))
        if "AND" in condicion:
            items = condicion["AND"]
            if not items:
                return 1.0
            return float(min(evaluar_condicion(c, fuzzy_out) for c in items))
        if "NOT" in condicion:
            return float(max(0.0, 1.0 - evaluar_condicion(condicion["NOT"], fuzzy_out)))

    if isinstance(condicion, list):
        if not condicion:
            return 1.0
        return float(min(evaluar_condicion(c, fuzzy_out) for c in condicion))

    raise ValueError(f"Condicion no reconocida: {condicion!r}")


def fuerza_activacion(fuzzy_out: dict, condiciones) -> float:
    if condiciones is None:
        return 1.0
    if not condiciones:
        return 1.0
    return float(min(evaluar_condicion(c, fuzzy_out) for c in condiciones))


def fuerza_regla(fuzzy_out: dict, fuerza, fallback: float) -> float:
    if fuerza is None:
        return float(fallback)

    fuerza = _resolver_condicion_declarativa(fuerza)

    if isinstance(fuerza, list):
        if not fuerza:
            return float(fallback)
        return float(max(evaluar_condicion(c, fuzzy_out) for c in fuerza))

    return float(evaluar_condicion(fuerza, fuzzy_out))


# ============================================================
# Acciones de una regla
# ============================================================
def _normalizar_acciones(regla: dict) -> list[dict]:
    acciones = (regla or {}).get("then", [])
    if isinstance(acciones, str):
        acciones = [acciones]
    return [normalizar_accion_regla(a, regla_id=str((regla or {}).get("id", ""))) for a in acciones]


# ============================================================
# Evaluación de un conjunto de reglas (un bloque)
# ============================================================
def _evaluar_set_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    estado_waits: dict,
    min_belief: float,
) -> list[dict]:
    fired = []
    reglas_ordenadas = sorted(reglas, key=lambda r: float(r.get("priority", 0.0)), reverse=True)

    for regla in reglas_ordenadas:
        acciones = _normalizar_acciones(regla)
        mu_activacion = fuerza_activacion(fuzzy_out, regla.get("if", []))
        if mu_activacion <= 0.0:
            continue

        mu_fuerza = fuerza_regla(fuzzy_out, regla.get("fuerza"), fallback=mu_activacion)
        belief = float(regla.get("weight", 1.0)) * mu_fuerza
        if belief < float(min_belief):
            continue

        acciones_nombres = [str(a["accion"]) for a in acciones]
        waits_bloqueantes = []
        waits_reinicio = []
        bloqueada = False

        for accion_spec in acciones:
            for wait in accion_spec.get("waits", []):
                waits_bloqueantes.append(wait)
                if _wait_esta_activo(wait, estado_waits, t_s):
                    bloqueada = True
                    break
            if bloqueada:
                break
            waits_reinicio.extend(accion_spec.get("reiniciar_waits", []))

        if bloqueada:
            continue

        waits_bloqueantes = _deduplicar_waits(waits_bloqueantes)
        waits_reinicio = _deduplicar_waits(waits_reinicio)
        waits_activados = _deduplicar_waits(waits_bloqueantes + waits_reinicio)

        fired.append({
            "t_s": float(t_s),
            "id": str(regla["id"]),
            "bloque": str(regla.get("bloque", "estabilidad")),
            "acciones": list(acciones_nombres),
            "accion": " | ".join(acciones_nombres),
            "acciones_detalle": list(acciones),
            "belief": belief,
            "mu_activacion": mu_activacion,
            "mu_fuerza": mu_fuerza,
            "priority": float(regla.get("priority", 0.0)),
            "conds": list(regla.get("if", [])),
            "fuerza": regla.get("fuerza"),
            "waits_bloqueantes": [str(w["wait_id"]) for w in waits_bloqueantes],
            "waits_reiniciados": [str(w["wait_id"]) for w in waits_reinicio],
            "waits_activados": [str(w["wait_id"]) for w in waits_activados],
            "variables_controladas": sorted({
                str(w["variable_controlada"])
                for w in waits_activados
                if w.get("variable_controlada") is not None
            }),
        })

        _activar_waits(waits_activados, estado_waits, t_s, str(regla["id"]))

    return fired


# ============================================================
# Evaluación completa con jerarquía de bloques
# ============================================================
def evaluar_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    bloques: dict,
    estado_waits: dict | None = None,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
) -> dict:
    """Evalúa reglas respetando la jerarquía de bloques dada.

    Parámetros
    ----------
    reglas : list[dict]
        Reglas a evaluar. Cada regla debe tener 'bloque', 'if', 'then', etc.
    fuzzy_out : dict
        Salida del fuzzificador ({var: {"pert": {label: mu}}}).
    t_s : float
        Timestamp actual en segundos.
    bloques : dict
        Definición de la jerarquía de bloques del proceso:
            {"nombre": {"level": int, "independent": bool}, ...}
    estado_waits : dict | None
        Estado mutable de los waits declarativos.
    min_belief : float
        Umbral mínimo de belief para que una regla dispare.
    """
    if estado_waits is None:
        estado_waits = {} if last_action_time is None else last_action_time

    bloques_a_reglas: dict[str, list[dict]] = {}
    for r in reglas:
        b = str(r.get("bloque", "estabilidad"))
        bloques_a_reglas.setdefault(b, []).append(r)

    bloques_jerarquicos = sorted(
        [b for b, meta in bloques.items() if not meta.get("independent", False)],
        key=lambda b: float(bloques[b].get("level", 99)),
    )
    bloques_independientes = [
        b for b, meta in bloques.items() if meta.get("independent", False)
    ]

    all_fired = []
    bloque_jerarquico_disparo = False

    for bloque in bloques_jerarquicos:
        if bloque_jerarquico_disparo:
            continue
        reglas_bloque = bloques_a_reglas.get(bloque, [])
        if not reglas_bloque:
            continue
        fired = _evaluar_set_reglas(reglas_bloque, fuzzy_out, t_s, estado_waits, min_belief)
        if fired:
            bloque_jerarquico_disparo = True
            all_fired.extend(fired)

    for bloque in bloques_independientes:
        reglas_bloque = bloques_a_reglas.get(bloque, [])
        if not reglas_bloque:
            continue
        fired = _evaluar_set_reglas(reglas_bloque, fuzzy_out, t_s, estado_waits, min_belief)
        all_fired.extend(fired)

    belief_accion: dict[str, float] = {}
    for ev in all_fired:
        for accion in ev["acciones"]:
            belief_accion[accion] = max(float(belief_accion.get(accion, 0.0)), float(ev["belief"]))

    return {
        "fired": all_fired,
        "belief_accion": belief_accion,
        "estado_waits": estado_waits,
        "last_action_time": estado_waits,
    }


def motor_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    bloques: dict,
    estado_waits: dict | None = None,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
) -> dict:
    """Alias retrocompatible para pruebas unitarias."""
    return evaluar_reglas(
        reglas=reglas,
        fuzzy_out=fuzzy_out,
        t_s=t_s,
        bloques=bloques,
        estado_waits=estado_waits,
        last_action_time=last_action_time,
        min_belief=min_belief,
    )
