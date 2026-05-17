# Arquitectura y relacion entre `core` y `Prototipo_1`

## Resumen

- `core/` es la fuente principal de verdad para la logica reusable del sistema experto.
- `Prototipo_1/` es una adaptacion standalone que replica parte del nucleo y le agrega simulacion, API REST y UI Flask.
- `Prototipo_1` no importa `core` en tiempo de ejecucion. La dependencia real entre ambos proyectos es de mantenimiento y sincronizacion de archivos, no de import directo.

## Proposito de cada carpeta importante

| Carpeta | Proposito | Observaciones |
|---|---|---|
| `core/` | Nucleo reusable del sistema experto difuso. | Es un paquete Python; por eso incluye `__init__.py` y usa imports relativos. |
| `Prototipo_1/` | Prototipo standalone para simulacion, edicion de reglas y pruebas manuales. | Duplica varios modulos del nucleo para poder ejecutarse sin depender del paquete `core`. |
| `core/__pycache__/` | Cache de Python. | No debe participar en sincronizaciones ni en revisiones de arquitectura. |
| `Prototipo_1/__pycache__/` | Cache de Python. | No debe participar en sincronizaciones ni en revisiones de arquitectura. |

## Diferencias estructurales entre ambos proyectos

| Tema | `core/` | `Prototipo_1/` |
|---|---|---|
| Tipo de proyecto | Paquete Python reusable | Proyecto standalone |
| Estilo de imports | Relativos: `from .modulo import ...` | Directos: `from modulo import ...` |
| Fuente activa de reglas | `reglas_estrategia_correcta.py` | `reglas.json` en ejecucion normal; `reglas_estrategia_correcta.py` queda como fallback |
| Parametros operacionales | Se inyectan desde fuera | Se fijan dentro de `simulacion.py` |
| Interfaz web | No tiene | `app.py` expone API REST y UI |
| Dependencias declaradas | No hay archivo de dependencias local | `requirements.txt` agrega `flask` ademas de `numpy` y `pandas` |

## Relacion entre archivos compartidos

La siguiente tabla describe la dependencia de mantenimiento entre archivos homologos. No significa que `Prototipo_1` importe a `core` en runtime; significa que estos archivos existen en ambos lados y deben compararse cuando cambia el nucleo.

| Archivo en `Prototipo_1/` | Base en `core/` | Estado actual | Dependencia interna principal |
|---|---|---|---|
| `config.py` | `core/config.py` | Espejo directo | Consumido por `runner.py` |
| `defuzzy_actions.py` | `core/defuzzy_actions.py` | Espejo directo | Consumido por `runner.py` |
| `fuzzys_eval.py` | `core/fuzzys_eval.py` | Espejo directo | Consumido por `runner.py` |
| `fuzzys_templates.py` | `core/fuzzys_templates.py` | Espejo directo | Consumido por `fuzzys_models_1A.py` |
| `motor.py` | `core/motor.py` | Espejo directo | Consumido por `runner.py` |
| `fuzzys_models_1A.py` | `core/fuzzys_models_1A.py` | Casi espejo | Solo cambia el estilo de import y comentarios de cabecera |
| `reglas_estrategia_correcta.py` | `core/reglas_estrategia_correcta.py` | Espejo directo hoy | Solo fallback; no es la fuente activa de reglas del prototipo |
| `runner.py` | `core/runner.py` | Derivado | Agrega carga de `reglas.json` y firma propia para modo standalone |

## Dependencias internas de `Prototipo_1`

| Archivo | Depende de | Motivo |
|---|---|---|
| `fuzzys_models_1A.py` | `fuzzys_templates.py` | Define los modelos fuzzy concretos a partir de las fabricas del nucleo |
| `runner.py` | `config.py`, `motor.py`, `defuzzy_actions.py`, `fuzzys_eval.py`, `fuzzys_models_1A.py`, `reglas_estrategia_correcta.py`, `reglas.json` | Orquesta la evaluacion completa y resuelve reglas activas |
| `simulacion.py` | `runner.py` | Genera datos sinteticos y provee setpoints, limites y meta flags |
| `app.py` | `config.py`, `defuzzy_actions.py`, `runner.py`, `simulacion.py`, `reglas.json` | Expone UI/API, valida reglas con metadatos compartidos y ejecuta simulaciones en vivo |

## Dependencias criticas y puntos de ruptura

### 1. Mismatch de imports entre paquete y standalone

`core` usa imports relativos y `Prototipo_1` usa imports absolutos locales. Si se copia un archivo desde `core` sin adaptar sus imports, el prototipo puede romperse al iniciar.

Casos ya identificados:

- `fuzzys_models_1A.py`: en `core` usa `from . import fuzzys_templates`; en `Prototipo_1` usa `import fuzzys_templates`.
- `runner.py`: en `core` usa imports relativos; en `Prototipo_1` usa imports directos.

### 2. `runner.py` no es un espejo del nucleo

`Prototipo_1/runner.py` agrega comportamiento que no existe en `core/runner.py`:

- `REGLAS_JSON_PATH`
- `cargar_reglas_json()`
- parametro `usar_reglas_json`
- seleccion entre reglas explicitas, `reglas.json` o fallback Python

Sobrescribirlo directamente con la version de `core` eliminaria la edicion en vivo de reglas.

### 3. La UI depende de metadatos compartidos del nucleo cercano

`Prototipo_1/app.py` ya no mantiene listas locales de:

- variables validas
- etiquetas validas
- acciones validas

Ahora las importa desde `config.py` y `defuzzy_actions.py`, y usa esa misma fuente para validar payloads de reglas. Esto reduce el riesgo de desalineacion entre la UI y el motor cuando se sincroniza la lista blanca.

### 4. La simulacion contiene parametros que `core` evita fijar

`Prototipo_1/simulacion.py` contiene:

- `SETPOINTS_BASE`
- `LIMITES_SP`
- `LIMITES_FUZZY`
- `META_FLAGS`

Eso es intencional: `core` no fija esos valores. Si cambia el contrato de entrada del nucleo, hay que revisar manualmente la simulacion.

### 5. Las reglas activas del prototipo ya divergen del fallback Python

Hallazgos actuales:

- `core/reglas_estrategia_correcta.py`: 28 reglas.
- `Prototipo_1/reglas_estrategia_correcta.py`: 28 reglas.
- `Prototipo_1/reglas.json`: 29 reglas.
- `reglas.json` contiene una regla extra con id `111`.

Conclusion: el comportamiento real de `Prototipo_1` ya no debe asumirse como una copia exacta del fallback Python.

## Lectura recomendada para mantenimiento

1. Leer primero `core/__init__.py` para entender el alcance del nucleo.
2. Leer `Prototipo_1/proyecto_contexto.md` para entender el modo standalone.
3. Usar `GUIA_SINCRONIZACION_CORE_PROTOTIPO.md` como procedimiento operativo cuando cambie `core`.