# ============================================================
# motor.py
# ------------------------------------------------------------
# Motor de reglas del Espesador con:
# - Operadores logicos OR / AND / NOT en `if` (decision C)
# - Jerarquia de bloques (decision C):
#     - Bloques no independientes -> orden por `level`, el primero que
#       dispara reglas bloquea a los siguientes en este tick.
#     - Bloques independientes (ej. "optimizacion") siempre evaluan.
# - Cooldown por FAMILIA de SP (decision D), tomado de config global.
#   Ya NO se acepta cooldown_s a nivel de regla.
# - Soporte de reglas con multiples acciones en `then`.
# ============================================================

from __future__ import annotations

from .config import BLOQUES, COOLDOWN_FAMILIA_S


# ============================================================
# Mapeo accion -> familia de SP
# ============================================================
def accion_a_sp(accion: str) -> str | None:
    a = str(accion).upper().strip()

    if a.startswith("DISMINUIR_TONELAJE") or a.startswith("AUMENTAR_TONELAJE"):
        return "sp_tonelaje"
    if a.startswith("DISMINUIR_FLOCULANTE") or a.startswith("AUMENTAR_FLOCULANTE"):
        return "sp_floculante"
    if a.startswith("DISMINUIR_VEL_BOMBA") or a.startswith("AUMENTAR_VEL_BOMBA"):
        return "sp_vel_bomba"
    # Acciones de la capa de optimizacion (TODO definir cuando se implemente)
    if a.startswith("SUBIR_OBJETIVO_DENSIDAD") or a.startswith("BAJAR_OBJETIVO_DENSIDAD"):
        return "objetivo_densidad"
    return None


def cooldown_family(accion: str) -> str:
    sp = accion_a_sp(accion)
    return str(sp) if sp is not None else str(accion)


def cooldown_segundos_para_accion(accion: str) -> float:
    """Cooldown por familia, leido de COOLDOWN_FAMILIA_S (global).

    Si la familia no esta en el dict, cooldown=0 (la accion puede repetirse
    en cada tick). El usuario decide el valor en config.py, no en cada regla.
    """
    familia = cooldown_family(accion)
    return float(COOLDOWN_FAMILIA_S.get(familia, 0.0))


# ============================================================
# Evaluacion de condiciones con operadores logicos
# ------------------------------------------------------------
# Cada item del `if` de una regla puede ser:
#   - tupla (var, label)            -> mu directo
#   - dict {"OR":  [c1, c2, ...]}    -> max de mus
#   - dict {"AND": [c1, c2, ...]}    -> min de mus
#   - dict {"NOT": c}                -> 1 - mu(c)
#
# El top-level del `if` es AND implicito (igual que antes, retrocompatible).
# ============================================================
def mu_condicion(fuzzy_out: dict, var: str, label: str) -> float:
    pert = (fuzzy_out.get(str(var), {}) or {}).get("pert", {}) or {}
    return float(pert.get(str(label).upper(), 0.0))


def evaluar_condicion(condicion, fuzzy_out: dict) -> float:
    # tupla simple
    if isinstance(condicion, tuple):
        if len(condicion) != 2:
            raise ValueError(f"Tupla de condicion mal formada: {condicion}")
        var, label = condicion
        return mu_condicion(fuzzy_out, var, label)

    # dict con operador
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

    # lista plana = AND
    if isinstance(condicion, list):
        if not condicion:
            return 1.0
        return float(min(evaluar_condicion(c, fuzzy_out) for c in condicion))

    raise ValueError(f"Condicion no reconocida: {condicion!r}")


def fuerza_regla(fuzzy_out: dict, condiciones) -> float:
    if not condiciones:
        return 0.0
    # top-level AND
    return float(min(evaluar_condicion(c, fuzzy_out) for c in condiciones))


# ============================================================
# Acciones de una regla
# ============================================================
def _normalizar_acciones(regla: dict) -> list[str]:
    acciones = (regla or {}).get("then", [])
    if isinstance(acciones, str):
        return [acciones]
    return [str(a) for a in acciones]


# ============================================================
# Evaluacion de un conjunto de reglas (un bloque)
# ============================================================
def _evaluar_set_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    last_action_time: dict,
    min_belief: float,
) -> list[dict]:
    """Evalua reglas ordenadas por prioridad descendente; retorna disparadas.

    Aplica cooldown por familia de SP (estado en `last_action_time`).
    Muta `last_action_time` para registrar nuevas activaciones.
    """
    fired = []
    reglas_ordenadas = sorted(reglas, key=lambda r: float(r.get("priority", 0.0)), reverse=True)

    for regla in reglas_ordenadas:
        acciones = _normalizar_acciones(regla)
        belief = float(regla.get("weight", 1.0)) * fuerza_regla(fuzzy_out, regla.get("if", []))
        if belief < float(min_belief):
            continue

        familias = []
        cooldown_por_accion = {}
        bloqueada = False

        for accion in acciones:
            familia = cooldown_family(accion)
            cooldown_s = cooldown_segundos_para_accion(accion)
            familias.append(familia)
            cooldown_por_accion[accion] = float(cooldown_s)
            t_last = float(last_action_time.get(familia, -1e18))
            if (float(t_s) - t_last) < float(cooldown_s):
                bloqueada = True
                break

        if bloqueada:
            continue

        fired.append({
            "t_s": float(t_s),
            "id": str(regla["id"]),
            "bloque": str(regla.get("bloque", "estabilidad")),
            "acciones": list(acciones),
            "accion": " | ".join(acciones),
            "belief": belief,
            "priority": float(regla.get("priority", 0.0)),
            "conds": list(regla.get("if", [])),
            "cooldown_por_accion": dict(cooldown_por_accion),
            "familias_cooldown": list(familias),
            "familia_cooldown": " | ".join(familias),
        })

        for accion in acciones:
            familia = cooldown_family(accion)
            last_action_time[familia] = float(t_s)

    return fired


# ============================================================
# Evaluacion completa con jerarquia de bloques
# ============================================================
def evaluar_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
) -> dict:
    """Evalua reglas respetando jerarquia de bloques.

    Bloques no independientes:
      - se procesan en orden por BLOQUES[name]["level"] (menor = mas critico)
      - el primer bloque que dispara una regla BLOQUEA a los siguientes en
        este tick.

    Bloques independientes:
      - siempre se evaluan, sin importar quien mas haya disparado.
    """
    if last_action_time is None:
        last_action_time = {}

    # Agrupar por bloque
    bloques_a_reglas: dict[str, list[dict]] = {}
    for r in reglas:
        b = str(r.get("bloque", "estabilidad"))
        bloques_a_reglas.setdefault(b, []).append(r)

    # Separar bloques jerarquicos vs independientes
    bloques_jerarquicos = sorted(
        [b for b, meta in BLOQUES.items() if not meta.get("independent", False)],
        key=lambda b: float(BLOQUES[b].get("level", 99)),
    )
    bloques_independientes = [
        b for b, meta in BLOQUES.items() if meta.get("independent", False)
    ]

    all_fired = []
    bloque_jerarquico_disparo = False

    for bloque in bloques_jerarquicos:
        if bloque_jerarquico_disparo:
            # un bloque mas prioritario ya disparo; los demas no evaluan
            continue
        reglas_bloque = bloques_a_reglas.get(bloque, [])
        if not reglas_bloque:
            continue
        fired = _evaluar_set_reglas(
            reglas_bloque, fuzzy_out, t_s, last_action_time, min_belief
        )
        if fired:
            bloque_jerarquico_disparo = True
            all_fired.extend(fired)

    for bloque in bloques_independientes:
        reglas_bloque = bloques_a_reglas.get(bloque, [])
        if not reglas_bloque:
            continue
        fired = _evaluar_set_reglas(
            reglas_bloque, fuzzy_out, t_s, last_action_time, min_belief
        )
        all_fired.extend(fired)

    belief_accion: dict[str, float] = {}
    for ev in all_fired:
        for accion in ev["acciones"]:
            belief_accion[accion] = max(float(belief_accion.get(accion, 0.0)), float(ev["belief"]))

    return {
        "fired": all_fired,
        "belief_accion": belief_accion,
        "last_action_time": last_action_time,
    }


def motor_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
) -> dict:
    """Alias retrocompatible para pruebas unitarias."""
    return evaluar_reglas(
        reglas=reglas,
        fuzzy_out=fuzzy_out,
        t_s=t_s,
        last_action_time=last_action_time,
        min_belief=min_belief,
    )
