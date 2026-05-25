# Guia de sincronizacion segura entre `core` y `Prototipo_2`

## Principio de trabajo

- `core/` es la fuente principal de verdad para la logica reusable del sistema experto.
- `Prototipo_2/` conserva su propia capa de simulacion, UI y reglas en vivo.
- La sincronizacion debe ser selectiva. Copiar todo desde `core` rompe la mantenibilidad y puede desactivar funciones del prototipo.

## Clasificacion de archivos

### Archivos que se pueden sincronizar automaticamente

Estos archivos son espejos directos hoy y no contienen logica exclusiva del prototipo.

| Archivo en `Prototipo_2/` | Fuente canonica | Motivo |
|---|---|---|
| `config.py` | `core/config.py` | Contrato de datos comun al motor |
| `defuzzy_actions.py` | `core/defuzzy_actions.py` | Traduccion de acciones comun a ambos proyectos |
| `fuzzys_eval.py` | `core/fuzzys_eval.py` | Evaluacion fuzzy comun |
| `fuzzys_templates.py` | `core/fuzzys_templates.py` | Fabricas de modelos comunes |
| `motor.py` | `core/motor.py` | Motor de reglas comun |

Regla practica: si uno de estos archivos cambia en `core`, la copia en `Prototipo_2` debe actualizarse de forma prioritaria.

### Archivos que deben mantenerse separados

Estos archivos cumplen un rol propio del prototipo o del paquete `core`.

| Archivo | Decision | Motivo |
|---|---|---|
| `Prototipo_2/app.py` | Mantener separado | Implementa API REST, UI web y estado de simulacion en vivo |
| `Prototipo_2/simulacion.py` | Mantener separado | Define datos sinteticos y parametros de prueba que `core` no debe fijar |
| `Prototipo_2/reglas.json` | Mantener separado | Es la fuente activa de reglas del prototipo y ya divergio del fallback Python |
| `Prototipo_2/requirements.txt` | Mantener separado | Declara dependencias de ejecucion del prototipo, incluyendo Flask |
| `Prototipo_2/README.txt` | Mantener separado | Documenta el uso standalone |
| `Prototipo_2/proyecto_contexto.md` | Mantener separado | Resume el contexto funcional del prototipo |
| `core/__init__.py` | Mantener separado | Solo tiene sentido en el paquete `core` |

### Archivos que se auto-adaptan durante la sincronizacion

Estos archivos dependen del nucleo, pero el script ya sabe transformarlos para mantener el modo standalone de `Prototipo_2/`.

| Archivo en `Prototipo_2/` | Base en `core/` | Adaptacion requerida |
|---|---|---|
| `fuzzys_models_1A.py` | `core/fuzzys_models_1A.py` | Reescribir imports relativos a imports locales |
| `runner.py` | `core/runner.py` | Preservar soporte de `reglas.json`, `cargar_reglas_json()` y `usar_reglas_json` |

### Archivo que sigue bajo revision manual

Este archivo puede copiarse como fallback, pero requiere revisar la divergencia funcional con las reglas activas del prototipo.

| Archivo en `Prototipo_2/` | Base en `core/` | Revision requerida |
|---|---|---|
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

### Paso 3. Auto-adaptar los archivos sensibles

Aplicar despues la sincronizacion con adaptadores y revisar solo los casos que sigan siendo manuales:

- `fuzzys_models_1A.py`
  - el script cambia imports relativos por imports locales
  - validar que el archivo siga importando `fuzzys_templates` sin prefijo `.` si cambian los imports del nucleo

- `runner.py`
  - el script conserva `REGLAS_JSON_PATH`
  - el script conserva `cargar_reglas_json()`
  - el script conserva el parametro `usar_reglas_json`
  - validar el adaptador si el runner del nucleo cambia su firma o la carga de reglas

- `reglas_estrategia_correcta.py`
  - actualizar solo como fallback
  - comparar despues contra `reglas.json`
  - no asumir que sobreescribir el fallback actualiza el comportamiento real del prototipo

### Paso 4. Revisar la capa propia de `Prototipo_2`

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

1. Ejecutar `python Prototipo_2/simulacion.py`.
2. Ejecutar `python Prototipo_2/app.py`.
3. Verificar que `/api/meta` siga reflejando las variables y acciones esperadas.
4. Verificar que `/api/reglas` cargue `reglas.json` sin errores.
5. Revisar que la simulacion desde la UI siga produciendo eventos y setpoints finales.

## Riesgos al sobrescribir archivos sin filtro

- Sobrescribir `Prototipo_2/runner.py` con `core/runner.py` elimina la carga en vivo de `reglas.json`.
- Sobrescribir `Prototipo_2/fuzzys_models_1A.py` con la version de `core` sin adaptar imports rompe el modo standalone.
- Sobrescribir `Prototipo_2/reglas.json` descarta ajustes locales; hoy contiene 29 reglas y una regla extra con id `111`.
- Cambiar `core/config.py` o `core/defuzzy_actions.py` sin sincronizarlos hacia `Prototipo_2` puede dejar la UI y la validacion del API desalineadas.
- Cambiar el contrato del motor sin revisar `simulacion.py` puede generar datos incompatibles con el runner.

## Estrategia segura recomendada

La estrategia mas estable para el proyecto actual es una sincronizacion en tres capas.

## Script disponible

Existe un script en la raiz del workspace para automatizar la lista blanca y los archivos standalone adaptables:

- `sync_core_to_prototipo.py`

Por defecto, el script sincroniza contra `Prototipo_2/`. Si necesitas apuntar temporalmente a otra carpeta hermana del workspace, usa `--target-name`.

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
6. Apuntar a otro prototipo hermano si hace falta:
  `python sync_core_to_prototipo.py --target-name Prototipo_1 --check`

Comportamiento del script:

- Copia los archivos de auto-sync.
- Auto-adapta `runner.py` y `fuzzys_models_1A.py` a partir de `core/`.
- Reporta los archivos que aun requieren revision manual.
- Protege la capa standalone del prototipo y no la sobrescribe.
- Al aplicar cambios, guarda backups en `.sync_backups/`.
- Cuando se usa `--write-report`, genera un `.md` en `.sync_reports/`.
- La ruta de los reportes puede cambiarse con `--report-root`.

## Watcher disponible

Existe un watcher en la raiz del workspace para mantener `Prototipo_2/` sincronizado mientras editas `core/`:

- `watch_core_to_prototipo.py`

Uso recomendado:

1. Iniciar monitoreo continuo con sincronizacion inicial:
  `python watch_core_to_prototipo.py`
2. Generar reporte Markdown en cada sincronizacion:
  `python watch_core_to_prototipo.py --write-report`
3. Incluir tambien la regla fallback en la vigilancia para recibir alertas cuando cambie:
  `python watch_core_to_prototipo.py --include-manual-review`
4. Ejecutar solo la sincronizacion inicial y salir:
  `python watch_core_to_prototipo.py --run-once`

Comportamiento del watcher:

- Monitorea en `core/` los archivos auto-sync y auto-adaptados.
- Ejecuta `sync_core_to_prototipo.py --apply` cuando detecta cambios.
- Puede incluir `reglas_estrategia_correcta.py` en modo alerta con `--include-manual-review`.
- Si cambia `reglas_estrategia_correcta.py`, genera un reporte Markdown de revision manual en `.sync_reports/`.
- Si el cambio detectado es solo manual-review, no ejecuta `--apply` automaticamente.
- Puede dejarse corriendo en una terminal o lanzarse desde la tarea de VS Code.

### Capa 1. Sincronizacion automatica con lista blanca

Automatizar solo estos archivos:

- `config.py`
- `defuzzy_actions.py`
- `fuzzys_eval.py`
- `fuzzys_templates.py`
- `motor.py`

### Capa 2. Adaptacion guiada

Automatizar con adaptadores controlados estos archivos:

- `fuzzys_models_1A.py`
- `runner.py`

### Capa 3. Revision manual focalizada

Mantener una revision humana obligatoria para:

- `reglas_estrategia_correcta.py`

### Capa 4. Proteccion de la capa standalone

No sobrescribir automaticamente:

- `app.py`
- `simulacion.py`
- `reglas.json`

## Criterio operativo para futuras actualizaciones

Usar esta regla simple:

- Si el archivo define logica reusable del experto, `core` manda.
- Si el archivo define ejecucion standalone, simulacion, UI o edicion en vivo, `Prototipo_2` manda.
- Si el archivo conecta ambos mundos, la actualizacion debe pasar por revision manual.

Con la estructura actual, la opcion mas segura no es una copia completa, sino una sincronizacion selectiva con lista blanca, adaptadores controlados para `runner.py` y `fuzzys_models_1A.py`, y una revision manual pequena sobre las reglas.