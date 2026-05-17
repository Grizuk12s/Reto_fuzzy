# Guia de sincronizacion segura entre `core` y `Prototipo_1`

## Principio de trabajo

- `core/` es la fuente principal de verdad para la logica reusable del sistema experto.
- `Prototipo_1/` conserva su propia capa de simulacion, UI y reglas en vivo.
- La sincronizacion debe ser selectiva. Copiar todo desde `core` rompe la mantenibilidad y puede desactivar funciones del prototipo.

## Clasificacion de archivos

### Archivos que se pueden sincronizar automaticamente

Estos archivos son espejos directos hoy y no contienen logica exclusiva del prototipo.

| Archivo en `Prototipo_1/` | Fuente canonica | Motivo |
|---|---|---|
| `config.py` | `core/config.py` | Contrato de datos comun al motor |
| `defuzzy_actions.py` | `core/defuzzy_actions.py` | Traduccion de acciones comun a ambos proyectos |
| `fuzzys_eval.py` | `core/fuzzys_eval.py` | Evaluacion fuzzy comun |
| `fuzzys_templates.py` | `core/fuzzys_templates.py` | Fabricas de modelos comunes |
| `motor.py` | `core/motor.py` | Motor de reglas comun |

Regla practica: si uno de estos archivos cambia en `core`, la copia en `Prototipo_1` debe actualizarse de forma prioritaria.

### Archivos que deben mantenerse separados

Estos archivos cumplen un rol propio del prototipo o del paquete `core`.

| Archivo | Decision | Motivo |
|---|---|---|
| `Prototipo_1/app.py` | Mantener separado | Implementa API REST, UI web y estado de simulacion en vivo |
| `Prototipo_1/simulacion.py` | Mantener separado | Define datos sinteticos y parametros de prueba que `core` no debe fijar |
| `Prototipo_1/reglas.json` | Mantener separado | Es la fuente activa de reglas del prototipo y ya divergio del fallback Python |
| `Prototipo_1/requirements.txt` | Mantener separado | Declara dependencias de ejecucion del prototipo, incluyendo Flask |
| `Prototipo_1/README.txt` | Mantener separado | Documenta el uso standalone |
| `Prototipo_1/proyecto_contexto.md` | Mantener separado | Resume el contexto funcional del prototipo |
| `core/__init__.py` | Mantener separado | Solo tiene sentido en el paquete `core` |

### Archivos que requieren adaptacion manual o semiautomatica

Estos archivos si dependen del nucleo, pero no pueden copiarse sin revisar diferencias locales.

| Archivo en `Prototipo_1/` | Base en `core/` | Adaptacion requerida |
|---|---|---|
| `fuzzys_models_1A.py` | `core/fuzzys_models_1A.py` | Reescribir imports relativos a imports locales |
| `runner.py` | `core/runner.py` | Preservar soporte de `reglas.json`, `cargar_reglas_json()` y `usar_reglas_json` |
| `reglas_estrategia_correcta.py` | `core/reglas_estrategia_correcta.py` | Sincronizar como fallback, pero revisar en paralelo la migracion de `reglas.json` |

## Flujo de actualizacion recomendado

### Paso 1. Clasificar el cambio que llego a `core`

Antes de copiar nada, responder estas preguntas:

1. El cambio afecta solo calculo fuzzy, cooldowns o contrato comun?
2. El cambio agrega o elimina variables, etiquetas, acciones o familias?
3. El cambio toca reglas o solo cambia el motor?
4. El cambio altera la forma en que el runner carga o selecciona reglas?

### Paso 2. Sincronizar la lista segura

Actualizar primero los archivos de la lista automatica:

- `config.py`
- `defuzzy_actions.py`
- `fuzzys_eval.py`
- `fuzzys_templates.py`
- `motor.py`

Esto reduce divergencia sin tocar la capa standalone.

### Paso 3. Adaptar los archivos sensibles

Aplicar despues una revision dirigida sobre:

- `fuzzys_models_1A.py`
  - cambiar imports relativos por imports locales
  - verificar que el archivo siga importando `fuzzys_templates` sin prefijo `.`

- `runner.py`
  - conservar `REGLAS_JSON_PATH`
  - conservar `cargar_reglas_json()`
  - conservar el parametro `usar_reglas_json`
  - conservar la logica que prioriza `reglas.json` sobre el fallback Python

- `reglas_estrategia_correcta.py`
  - actualizar solo como fallback
  - comparar despues contra `reglas.json`
  - no asumir que sobreescribir el fallback actualiza el comportamiento real del prototipo

### Paso 4. Revisar la capa propia de `Prototipo_1`

Si el cambio en `core` introduce nuevas variables, acciones, etiquetas o campos de entrada, revisar manualmente:

- `app.py`
  - normalmente no requiere editar listas locales, porque consume metadatos desde `config.py` y `defuzzy_actions.py`
  - validar `GET /api/meta` despues de sincronizar la lista blanca
  - revisar la UI solo si cambia el comportamiento de validacion o aparecen nuevos meta flags

- `simulacion.py`
  - ajustar datos sinteticos
  - ajustar limites fuzzy y limites de setpoints
  - ajustar `META_FLAGS` si cambia el motor o el conjunto de reglas

- `reglas.json`
  - migrar reglas existentes
  - validar ids, condiciones, acciones y cooldowns

### Paso 5. Validar por partes

Validacion minima recomendada despues de cada sincronizacion:

1. Ejecutar `python Prototipo_1/simulacion.py`.
2. Ejecutar `python Prototipo_1/app.py`.
3. Verificar que `/api/meta` siga reflejando las variables y acciones esperadas.
4. Verificar que `/api/reglas` cargue `reglas.json` sin errores.
5. Revisar que la simulacion desde la UI siga produciendo eventos y setpoints finales.

## Riesgos al sobrescribir archivos sin filtro

- Sobrescribir `Prototipo_1/runner.py` con `core/runner.py` elimina la carga en vivo de `reglas.json`.
- Sobrescribir `Prototipo_1/fuzzys_models_1A.py` con la version de `core` sin adaptar imports rompe el modo standalone.
- Sobrescribir `Prototipo_1/reglas.json` descarta ajustes locales; hoy contiene 29 reglas y una regla extra con id `111`.
- Cambiar `core/config.py` o `core/defuzzy_actions.py` sin sincronizarlos hacia `Prototipo_1` puede dejar la UI y la validacion del API desalineadas.
- Cambiar el contrato del motor sin revisar `simulacion.py` puede generar datos incompatibles con el runner.

## Estrategia segura recomendada

La estrategia mas estable para el proyecto actual es una sincronizacion en tres capas.

## Script disponible

Existe un script en la raiz del workspace para automatizar la lista blanca:

- `sync_core_to_prototipo.py`

Uso recomendado:

1. Revisar cambios sin tocar archivos:
  `python sync_core_to_prototipo.py`
2. Verificar en automatizacion si la lista blanca esta desalineada:
  `python sync_core_to_prototipo.py --check`
3. Aplicar la sincronizacion segura:
  `python sync_core_to_prototipo.py --apply`
4. Generar un reporte Markdown de la ejecucion:
  `python sync_core_to_prototipo.py --write-report`
5. Aplicar cambios y dejar evidencia en reporte:
  `python sync_core_to_prototipo.py --apply --write-report`

Comportamiento del script:

- Solo copia los archivos de auto-sync.
- Reporta los archivos que requieren revision manual.
- Protege la capa standalone del prototipo y no la sobrescribe.
- Al aplicar cambios, guarda backups en `.sync_backups/`.
- Cuando se usa `--write-report`, genera un `.md` en `.sync_reports/`.
- La ruta de los reportes puede cambiarse con `--report-root`.

### Capa 1. Sincronizacion automatica con lista blanca

Automatizar solo estos archivos:

- `config.py`
- `defuzzy_actions.py`
- `fuzzys_eval.py`
- `fuzzys_templates.py`
- `motor.py`

### Capa 2. Adaptacion guiada

Mantener una revision humana obligatoria para:

- `fuzzys_models_1A.py`
- `runner.py`
- `reglas_estrategia_correcta.py`

### Capa 3. Proteccion de la capa standalone

No sobrescribir automaticamente:

- `app.py`
- `simulacion.py`
- `reglas.json`

## Criterio operativo para futuras actualizaciones

Usar esta regla simple:

- Si el archivo define logica reusable del experto, `core` manda.
- Si el archivo define ejecucion standalone, simulacion, UI o edicion en vivo, `Prototipo_1` manda.
- Si el archivo conecta ambos mundos, la actualizacion debe pasar por revision manual.

Con la estructura actual, la opcion mas segura no es una copia completa, sino una sincronizacion selectiva con lista blanca y una revision manual pequena sobre `runner.py`, `fuzzys_models_1A.py` y las reglas.