# ============================================================
# defuzzy_actions.py
# ------------------------------------------------------------
# Traducción de acciones -> actualización de SPs
# (usamos tamaño de paso por fracción del rango [LL, HL])
# ============================================================

from __future__ import annotations

def _clip(x: float, ll: float, hl: float) -> float:
    return float(max(ll, min(hl, x)))

MAG = {
    "MUY_FUERTE": 0.08,
    "FUERTE": 0.05,
    "SUAVE": 0.015,
    "STD": 0.03,
}

def _mag_frac(action: str) -> float:
    a = action.upper()
    if a.endswith("_MUY_FUERTE"): return MAG["MUY_FUERTE"]
    if a.endswith("_FUERTE"):     return MAG["FUERTE"]
    if a.endswith("_SUAVE"):      return MAG["SUAVE"]
    return MAG["STD"]

def apply_action(
    action: str,
    # SPs actuales:
    sp_ton: float,
    sp_am: float,
    sp_ac: float,
    sp_rpm: float,
    # límites:
    ton_ll: float, ton_hl: float,
    am_ll: float,  am_hl: float,
    ac_ll: float,  ac_hl: float,
    rpm_ll: float, rpm_hl: float,
):
    frac = _mag_frac(action)

    ton_step = frac * (ton_hl - ton_ll)
    am_step  = frac * (am_hl  - am_ll)
    ac_step  = frac * (ac_hl  - ac_ll)
    rpm_step = frac * (rpm_hl - rpm_ll)

    a = action.upper()

    if a.startswith("DISMINUIR_TONELAJE"):
        sp_ton -= ton_step
    elif a.startswith("AUMENTAR_TONELAJE"):
        sp_ton += ton_step

    elif a.startswith("DISMINUIR_AGUA_MOLINO"):
        sp_am -= am_step
    elif a.startswith("AUMENTAR_AGUA_MOLINO"):
        sp_am += am_step

    elif a.startswith("DISMINUIR_AGUA_CAJON"):
        sp_ac -= ac_step
    elif a.startswith("AUMENTAR_AGUA_CAJON"):
        sp_ac += ac_step

    elif a.startswith("DISMINUIR_RPM_BOMBA"):
        sp_rpm -= rpm_step
    elif a.startswith("AUMENTAR_RPM_BOMBA"):
        sp_rpm += rpm_step

    # clip
    sp_ton = _clip(sp_ton, ton_ll, ton_hl)
    sp_am  = _clip(sp_am,  am_ll,  am_hl)
    sp_ac  = _clip(sp_ac,  ac_ll,  ac_hl)
    sp_rpm = _clip(sp_rpm, rpm_ll, rpm_hl)

    return sp_ton, sp_am, sp_ac, sp_rpm
