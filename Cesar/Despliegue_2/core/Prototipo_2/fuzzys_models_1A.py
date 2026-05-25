# ============================================================
# fuzzys_models_1A.py  (standalone — Prototipo_2)
# ------------------------------------------------------------
# FUZZYS hardcodeados desde "fuzzys 1A Cuajone.xlsx" (Hoja1)
# + se agrega DENSIDAD (no venía en ese Excel)
#
# Requiere: fuzzys_templates.py (tu template)
# ============================================================

from __future__ import annotations
import fuzzys_templates

# -----------------------------
# ESTADO / PV vs límites
# -----------------------------

# POTENCIA (low offset)  offset = pv - lmin
PotenciaFz = fuzzys_templates.crear_clase_fuzzy_Low(
    "PotenciaFz",
    offset=[0.0, 120.0, 500.0, 500.0001, 800.0],
    LOW=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    HIGH=[0.0, 0.0, 0.0, 0.5, 1.0],
)

# NIVEL (normalizado) offset = (pv-lmin)/(lmax-lmin)
NivelFz = fuzzys_templates.crear_clase_fuzzy_norm(
    "NivelFz",
    offset=[0.0, 0.5, 0.95, 0.95001, 1.0],
    LOW=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    HIGH=[0.0, 0.0, 0.0, 0.5, 1.0],
)

# PRESION (normalizado)
PresionFz = fuzzys_templates.crear_clase_fuzzy_norm(
    "PresionFz",
    offset=[0.0, 0.05, 0.25, 0.438, 0.8],
    HIGH=[0.0, 0.0, 0.0, 0.5, 1.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    LOW=[1.0, 0.5, 0.0, 0.0, 0.0],
)

# P80 (high offset) offset = lmax - pv
P80Fz = fuzzys_templates.crear_clase_fuzzy_high(
    "P80Fz",
    offset=[-1.5, -0.3, -0.299999, 2.5, 4.0],
    HIGH=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    LOW=[0.0, 0.0, 0.0, 0.5, 1.0],
)

# DENSIDAD (NO venía en el Excel fuzzys 1A)
# -> usamos un fuzzy normalizado genérico (BAJO/OK/ALTO) compatible con reglas
DensidadFz = fuzzys_templates.crear_clase_fuzzy_norm(
    "DensidadFz",
    offset=[0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0],
    LOW=[1.0, 1.0, 0.5, 0.0, 0.0, 0.0, 0.0],
    OK=[0.0, 0.2, 0.8, 1.0, 0.8, 0.2, 0.0],
    HIGH=[0.0, 0.0, 0.0, 0.0, 0.5, 1.0, 1.0],
)

FUZZY_MODELOS = {
    "potencia": {"type": "low",  "model": PotenciaFz()},
    "nivel":    {"type": "norm", "model": NivelFz()},
    "presion":  {"type": "norm", "model": PresionFz()},
    "p80":      {"type": "high", "model": P80Fz()},
    "densidad": {"type": "norm", "model": DensidadFz()},
}

# -----------------------------
# PENDIENTE (por minuto)
# labels: DEC / STABLE / INC (en tu Excel aparecen en minúscula, fuzzys_eval las sube)
# -----------------------------

PendPot = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendPot",
    x=[-17.0, -5.0, 0.0, 5.0, 17.0],
    DEC=[1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=[0.0, 0.0, 0.0, 0.5, 1.0],
)

PendNiv = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendNiv",
    x=[-0.8, -0.3, 0.0, 0.3, 0.8],
    DEC=[1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=[0.0, 0.0, 0.0, 0.5, 1.0],
)

PendPre = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendPre",
    x=[-0.05, -0.01, 0.0, 0.01, 0.05],
    DEC=[1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=[0.0, 0.0, 0.0, 0.5, 1.0],
)

PendP80 = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendP80",
    x=[-0.2, -0.1, 0.0, 0.1, 0.2],
    DEC=[1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=[0.0, 0.0, 0.0, 0.5, 1.0],
)

# DENSIDAD pendiente (genérico)
PendDen = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendDen",
    x=[-0.2, -0.1, 0.0, 0.1, 0.2],
    DEC=[1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=[0.0, 0.0, 0.0, 0.5, 1.0],
)

PEND_MODELOS = {
    "potencia": PendPot(),
    "nivel":    PendNiv(),
    "presion":  PendPre(),
    "p80":      PendP80(),
    "densidad": PendDen(),
}
