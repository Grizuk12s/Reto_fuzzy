# -*- coding: utf-8 -*-
"""Reglas del sistema experto Espesador.

Cada regla se define por:
- `fuerza`: variable o conjunto de variables que aportan el belief
- `if`: condicion de activacion, construida con estados/subestados reutilizables
- `then`: acciones con waits declarativos
"""

from __future__ import annotations

from .estados_builder import (
    alto,
    bajo,
    crear_fuerza,
    crear_regla,
)
from .estados_espesador import (
    BED_LEVEL_BAJO,
    BED_LEVEL_NO_BAJO,
    DENSIDAD_BAJANDO,
    DENSIDAD_SUBIENDO,
    ESTADO_10A_BASE,
    ESTADO_10B_BASE,
    ESTADO_11_BASE,
    ESTADO_1_BASE,
    ESTADO_2_BASE,
    ESTADO_3_BASE,
    ESTADO_4_BASE,
    ESTADO_5_BASE,
    ESTADO_6_BASE,
    ESTADO_7_BASE,
    ESTADO_8_BASE,
    ESTADO_9_BASE,
    TORQUE_ALTO,
    TORQUE_NO_ALTO,
)
from .waits import accion_con_waits, usar_wait
from .waits_catalogo import (
    WAIT_FLOCULANTE_CRITICO,
    WAIT_FLOCULANTE_ESTABILIDAD,
    WAIT_TONELAJE_CRITICO,
    WAIT_TONELAJE_ESTABILIDAD,
    WAIT_VEL_BOMBA_CRITICO,
    WAIT_VEL_BOMBA_ESTABILIDAD,
)


def _accion(
    accion: str,
    wait_accion: dict,
    duracion_wait_s: float,
    reiniciar_waits: list[tuple[dict, float]] | None = None,
) -> dict:
    duracion_s = float(duracion_wait_s)

    return accion_con_waits(
        accion=accion,
        waits=[usar_wait(wait_accion, duracion_s)],
        reiniciar_waits=[
            usar_wait(wait_ref, wait_s)
            for wait_ref, wait_s in (reiniciar_waits or [])
        ],
    )


REGLAS_CRITICO: list[dict] = [
    crear_regla(
        regla_id="E1.S1",
        bloque="critico",
        priority=100.0,
        fuerza=crear_fuerza(bajo("presion_diferencial")),
        activacion=[ESTADO_1_BASE, BED_LEVEL_BAJO, TORQUE_ALTO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA_FUERTE", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("DISMINUIR_TONELAJE_FUERTE", WAIT_TONELAJE_CRITICO, 2700, reiniciar_waits=[(WAIT_TONELAJE_ESTABILIDAD, 2700)]),
        ],
    ),
    crear_regla(
        regla_id="E1.S2",
        bloque="critico",
        priority=100.0,
        fuerza=crear_fuerza(bajo("presion_diferencial")),
        activacion=[ESTADO_1_BASE, BED_LEVEL_BAJO, TORQUE_NO_ALTO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA_FUERTE", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("DISMINUIR_TONELAJE", WAIT_TONELAJE_CRITICO, 2700, reiniciar_waits=[(WAIT_TONELAJE_ESTABILIDAD, 2700)]),
        ],
    ),
    crear_regla(
        regla_id="E1.S3",
        bloque="critico",
        priority=100.0,
        fuerza=crear_fuerza(bajo("presion_diferencial")),
        activacion=[ESTADO_1_BASE, BED_LEVEL_NO_BAJO, TORQUE_ALTO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("DISMINUIR_TONELAJE_FUERTE", WAIT_TONELAJE_CRITICO, 2700, reiniciar_waits=[(WAIT_TONELAJE_ESTABILIDAD, 2700)]),
        ],
    ),
    crear_regla(
        regla_id="E1.S4",
        bloque="critico",
        priority=100.0,
        fuerza=crear_fuerza(bajo("presion_diferencial")),
        activacion=[ESTADO_1_BASE, BED_LEVEL_NO_BAJO, TORQUE_NO_ALTO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("DISMINUIR_TONELAJE", WAIT_TONELAJE_CRITICO, 2700, reiniciar_waits=[(WAIT_TONELAJE_ESTABILIDAD, 2700)]),
        ],
    ),
    crear_regla(
        regla_id="E2.S1",
        bloque="critico",
        priority=99.0,
        fuerza=crear_fuerza(alto("presion_descarga")),
        activacion=[ESTADO_2_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("DISMINUIR_TONELAJE", WAIT_TONELAJE_CRITICO, 2700, reiniciar_waits=[(WAIT_TONELAJE_ESTABILIDAD, 2700)]),
        ],
    ),
    crear_regla(
        regla_id="E2.S2",
        bloque="critico",
        priority=99.0,
        fuerza=crear_fuerza(alto("presion_descarga")),
        activacion=[ESTADO_2_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)])],
    ),
    crear_regla(
        regla_id="E3.S1",
        bloque="critico",
        priority=98.0,
        fuerza=crear_fuerza(alto("nivel_rastra")),
        activacion=[ESTADO_3_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("DISMINUIR_TONELAJE", WAIT_TONELAJE_CRITICO, 2700, reiniciar_waits=[(WAIT_TONELAJE_ESTABILIDAD, 2700)]),
        ],
    ),
    crear_regla(
        regla_id="E3.S2",
        bloque="critico",
        priority=98.0,
        fuerza=crear_fuerza(alto("nivel_rastra")),
        activacion=[ESTADO_3_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)])],
    ),
    crear_regla(
        regla_id="E4.S1",
        bloque="critico",
        priority=97.0,
        fuerza=crear_fuerza(alto("torque")),
        activacion=[ESTADO_4_BASE, BED_LEVEL_BAJO],
        then=[_accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)])],
    ),
    crear_regla(
        regla_id="E4.S2",
        bloque="critico",
        priority=97.0,
        fuerza=crear_fuerza(alto("torque")),
        activacion=[ESTADO_4_BASE, BED_LEVEL_NO_BAJO],
        then=[
            _accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("DISMINUIR_FLOCULANTE", WAIT_FLOCULANTE_CRITICO, 1800, reiniciar_waits=[(WAIT_FLOCULANTE_ESTABILIDAD, 1800)]),
        ],
    ),
    crear_regla(
        regla_id="E5.S1",
        bloque="critico",
        priority=96.0,
        fuerza=crear_fuerza(alto("bed_mass")),
        activacion=[ESTADO_5_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)]),
            _accion("AUMENTAR_FLOCULANTE_SUAVE", WAIT_FLOCULANTE_CRITICO, 1800, reiniciar_waits=[(WAIT_FLOCULANTE_ESTABILIDAD, 1800)]),
        ],
    ),
    crear_regla(
        regla_id="E5.S2",
        bloque="critico",
        priority=96.0,
        fuerza=crear_fuerza(alto("bed_mass")),
        activacion=[ESTADO_5_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_CRITICO, 900, reiniciar_waits=[(WAIT_VEL_BOMBA_ESTABILIDAD, 900)])],
    ),
]


REGLAS_ESTABILIDAD: list[dict] = [
    crear_regla(
        regla_id="E6.S1",
        bloque="estabilidad",
        priority=89.0,
        fuerza=crear_fuerza(bajo("presion_diferencial")),
        activacion=[ESTADO_6_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900),
            _accion("AUMENTAR_FLOCULANTE", WAIT_FLOCULANTE_ESTABILIDAD, 1800),
        ],
    ),
    crear_regla(
        regla_id="E6.S2",
        bloque="estabilidad",
        priority=89.0,
        fuerza=crear_fuerza(bajo("presion_diferencial")),
        activacion=[ESTADO_6_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900)],
    ),
    crear_regla(
        regla_id="E7.S1",
        bloque="estabilidad",
        priority=88.0,
        fuerza=crear_fuerza(alto("presion_descarga")),
        activacion=[ESTADO_7_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900),
            _accion("AUMENTAR_FLOCULANTE", WAIT_FLOCULANTE_ESTABILIDAD, 1800),
        ],
    ),
    crear_regla(
        regla_id="E7.S2",
        bloque="estabilidad",
        priority=88.0,
        fuerza=crear_fuerza(alto("presion_descarga")),
        activacion=[ESTADO_7_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900)],
    ),
    crear_regla(
        regla_id="E8.S1",
        bloque="estabilidad",
        priority=87.0,
        fuerza=crear_fuerza(alto("torque")),
        activacion=[ESTADO_8_BASE, BED_LEVEL_BAJO],
        then=[_accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900)],
    ),
    crear_regla(
        regla_id="E8.S2",
        bloque="estabilidad",
        priority=87.0,
        fuerza=crear_fuerza(alto("torque")),
        activacion=[ESTADO_8_BASE, BED_LEVEL_NO_BAJO],
        then=[
            _accion("DISMINUIR_FLOCULANTE", WAIT_FLOCULANTE_ESTABILIDAD, 1800),
            _accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900),
        ],
    ),
    crear_regla(
        regla_id="E9.S1",
        bloque="estabilidad",
        priority=86.0,
        fuerza=crear_fuerza(alto("bed_mass")),
        activacion=[ESTADO_9_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900),
            _accion("AUMENTAR_FLOCULANTE_SUAVE", WAIT_FLOCULANTE_ESTABILIDAD, 1800),
        ],
    ),
    crear_regla(
        regla_id="E9.S2",
        bloque="estabilidad",
        priority=86.0,
        fuerza=crear_fuerza(alto("bed_mass")),
        activacion=[ESTADO_9_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900)],
    ),
    crear_regla(
        regla_id="E10.A.S1",
        bloque="estabilidad",
        priority=85.0,
        fuerza=crear_fuerza(alto("densidad")),
        activacion=[ESTADO_10A_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900),
            _accion("DISMINUIR_FLOCULANTE_SUAVE", WAIT_FLOCULANTE_ESTABILIDAD, 1800),
        ],
    ),
    crear_regla(
        regla_id="E10.A.S2",
        bloque="estabilidad",
        priority=85.0,
        fuerza=crear_fuerza(alto("densidad")),
        activacion=[ESTADO_10A_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900)],
    ),
    crear_regla(
        regla_id="E10.B.S1",
        bloque="estabilidad",
        priority=85.0,
        fuerza=crear_fuerza(bajo("densidad")),
        activacion=[ESTADO_10B_BASE, BED_LEVEL_BAJO],
        then=[
            _accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900),
            _accion("AUMENTAR_FLOCULANTE_SUAVE", WAIT_FLOCULANTE_ESTABILIDAD, 1800),
        ],
    ),
    crear_regla(
        regla_id="E10.B.S2",
        bloque="estabilidad",
        priority=85.0,
        fuerza=crear_fuerza(bajo("densidad")),
        activacion=[ESTADO_10B_BASE, BED_LEVEL_NO_BAJO],
        then=[_accion("DISMINUIR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900)],
    ),
    crear_regla(
        regla_id="E11.S3",
        bloque="estabilidad",
        priority=84.0,
        fuerza=crear_fuerza(bajo("bed_level")),
        activacion=[ESTADO_11_BASE, DENSIDAD_BAJANDO],
        then=[
            _accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900),
            _accion("AUMENTAR_FLOCULANTE_SUAVE", WAIT_FLOCULANTE_ESTABILIDAD, 1800),
        ],
    ),
    crear_regla(
        regla_id="E11.S4",
        bloque="estabilidad",
        priority=84.0,
        fuerza=crear_fuerza(bajo("bed_level")),
        activacion=[ESTADO_11_BASE, DENSIDAD_SUBIENDO],
        then=[_accion("AUMENTAR_VEL_BOMBA", WAIT_VEL_BOMBA_ESTABILIDAD, 900)],
    ),
]


REGLAS_OPTIMIZACION: list[dict] = []


REGLAS_ESPESADOR: list[dict] = (
    REGLAS_CRITICO
    + REGLAS_ESTABILIDAD
    + REGLAS_OPTIMIZACION
)
