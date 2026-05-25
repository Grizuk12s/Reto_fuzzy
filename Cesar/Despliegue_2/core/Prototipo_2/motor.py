# ============================================================
# motor.py
# ------------------------------------------------------------
# Motor de reglas con soporte para reglas de múltiples acciones.
# Si una regla compuesta dispara, se trata como una única activación
# lógica y se ejecutan todas sus acciones asociadas.
# ============================================================

from __future__ import annotations

COOLDOWN_FAMILIA_S = {
    "sp_tonelaje": 180,
    "sp_agua_molino": 60,
    "sp_agua_cajon": 60,
    "sp_agua_ciclones": 60,
    "sp_rpm_bomba": 30,
}

ACCION_COOLDOWN_FAMILIA: dict[str, str] = {}
COOLDOWN_ACCION_S: dict[str, float] = {}


def accion_a_sp(accion: str) -> str | None:
    a = str(accion).upper().strip()

    if a.startswith("DISMINUIR_TONELAJE") or a.startswith("AUMENTAR_TONELAJE"):
        return "sp_tonelaje"
    if a.startswith("DISMINUIR_AGUA_MOLINO") or a.startswith("AUMENTAR_AGUA_MOLINO"):
        return "sp_agua_molino"
    if a.startswith("DISMINUIR_AGUA_CAJON") or a.startswith("AUMENTAR_AGUA_CAJON"):
        return "sp_agua_cajon"
    if a.startswith("DISMINUIR_AGUA_CICLONES") or a.startswith("AUMENTAR_AGUA_CICLONES"):
        return "sp_agua_ciclones"
    if a.startswith("DISMINUIR_RPM_BOMBA") or a.startswith("AUMENTAR_RPM_BOMBA"):
        return "sp_rpm_bomba"
    return None


def cooldown_family(accion: str) -> str:
    a = str(accion)
    if a in ACCION_COOLDOWN_FAMILIA:
        return str(ACCION_COOLDOWN_FAMILIA[a])

    sp = accion_a_sp(a)
    if sp is not None:
        return str(sp)
    return a


def cooldown_segundos_para_accion(accion: str, regla: dict) -> float:
    a = str(accion)
    familia = cooldown_family(a)

    if a in COOLDOWN_ACCION_S:
        try:
            return float(COOLDOWN_ACCION_S[a])
        except Exception:
            return 0.0

    duracion = (regla or {}).get("cooldown_s", None)
    if isinstance(duracion, dict):
        if a in duracion:
            try:
                return float(duracion[a])
            except Exception:
                return 0.0
        if familia in duracion:
            try:
                return float(duracion[familia])
            except Exception:
                return 0.0
        return 0.0

    if duracion is not None:
        try:
            return float(duracion)
        except Exception:
            return 0.0

    return 0.0


def mu_condicion(fuzzy_out: dict, var: str, label: str) -> float:
    pert = (fuzzy_out.get(str(var), {}) or {}).get("pert", {}) or {}
    return float(pert.get(str(label).upper(), 0.0))


def fuerza_regla(fuzzy_out: dict, condiciones: list[tuple[str, str]]) -> float:
    if not condiciones:
        return 0.0
    mus = [mu_condicion(fuzzy_out, var, label) for var, label in condiciones]
    return float(min(mus)) if mus else 0.0


def _normalizar_acciones(regla: dict) -> list[str]:
    acciones = (regla or {}).get("then", [])
    if isinstance(acciones, str):
        return [acciones]
    return [str(a) for a in acciones]


def evaluar_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
) -> dict:
    if last_action_time is None:
        last_action_time = {}

    fired = []
    belief_accion = {}
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
            cooldown_s = cooldown_segundos_para_accion(accion, regla)
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
            belief_accion[accion] = max(float(belief_accion.get(accion, 0.0)), belief)
            last_action_time[familia] = float(t_s)

    return {
        "fired": fired,
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
    """Alias retrocompatible para la interfaz usada por las pruebas unitarias."""
    return evaluar_reglas(
        reglas=reglas,
        fuzzy_out=fuzzy_out,
        t_s=t_s,
        last_action_time=last_action_time,
        min_belief=min_belief,
    )
