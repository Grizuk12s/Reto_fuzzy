# -*- coding: utf-8 -*-
"""Modelos fuzzy del Espesador.

Fuente: hoja "Fuzzy" de "Estrategia de control Espesador.xlsx"

Bloques calibrados (4):
- Torque    : Fuzzy High  (offset = lmax - pv)
- Bed Mass  : Fuzzy High  (offset = lmax - pv)
- Bed Level : Fuzzy Norm  (offset = (pv - lmin) / (lmax - lmin))
- Densidad  : Fuzzy Low   (offset = pv - lmin)
                          NOTA: el Excel asigna HIGH=1 a offset=0 (pv=lmin) y
                          LOW=1 a offset=5. Se respeta la asignacion de
                          etiquetas tal cual viene del Excel.

Bloques placeholder (5, decision A2):
- Torque Bomba, Potencia Bomba, Presion Descarga, Presion Diferencial,
  Nivel Rastra
- Usan la MISMA forma calibrada de Torque (lmax - pv, HIGH/OK/LOW) hasta
  que el usuario suba sus tablas propias.

Detalles de reordenamiento (decision B):
- En el Excel, las filas R17/R18 de Bed Level y R33/R34 de Densidad
  estaban en orden no monotonico. Se asume typo y se reordenan a eje
  ascendente.
"""

from __future__ import annotations
import fuzzys_templates


# ============================================================
# Helpers de offset_fn
# ============================================================
def _offset_fn_low(pv, lmin):
    """Fuzzy Low: offset = pv - lmin."""
    return pv - lmin


def _offset_fn_high(pv, lmax):
    """Fuzzy High: offset = lmax - pv."""
    return lmax - pv


def _offset_fn_norm(pv, lmin, lmax):
    """Fuzzy Norm: offset = (pv - lmin) / (lmax - lmin)."""
    den = (lmax - lmin)
    if abs(den) < 1e-12:
        return 0.5
    return (pv - lmin) / den


# ============================================================
# BLOQUES CALIBRADOS (del Excel)
# ============================================================

# ----- TORQUE -- Fuzzy High -----
# Eje: lmax - pv. Cuando pv=lmax -> offset=0 -> HIGH=1
TorqueFz = fuzzys_templates.crear_clase_fuzzy_offset(
    "TorqueFz",
    offset=[0.0, 5.5, 5.50001, 13.0, 19.0],
    offset_fn=_offset_fn_high,
    offset_args=("lmax",),
    HIGH=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=  [0.0, 0.5, 1.0, 0.5, 0.0],
    LOW= [0.0, 0.0, 0.0, 0.5, 1.0],
)

# ----- BED MASS -- Fuzzy High -----
BedMassFz = fuzzys_templates.crear_clase_fuzzy_offset(
    "BedMassFz",
    offset=[-5.0, 5.0, 5.001, 25.0, 35.0],
    offset_fn=_offset_fn_high,
    offset_args=("lmax",),
    HIGH=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=  [0.0, 0.5, 1.0, 0.5, 0.0],
    LOW= [0.0, 0.0, 0.0, 0.5, 1.0],
)

# ----- BED LEVEL -- Fuzzy Norm -----
# Filas R17/R18 reordenadas a eje ascendente.
# Eje normalizado [-0.2, 0, 1, 1.0001, 1.2]
BedLevelFz = fuzzys_templates.crear_clase_fuzzy_offset(
    "BedLevelFz",
    offset=[-0.2, 0.0, 1.0, 1.0001, 1.2],
    offset_fn=_offset_fn_norm,
    offset_args=("lmin", "lmax"),
    LOW= [1.0, 0.5, 0.0, 0.0, 0.0],
    OK=  [0.0, 0.5, 1.0, 0.5, 0.0],
    HIGH=[0.0, 0.0, 0.0, 0.5, 1.0],
)

# ----- DENSIDAD -- Fuzzy Low -----
# Filas R33/R34 reordenadas a eje ascendente.
# Eje: pv - lmin
# IMPORTANTE: a pv=lmin (offset=0) el Excel asigna HIGH=1.
# Se respeta esa asignacion tal cual viene del Excel.
DensidadFz = fuzzys_templates.crear_clase_fuzzy_offset(
    "DensidadFz",
    offset=[0.0, 0.5, 3.0, 3.0001, 5.0],
    offset_fn=_offset_fn_low,
    offset_args=("lmin",),
    HIGH=[1.0, 0.5, 0.0, 0.0, 0.0],
    OK=  [0.0, 0.5, 1.0, 0.5, 0.0],
    LOW= [0.0, 0.0, 0.0, 0.5, 1.0],
)


# ============================================================
# BLOQUES PLACEHOLDER (decision A2)
# Usan la misma forma calibrada de Torque hasta que se carguen sus
# tablas reales. Mismo offset_fn (lmax - pv) y mismas tablas HIGH/OK/LOW.
# ============================================================
def _crear_placeholder_tipo_torque(nombre_clase: str):
    return fuzzys_templates.crear_clase_fuzzy_offset(
        nombre_clase,
        offset=[0.0, 5.5, 5.50001, 13.0, 19.0],
        offset_fn=_offset_fn_high,
        offset_args=("lmax",),
        HIGH=[1.0, 0.5, 0.0, 0.0, 0.0],
        OK=  [0.0, 0.5, 1.0, 0.5, 0.0],
        LOW= [0.0, 0.0, 0.0, 0.5, 1.0],
    )


# TODO calibrar: reemplazar cuando se tengan los parametros reales.
TorqueBombaFz        = _crear_placeholder_tipo_torque("TorqueBombaFz")        # TODO
PotenciaBombaFz      = _crear_placeholder_tipo_torque("PotenciaBombaFz")      # TODO
PresionDescargaFz    = _crear_placeholder_tipo_torque("PresionDescargaFz")    # TODO
PresionDiferencialFz = _crear_placeholder_tipo_torque("PresionDiferencialFz") # TODO
NivelRastraFz        = _crear_placeholder_tipo_torque("NivelRastraFz")        # TODO


# ============================================================
# REGISTRO FUZZY PARA EVALUACION
# ============================================================
# El campo "type" informa al evaluator que limite usar (lmin, lmax o ambos).
FUZZY_MODELOS = {
    # calibrados
    "torque":              {"type": "high", "model": TorqueFz()},
    "bed_mass":            {"type": "high", "model": BedMassFz()},
    "bed_level":           {"type": "norm", "model": BedLevelFz()},
    "densidad":            {"type": "low",  "model": DensidadFz()},
    # placeholders (TODO calibrar)
    "torque_bomba":        {"type": "high", "model": TorqueBombaFz()},
    "potencia_bomba":      {"type": "high", "model": PotenciaBombaFz()},
    "presion_descarga":    {"type": "high", "model": PresionDescargaFz()},
    "presion_diferencial": {"type": "high", "model": PresionDiferencialFz()},
    "nivel_rastra":        {"type": "high", "model": NivelRastraFz()},
}


# ============================================================
# PENDIENTES (por minuto)
# Calibradas (4) del Excel:
# ============================================================

PendTorque = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendTorque",
    x=[-1.2, -0.35, 0.0, 0.35, 1.2],
    DEC=   [1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=   [0.0, 0.0, 0.0, 0.5, 1.0],
)

PendBedLevel = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendBedLevel",
    x=[-0.02, -0.007, 0.0, 0.007, 0.02],
    DEC=   [1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=   [0.0, 0.0, 0.0, 0.5, 1.0],
)

PendBedMass = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendBedMass",
    x=[-0.1, -0.03, 0.0, 0.03, 0.1],
    DEC=   [1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=   [0.0, 0.0, 0.0, 0.5, 1.0],
)

PendDensidad = fuzzys_templates.crear_clase_fuzzy_pendiente(
    "PendDensidad",
    x=[-0.5, -0.1, 0.0, 0.1, 0.5],
    DEC=   [1.0, 0.5, 0.0, 0.0, 0.0],
    STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
    INC=   [0.0, 0.0, 0.0, 0.5, 1.0],
)


# Placeholders para pendientes de variables no calibradas (decision A2)
def _crear_pendiente_placeholder(nombre):
    return fuzzys_templates.crear_clase_fuzzy_pendiente(
        nombre,
        x=[-1.2, -0.35, 0.0, 0.35, 1.2],   # mismo perfil que torque
        DEC=   [1.0, 0.5, 0.0, 0.0, 0.0],
        STABLE=[0.0, 0.5, 1.0, 0.5, 0.0],
        INC=   [0.0, 0.0, 0.0, 0.5, 1.0],
    )


PendTorqueBomba        = _crear_pendiente_placeholder("PendTorqueBomba")        # TODO
PendPotenciaBomba      = _crear_pendiente_placeholder("PendPotenciaBomba")      # TODO
PendPresionDescarga    = _crear_pendiente_placeholder("PendPresionDescarga")    # TODO
PendPresionDiferencial = _crear_pendiente_placeholder("PendPresionDiferencial") # TODO
PendNivelRastra        = _crear_pendiente_placeholder("PendNivelRastra")        # TODO


PEND_MODELOS = {
    # calibrados
    "torque":              PendTorque(),
    "bed_mass":            PendBedMass(),
    "bed_level":           PendBedLevel(),
    "densidad":            PendDensidad(),
    # placeholders
    "torque_bomba":        PendTorqueBomba(),
    "potencia_bomba":      PendPotenciaBomba(),
    "presion_descarga":    PendPresionDescarga(),
    "presion_diferencial": PendPresionDiferencial(),
    "nivel_rastra":        PendNivelRastra(),
}
