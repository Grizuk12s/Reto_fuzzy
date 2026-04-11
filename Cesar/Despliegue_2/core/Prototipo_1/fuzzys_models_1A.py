# ============================================================
# fuzzys_models_1A.py  (standalone — Prototipo_1)
# ============================================================

from __future__ import annotations
import fuzzys_templates

# --- ESTADO / PV vs límites ---

PotenciaFz = fuzzys_templates.crear_clase_fuzzy_Low(
    "PotenciaFz",
    offset=[0.0, 120.0, 500.0, 500.0001, 800.0],
    LOW=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    HIGH=[0.0, 0.0, 0.0, 0.5, 1.0],
)

NivelFz = fuzzys_templates.crear_clase_fuzzy_norm(
    "NivelFz",
    offset=[0.0, 0.5, 0.95, 0.95001, 1.0],
    LOW=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    HIGH=[0.0, 0.0, 0.0, 0.5, 1.0],
)

PresionFz = fuzzys_templates.crear_clase_fuzzy_norm(
    "PresionFz",
    offset=[0.0, 0.05, 0.25, 0.438, 0.8],
    HIGH=[0.0, 0.0, 0.0, 0.5, 1.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    LOW=[1.0, 0.5, 0.0, 0.0, 0.0],
)

P80Fz = fuzzys_templates.crear_clase_fuzzy_high(
    "P80Fz",
    offset=[-1.5, -0.3, -0.299999, 2.5, 4.0],
    HIGH=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=[0.0, 0.5, 1.0, 0.5, 0.0],
    LOW=[0.0, 0.0, 0.0, 0.5, 1.0],
)

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

# --- PENDIENTE ---

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
