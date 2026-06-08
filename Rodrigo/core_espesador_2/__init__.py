"""Nucleo del sistema experto Espesador.

Modulos:
- config                  : variables canonicas, setpoints, waits base por variable
- fuzzys_templates        : factories Low/High/Norm/Pendiente (igual que v3)
- fuzzys_models_espesador : modelos fuzzy del Excel + placeholders (TODO calibrar)
- fuzzys_eval             : evaluacion, pendientes, etiquetas compuestas
- exp_q_filter            : filtro Exp-Q por variable (config_por_variable obligatoria)
- motor                   : reglas con OR/AND/NOT + jerarquia por bloque + waits
- waits_catalogo          : definicion libre de waits reutilizables sin duracion
- waits                   : asignacion de duracion y wiring de waits en reglas
- defuzzy_actions         : defuzzy estilo Sugeno con tablas (belief -> step)
- permisivos              : permisivos con OR/AND/NOT + permisivos del Excel
- reglas_espesador        : 12 estados agrupados por bloque (critico/estabilidad/optimizacion)
- runner                  : pipeline completo data -> SP
"""

from .exp_q_filter import ExpQFilter
from .permisivos import PERMISIVOS, evaluar_permisivos, inyectar_permisivos_en_fuzzy_out
from .waits_catalogo import (
    WAIT_FLOCULANTE_CRITICO,
    WAIT_FLOCULANTE_ESTABILIDAD,
    WAIT_TONELAJE_CRITICO,
    WAIT_TONELAJE_ESTABILIDAD,
    WAIT_VEL_BOMBA_CRITICO,
    WAIT_VEL_BOMBA_ESTABILIDAD,
    WAIT_VEL_PRESION_CAMA,
    WAIT_VEL_SOLIDOS,
    crear_wait,
    crear_wait_desde_accion,
)
from .waits import (
    accion_con_waits,
    accion_con_tipos_wait,
    usar_wait,
)

__all__ = [
    "ExpQFilter",
    "PERMISIVOS",
    "evaluar_permisivos",
    "inyectar_permisivos_en_fuzzy_out",
    "accion_con_waits",
    "accion_con_tipos_wait",
    "usar_wait",
    "crear_wait",
    "crear_wait_desde_accion",
    "WAIT_VEL_BOMBA_CRITICO",
    "WAIT_VEL_BOMBA_ESTABILIDAD",
    "WAIT_TONELAJE_CRITICO",
    "WAIT_TONELAJE_ESTABILIDAD",
    "WAIT_FLOCULANTE_CRITICO",
    "WAIT_FLOCULANTE_ESTABILIDAD",
    "WAIT_VEL_SOLIDOS",
    "WAIT_VEL_PRESION_CAMA",
]
