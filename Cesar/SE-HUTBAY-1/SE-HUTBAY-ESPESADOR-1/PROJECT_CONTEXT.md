# PROJECT_CONTEXT.md
# Sistema Experto para Espesadores — SE-HUTBAY-ESPESADOR-1

> **Este archivo es la memoria técnica viva del proyecto.**
> Debe actualizarse después de cada iteración de refactorización.
> Es la fuente de verdad para entender la arquitectura, decisiones y estado actual.

---

## Objetivo del Proyecto

Sistema experto basado en lógica difusa para el **control automático del proceso de Espesadores** en planta minera. Evalúa variables de proceso en tiempo real, aplica reglas difusas y genera recomendaciones de ajuste de setpoints.

Diseñado como plataforma reutilizable para múltiples procesos industriales (Espesadores, Molinos, Flotación, Chancado, etc.).

### Modo de operación
- Sugiere ajustes de setpoint; **no actúa directamente sobre actuadores**
- Frecuencia de evaluación: cada muestra temporal (configurable, típicamente 60 s)
- Interfaz web Flask para edición en vivo de reglas, fuzzy, filtros y permisivos
- Conexión a KEPserver vía OPC-UA para lectura/escritura de tags

---

## Estado Actual del Proyecto

| Campo | Valor |
|---|---|
| Versión | **v1.10 — Refactoring completo (IT-2 → IT-10)** |
| Proceso implementado | Espesadores |
| Origen | Copiado desde `Base-Prototipo_2` (solo lectura, referencia) |
| Arquitectura | core/ + processes/ + connectors/ + config/espesador/ + web/api/ |
| Próxima iteración | Sin iteraciones pendientes — proyecto estable |

---

## Arquitectura Actual (v1.10 — Final)

### Estructura de carpetas

```
SE-HUTBAY-ESPESADOR-1/
│
├── app.py                        ← Factory Flask (616 líneas) — punto de entrada
│                                    Registra 4 blueprints + CHARTS_PAGE + _startup_checks()
│
├── requirements.txt              ← numpy>=1.24, pandas>=2.0, flask>=3.0, opcua>=0.98
├── runner.py                     ← Pipeline parametrizado para ejecución por consola (IT-4)
├── simulacion.py                 ← Generador de datos sintéticos (sin Flask)
│
├── [shims de compatibilidad — raíz]
│   ├── config.py                 ← Contrato: PVs, SPs, BLOQUES, límites del Espesador
│   ├── motor.py                  ← shim → core.engine.motor + BLOQUES de config (IT-4)
│   ├── defuzzy_actions.py        ← shim → core.engine.defuzzy + defuzzy_tables (IT-3)
│   ├── permisivos.py             ← shim → core.engine.permisivos + permisivos_config (IT-3)
│   ├── calculos_variables.py     ← shim → processes.espesador.variables (IT-5)
│   ├── fun_calc_variables.py     ← shim → core.variables.calculator + espesador.variables (IT-5)
│   ├── variables_calculadas.py   ← API pública estable de variables (IT-5)
│   ├── fuzzys_models_espesador.py← FUZZY_MODELOS, PEND_MODELOS calibrados
│   ├── reglas_espesador.py       ← Reglas declarativas del Espesador
│   ├── estados_espesador.py      ← Catálogo de estados y subestados
│   ├── waits_catalogo.py         ← 8 waits específicos del proceso
│   └── waits.py                  ← Helpers de waits
│
├── config/                       ← Configuración en vivo (IT-9)
│   └── espesador/
│       ├── reglas.json           ← Reglas del motor difuso (fuente de verdad en vivo)
│       ├── filtros.json          ← Parámetros filtro Exp-Q
│       ├── defuzzy.json          ← Tablas de defuzzificación Sugeno
│       ├── fuzzy.json            ← Funciones de membresía
│       ├── variables.json        ← Variables crudas y calculadas
│       ├── permisivos.json       ← Condiciones de permisivos
│       ├── tags.json             ← Tags KEPserver configurados
│       ├── estados.json          ← Estados y subestados del proceso
│       └── waits.json            ← Catálogo de waits (cooldowns)
│
├── connectors/                   ← Adaptadores de sistemas externos (IT-8)
│   ├── __init__.py
│   └── kepserver.py              ← Toda la lógica OPC-UA; imports lazy de `opcua`
│
├── core/                         ← Módulos 100% genéricos (sin datos de proceso)
│   ├── engine/
│   │   ├── motor.py              ← Motor de reglas con `bloques` como parámetro (IT-4)
│   │   ├── defuzzy.py            ← Lógica Sugeno paramétrica (IT-3)
│   │   └── permisivos.py         ← Evaluación de permisivos paramétrica (IT-3)
│   ├── fuzzy/
│   │   ├── templates.py          ← Factory de clases fuzzy (IT-2)
│   │   └── evaluator.py          ← Evaluación, pendientes, etiquetas compuestas (IT-2)
│   ├── filters/
│   │   └── exp_q.py              ← Filtro Exponencial-Q configurable (IT-2)
│   ├── states/
│   │   └── builder.py            ← DSL declarativo de estados y subestados (IT-2)
│   └── variables/
│       └── calculator.py         ← calcular_variables_df, detectar_dt_s (IT-5)
│
├── processes/                    ← Datos calibrados específicos del proceso
│   └── espesador/
│       ├── defuzzy_tables.py     ← Tablas Sugeno calibradas (IT-3)
│       ├── permisivos_config.py  ← PERMISIVOS del Espesador (IT-3)
│       └── variables.py          ← VARIABLES_CRUDAS + DEFINICIONES_CALCULADAS (IT-5)
│
└── web/                          ← Capa Flask
    ├── __init__.py
    ├── state.py                  ← Estado compartido; sin imports de Flask ni de api/ (IT-7)
    │                                AlertCollector, catálogos, helpers JSON, TagGenerator,
    │                                SEEngine, _sim_state, templates HTML, _startup_checks()
    ├── templates/
    │   ├── index.html            ← Editor de reglas + simulación (IT-6)
    │   ├── diagrama.html         ← Diagrama de flujo del SE (IT-6)
    │   └── entrada.html          ← Entrada de datos / tags live (IT-6)
    └── api/
        ├── __init__.py
        ├── config.py             ← bp_config: CRUD de reglas, filtros, defuzzy, fuzzy,
        │                            variables, permisivos, estados, waits (1030 líneas)
        ├── tags.py               ← bp_tags: tags CRUD, lectura KEPserver, generador
        ├── se.py                 ← bp_se: alertas, SE engine, entrada, simulación streaming
        └── views.py              ← bp_views: /, /graficos, /diagrama, /entrada
```

---

## Módulos en `core/` (genéricos, sin datos de proceso)

| Módulo | Responsabilidad |
|---|---|
| `core.engine.motor` | Motor de reglas con jerarquía de bloques paramétrica |
| `core.engine.defuzzy` | Parsear/evaluar acciones Sugeno; tablas como parámetro |
| `core.engine.permisivos` | Evaluar permisivos; config como parámetro |
| `core.fuzzy.templates` | Factory de clases fuzzy (Low/High/Norm/Pendiente/Varianza) |
| `core.fuzzy.evaluator` | Evaluación de membresías, pendientes, etiquetas compuestas |
| `core.filters.exp_q` | Filtro Exponencial-Q configurable por variable |
| `core.states.builder` | DSL declarativo para condiciones, estados y subestados |
| `core.variables.calculator` | `calcular_variables_df()`, `detectar_dt_s()`, `calcular_variable()` |

## Módulos en `processes/espesador/`

| Módulo | Contenido |
|---|---|
| `processes.espesador.defuzzy_tables` | Tablas Sugeno calibradas + `DEFUZZY_POR_FAMILIA_DEFAULT` |
| `processes.espesador.permisivos_config` | `PERMISIVOS` — 4 permisivos operacionales |
| `processes.espesador.variables` | `VARIABLES_CRUDAS` + `DEFINICIONES_CALCULADAS` |

## Shims en raíz

| Shim | Combina | Comportamiento especial |
|---|---|---|
| `motor.py` | `core.engine.motor` | Inyecta `BLOQUES` de config; acepta `bloques=` override |
| `defuzzy_actions.py` | `core.engine.defuzzy` + `defuzzy_tables` | `DEFUZZY_POR_FAMILIA` mutable para monkey-patch |
| `permisivos.py` | `core.engine.permisivos` + `permisivos_config` | Re-exporta `PERMISIVOS` |
| `calculos_variables.py` | `espesador.variables` | Solo re-export de datos |
| `fun_calc_variables.py` | `core.variables.calculator` + `espesador.variables` | Re-export completo |
| `variables_calculadas.py` | Ambos anteriores | API pública estable para el resto del proyecto |

---

## Variables del Proceso Espesador

### Variables PV (se fuzzifican)

| Variable | Descripción | Tipo Fuzzy | Calibración |
|---|---|---|---|
| `torque` | Torque Espesador (%) | High | ✅ Calibrado |
| `bed_mass` | Bed Mass | High | ✅ Calibrado |
| `bed_level` | Bed Level (mts) | Norm | ✅ Calibrado |
| `densidad` | Densidad Descarga (%) | Low | ✅ Calibrado |
| `torque_bomba` | Torque Bomba (%) | High | ⬜ Placeholder |
| `potencia_bomba` | Potencia Bomba (kW) | High | ⬜ Placeholder |
| `presion_descarga` | Presión Descarga | High | ⬜ Placeholder |
| `presion_diferencial` | Presión Diferencial (imp - sello) | High | ⬜ Placeholder |
| `nivel_rastra` | Nivel Rastra (%) | High | ⬜ Placeholder |

### Variables Crudas (sensores)

`tonelaje_sag_1`, `tonelaje_sag_2`, `tonelaje_relave`, `presion_bomba_1`, `presion_bomba_2`, `turbiedad_agua`

### Variables Calculadas (derivadas automáticamente)

| Variable | Tipo | Descripción |
|---|---|---|
| `tonelaje_sag_total` | aritmetica (suma) | SAG1 + SAG2 |
| `tonelaje_sag_delta_30min` | rolling_delta | Cambio de tonelaje SAG en 30 min |
| `tonelaje_sag_desv_est_30min` | rolling_std | Variabilidad SAG en 30 min |
| `diferencial_ton_sag_relave` | aritmetica (resta) | SAG total - relave |
| `diferencial_presion_bbas` | aritmetica (resta) | bomba_1 - bomba_2 |

### Setpoints Controlados

| Clave | Descripción | Cooldown base |
|---|---|---|
| `sp_tonelaje` | Setpoint tonelaje (t/h) | 45 min |
| `sp_floculante` | Setpoint flujo floculante (g/t) | 30 min |
| `sp_vel_bomba` | Setpoint velocidad bomba descarga (%) | 15 min |

---

## Pipeline de Ejecución (por muestra)

```
Row del DataFrame
    |
    +-- Paso 0: Calcular variables derivadas
    |           [core.variables.calculator via variables_calculadas]
    |
    +-- Paso 1: Extraer PV crudas -> dict {var: float}
    |
    +-- Paso 2: Filtrar con Exp-Q [core.filters.exp_q]
    |
    +-- Paso 3: Fuzzificar + pendientes [core.fuzzy.evaluator + fuzzy_modelos]
    |
    +-- Paso 4: Expandir etiquetas compuestas (NO-X, CERCA_ALTO, CERCA_BAJO)
    |
    +-- Paso 5: Evaluar permisivos [core.engine.permisivos + permisivos_config]
    |
    +-- Paso 6: Motor de reglas [core.engine.motor via motor.py shim]
    |
    +-- Paso 7: Aplicar acciones Sugeno [core.engine.defuzzy via defuzzy_actions.py]
```

---

## Jerarquía de Bloques del Motor

| Bloque | Level | Independiente |
|---|---|---|
| `critico` | 1 | No — bloquea inferiores si dispara |
| `estabilidad` | 2 | No |
| `optimizacion` | 99 | Sí — siempre evalúa |

---

## Naming de Acciones

```
{DIRECCION}_{FAMILIA}[_{INTENSIDAD}]
  DIRECCION  : AUMENTAR | DISMINUIR
  FAMILIA    : VEL_BOMBA | TONELAJE | FLOCULANTE
  INTENSIDAD : FUERTE | (vacío = Normal) | SUAVE
```

---

## Permisivos Operacionales

| Permisivo | Descripción |
|---|---|
| `PERMITIR_FRENAR_DESCARGA` | ON cuando no hay alertas de frenado |
| `PERMITIR_SOLTAR_DESCARGA` | ON cuando no hay alertas de descarga |
| `OPTIMIZAR_SUBIR_OBJETIVO_DENSIDAD` | ON cuando condiciones permiten subir objetivo |
| `OPTIMIZAR_BAJAR_OBJETIVO_DENSIDAD` | ON cuando condiciones requieren bajar objetivo |

---

## Convenciones

- Imports directos (no relativos) desde la raíz: `import motor`, `from config import ...`
- Módulos core por ruta completa: `from core.engine.motor import ...`
- Variables `snake_case`; etiquetas y acciones `MAYUSCULAS`
- `reglas.json` es la fuente de verdad en vivo; Python es el fallback
- `DEFUZZY_POR_FAMILIA` en `defuzzy_actions.py` es mutable (monkey-patch en vivo)
- Los waits no tienen duración en el catálogo; la duración la asigna cada regla

---

## Problemas Conocidos

| # | Problema | Severidad | Estado |
|---|---|---|---|
| P1 | `app.py` monolito 2900 líneas con HTML/CSS/JS inline | Alta | ✅ Resuelto — IT-6 (templates) + IT-7 (Blueprints) |
| P2 | Archivos legacy de Molinos en la raíz | Baja | ✅ Resuelto — IT-10 (eliminados) |
| P3 | JSONs de configuración mezclados con código en raíz | Baja | ✅ Resuelto — IT-9 (config/espesador/) |
| P4 | OPC-UA mezclado con lógica de negocio | Media | ✅ Resuelto — IT-8 (connectors/kepserver.py) |

---

## Roadmap de Iteraciones

| Iteración | Descripción | Estado |
|---|---|---|
| **v1.0** | Copia funcional del prototipo original | ✅ COMPLETADO |
| **IT-2** | Mover módulos genéricos a `core/` | ✅ COMPLETADO |
| **IT-3** | Separar engine de datos en defuzzy y permisivos | ✅ COMPLETADO |
| **IT-4** | Desacoplar `runner.py` y `motor.py` de Espesador | ✅ COMPLETADO |
| **IT-5** | Consolidar archivos de variables (3 → 2 capas) | ✅ COMPLETADO |
| **IT-6** | Extraer HTML/CSS/JS de app.py a web/templates/ | ✅ COMPLETADO |
| **IT-7** | Fragmentar app.py en Blueprints Flask | ✅ COMPLETADO |
| **IT-8** | Extraer KEPserver a `connectors/kepserver.py` | ✅ COMPLETADO |
| **IT-9** | Mover JSONs a `config/espesador/` | ✅ COMPLETADO |
| **IT-10** | Eliminar archivos legacy (Molinos) | ✅ COMPLETADO |

---

## Cómo Ejecutar

Ver `COMO_EJECUTAR.txt` para el flujo completo con `.venv`.

```bash
# Primera vez
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt

# Lanzar la app
python app.py
# -> http://127.0.0.1:5000

# Verificar imports sin lanzar el servidor
python -c "import app; print('OK —', len(list(app.app.url_map.iter_rules())), 'rutas')"

# Solo pipeline por consola (sin Flask)
python runner.py
```

---

## Decisiones Arquitectónicas Registradas

| ID | Decisión | Motivo |
|---|---|---|
| DA-1 | `Base-Prototipo_2` es solo lectura | Mantener referencia intacta para rollback |
| DA-2 | Iniciar con copia funcional antes de refactorizar | Cada iteración parte de código que funciona |
| DA-3 | Jerarquía de bloques (critico → estabilidad → optimizacion) | El más crítico que dispara bloquea inferiores en ese tick |
| DA-4 | Waits declarativos sin duración en el catálogo | La regla decide la duración; el catálogo define identidad |
| DA-5 | JSON como fuente de verdad en vivo; Python como fallback | Permite edición sin reiniciar el proceso |
| DA-6 | Monkey-patch de modelos fuzzy al cargar fuzzy.json | Permite actualizar membresías en caliente desde la UI |
| DA-7 | Alias de import para call sites de fuzzys_templates | Preserva call sites sin modificarlos |
| DA-8 | `waits.py` permanece en raíz | Depende de `waits_catalogo.py` (Espesador-específico) |
| DA-9 | `motor.py` shim inyecta BLOQUES por defecto | Call sites existentes no requieren cambio |
| DA-10 | `DEFUZZY_POR_FAMILIA` mutable en shim `defuzzy_actions.py` | `app.py` hace `.clear()` + `.update()` en vivo |
| DA-11 | Funciones de `core.engine.defuzzy` reciben tabla como parámetro | Evita estado global en el core |
| DA-12 | Shims en raíz para todos los módulos refactorizados | Cero cambios en call sites de app.py, runner.py, etc. |
| DA-13 | Lazy imports en runner.py para módulos Espesador | Permite instanciar el pipeline con modelos de otro proceso |
| DA-14 | `calcular_variables_df` recibe `definiciones` como parámetro | El core no hardcodea ninguna definición de proceso |
| DA-15 | `variables_calculadas.py` como API pública estable | Punto de importación único para todo el proyecto; no cambia aunque muevan las fuentes |

---

| DA-16 | Templates HTML cargados desde archivo en vez de inline | app.py pasa de 6665 a 3124 líneas; los .replace() de las rutas no cambian |

*Última actualización: v1.10 — Refactoring completo IT-2 → IT-10 (2026-07-20)*
*Proceso: Espesadores*
*Referencia original: `Base-Prototipo_2/` (solo lectura — NO modificar)*

---

## IT-7 — Fragmentar app.py en Flask Blueprints (v1.6)

**Objetivo:** Separar las 3124 líneas de app.py en módulos por responsabilidad.

### Estructura creada

```
web/
├── state.py              ← Estado compartido (918 líneas)
│   - AlertCollector + _alerts
│   - Catálogos (VARIABLES_DISPONIBLES, ACCIONES_DISPONIBLES, etc.)
│   - Helpers JSON cross-blueprint (_load_estados, _load_waits, _definiciones_lista_a_dict)
│   - Tag helpers (KEPserver, history, _tres_fases_valor)
│   - GENERATOR_DEFAULTS + TagGenerator + _tag_generator
│   - SEEngine + _se_engine
│   - _sim_state (estado streaming)
│   - Templates HTML (HTML_PAGE, DIAGRAM_PAGE, ENTRADA_PAGE, CHART_VARS)
│   - _startup_checks()
└── api/
    ├── __init__.py
    ├── config.py         ← Blueprint bp_config (meta + reglas + filtros + defuzzy + fuzzy
    │                       + variables + permisivos + estados + waits) — 1030 líneas
    ├── tags.py           ← Blueprint bp_tags (tags CRUD + simulation mode + generator) — 120 líneas
    ├── se.py             ← Blueprint bp_se (alerts + SE engine + entrada + simulación) — 200 líneas
    └── views.py          ← Blueprint bp_views (/, /graficos, /diagrama, /entrada) — 55 líneas

app.py → 616 líneas (factory: imports, CHARTS_PAGE inline, register_blueprint x4, _startup_checks)
```

### Decisiones arquitectónicas

**DA-17 — web/state.py como módulo de estado puro (sin Flask):**
No importa nada de Flask ni de web/api/*.py. Contiene clases, instancias y helpers
compartidos. Garantiza que ningún blueprint importe de otro blueprint (sin deps cruzadas).

**DA-18 — CHARTS_PAGE inline en app.py:**
El bloque HTML de gráficos permanece inline en app.py (como en IT-6) y se expone via
`app.charts_page`. Se extraerá a web/templates/ en una iteración futura.

**DA-19 — Helpers de estados y waits en state.py:**
`_load_estados/_save_estados` y `_load_waits/_save_waits` se movieron a state.py porque
tanto `web/api/config.py` (rutas CRUD) como `web/api/views.py` (ruta `/`) los necesitan.

**DA-20 — _definiciones_lista_a_dict en state.py:**
Usada tanto por la ruta `/api/variables/reset` (config.py) como por `_ejecutar_simulacion`
(se.py). Se centraliza en state.py para evitar importación cruzada entre blueprints.

### Verificación
- `python3 -c "import app"` → OK sin errores
- 62 rutas registradas (4 blueprints + static)
- Rutas verificadas: todas las del app.py original están presentes

---

## IT-8 — Extraer KEPserver a connectors/kepserver.py (v1.7)

**Objetivo:** Aislar toda la lógica OPC-UA en un solo módulo reutilizable.

### Estructura creada

```
connectors/
├── __init__.py
└── kepserver.py        ← API pública OPC-UA
    - URL (constante)
    - read_tags_batch(tag_names) -> dict[str, dict]
    - write_tag(tag_name, value, data_type) -> dict
    - write_float_batch(tag_values) -> None
    - enrich_tags(tags) -> list[dict]
    - check_connection() -> tuple[bool, str]
```

### Cambios en web/state.py
- Eliminados todos los bloques `from opcua import ...` inline
- `_read_kepserver_tags_batch` → delega a `_kep.read_tags_batch`
- `_try_write_kepserver_tag` → delega a `_kep.write_tag`
- `_enrich_tags_with_kepserver` → delega a `_kep.enrich_tags`
- `TagGenerator._write_tick` → usa `_kep.write_float_batch`
- `SEEngine._write_setpoints` → usa `_kep.write_float_batch`
- `_startup_checks` → usa `_kep.check_connection()`

### Decisiones arquitectónicas

**DA-21 — Wrapper de compatibilidad en web/state.py:**
Las funciones `_read_kepserver_tags_batch`, `_try_write_kepserver_tag` y
`_enrich_tags_with_kepserver` permanecen como delegados delgados en state.py
para no romper las importaciones existentes en web/api/tags.py y web/api/se.py.

**DA-22 — `opcua` como import lazy en connectors/kepserver.py:**
Todos los `from opcua import ...` son lazy (dentro de funciones), igual que antes.
Esto permite que el SE arranque sin el módulo `opcua` instalado; solo se alerta.

### Verificación
- grep `opcua` en todo el proyecto → solo en `connectors/kepserver.py`
- `import app` → OK, 62 rutas, 4 blueprints

---

## IT-9 — Mover JSONs a config/espesador/ (v1.8)

**Objetivo:** Sacar los JSONs de configuración de la raíz del proyecto.

### Cambios

```
Antes:  SE-HUTBAY-ESPESADOR-1/*.json  (9 archivos en la raíz)
Después: SE-HUTBAY-ESPESADOR-1/config/espesador/*.json
```

Archivos movidos: `reglas.json`, `filtros.json`, `defuzzy.json`, `fuzzy.json`,
`variables.json`, `permisivos.json`, `tags.json`, `estados.json`, `waits.json`.

### Archivos actualizados
- `web/state.py` — `_CFG_DIR = config/espesador/`, todas las constantes `*_JSON`
- `runner.py` — `_CFG_DIR` y las 6 constantes `*_JSON_PATH`

### Verificación
- `REGLAS_JSON` apunta a `config/espesador/reglas.json` y existe → True
- `import app` → OK, 62 rutas

---

## IT-10 — Eliminar archivos legacy (v1.9)

**Archivos eliminados:**
- `fuzzys_models_1A.py` (123 líneas) — modelos fuzzy hardcodeados de "fuzzys 1A Cuajone.xlsx", supersedidos por `fuzzys_models_espesador.py`
- `reglas_estrategia_correcta.py` (120 líneas) — estrategia hardcodeada, supersedida por `reglas_espesador.py` y `config/espesador/reglas.json`

**Verificación:** ningún archivo activo importaba estos módulos. `import app` → OK, 62 rutas.

---

## Estado final del refactoring (v1.9)

```
SE-HUTBAY-ESPESADOR-1/
├── app.py                      ← Factory Flask (616 líneas, IT-7)
├── config.py                   ← Shim: constantes del proceso
├── runner.py                   ← Motor de simulación (parametrizado, IT-4)
├── motor.py / waits.py / ...   ← Shims de compatibilidad
├── config/espesador/           ← JSONs de configuración (IT-9)
│   └── *.json (9 archivos)
├── connectors/                 ← Adaptadores externos (IT-8)
│   └── kepserver.py
├── core/                       ← Módulos genéricos reutilizables (IT-2 a IT-5)
│   ├── engine/defuzzy.py
│   ├── engine/motor.py
│   ├── engine/permisivos.py
│   ├── filters/exp_q.py
│   ├── fuzzy/
│   ├── states/
│   └── variables/calculator.py
├── processes/espesador/        ← Datos específicos del proceso (IT-3, IT-5)
│   ├── defuzzy_tables.py
│   ├── permisivos_config.py
│   └── variables.py
└── web/                        ← Capa web Flask (IT-6, IT-7)
    ├── state.py                ← Estado compartido
    ├── templates/              ← HTML extraído (IT-6)
    └── api/                    ← Blueprints (IT-7)
        ├── config.py
        ├── tags.py
        ├── se.py
        └── views.py
```
