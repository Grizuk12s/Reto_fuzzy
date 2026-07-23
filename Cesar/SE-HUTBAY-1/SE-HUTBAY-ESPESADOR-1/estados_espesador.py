"""Catalogo de estados y subestados reutilizables para reglas del espesador."""

from __future__ import annotations

from core.states.builder import (
    alto,
    bajando,
    bajo,
    cerca_alto,
    cerca_bajo,
    crear_estado,
    crear_subestado,
    no_alto,
    no_bajando,
    no_bajo,
    no_subiendo,
    ok,
    subiendo,
)


BED_LEVEL_BAJO = crear_subestado("BED_LEVEL_BAJO", bajo("bed_level"))
BED_LEVEL_NO_BAJO = crear_subestado("BED_LEVEL_NO_BAJO", no_bajo("bed_level"))

TORQUE_ALTO = crear_subestado("TORQUE_ALTO", alto("torque"))
TORQUE_NO_ALTO = crear_subestado("TORQUE_NO_ALTO", no_alto("torque"))

DENSIDAD_OK = crear_subestado("DENSIDAD_OK", ok("densidad"))
DENSIDAD_SUBIENDO = crear_subestado("DENSIDAD_SUBIENDO", subiendo("densidad"))
DENSIDAD_BAJANDO = crear_subestado("DENSIDAD_BAJANDO", bajando("densidad"))

ESTADO_1_BASE = crear_estado(
    "ESTADO_1_BASE",
    bajo("presion_diferencial"),
    no_subiendo("presion_diferencial"),
    alto("torque_bomba"),
    no_bajando("torque_bomba"),
    alto("potencia_bomba"),
    no_bajando("potencia_bomba"),
)

ESTADO_2_BASE = crear_estado(
    "ESTADO_2_BASE",
    alto("presion_descarga"),
    no_bajando("presion_descarga"),
)

ESTADO_3_BASE = crear_estado(
    "ESTADO_3_BASE",
    alto("nivel_rastra"),
    no_bajando("nivel_rastra"),
)

ESTADO_4_BASE = crear_estado(
    "ESTADO_4_BASE",
    alto("torque"),
    no_bajando("torque"),
)

ESTADO_5_BASE = crear_estado(
    "ESTADO_5_BASE",
    alto("bed_mass"),
    no_bajando("bed_mass"),
)

ESTADO_6_BASE = crear_estado(
    "ESTADO_6_BASE",
    cerca_bajo("presion_diferencial"),
    bajando("presion_diferencial"),
)

ESTADO_7_BASE = crear_estado(
    "ESTADO_7_BASE",
    cerca_alto("presion_descarga"),
    subiendo("presion_descarga"),
)

ESTADO_8_BASE = crear_estado(
    "ESTADO_8_BASE",
    cerca_alto("torque"),
    subiendo("torque"),
)

ESTADO_9_BASE = crear_estado(
    "ESTADO_9_BASE",
    cerca_alto("bed_mass"),
    subiendo("bed_mass"),
)

ESTADO_10A_BASE = crear_estado(
    "ESTADO_10A_BASE",
    alto("densidad"),
    ("__PERM_PERMITIR_SOLTAR_DESCARGA", "OFF"),
)

ESTADO_10B_BASE = crear_estado(
    "ESTADO_10B_BASE",
    bajo("densidad"),
    ("__PERM_PERMITIR_FRENAR_DESCARGA", "OFF"),
)

ESTADO_11_BASE = crear_estado(
    "ESTADO_11_BASE",
    bajo("bed_level"),
    ("__PERM_PERMITIR_SOLTAR_DESCARGA", "OFF"),
    ok("densidad"),
)


ESTADOS_ESPESADOR = {
    "BED_LEVEL_BAJO": BED_LEVEL_BAJO,
    "BED_LEVEL_NO_BAJO": BED_LEVEL_NO_BAJO,
    "TORQUE_ALTO": TORQUE_ALTO,
    "TORQUE_NO_ALTO": TORQUE_NO_ALTO,
    "DENSIDAD_OK": DENSIDAD_OK,
    "DENSIDAD_SUBIENDO": DENSIDAD_SUBIENDO,
    "DENSIDAD_BAJANDO": DENSIDAD_BAJANDO,
    "ESTADO_1_BASE": ESTADO_1_BASE,
    "ESTADO_2_BASE": ESTADO_2_BASE,
    "ESTADO_3_BASE": ESTADO_3_BASE,
    "ESTADO_4_BASE": ESTADO_4_BASE,
    "ESTADO_5_BASE": ESTADO_5_BASE,
    "ESTADO_6_BASE": ESTADO_6_BASE,
    "ESTADO_7_BASE": ESTADO_7_BASE,
    "ESTADO_8_BASE": ESTADO_8_BASE,
    "ESTADO_9_BASE": ESTADO_9_BASE,
    "ESTADO_10A_BASE": ESTADO_10A_BASE,
    "ESTADO_10B_BASE": ESTADO_10B_BASE,
    "ESTADO_11_BASE": ESTADO_11_BASE,
}
