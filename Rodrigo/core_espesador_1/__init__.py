"""Nucleo del sistema experto Espesador.

Modulos:
- config                  : variables canonicas, setpoints, cooldowns por familia
- fuzzys_templates        : factories Low/High/Norm/Pendiente (igual que v3)
- fuzzys_models_espesador : modelos fuzzy del Excel + placeholders (TODO calibrar)
- fuzzys_eval             : evaluacion, pendientes, etiquetas compuestas
- exp_q_filter            : filtro Exp-Q por variable (config_por_variable obligatoria)
- motor                   : reglas con OR/AND/NOT + jerarquia por bloque
- defuzzy_actions         : defuzzy estilo Sugeno con tablas (belief -> step)
- permisivos              : permisivos con OR/AND/NOT + permisivos del Excel
- reglas_espesador        : 12 estados agrupados por bloque (critico/estabilidad/optimizacion)
- runner                  : pipeline completo data -> SP
"""

from .exp_q_filter import ExpQFilter
from .permisivos import PERMISIVOS, evaluar_permisivos, inyectar_permisivos_en_fuzzy_out

__all__ = [
    "ExpQFilter",
    "PERMISIVOS",
    "evaluar_permisivos",
    "inyectar_permisivos_en_fuzzy_out",
]
