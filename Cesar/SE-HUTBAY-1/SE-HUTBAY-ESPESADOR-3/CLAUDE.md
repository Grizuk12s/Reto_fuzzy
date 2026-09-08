# Contexto del proyecto — SE HUTBAY Espesador

Archivo de continuidad. Léelo antes de tocar nada: explica qué es el proyecto, en qué
estado quedó y qué está pendiente.

**Guía operativa completa:** `PUESTA_EN_MARCHA.md` (export/import: sección 8)
**Backlog vivo de afinación:** `AFINACION_PENDIENTE.md` (qué se cerró, qué falta, con
reproducción y arreglo propuesto)
**Último cambio de interfaz:** `CAMBIOS_2026-09-08.md` (heartbeat con gráfico y
Explorador de Series operativo — endpoints nuevos, decisiones y verificación)

---

## Qué es

Sistema Experto difuso que lee variables de proceso desde **KEPserver vía OPC-UA**,
las evalúa con lógica fuzzy y escribe **setpoints** de vuelta al DCS. Interfaz web
Flask, empaquetado en Docker.

Nació como prototipo de **control de espesador** (9 PV: torque, bed_mass, bed_level,
densidad, etc.) y se está convirtiendo en un **producto estándar que se moldea a cada
cliente**. Ese es el hilo conductor de casi todo el trabajo reciente.

---

## Estado actual

### El contrato ya es el del cliente, no el del espesador

Se reemplazó la plantilla del espesador por el contrato de esta planta, recortado a lo
que está instrumentado. **Estado en disco al 2026-08-25** (verificado leyendo los
archivos, no de memoria):

| Archivo | Estado |
|---|---|
| `contrato.json` | **4 PV (`hopper_nvl_pv_a`, `hopper_nvl_pv_b`, `velocidad_pv`, `corriente`) + 1 SP (`velocidad_sp`) + `limites_sp` (`velocidad_sp: [50, 100]`)** |
| | **`rate_sp` no está declarado** → ese SP se escribe sin límite de velocidad. Es un valor de proceso: lo define el experto de planta |
| `tags.json` | 27 tags, **los 27 habilitados** |
| `variables.json` | `crudas: {}` y `definiciones: []` — desde 2026-08-21 el motor **sí** las calcula |
| `filtros.json` | 4 entradas, una por PV (`q=0.15`, `ventana_s=50.0`) |
| `fuzzy.json` | solo `hopper_nvl_pv_a` (`high`). `velocidad_pv` sin fuzzy **a propósito**: es readback. Desde 2026-09-03 cada fuzzy trae además su bloque `limites` con los **tags** de `lmin`/`lmax` |
| `pendientes.json` | vacío. Desde 2026-08-21 los fuzzy de pendiente son **configurables**, uno o varios por variable |
| `tracking.json` | 1 familia (`velocidad_sp`), con `pv_key: velocidad_pv` y `rango: 0.3` — **conectada al motor desde 2026-08-21** |
| `reglas.json` | 2 reglas de prueba (`A1_Prueba_Hopper_Lmita`, `A2_Prueba_Hopper_Lmita`) |
| `estados.json` | `{}`. Desde 2026-09-01 el default tambien es `{}`: ya no hay plantilla del espesador que sembrar |
| `waits.json` | 1 wait (`SP Velocidad A Hopper`) |
| `permisivos.json` | `{}`. Desde 2026-09-01 el catalogo de la interfaz sale de aca y no del dict del espesador |
| `defuzzy.json` | 1 familia (`velocidad_sp`), con su bloque `limites` (tags que acotan la escritura de ese SP) y su `limites_num` (respaldo numérico, migrado de `limites_sp`) |
| `kepserver.json` | `timeout_s: 2.0`, `aceptar_uncertain: false`, `estancado_alerta_s: 60`. **`retencion_s` no está en el archivo**: se aplica el default de 5,0 s |

> **"Vacío" es un estado válido.** Los loaders solo caen a la plantilla de Python si
> el archivo **falta o está corrupto**, no si está vacío. Desde 2026-08-20 tampoco hay
> plantilla de espesador que sembrar: `_defaults_defuzzy()` y `_defaults_tracking()`
> devuelven `{}`, y el catálogo de acciones sale de `defuzzy.json`.

### El mapeo está completo y el SE decide

El pipeline corre de punta a punta: lee los tags con `Good`, filtra, fuzzifica
`hopper_nvl_pv_a` en `HIGH` con belief 1.000, dispara `A1`, aplica el paso Sugeno y
escribe `velocidad_sp` al DCS. Verificado en la traza y en la simulación offline.

Dos cosas que conviene saber antes de mirar la pantalla y sacar conclusiones:

- **Los límites del hopper están adulterados a propósito** para que la regla dispare
  siempre, y el wait bajado a 10 s. Es un banco de pruebas, no una calibración.
- **`velocidad_sp` llega a 100.00 con límites 50–100 y se satura**: la regla sigue
  disparando y el paso se clipea a cero. El paso 8 de la traza lo dice explícitamente
  en vez de mostrar un delta de 0 sin explicación, y desde el 2026-08-21 ese disparo
  **ya no reinicia el wait** (ver *El wait cuenta solo si la regla actuó*).

### Qué falta para operar de verdad

No es cableado, es lógica de proceso: definir las reglas reales con el experto de
planta, sus permisivos, sus estados y la calibración del defuzzy. Y cerrar el backlog
de `AFINACION_PENDIENTE.md`, sobre todo lo marcado **Operativo**.

---

## Arquitectura

```
KEPserver (OPC-UA)
      │  lee PV / LIM
      ▼
SEEngine  (web/state.py)  ── hilo en background, CICLO LIBRE (piso configurable)
      │
      ├─ filtro Exp-Q          core/filters/exp_q.py   ← config de filtros.json
      ├─ variables calculadas  core/variables/online.py ← variables.json
      ├─ fuzzificación         core/fuzzy/evaluator.py + templates.py ← fuzzy.json
      ├─ pendientes            core/fuzzy/pendientes.py ← pendientes.json
      ├─ permisivos            core/engine/permisivos.py
      ├─ motor de reglas       core/engine/motor.py
      └─ defuzzificación       core/engine/defuzzy.py
      │
      ▼  escribe SP
KEPserver → DCS
```

> El paso de *variables calculadas* se incorporó el 2026-08-21. Hasta entonces solo
> existía en el runner de CSV. Ver *Las variables calculadas existen en vivo*.

### Mapa de archivos

| Ruta | Qué hay |
|---|---|
| `app.py` | Factory Flask, registra blueprints |
| `web/state.py` | `SEEngine`, generador, heartbeat, mapeo tag↔rol, traza |
| `web/api/tags.py` | CRUD de tags, generador, lectura en vivo, heartbeat |
| `web/api/config.py` | Filtros, fuzzy, defuzzy, tracking, variables, estados, waits, permisivos |
| `web/api/contrato.py` | Contrato de variables + límites de SP + análisis de impacto |
| `web/api/se.py` | Arranque/parada del motor, traza, alertas, grabadores por regla |
| `web/api/export_import.py` | Paquetes export/import, respaldos e historial de importaciones |
| `web/templates/export_import.html` | UI Export / Import (`/espesador/export-import`) |
| `core/` | Núcleo genérico, sin nada específico del espesador |
| `processes/espesador/` | Definiciones del proceso (plantilla) |
| `connectors/kepserver.py` | Toda la lógica OPC-UA + el pool de sesiones persistentes |
| `scripts/seed_tags_planta.py` | Siembra los tags PCS7 (corre en el arranque del contenedor) |
| `web/templates/historial.html` | Historial grabado de UNA regla (`/espesador/historial?regla=<id>`) |
| `core/jsonio.py` | Escritura atómica de los JSON (`.tmp` + `os.replace`) + lock por ruta |
| `tests/test_pipeline.py` | Red de seguridad del cableado, no de la lógica difusa |
| `tests/test_kepserver.py` | 23 tests. Sesión OPC-UA (reuso, reconexión, hilos) + calidad de dato |
| `tests/test_export_import.py` | 9 tests. Paquetes, merge, historial y undo de importación |

---

## Decisiones de diseño tomadas

### Tag ≠ variable: categoría + identificador

Un tag tiene **categoría** (PV, CRUDA, LIM, SP, OTRO) que se elige a mano. El
**identificador** de la variable se genera del pseudónimo y **queda congelado**: si
siguiera al pseudónimo, mejorar una etiqueta rompería en silencio todas las reglas
que la nombran.

### Degradar por partes, nunca adivinar (2026-08-21)

**El SE nunca decide sobre datos inventados, pero tampoco se planta entero.** Antes
`start()` se negaba a arrancar ante cualquier hueco de configuración. Como el catálogo
de roles sale del contrato, bastaba declarar una variable que todavía no estaba
instrumentada para dejar a la planta sin experto. Ahora cada falta degrada **solo lo
suyo**:

| Falta | Antes | Ahora |
|---|---|---|
| Rol sin tag asignado | no arranca | arranca; esa variable no entra al pipeline |
| PV sin filtro en `filtros.json` | no arranca | se siembra `FILTRO_NUEVO_DEFAULT` y se avisa |
| PV sin modelo difuso | avisa | igual (ya degradaba) |
| SP sin límites en `contrato.json` | no arranca | se **inhibe la escritura de ese SP** |
| SP cuyo valor no se pudo leer | no arranca | se inhibe **y se reintenta cada 5 s** |
| PV con calidad mala en un tick | **aborta el tick** | esa PV no se fuzzifica; el tick sigue |
| Límite con calidad mala | **aborta el tick** | su PV no se fuzzifica; el tick sigue |

Lo único que se conserva intacto es la regla de oro: **lo que no se pudo leer no se
rellena**. Una PV sin su límite no se fuzzifica contra una escala inventada — sale del
`fuzzy_out`, y las reglas que la nombran quedan `no_evaluable` con el motivo en la traza
(que es el mecanismo que el motor ya tenía). Un SP sin límites no se clipea, así que
**no se escribe**.

#### El criterio de alerta es "se usa aguas abajo"

`roles_en_uso()` (en `web/state.py`) responde qué roles consume realmente el pipeline,
leyendo los mismos JSON que edita la interfaz:

- **PV** — tiene modelo en `fuzzy.json`, la nombra una regla habilitada (directa o como
  `pend_<var>`), la nombra un permisivo, es argumento de una variable calculada, o es el
  readback de una familia de `tracking.json`.
- **LIM** — solo el bound que su tipo de fuzzy necesita: `high` usa `lmax`, `low` usa
  `lmin`, `norm` los dos. Pedir los dos siempre era pedir un tag que nadie iba a leer.
- **SP** — su familia tiene al menos una acción en `defuzzy.json` (el único enlace
  regla → setpoint que existe).
- **CRUDA** — la usa una variable calculada o un permisivo.

`construir_mapeo()` devuelve `faltantes` (cobertura completa del contrato, informativa)
y **`faltantes_en_uso`** (lo que falta *y* alguien consume). La pantalla de Mapeo y el
cartel de la traza destacan el segundo; mezclarlos entrenaba al operador a ignorar el
aviso. `listo` sigue significando "cobertura completa"; `listo_en_uso` es el que importa.

> `roles_en_uso()` lee los JSON con `_leer_json()`, **no** con los `cargar_*_json()` de
> `runner.py`: esos caen a la plantilla del espesador cuando el archivo falta, y aquí eso
> produciría avisos por variables de otra planta.

#### Lo que esto arrastró

- `_read_tags()` ya no devuelve `None` ni aborta: devuelve lo que sí llegó.
- `extraer_limites_fuzzy_desde_row()` y el `inputs_override` de `_evaluar_estado_fuzzy()`
  ahora toleran que falte una columna; antes reventaban con `KeyError` y se llevaban el
  tick completo.
- El tick recorta el registry difuso a las variables **fuzzificables en este tick**
  (valor + los límites que su tipo pide) y publica el resto en `tz["fuzzy_omitidas"]`.
- `_init_state()` pasa `pend_modelos={}` explícito: con `None`, `_evaluar_estado_fuzzy`
  importaba `PEND_MODELOS` del espesador y el motor en vivo terminaba con las pendientes
  de otra planta. Cierra el `or None` que estaba en el backlog (los modelos difusos ahora
  caen a `{}`, no a `FUZZY_MODELOS`).
- `FILTRO_NUEVO_DEFAULT` se mudó de `web/api/config.py` a `web/state.py`: lo usan el botón
  Sincronizar y el arranque del motor.
- `_write_setpoints()` respeta `self._sp_inhibidos` y lo reporta en
  `tz["escritura"]["inhibidos"]`.

### El wait cuenta solo si la regla actuó (2026-08-21)

**Un disparo que no movió ningún setpoint no arma su wait.** El wait existe para darle
tiempo al proceso a responder a un cambio; si no hubo cambio, no hay nada que esperar y
dejarlo corriendo silencia a la regla durante minutos por una acción que nunca ocurrió.

Pasa de verdad, y es la confusión más cara de la pantalla: SP pegado a su límite (el
clipeo se come el paso), tabla defuzzy que da 0 para ese belief, o `apply_actions_tabla`
que lanza. En los tres casos la regla se auto-bloqueaba sin haber hecho nada, y en la
traza se leía igual que un disparo efectivo.

#### Cómo está implementado: armar y revertir, no diferir

Los waits **se siguen armando en el momento del disparo**, dentro de
`_evaluar_set_reglas`. Es a propósito: así bloquean a las reglas que faltan evaluar en
ese mismo barrido, que es lo que impide que dos reglas se peleen el mismo SP. Diferir el
armado hasta después de aplicar habría roto esa propiedad.

Lo que se agregó es la vuelta atrás:

- `_activar_waits()` devuelve el estado **previo** de cada wait tocado, que viaja en el
  evento como `waits_previos`.
- `revertir_waits(evento, estado_waits)` (nuevo, en `core/engine/motor.py`) restaura ese
  estado exacto — o borra el wait si no existía. Un wait que ya venía corriendo por otra
  regla **conserva su cuenta original**; no se regala un wait limpio a nadie.
- Quien aplica las acciones decide: `SEEngine._run_tick` compara los SP contra un
  snapshot tomado **justo antes de esa acción** (`sp_prev`, no `sp_antes`: con dos reglas
  disparando en el mismo barrido, la segunda heredaría el delta de la primera) y llama a
  `revertir_waits` si nada se movió más que `SP_DEADBAND`.
- `runner.correr_prueba_general` hace lo mismo, para que simulación y producción no
  diverjan justo en el temporizador.

> **Alcance conocido:** revertir no le devuelve el turno a una regla que quedó bloqueada
> en *ese* tick por el wait espurio. Lo recupera en el siguiente, que en ciclo libre llega
> en décimas de segundo.

#### El caso "por tracking" — cerrado el mismo día

El estándar pide además que el wait no se reinicie cuando la regla no pudo actuar **por
tracking**. Quedó cubierto sin tocar el motor de waits: una familia retenida no mueve su
setpoint, y el mecanismo de arriba ya no arma el wait cuando el SP no se movió. Ver
*El tracking espera al proceso*.

#### Observabilidad

Cada disparo lleva ahora `movio_sp`, `delta_sp` y `waits_revertidos`. Se ven en el paso 7
de la traza (aviso "disparo sin efecto"), como badge **SIN EFECTO** en 7b · Últimos
disparos, y en el historial por regla — que es justamente la página que existe para
responder "¿por qué esta regla no actúa?".

### Las variables calculadas existen en vivo (2026-08-21)

**Una variable calculada es una PV más.** Se define en la página de Variables a partir
de una o más PV, el motor la calcula en **cada tick**, se le puede crear un fuzzy, y las
reglas y los permisivos la nombran igual que a cualquier medición.

#### Por qué un evaluador nuevo y no el de siempre

`core/variables/calculator.py` calcula sobre un DataFrame completo. Sirve para el runner
de CSV, pero no para el motor, que ve una muestra por vez. `core/variables/online.py` es
el equivalente incremental, y las tres diferencias son deliberadas:

| | Batch (`calculator.py`) | En vivo (`online.py`) |
|---|---|---|
| Ventana | `ventana_min` → nº de filas con un `dt_s` fijo | segundos de proceso, contra el `t_s` real |
| Fuente ausente | `KeyError` | la variable no se produce y se reporta en `omitidas` |
| Memoria | toda la serie | ≤ `MAX_MUESTRAS` (240) por variable, decimadas |

Lo de la ventana no es cosmético: el motor corre en ciclo libre, el período cambia tick
a tick, y una ventana contada en muestras significaría 30 min a una cadencia y 20 s a
otra — el mismo problema que ya había obligado a migrar el filtro Exp-Q de
`window_size` a `ventana_s`.

#### Decisiones

- **Se calculan sobre la PV FILTRADA.** Es lo que ve el resto del pipeline; una
  calculada no debería reaccionar a ruido que el filtro Exp-Q ya decidió ignorar. La
  contra conocida: un `rolling_std` sobre señal filtrada mide menos dispersión que sobre
  la cruda. Si hiciera falta medir el ruido, habría que hacer la fuente elegible por
  definición.
- **Una calculada NO se filtra.** Se calcula sobre entradas ya filtradas; volver a
  suavizarla sería filtrar dos veces. Por eso `entradas_fuzzificables()` (PV + calculadas)
  está separada de `entradas_pv_disponibles()` (solo PV), que es la que alimenta al
  sincronizador de filtros.
- **Los límites salen del tag si existe, del JSON si no.** Para fuzzificarla hacen falta
  `lmin`/`lmax`. `catalogo_roles()["lim"]` ofrece ahora `<calc>_lmin` / `<calc>_lmax`, así
  que se pueden cablear al DCS; y `variables.json` acepta un respaldo fijo por definición.
  **El tag manda**: si el límite de ingeniería cambia en el DCS, el SE lo sigue sin que
  nadie edite un JSON. Sin ninguno de los dos, la variable existe y sirve en un permisivo,
  pero no se fuzzifica (y la traza lo dice).
- **División por cero no produce la variable.** No es un error del operador, es un estado
  del proceso. Propagar un `NaN` daría pertenencias sin sentido en silencio.

#### Lo que esto arrastró

- **`VARIABLES_VALIDAS` dejó de poder ser una constante**, por el mismo motivo que
  `ETIQUETAS_DISPONIBLES`: era una foto al importar que ofrecía `VARIABLES_EXTERNAS` — la
  lista de calculadas **hardcodeada del espesador**. O sea, se ofrecían cinco variables de
  otra planta que el motor nunca iba a producir, y no se ofrecía la que el operador
  acababa de crear. Ahora hay `variables_disponibles()` / `variables_validas()`, que releen
  `variables.json`.
- **`_defaults_variables()` devuelve `{"crudas": {}, "definiciones": []}`.** Sembraba las
  crudas y las calculadas del espesador; mientras esto solo existía offline era ruido
  cosmético, ahora serían variables de otra planta apareciendo en el editor de reglas.
  Mismo criterio que `_defaults_defuzzy` y `_defaults_tracking`.
- **`roles_en_uso()` sigue la cadena.** Si una regla nombra `nivel_promedio`, las PV que
  lo alimentan quedan *en uso* aunque nadie las nombre. Se recorre de la última definición
  a la primera (el orden de `variables.json` es el de encadenamiento), así una cadena de
  varios saltos se propaga en una pasada.
- **`_evaluar_estado_fuzzy()` acepta `limites_override`.** Los límites de una calculada no
  salen de la fila: los arma el motor mezclando tags LIM con el respaldo fijo.
- **El runner batch usa el mismo evaluador dentro del bucle.** La pre-pasada
  `calcular_variables_df` sigue existiendo para enriquecer `data_proceso` (las curvas que
  grafica la página), pero **no es lo que decide**: calcula sobre la columna cruda y con un
  `dt` fijo. Lo que ve el fuzzy es el evaluador online, igual que en producción.
- **El paso 3 de la traza pasó a llamarse "Variables calculadas"** y muestra las de
  verdad, con `derivadas_omitidas` explicando las que no se pudieron calcular.

#### Lo que sigue sin poder hacerse

Una variable calculada **no puede ser el origen de una PV del contrato**: `construir_mapeo`
sigue exigiendo un tag OPC-UA por cada rol PV. No hace falta para el estándar — la
calculada ya es utilizable en todo lo que importa — pero conviene saberlo antes de
declarar una PV que en realidad se computa.

### Las pendientes son configurables, y puede haber varias por variable (2026-08-21)

Una pendiente es **la tendencia** de una variable: cuánto sube o baja por minuto. Antes
eran `PEND_MODELOS`, un dict hardcodeado en `fuzzys_models_espesador.py` con las cuatro
variables calibradas del espesador y cinco placeholders. Eso dejaba tres cosas rotas:

- **No se podían configurar.** No había equivalente a `fuzzy.json`, así que otra planta
  no tenía forma de declarar ninguna.
- **El tiempo de cálculo no era asignable.** `evaluar_pendiente_var` recibía
  `ventana_s=60.0` como valor por defecto y **nadie se lo pasaba nunca**: toda pendiente
  medía exactamente un minuto, dijera lo que dijera la documentación.
- **Solo podía haber una por variable**, porque la clave era `pend_<var>`. No se podía
  mirar la misma señal a 5 min y a 30 min — que es justo lo que distingue un arranque de
  una deriva lenta.

Ahora se declaran en `pendientes.json`, cada una con su variable de origen, su ventana,
su eje y sus etiquetas, y se editan en un bloque propio dentro de la página Fuzzy.

#### Decisiones

- **Nombre propio y congelado.** Derivarlo de la ventana (`pend_torque_5min`) parecía más
  predecible, pero entonces cambiar la ventana de 5 a 10 min **renombraría la variable** y
  dejaría mudas en silencio las reglas que la nombran — el mismo problema que el proyecto
  ya había decidido evitar con los identificadores de tag. La página propone
  `pend_<pv>_<n>min` al crear y después no se renombra; borrar o renombrar algo que una
  regla usa se rechaza con 409, igual que con las filas del fuzzy.
- **Sin historia no se afirma nada.** Una pendiente no se produce hasta que la ventana
  esté cubierta. Con medio minuto de datos, una ventana de 30 min no puede decir si el
  proceso está estable: reportar `STABLE` mientras tanto sería afirmar algo que el motor
  no puede sostener, y las reglas actuarían sobre esa afirmación. La variable no existe,
  las reglas que la nombran quedan `no_evaluable`, y la traza dice cuántos segundos faltan.
  Mismo criterio que no fuzzificar una PV sin sus límites.
- **El eje está en unidades de ingeniería por minuto** y debe ser estrictamente creciente
  — con el eje desordenado `np.interp` devuelve basura **en silencio**, igual que el
  `belief_axis` del defuzzy.
- **La fuente puede ser una PV o una variable calculada.** La tendencia del promedio de
  dos transmisores suele ser más útil que la de cada uno por separado.
- **Se estima por regresión lineal**, no por `(último − primero)`: una sola muestra
  ruidosa en cualquiera de los dos extremos definiría toda la tendencia.
- **Las filas son libres**, igual que en un fuzzy de PV: `INC`/`DEC`/`STABLE` es solo la
  plantilla.

#### Lo que esto arrastró

- **`variables_disponibles()` dejó de derivar `pend_<var>` de la lista de PV.** Ofrecerlo
  para toda PV era ofrecer una variable que el motor no podía producir: la regla se
  escribía, se guardaba y quedaba `no_evaluable` para siempre (el "agravante" que estaba
  en el backlog). Ahora existen las declaradas, ni una más ni una menos.
- **`/api/meta` recalcula `variables` en cada request** y `VARS` en `index.html` pasó de
  `const` a `let`, refrescable — mismo tratamiento que `LABELS` y `ACTIONS`.
- **`etiquetas_disponibles()` lee también `pendientes.json`.**
- **Las pendientes se mezclan en `fuzzy_out` ANTES de expandir las etiquetas compuestas**
  (`pend_extra` en `_evaluar_estado_fuzzy`). Si no, se quedarían sin sus `NO-<X>` y una
  regla que use `NO-INC` no podría evaluarse.
- **`roles_en_uso()` sigue la cadena**: una pendiente en uso deja *en uso* a su variable de
  origen aunque nadie la nombre.
- **El paso 4 de la traza** muestra, por pendiente, la variable fuente, la ventana en
  minutos y el valor en u/min — donde una PV muestra `lmin`/`lmax`.
- **El runner batch usa el mismo objeto** (`pendientes_config`), así que la simulación y
  producción ya no difieren en las tendencias.

### El tracking espera al proceso (2026-08-21)

`tracking.json` declara, por familia de SP, cuál es su **readback** (`pv_key`) y cuánto
desvío se tolera (`rango`). Se configuraba desde la página y **nadie lo leía en runtime**:
la pantalla prometía una verificación que no existía.

Ahora el motor lo evalúa **antes de aplicar las acciones**. Si el readback está más lejos
del setpoint que el rango, el proceso viene en camino y esa familia se **retiene**:

- **La acción se descarta, no se acumula.** Si solo se retuviera la escritura, el SP
  interno seguiría subiendo mientras el proceso quedó atrás, y al liberarse se mandaría un
  salto de varios pasos juntos.
- **El wait no se reinicia**, porque el SP no se movió — cae solo del mecanismo de
  *El wait cuenta solo si la regla actuó*. Es el caso que faltaba del estándar.
- **Solo esa familia.** El resto del pipeline corre igual: se lee, se filtra, se
  fuzzifica, las reglas se evalúan y los otros setpoints se mueven.

#### Decisiones

- **La referencia es `_sp_escritos`, no `_setpoints`**: lo que el DCS realmente aceptó.
  Comparar el proceso contra un valor que el DCS quizá nunca recibió mediría el desvío
  equivocado.
- **Fail-closed sin readback.** Si el `pv_key` no se puede leer, la familia se retiene
  igual. Sin poder verificar que el proceso responde, seguir empujando el setpoint es
  exactamente lo que el tracking existe para impedir.
- **`pv_key` vacío significa "sin readback"**: esa familia no se verifica, y sigue siendo
  un estado válido.
- **El readback pasa por el filtro Exp-Q**, como cualquier PV — el tracking mira la señal
  filtrada, la misma que ve el resto del pipeline. Consecuencia práctica: la liberación
  tiene el lag del filtro. Si eso molesta, se baja `ventana_s` de esa PV.

Se ve en la traza como un cartel propio ("Tracking: esperando al proceso"), aparte de los
setpoints inhibidos por configuración — uno es transitorio y se libera solo, el otro no.

### El contrato se recarga en caliente (2026-08-21)

`config.py` leía `contrato.json` **una sola vez, al importarse**. Guardar desde la página
obligaba a reiniciar el servicio, y mientras tanto la pantalla mostraba el contrato nuevo
y el catálogo de roles seguía exigiendo el viejo, sin ninguna pista de cuál de los dos
era el real.

`config.recargar_contrato()` lo relee y **muta en el mismo objeto**. Ese detalle es el
punto entero: medio proyecto hace `from config import VARIABLES_PROCESO`, así que
reasignar el nombre en `config.py` no cambiaría nada para quien ya lo importó — cada
módulo seguiría mirando la lista vieja. Mutando la lista/dict que ya tienen en la mano
(`VARIABLES_PROCESO[:] = ...`, `COLUMNAS_ENTRADA.clear()` + rebuild), el cambio llega a
todos a la vez. Se recargan `VARIABLES_PROCESO`, `SETPOINT_KEYS`, `LIMITES_SP_CONTRATO`,
`RATE_SP_CONTRATO`, `VARIABLES_CRUDAS_REQUERIDAS`, `COLUMNAS_ENTRADA`,
`LIMITES_FUZZY_POR_VARIABLE` y los dos `ROLES_*`.

**Lo que NO se recarga en caliente es el motor.** Cambiar el contrato bajo un lazo de
control en marcha significaría fuzzificar contra otra escala y escribir a otro setpoint a
mitad de tick. El motor toma el contrato nuevo en su próximo `start()`, y `PUT
/api/contrato` devuelve `motor_corriendo` para que la página lo diga.

`VAR_NOMBRES_RESERVADOS` pasó a ser `var_nombres_reservados()` por el mismo motivo que
`etiquetas_disponibles()`: una foto al importar seguiría reservando las variables del
contrato viejo y dejando libres las del nuevo.

`estado_contrato()` se conserva: sigue siendo la única forma de detectar el caso raro de
alguien editando `contrato.json` a mano en el disco, sin pasar por la API.

### Los límites se declaran donde se usan (2026-09-03)

**El límite de una variable lo declara quien lo consume**, no el tag. En la página de
Fuzzy, debajo de la tabla de membresías de cada PV, hay una tabla que elige el tag de
`lmin` y el de `lmax` por pseudónimo; en Defuzzificación, la misma tabla elige los
límites que **acotan la escritura de ese SP al DCS**. Se guardan con el mismo botón
Guardar que la tabla de arriba.

Antes el cableado era el campo `rol` de un tag LIM (`hopper_nvl_pv_a_lmax`), asignado en
la página de Tags. Tenía dos problemas y el segundo era el caro:

- No se veía desde donde se usa. Mirando el fuzzy de una variable era imposible saber
  contra qué escala se estaba normalizando.
- **Un tag tiene un solo rol.** `PU009_Speed_MIN/MAX` son los límites de la bomba: son a
  la vez la escala de la PV de velocidad **y** el tope de escritura de su setpoint. Con el
  rol había que elegir uno de los dos o duplicar el tag en KEPserver.

#### Formato y fuente de verdad

`fuzzy.json` y `defuzzy.json` ganan la misma clave, con el **nombre del tag**:

```json
"hopper_nvl_pv_a": {
  "type": "norm", "offset": [...], "labels": {...},
  "limites": {"lmin": "PCS7.OS01.Hopper_Lvl_MIN", "lmax": "PCS7.OS01.Hopper_Lvl_MAX"}
}
```

`bindings_limites()` (en `web/state.py`) es la **única** fuente de verdad, y
`construir_mapeo()` arma `tag_to_lim` desde ahí. Por eso `tag_to_lim` pasó a ser
`dict[tag, [(var, bound), …]]`: un tag puede acotar varias variables. El *namespace* de
identidad sigue siendo `<var>_lmin` / `<var>_lmax`, que es lo que ya entienden
`roles_en_uso()`, `faltantes_en_uso`, la página de Mapeo y la traza.

#### El respaldo numérico: la red debajo del tag

Debajo de los dos selectores hay un `limites_num`, con la **misma forma en las dos
páginas**:

```json
"limites_num": {"habilitado": false, "lmin": null, "lmax": null}
```

**Nace apagado, y se enciende a mano.** Un respaldo que valiera solo por existir es
exactamente lo que hacía invisible al viejo `limites_sp` del contrato: nadie sabía contra
qué se estaba clipeando. Los números se guardan aunque esté apagado — apagarlo y volver a
encenderlo no tiene por qué hacer reescribirlos.

Precedencia, por bound y en cada tick:

| Situación | Qué pasa |
|---|---|
| Hay tag cableado y se pudo leer | **manda el tag**, siempre |
| No hay tag y el respaldo está encendido | manda el número |
| Hay tag y **no** se pudo leer | falla: la PV sale del fuzzy / la familia SP se bloquea |
| Ni tag ni respaldo | ídem |

El tercer caso es fail-closed a propósito y **no** cae al número: el servidor acaba de
decir que ese límite no es confiable, y medir contra un número viejo sería decidir sobre
una escala que el DCS no sostiene. La retención del último valor bueno ya cubre el
parpadeo; acá solo llega lo que estuvo caído más de `retencion_s`. `lmin >= lmax` también
bloquea, en las dos páginas.

Un bound con respaldo encendido **sale de `faltantes` y de `roles_en_uso`**: ahí el tag es
opcional y pedirlo sería un aviso que no lleva a ninguna acción.

`contrato.json → limites_sp` quedó como **legado**: `migrar_limites_sp_del_contrato()` lo
adopta una vez como `limites_num` de la familia de defuzzy que lo usa —**encendido**,
porque esos números están clipeando hoy y apagarlos al actualizar dejaría el SP sin tope, o
sea sin escritura— y a partir de ahí el motor **ya no lee el contrato** para esto. El campo
sigue en el archivo solo para que un paquete viejo se pueda importar y migrar igual.

**La falta de límites dejó de ser un veredicto de arranque.** Antes se inhibía la familia
en `_init_state()` y quedaba pegada toda la corrida; con los límites leídos de un tag eso
sería falso apenas el tag conteste. Ahora vive en `_sp_sin_limite`, que
`_familias_sin_escritura()` recalcula en cada tick, así que **la familia se libera sola**.

#### Migración y reglas que hay que respetar

`migrar_roles_lim_a_bindings()` corre en `_startup_checks()` y es idempotente: adopta cada
rol LIM viejo como binding en `fuzzy.json`/`defuzzy.json`. **Es aditiva: NO borra el rol**,
que queda como metadato inerte (`construir_mapeo` ya no lo mira) y la página de Tags lo
muestra como "rol viejo". Blanquearlo parecía más prolijo y era peligroso: `_startup_checks`
corre en cada `import app` — incluido el que hace el suite de tests **contra la config viva
de la planta**. Si el borrado se persiste y la escritura del binding se pierde o se pisa
después, el cableado desaparece y no queda de dónde reconstruirlo; ya pasó una vez. Un rol
cuya variable todavía no tiene fuzzy ni tabla queda pendiente hasta que la membresía exista,
o `crear fuzzy` lo hereda al vuelo (`_limites_heredados_del_rol`).

> **Ojo al agregar cosas a `_startup_checks()`:** importar `app` no puede destruir
> configuración. Aditivo sí, destructivo no.

- **Un binding colgado NUNCA se reemplaza solo** (regla A23). `limites_huerfanos()` lo
  reporta, la página lo pinta en rojo y bloquea el guardado hasta elegir reemplazo.
- **Los límites viajan con la tabla al guardar.** `_readFuzzyTable()` y
  `_leerFamiliaDelDOM()` los devuelven; si no, agregar una columna borraría el cableado.
- **Un binding de un bound que el `type` actual no usa se conserva.** Cambiar un fuzzy a
  `high` y volver a `norm` no puede perder el `lmin`.
- En la página de **Tags**, el rol de un tag LIM es ahora derivado y de solo lectura.

### El handshake con el DCS se configura en Tags (2026-09-03)

El permiso del DCS para que el SE escriba (`ENABLE_EXT` lo pide, `ENABLE_FBK` lo responde)
**siempre** vivió en `tags.json` bajo `"handshake"` — pero hasta el 2026-09-03 no había ni
endpoint ni página: se editaba a mano el archivo. Era el mismo agujero que tenían los
límites de SP en `contrato.json`, y con peores consecuencias, porque el handshake es
fail-closed: mal configurado, el SE no escribe **nada** y no dice por qué.

Ahora hay `GET/PUT /api/tags/handshake` y un panel en la página de Tags, al lado del de
heartbeat, con el estado en vivo (NO EXIGIDO / EXIGIDO / AUTORIZADO / DENEGADO). Dos
validaciones que no hay que sacar: **exigir el handshake sin tag FBK se rechaza** (sería
fail-closed permanente), y FBK y EXT tienen que ser distintos. Un tag guardado que dejó de
servir se conserva en rojo en vez de reemplazarse solo (regla A23).

`handshake` **entró a `_TAG_STORE_KEYS`** de export/import: antes no viajaba, así que una
planta clonada arrancaba sin el permiso configurado y no escribía ningún setpoint.

### El Explorador de Series es una consola, no un gráfico (2026-09-08)

Detalle completo en `CAMBIOS_2026-09-08.md`. Lo que hay que saber antes de tocarlo:

**Lo que se muestra sale del pipeline, no de una segunda cuenta.** El dominio fuzzy y la
pendiente que aparecen en el resumen y en el tooltip vienen de la última traza
(`GET /api/se/grafico/anotaciones`), traducidos de variable a tag con el **mismo mapeo
rol↔tag que usa el motor**. Calcularlos en el navegador habría sido más simple y habría
podido discrepar de lo que deciden las reglas: una pantalla que miente sobre el motor es
peor que una pantalla sin ese dato. Sin motor corriendo no hay traza y la interfaz lo
dice (`running: false`), no inventa.

**El emparejado del hover es por tiempo, no por índice.** `interaction.mode: 'index'` de
Chart.js asume un vector de X compartido; acá cada tag entra al historial cuando el
KEPserver responde, así que las series tienen distinta cantidad de muestras y el tooltip
mostraba una serie de más o de menos. Hay un modo propio, `tiempoCercano`, con tolerancia
del 3 % de la ventana: si una serie **no** tiene dato cerca de ese instante, se omite en
vez de mostrar un valor de hace minutos. El crosshair usa el mismo emparejado — si se
tocan uno y el otro no, vuelven a discrepar.

**El zoom y el arrastre solo actúan con la celda tomada.** Se habilitan al hacer clic
dentro del gráfico (borde verde) y se apagan al hacer clic fuera. Antes, pasar el mouse
por encima mientras se hacía scroll cambiaba el zoom sin querer. El gesto se traduce a
estado propio (`positionSec`, `yDrag`) en `onPanComplete` / `onZoomComplete`: si no, el
refresco de 2 s reimponía la ventana y el arrastre se perdía.

**Las franjas rojas son el permiso del DCS.** El FBK del handshake se lee aparte
(`GET /api/tags/handshake/historial`) porque `/api/tags/lectura` excluye los tags OTRO y
descarta los booleanos del historial: nunca entraba al buffer. Lectura fail-closed, igual
que el motor: si no se pudo leer no se afirma ni autorizado ni denegado.

**Color y límites por tag viven en `tags.json` → `"grafico"`, no en el navegador.** El
color con el que uno reconoce una variable no debería depender de la máquina ni perderse
al limpiar el caché. El PUT hace merge parcial (`null` borra) y **no** pasa por
`_require_license`: es visualización, no configuración del SE. La clave entró a
`_TAG_STORE_KEYS` de export/import — una planta clonada se lleva sus colores.

**La sonda de catálogo es `/api/tags/catalogo`, no `/api/entrada`.** Esta última hace un
batch OPC completo de todos los tags habilitados: como sonda cada 10 s encarecía el
refresco sin necesidad. Un tag suspendido o borrado sale de la selección en vez de quedar
como serie muerta.

### Los límites de SP viven en el contrato

`limites_sp` en `contrato.json` es lo que clipea la escritura al DCS.
`apply_actions_tabla` solo recorre las familias presentes en ese dict: **un SP sin
límites no se clipea y se escribe sin tope.** Por eso desde el 2026-08-21 esa familia
queda **inhibida** (se calcula, no se escribe) en vez de impedir el arranque del motor
entero, y `_normalizar()` conserva el campo cuando el payload no lo trae (la UI todavía
no lo edita, y sin esa protección cualquier guardado lo borraría en silencio).

### Arranque bumpless

`_init_state()` lee del DCS el valor vigente de cada SP y parte de ahí. Si cae fuera
del rango declarado, entra al rango de una vez. Antes partía de `SETPOINTS_BASE`,
hardcodeado, y pisaba el setpoint del operador en el primer tick.

### Ciclo libre: el motor no espera, y todo se mide en segundos

El `SEEngine` ya no tiene período fijo. `_worker()` corre **leer → pipeline → escribir
SP → volver a empezar**, sin `wait` entre vueltas. El único retardo es un **piso
configurable** (`piso_s`, default 0,05 s) y se le **descuenta** lo que tardó el tick,
así que es un período mínimo de verdad y no un tiempo muerto que se suma. `piso_s = 0`
lo desactiva del todo.

Eso obligó a que todo lo que antes contaba *ticks* pase a contar *segundos*:

| Pieza | Antes | Ahora |
|---|---|---|
| `_t_s` | `+= intervalo_s` (ficticio) | `time.monotonic() - t0` |
| Filtro Exp-Q | `window_size` muestras | `ventana_s` segundos |
| Escritura de SP | cada tick | solo si cambió |
| Historial de tags | cada tick | decimado a 1 s |
| PostgreSQL | cada tick | decimado a 500 / 1000 ms |

`status()` ya no informa un período nominal: lo **mide** (`periodo_ms`, `ticks_por_s`,
`dur_tick_ms`, `dur_tick_max_ms`).

> **La velocidad real la fija la fuente de datos, no el `while`.** Con un KEPserver
> falso el lazo da ~1400 tick/s; contra OPC-UA real manda la latencia de lectura. Y si
> el SE gira más rápido que el generador de tags, relee el mismo valor: pendiente 0 y
> filtro convergido a constante. El piso existe justamente para eso.

### El filtro Exp-Q mide segundos, no muestras

`filtros.json` pasó de `{q, window_size}` a `{q, ventana_s}`. El peso de cada muestra
depende de su **edad real**: `w(Δt) = exp(-q · (Δt / τ)²)` con `τ = ventana_s / 10`.
Con `window_size` el mismo JSON significaba 50 s de suavizado a 5 s/tick y 0,5 s a
50 ms/tick — el filtro dejaba de filtrar al acelerar el lazo, en silencio.

`PASOS_REFERENCIA = 10` conserva el significado histórico de `q`, así que la migración
`ventana_s = window_size × 5` (el período que tenía el lazo) es **numéricamente
idéntica**: verificado, 146.508 con ambos filtros a 5 s.

El costo por tick queda acotado por **buckets de decimación**: las muestras se agrupan
en cubos de `ventana_s / 100` promediando dentro de cada uno. A 11 PV con la ventana
llena son 0,3 ms por tick corra el lazo a la velocidad que corra — y el promedio
interno *mejora* el rechazo de ruido. Los loaders siguen aceptando `window_size` y lo
traducen, para no romper un `filtros.json` viejo.

### La persistencia tiene su propio reloj

El motor gira libre; PostgreSQL recibe **un fotograma cada `persist_periodo_ms`**
(500 o 1000, configurable en `/espesador/postgres`). Sin esto, 27 tags a 20 tick/s son
~46 millones de filas/día y —peor— la base pasaría a fijar la velocidad del lazo de
control. Los ticks descartados no pierden decisiones: el SE ya actuó sobre ellos.

`persistencia_debida()` se consulta **antes** de armar el payload, porque armarlo
implica releer `tags.json`. `_load_pg_config()` cachea con testigo de mtime por la
misma razón.

La página valida ahora dos cosas distintas: **Probar Conexión** (la base responde) y
**Validar Persistencia** (escribe una fila sonda real, la lee de vuelta y la borra —
comprueba tablas, permiso de INSERT y si la base llega a tiempo para el período).

### Las filas del fuzzy son configurables, no HIGH/OK/LOW fijas

La página Fuzzy deja **crear, renombrar y borrar filas** con nombre libre. Una planta
puede necesitar `CRITICO_ALTO / ALTO / NORMAL / BAJO`, o solo dos etiquetas.

La factory de `core/fuzzy/templates.py` **siempre** aceptó `**conjuntos` arbitrarios: la
restricción a HIGH/OK/LOW vivía únicamente en `construir_registry_fuzzy`, en el
validador de `web/api/config.py` y en la UI. Los tres se generalizaron.

Lo que esto arrastró:

- **`ETIQUETAS_DISPONIBLES` dejó de poder ser una constante.** Ahora hay
  `etiquetas_disponibles()` en `web/state.py`, que une el catálogo base con las filas de
  `fuzzy.json` (y su `NO-<X>` derivada). Sin eso, el editor de reglas no ofrecería una
  etiqueta recién creada y la validación rechazaría una regla correcta. `LABELS` en
  `index.html` pasó de `const` a `let`, refrescable vía `/api/meta`.
- **Nombres reservados.** `NO-<X>`, `CERCA_ALTO`, `CERCA_BAJO` los genera
  `expandir_etiquetas_compuestas`; `INC/DEC/STABLE` son de pendiente y `ON/OFF` de
  permisivo. Una fila con esos nombres pisaría una etiqueta derivada, así que se
  rechazan (`FUZZY_LABELS_RESERVADAS`).
- **La fila `offset` no es una etiqueta**, es el eje: no se renombra ni se borra.
- **Borrar o renombrar una fila que una regla nombra se RECHAZA** (409), en línea con
  "fallar ruidoso". Desde el JSON un renombre es un borrado + un alta, así que rompe
  igual. `pares_de_regla()` en `core/engine/motor.py` es el helper nuevo: como
  `variables_de_regla` pero conservando la etiqueta. El escape es `__forzar__: true` en
  el PUT, para cuando se quieran cambiar reglas y fuzzy a la vez.

> **Inconsistencia preexistente que quedó a la vista:** el validador de *fuzzy* acepta
> variables según `entradas_pv_disponibles()` (los tags PV), pero el validador de
> *reglas* usa `VARIABLES_VALIDAS`, derivada de `VARIABLES_PROCESO` (el contrato). Con
> tags PV cuyo identificador no está en el contrato se puede definir un fuzzy y después
> no poder escribir la regla que lo usa. No lo toqué; es anterior a este cambio.

### Defuzzy = variables manipuladas (y la página ahora lo dice)

Fuzzy son las variables que el experto **mide**; Defuzzy, las que **mueve**. La tabla
Sugeno de cada familia traduce la convicción de una regla en cuánto se mueve ese SP.

Dos ejes con reglas distintas, que la UI mezclaba:

| | Rango | Por qué |
|---|---|---|
| `belief_axis` (encabezado) | **[0, 1], estrictamente creciente** | Es una convicción, no una magnitud. El motor interpola sobre él: con el eje desordenado `np.interp` devuelve basura **en silencio**. |
| `steps_por_accion` (filas) | **sin tope, admite negativos** | Están en unidades de ingeniería del SP (%, t/h, g/t). Quien acota es `limites_sp` del contrato al clipear la escritura al DCS. |

`+ Columna` ya no se traba: antes sumaba 0,25 al final y se negaba apenas el eje llegaba
a 1.0 — o sea, casi siempre, porque un belief de 1.0 es la convicción máxima y toda
tabla razonable lo incluye. Ahora **inserta en el hueco más grande** e interpola el paso
entre los vecinos, así el orden se mantiene por construcción.

La página muestra además los dos extremos del enlace, que antes no aparecían por ningún
lado: el **tag SP** concreto al que escribe la familia (tag, pseudónimo, equipo, unidad,
límites del contrato) y `enlaces_defuzzy()`, que arma el mapa
`fuzzy → regla → acción → SP` para responder "¿qué variables terminan moviendo esto?".

**Todas las familias se editan a la vez.** Se eliminó el selector "Familia" que mostraba
una tabla por vez: con varios SP obligaba a ir de a uno y no dejaba comparar. Ahora hay
una tarjeta por SP —panel del tag, tabla, `+/- Columna`, `+ Acción`, `Borrar familia`,
enlace— más un índice para saltar entre ellas y un único **Guardar todo** (el PUT
reemplaza la config completa, así que un "guardar familia actual" era una ilusión).

> **Ojo al tocar este render:** `_sincronizarDefuzzy()` vuelca al estado lo escrito en
> **todas** las tarjetas y hay que llamarla antes de cualquier `_renderDefuzzy()`. Sin
> eso, agregar una columna en un SP borra lo que el operador venía tipeando en los otros.
> Verificado en navegador: editar el eje de una tarjeta y agregar columna en otra
> conserva ambas ediciones.

### Observabilidad: tres ventanas de tiempo distintas

Ver "qué está pasando" y ver "por qué esta regla no actúa" son preguntas distintas y
necesitan buffers distintos. Hay tres, y cada uno existe porque el anterior no
alcanzaba:

| Qué | Dónde | Alcance | Para qué |
|---|---|---|---|
| **Traza** | anillo de 60 ticks | segundos (el tick dura ~0.1 s en ciclo libre) | el detalle completo de un tick: lectura, filtro, fuzzy, reglas, defuzzy, escritura |
| **Últimos disparos** | 50 eventos, en el motor | horas | "¿está actuando?" — global, todas las reglas, con el efecto real sobre el SP |
| **Grabador por regla** | 500 eventos por regla, a nivel de módulo | horas o días | "¿por qué ESTA regla no dispara?" — se activa a demanda desde la traza |

El grabador **se graba por transición, no por tick**. Con el tick a ~0.1 s, guardar
una entrada por vuelta llenaría las 500 en menos de un minuto y 499 dirían lo mismo
("bloqueada por wait activo"). Mientras el estado y el motivo no cambian se incrementa
`repeticiones`; **cada disparo, en cambio, es siempre una entrada propia**, porque es
el evento que hay que poder contar. Así el resumen distingue *evaluaciones* de
*disparos*, y 500 entradas cubren horas de operación.

Se pueden grabar **varias reglas a la vez**; la página muestra una, y el id va en la
query string (`?regla=<id>`) para poder abrir varias pestañas. `start` vacía lo
grabado antes — el botón significa "desde ahora". Vive a nivel de módulo, no en la
instancia del motor: sobrevive a stop/start del SE y se pierde solo al reiniciar el
proceso Flask.

**Grabación simultánea:** como máximo **5 reglas** a la vez; la traza lo indica en el
paso 6 y el historial muestra el contador global.

### Planta, interno y escrito en la traza (2026-08-27)

Los pasos **7b** y **8** separan tres capas que antes se mostraban como un solo número
(el objetivo interno `_setpoints`, que podía subir aunque el DCS no recibiera nada):

| Capa | Fuente | Dónde se ve |
|---|---|---|
| **Planta (tag)** | Lectura OPC del tag SP en el tick | Paso **1** (filas `sp`), paso **8** (número grande), columna **Planta** en 7b |
| **Interno SE** | `_setpoints` tras el defuzzy | Paso **8** · Interno; columna **Interno SE** en 7b |
| **Escrito DCS** | `_sp_escritos` (último write aceptado) | Paso **8** · Escrito DCS; referencia del **tracking** |

El badge **SIN EFECTO** en 7b significa que **la planta no cambió** (`movio_planta`),
aunque el interno haya calculado un paso.

**Sin escritura al DCS, el interno no acumula pasos fantasma** (2026-08-27): al inicio
de cada tick, las familias inhibidas (config, **tracking**, **handshake**) alinean
`_setpoints` con `_sp_escritos` antes de evaluar reglas, y el defuzzy no aplica pasos
sobre esas familias. Las **rampas** (`rate_sp`) siguen pudiendo dejar el interno por
delante del escrito solo mientras el SP **sí** se puede escribir; un write rechazado por
el DCS no borra el objetivo (se reintenta en el siguiente tick).

### La sesión OPC-UA es persistente, y hay una por hilo (2026-08-25)

Antes cada lectura y cada escritura abría su propia sesión: `Client(url)` + `connect()` +
`disconnect()`. Con el motor en ciclo libre eso son **decenas de sesiones TCP por segundo**
contra el KEPserver. Medido: 191 ticks abrían 191 sesiones; ahora abren **una**.

**Corrección al diagnóstico viejo:** el backlog decía *"`connect()` no tiene timeout"*.
Es falso — `python-opcua` trae `Client(url, timeout=4)` por defecto y ese valor viaja al
`socket.create_connection` **y** al `future.result()` de cada request. El riesgo real era
que el timeout es **por request**: `read_tags_batch` capturaba la excepción de cada tag
para seguir con el siguiente, así que un servidor que aceptaba el socket y después dejaba
de contestar costaba 27 tags × 4 s = **108 s en un tick**, con `stop()` haciendo
`join(timeout=3)`. Eso es lo que dejaba al motor imposible de parar.

#### Por qué una por hilo y no una sola con lock

El cliente OPC-UA no es reentrante, así que una sesión compartida obliga a **serializar a
todos sus usuarios**. Y no son solo el motor: el heartbeat pulsa cada 2 s y las rutas HTTP
releen todos los tags en cada carga de la página de Tags. Con un lock global, abrir esa
página esperaría al tick del lazo de control y el tick esperaría a la página.

La cuenta queda acotada porque los hilos que llaman son de vida larga: los tres workers
del SE y el pool de gunicorn (`worker_class = "gthread"`, `threads = 4`, hilos
reutilizados, **no** uno por request). El servidor de desarrollo de Flask sí crea un hilo
por request, y para eso está la cosecha: se cierran las sesiones de hilos muertos y las
que llevan más de 120 s sin uso.

La cosecha es **oportunista** — corre cuando alguien pide una sesión, no en un hilo de
fondo, porque un hilo más para esto no se paga solo. Con el motor corriendo se dispara
varias veces por segundo; con el motor parado y nadie mirando la página de Tags puede
quedar una sesión en pie más allá del umbral hasta el próximo uso. No se pierde nada: el
`KeepAlive` de la librería la mantiene sana.

#### Decisiones

- **El timeout es configurable** (`timeout_s` en `kepserver.json`, default 2,0 s, acotado
 a 0,2–30). Está por debajo del `join(timeout=3)` de `stop()` **a propósito**: si el
 KEPserver deja de responder, el motor se tiene que poder parar de verdad.
- **Un fallo de transporte aborta el lote.** No es una optimización: es lo que evita que
 un socket muerto a mitad de camino cueste un timeout por cada tag que faltaba.
- **"El servidor respondió" ≠ "el tag sirve".** Distinción nueva que la sesión persistente
 obliga a hacer. Compartiendo la conexión, si se cae a mitad del lote los tags que
 faltaban se reportarían como `exists=False, quality="Bad"`: el SE vería **"todos los
 instrumentos rotos"** en vez de "me quedé sin KEPserver", y no reconectaría nunca. Si
 hubo `StatusCode`, la conexión está viva y el problema es de ese tag; si no hubo
 respuesta, es transporte. Un error que no es ninguna de las dos cosas (un `ValueError`
 convirtiendo un valor) cuenta como problema del tag, porque cerrar la sesión por eso la
 haría reconectar en cada tick **en silencio**, y volveríamos a una sesión por tick sin
 que nadie se enterara.
- **Lo que no se llegó a escribir no se marca como escrito.** Los SP que quedaron sin
 intentar salen en `fallidos`. Marcarlos como escritos dejaría al DCS con el valor viejo y
 al write-on-change convencido de haberlo mandado, para siempre.
- **Cerrar por error corta el socket; cerrar ordenado saluda.** Un `disconnect()` limpio
 manda `close_session` y `close_secure_channel` y espera respuesta de cada uno: sobre una
 conexión ya rota son dos esperas de `timeout_s` que el tick paga para nada. En el camino
 duro hay que bajar además el hilo `KeepAlive` a mano, o queda girando contra una conexión
 muerta.
- **Si cambia la URL, se reconecta.** Seguir leyendo del KEPserver anterior devolvería
 valores buenos del servidor equivocado, que es peor que fallar.
- **Cerrar es I/O**, así que nunca se hace con el candado del registro tomado: una sesión
 moribunda dejaría al motor esperando para pedir la suya.

#### Lo que esto arrastró

- **`close_thread_client()` en el `finally` de los tres workers.** Sin eso, parar el motor
 dejaba su sesión abierta hasta que venciera el `session_timeout` (el servidor lo revisa
 a 60 s) y cada stop/start sumaba una huérfana.
- **`_load_config()` cachea con testigo de mtime.** `get_url()` pasó al camino caliente
 (cada uso compara su URL contra la vigente) y sin caché serían tantas lecturas de disco
 como ticks. El testigo conserva la propiedad de que editar el JSON a mano se note.
- **Los comentarios del piggyback en `_read_tags` mentían.** Decían que leer el handshake
 y los SP en el mismo lote "ahorra una sesión OPC-UA por tick". Ya no: ahora valen porque
 la autorización y las PV sobre las que se decide vienen de la **misma pasada**.
- **`GET /api/kepserver/sesiones`** responde la pregunta operativa: la sesión se está
 reusando o se está reabriendo.

#### Lo que NO mejoró tanto como parece

La latencia por lote bajó de 114,4 ms a 92,3 ms — **1,2×**, no un orden de magnitud. Manda
el round-trip de las lecturas secuenciales, no el armado de sesión. Lo que se gana de
verdad es no quemar sesiones en un servidor con conexiones licenciadas, el timeout
acotado, y no confundir una caída con instrumentos rotos.

### La calidad de dato es real, y se juzga en un solo lugar (2026-08-25)

El conector leía con `node.get_value()`, que **descarta el `StatusCode`**, y rellenaba
`"quality": "Good"` si no había excepción. La columna QUALITY de la interfaz decía siempre
*Good*, dijera lo que dijera el servidor.

**Era cosmética dos veces**, y la segunda mitad no estaba en el diagnóstico: `_read_tags`
**tampoco miraba la calidad** — su `_ok()` solo preguntaba si había llegado un número. Así
que aunque el conector hubiera dicho la verdad, el motor habría decidido igual. Se
arreglaron las dos.

Ahora `get_data_value()` trae el DataValue y el conector publica, por tag, `quality`
(severidad OPC-UA), `status_code` (el nombre exacto), `source_ts` y `estancado_s`.

#### La política

| Severidad | Qué hace el motor |
|---|---|
| `Good` | se usa |
| `Uncertain` | **no** se usa. Configurable con `aceptar_uncertain` |
| `Bad` | no se usa, tenga valor o no |
| `Unknown` (StatusCode ilegible) | no se usa — fail-closed |

- **Un `Bad` con valor no cuenta como existente.** Es el caso traicionero: llega un
 número y no hay excepción. Se resuelve en el conector (`exists=False`) y no aguas abajo,
 para que nadie tenga que revisar dos campos y se olvide de uno. El valor se conserva
 solo para diagnóstico.
- **`Uncertain` se rechaza por defecto**, en línea con la regla de oro: es el servidor
 diciendo "tomá el número pero no me hago responsable", y algunos de esos códigos son
 literalmente un dato viejo — `UncertainLastUsableValue` significa que la fuente se cayó y
 esto es lo último que hubo. Es **configurable** porque la alternativa no es gratis:
 `UncertainEngineeringUnitsExceeded` es solo "fuera de rango de ingeniería", y en una
 planta que los emita seguido rechazarlos deja al experto mudo. Que la decisión esté en un
 JSON y no hundida en el código es a propósito.
- **Un solo criterio: `valor_utilizable()`** en `web/state.py`. Lo usan las tres lecturas
 que importan — PV/CRUDA/LIM, el valor inicial de un SP, y la reconciliación con el DCS.
 Antes cada una tenía su propio `exists and value is not None`; con una política de verdad
 detrás, tres copias es el camino corto a que una quede atrás. Reconciliar contra un
 readback dudoso (B1.3) es peor que no reconciliar: el SE se llevaría como "lo que quiso el
 operador" un valor que el servidor mismo no sostiene, y después lo defendería.
- **El handshake queda aparte**, con una exigencia más dura: `Good` a secas, sin escotilla
 configurable. Un permiso que no se puede verificar es un permiso denegado.

#### El SourceTimestamp no sirve para detectar congelados

El backlog proponía detectar valor congelado con el `SourceTimestamp` que no avanza.
**Verificado contra el servidor: no funciona.** El `SourceTimestamp` avanza en **cada
lectura** aunque el valor no se mueva — mismo `66.90608978271484` con estampas
`18.509 → 20.011 → 21.516`. Estampa el momento de la lectura, no el del último cambio, así
que como detector de congelado da siempre "fresco". (`ServerTimestamp` viene `None`.)

Se mide entonces el estancamiento **del valor**: un float analógico real jitterea en los
últimos bits, así que la igualdad exacta sostenida es la mejor señal disponible. El
conector lo publica como **hecho** (`estancado_s`), no como juicio — no sabe qué tags
deberían moverse; quien decide es `web/state.py`, que sí sabe la categoría.

- **Avisa, no inhibe.** Es la decisión central. El detector no puede distinguir un scan
 congelado de un proceso genuinamente quieto: un nivel en un tanque lleno o un readback
 clavado en su setpoint no se mueven y están perfectos. Inhibir por esto dejaría al
 experto mudo justo en régimen estacionario, que es cuando más se lo necesita. Es un
 diagnóstico para el instrumentista, no un interlock.
- **Solo PV.** Un tag LIM vale 90.0 para siempre y eso es correcto.
- Umbral en `estancado_alerta_s` (default 60 s, 0 lo desactiva).

#### Lo que esto arrastró

- **Categoría de alerta propia, `calidad`.** `_run_tick` cierra la categoría `kep` al final
 de cada tick sano, y eso es correcto para un error de **conexión**: un tick que termina
 bien prueba que la conexión anda. No prueba nada sobre los instrumentos. Con las dos cosas
 en la misma categoría el aviso de calidad se auto-resolvía en el mismo tick que lo creaba
 y **no llegaba nunca a la pantalla** — defecto preexistente que afectaba igual a los
 avisos de "PV/LIM en uso que no se pudieron leer" desde 2026-08-21. Ver A18 en
 `AFINACION_PENDIENTE.md`.
- **El mensaje de alerta no lleva números que cambien.** `AlertCollector.add` deduplica por
 mensaje **exacto**; con los segundos adentro, cada tick creaba una alerta nueva — a
 ~6 tick/s son cientos de miles por día en una lista que se recorre entera en cada `add`.
 El número vive en la traza.
- **El fail-closed del handshake se ejecuta por primera vez.**
 `_chequear_handshake_dcs` exigía `quality == "Good"` desde que se escribió, pero como la
 calidad era `"Good"` por construcción, esa rama nunca se había podido ejecutar.
- **El paso 1 de la traza** gana `StatusCode` y `Sin cambiar`, más un cartel propio para
 las PV estancadas. La columna `StatusCode` muestra `—` cuando coincide con la severidad:
 existe para el caso en que difieren.
- **`GET /api/tags/lectura` tuvo que dejar pasar los campos nuevos**: el enriquecimiento ya
 los traía y el endpoint los descartaba en el último paso, con una lista blanca.
- **El fixture de tests del conector pasó de `get_value` a `get_data_value`**, con
 `_DataValueFalso`; y hay un fixture `reloj` que controla `time.monotonic`, porque el
 estancamiento se mide en segundos reales y probar un umbral de 60 s no puede costar 60 s.

### Un parpadeo no saca al experto de servicio (2026-08-25)

Con la calidad ya real (ver arriba), una PV que llegaba `Bad` desaparecía del `fuzzy_out`
**en el mismo tick** y las reglas que la nombraban quedaban mudas de inmediato. Un
parpadeo de un tick del KEPserver —o una reconexión, que después de la sesión persistente
dura lo que dura un `connect()`— dejaba al SE sin decidir por algo que ya se había
resuelto solo.

`retencion_s` (en `kepserver.json`, default **5,0 s**, editable en la página) es cuánto se
sigue usando el último valor bueno de un tag cuya calidad se cayó. A ~6 tick/s son ~30
ticks: alcanza para cruzar un parpadeo y es corto frente a la ventana del filtro Exp-Q
(50 s) y a cualquier wait. `0` lo desactiva.

#### Decisiones

- **El reloj no puede ser el `source_ts`.** Este servidor lo estampa en el momento de la
 lectura, así que como "edad del dato" da siempre cero (verificado; está en *La calidad de
 dato es real*). Se cuenta desde el **último tick en que ese tag estuvo `Good`**, igual que
 se mide `estancado_s`.
- **Lo aplica `_read_tags`, no el conector.** El conector reporta lo que el servidor dijo
 en *esta* lectura y no debe mentir sobre eso: la página de Tags tiene que seguir mostrando
 `Bad` mientras el motor usa el valor retenido.
- **Una PV que nunca estuvo `Good` no se retiene.** No hay último valor bueno que retener,
 y rellenarla sería inventar — la regla de oro de siempre.
- **`_init_state()` olvida lo retenido.** Arrancar el motor con el valor de hace media hora
 es exactamente lo que el arranque bumpless existe para evitar.
- **El handshake no retiene nada.** Su fail-closed es más duro a propósito: un permiso que
 no se puede verificar es un permiso denegado, y ahí no hay parpadeo tolerable.
- **Un límite retenido sostiene a su PV.** Si no, la PV se caía del fuzzy igual y la
 retención no servía para nada.

#### La interacción con el tracking hubo que arbitrarla

El tracking es **fail-closed sin readback**: si el `pv_key` no se puede leer, la familia se
retiene. La retención mantiene vivo ese readback durante unos segundos, así que los dos
mecanismos se pisan. Quedó decidido y probado por separado:

| Caso | Qué pasa |
|---|---|
| El readback **nunca** fue legible | no hay nada que retener → fail-closed, la familia se retiene |
| El readback **parpadea** | la retención lo cubre → el tracking sigue verificando con el último valor bueno |

Es el criterio correcto: un readback que parpadea sigue siendo un readback: no hay motivo
para congelar el lazo por un tick.

Se ve en el paso 1 de la traza (antigüedad, valor retenido y calidad real) con cartel
propio, y avisa en la categoría de alerta `calidad`.

### El rate limit rampea, no descarta (2026-08-25)

`rate_sp` en `contrato.json` declara, por familia de SP, cuánto puede moverse ese setpoint
**por segundo** en unidades de ingeniería. Vacío = sin límite, que es el comportamiento
anterior.

**Rampea, no descarta, y ahí está toda la decisión.** El tracking sí descarta el paso —a
propósito, porque el proceso quedó atrás y acumular mandaría un salto de varios pasos
juntos al liberarse. Un rate limit es lo contrario: la acción es válida y el objetivo es
correcto, lo único que no se acepta es llegar de un salto. Descartar acá perdería la
decisión del experto; lo que se hace es entregarla en varios ticks.

#### Se aplica al escribir, no al calcular

No es un detalle de implementación, es lo que hace que el resto del motor siga siendo
coherente:

| | Por qué |
|---|---|
| `_setpoints` (el objetivo interno) avanza completo | El mecanismo de *el wait cuenta solo si la regla actuó* ve que el SP se movió y **no** revierte el wait. Correcto: la regla actuó, su efecto viaja en rampa. Si el rate limit tocara `_setpoints`, el wait se revertiría y la regla volvería a disparar en cada tramo |
| `_sp_escritos` guarda lo que el DCS aceptó | Es lo que compara la reconciliación con el DCS: un SP a mitad de rampa **no** se lee como intervención manual del operador |
| El write-on-change reintenta solo | Mientras quede diferencia entre objetivo y escrito, el tag sigue apareciendo como cambiado. La rampa avanza sin código extra |

El primer tick **no rampea**: es el arranque bumpless, que parte del valor vigente en el
DCS, y rampear hacia un valor que el DCS ya tiene no tiene sentido.

#### El tope del presupuesto salió de verificar, no de diseñar

La primera versión calculaba `paso_max = rate × (ahora − última escritura de este tag)`.
Medido en el contenedor: tras 5 s retenido por tracking, el SP saltaba **3,94 unidades en
un solo write**. O sea exactamente lo que el límite existe para impedir, y en el peor
momento, porque el equipo llevaba un rato quieto. El presupuesto se acumulaba durante toda
pausa: tracking, handshake denegado, write rechazado, motor recién arrancado.

`RATE_DT_MAX_S = 1.0` acota el presupuesto, y de paso le da a `rate_sp` una segunda lectura
fácil de explicar: **es también el paso máximo de un solo write.** La rampa sigue avanzando
a `rate` u/s porque el tick dura décimas de segundo. (Si alguien subiera `piso_s` por
encima de 1 s, la rampa iría más lenta que el rate declarado; es el lado conservador del
error.) Con el mismo escenario medido, el tramo de liberación avanza 1,00.

Se ve en el paso 8 de la traza: objetivo, escrito, cuánto falta y el rate aplicado.

### Guardar un JSON no puede dejarlo a medias (2026-08-25)

Todos los `_save_*` escribían directo sobre el archivo destino. El `open(..., "w")` trunca
**antes** de escribir nada, así que un corte a mitad de `json.dump` no dejaba "la edición
anterior": dejaba un JSON incompleto, que el loader considera corrupto y por el que **cae a
la plantilla de Python**. El modo de falla real no era "se perdió la última edición", era
**la planta arranca con la configuración de otra planta**.

`core/jsonio.escribir_json_atomico` escribe un `.tmp` completo y lo renombra encima del
destino con `os.replace`. En cualquier instante en que se corte, en el destino hay o la
versión vieja entera o la nueva entera.

- **El `.tmp` va en el mismo directorio** del destino, no en `/tmp`: `os.replace` solo es
 atómico dentro del mismo sistema de archivos, y `./config` es un volumen de Docker. Un
 temporal fuera degradaría el rename a copiar+borrar.
- **El `fsync` no es decorativo.** Sin él, `os.replace` puede publicar un inodo cuyo
 contenido sigue en el cache de página: ante un corte de energía el destino queda visible
 y **vacío**, que es justo el escenario que este módulo evita.
- **Hay un lock por ruta**, para que dos hilos que guardan el mismo archivo no se pisen el
 temporal ni el rename.

**El read-modify-write es otro problema.** El lock por ruta protege la escritura, no el
ciclo leer→modificar→escribir: para eso el lock tiene que estar tomado **desde antes de
leer**. `tags.json` lo necesita porque tiene cuatro escritores concurrentes —el generador,
el heartbeat, las rutas de la API y la sincronización del contrato— y ya se corrompió una
vez por esto. `_tags_lock` (un `RLock` en `web/state.py`) envuelve el ciclo completo en
`TagGenerator._save_config`, `HeartbeatManager._save_config` y todas las rutas de
`web/api/tags.py` y `web/api/contrato.py` que tocan tags.

> **Al escribir el test de concurrencia, ojo con el fixture `store`**: monkeypatchea
> `_save_tags` a un no-op, así que la carrera ocurre sobre un dict en memoria y el test
> pasa sin probar nada. El que vale corre el generador y el heartbeat **por el camino de
> producción** contra un `tags.json` real.

### Export / Import de configuración (2026-08-25)

Página `/espesador/export-import` y blueprint `web/api/export_import.py`. Empaqueta
bloques de `config/espesador/*.json` en un único archivo con
`"format": "se-hutbay-export"` (versión **2**). Guía operativa: `PUESTA_EN_MARCHA.md`
sección 8.

#### Qué entra en el paquete

Trece módulos opcionales por casilla: `tags`, `contrato`, `kepserver`, `variables`,
`filtros`, `fuzzy`, `pendientes`, `estados`, `tracking`, `permisivos`, `waits`,
`reglas`, `defuzzy`. **No** incluye PostgreSQL ni `licencia.json`.

`tags` exporta `tags.json` entero (OPC-UA + pseudónimos + generador + heartbeat).
Paquetes v1 con `tags_kepserver` + `entrada_datos` siguen importándose vía
`_bloques_tags_desde_paquete()`; el preview los muestra como un solo módulo Tags.

#### Decisiones

- **Orden de aplicación fijo** (`ORDEN_MODULOS`): tags → contrato → pipeline →
 kepserver al final. El contrato después de los tags porque el análisis de impacto
 y la limpieza de roles leen el mapeo vigente.
- **Motor detenido** para `apply` y `undo` (409 si corre). Evita mutar JSON que el
 hilo del SE tiene en memoria.
- **Respaldo automático antes de aplicar** en `config/espesador/.backup/import_*`.
 Solo copia los JSON que el import va a tocar. Historial de metadatos en
 `historial_import.json` (máx. 10 entradas: fecha, módulos, modo, archivos,
 `exported_at` del paquete).
- **Sin transacción global**: si `apply` falla a mitad, no hay rollback automático;
 el operador restaura desde el historial. El respaldo se crea **antes** de escribir.
- **Tres modos de duplicados** (`agregar` / `reemplazar` / `copias`): listas por id
 (reglas, waits), merge de dicts (fuzzy, filtros, …), merge fino de variables
 (`_aplicar_variables` + validador de config), fusión de tags por id/nombre.
- **KEPserver:** en agregar/copias solo fusiona `timeout_s`, `aceptar_uncertain`,
 `estancado_alerta_s`, `retencion_s`; host/port de la planta destino no se pisan.
- **Post-sync:** si entran tags o contrato pero no filtros/tracking, corre la misma
 lógica que los botones Sincronizar de esas páginas.
- **Contrato** se exporta/importa con `recargar_contrato()` y análisis de impacto
 en preview (reglas afectadas si se quitan variables).

#### API

`GET /api/export-import/modulos`, `POST …/export`, `POST …/preview`, `POST …/apply`,
`GET /api/export-import/historial`, `GET|POST /api/export-import/undo` (body opcional
`backup_dir`).

### Ningún catálogo sale ya de una plantilla de otra planta (2026-09-01)

Era el último resto del patrón "foto al importar": `variables_disponibles()` ofrecía
`__PERM_<X>` para cada clave del dict `PERMISIVOS` **hardcodeado del espesador**, no de
`permisivos.json`. El motor evalúa `cargar_permisivos_json()`, así que esas cinco
pseudo-variables no existían en ningún tick: la condición se escribía, se guardaba y
quedaba `no_evaluable` para siempre.

Ahora hay `permisivos_definidos()` / `nombres_permisivos()` en `web/state.py`, que releen
el JSON en cada llamada — con `_leer_json`, **no** con `cargar_permisivos_json()`, por el
mismo motivo que `roles_en_uso()`: ese cae a la plantilla del espesador cuando el archivo
falta y reproduce el ruido que se quiere sacar.

Junto con eso, `_defaults_estados()`, `_defaults_permisivos()` y
`_permisivos_defaults_deepcopy()` devuelven `{}`, y `correr_prueba_general` dejó de caer
al `PERMISIVOS` del espesador cuando no se le pasa configuración (hacía divergir la
simulación de la producción). El botón de la página Estados pasó a llamarse **Vaciar
estados**: ya no hay default que restaurar. Detalle en `AFINACION_PENDIENTE.md` A20.

### Las filas de una pendiente son tan libres como las de una PV (2026-09-01)

`FUZZY_LABELS_RESERVADAS` era un único conjunto para los dos validadores y contenía
`INC/DEC/STABLE`. En una PV eso es correcto; en una **pendiente** prohibía exactamente las
tres etiquetas de la plantilla, así que una pendiente recién creada no se podía guardar.

Quedó partido en dos:

| Conjunto | Contiene | Se aplica a |
|---|---|---|
| `LABELS_RESERVADAS_NUCLEO` | `CERCA_ALTO`, `CERCA_BAJO`, `ON`, `OFF` | siempre — las genera el núcleo |
| `FUZZY_LABELS_RESERVADAS` | núcleo + `INC`, `DEC`, `STABLE` | fuzzy de PV |
| `PENDIENTE_LABELS_RESERVADAS` | núcleo | fuzzy de pendiente |

El nombre de fila de una pendiente se **edita en la página** (antes era texto plano: la
única forma de renombrar era Quitar + `+ Fila`), el validador aplica las mismas reglas de
nombre que el de PV, y hay `- Columna`.

> **Renombrar una fila que una regla nombra se RECHAZA (409)**, con el escape
> `__forzar__`. Es la contrapartida obligatoria de poder renombrar: desde el JSON un
> renombre es un borrado + un alta, así que sin esa red la regla queda muda en silencio.
> `GET /api/pendientes` expone `uso_en_reglas` para avisarlo **antes** de guardar.

### Un valor guardado que ya no existe NUNCA se reemplaza solo (2026-09-01)

Un `<select>` cuyo valor no está entre sus opciones **selecciona la primera**. Aplicado al
editor de reglas eso significa que abrir y guardar una regla cuya variable fue borrada la
reescribe con otra variable, en silencio.

Todos los desplegables de catálogo del editor pasan por **`_selCatalogo()`**, que conserva
el valor huérfano como opción marcada en rojo, y **`_huerfanosEnModal()`** impide guardar
hasta que alguien elija un reemplazo. La lista de reglas los pinta con ⚠ vía
`_renderHoja()`. Si agregás un desplegable nuevo que ofrezca un catálogo, usá
`_selCatalogo` — no armes las `<option>` a mano.

Regla general del proyecto, y vale para lo que viene: **ante un dato que ya no cuadra,
fallar ruidoso**. Nunca descartar la condición, nunca rellenar con el primero de la lista.
Ver `AFINACION_PENDIENTE.md` A23.

> Para texto dentro de un atributo HTML usá **`_escapeAttr()`**, no `_escapeHtml()`: este
> último no escapa comillas y cierra el atributo antes de tiempo.

### Una regla guarda la COPIA del estado, más una etiqueta con su nombre (2026-09-01)

`evaluar_condicion` baja por AND/OR y **no sabe que existen los estados**. Por eso una regla
guarda las condiciones copiadas, no una referencia viva. Lo que se agregó al lado es
`ref_estado`:

```json
{"ref_estado": "HOPPER_LLENO", "AND": [["hopper_nvl_pv_a","HIGH"], ["velocidad_pv","OK"]]}
```

El motor ve el `AND` y evalúa; la clave de más la ignora — **no hubo que tocar el motor**.
La interfaz, en cambio, ya no adivina de qué estado salió cada copia comparando condiciones
(lo que fallaba en cuanto el estado se editaba, y confundía dos estados con las mismas
condiciones).

Consecuencias que hay que respetar al tocar esto:

- **Editar un estado NO propaga a las reglas.** `PUT /api/estados/<n>` devuelve `usos` con
  las que quedaron desactualizadas y la página pregunta; solo con `propagar: true` se
  reescriben. Nunca propagar en silencio.
- **No desenvolver un `if` de un solo grupo AND si trae `ref_estado`** — se pierde la
  etiqueta y la regla deja de reconocer el estado. Vale para el backend
  (`_normalizar_regla_payload`) y para la página (`_condsToItems`).
- **Anidamiento: un solo nivel**, verificado en las dos direcciones (el referido no puede
  referenciar; el referido por otro no puede ganar referencias). Eso es lo que hace
  imposible un ciclo.
- La copia de un estado anidado se **regenera en el servidor** desde la definición vigente.

Cobertura en `tests/test_estados.py`. Detalle en `AFINACION_PENDIENTE.md` A24.

### No hay botones de "Restaurar default" (2026-09-01)

Se eliminaron los de **Waits**, **Permisivos** y **Variables calc.**, con sus endpoints
(`/api/waits/reset`, `/api/permisivos/reset`, `/api/variables/reset`). El de Waits había
repuesto los ocho waits del espesador en `waits.json` — el mismo mecanismo de A20, en la
última página donde quedaba. `_defaults_waits()` devuelve `[]`.

Lo que queda son botones de **"Vaciar"**, que dejan el archivo en `{}` / `[]`. Si agregás un
botón de este tipo, que vacíe: no hay plantilla que restaurar.

**"Vaciar (todas)" en Fuzzy vacía también `pendientes.json`** — misma página, misma decisión.

Ver `AFINACION_PENDIENTE.md` A28.

### Ya no queda ninguna plantilla del espesador (2026-09-01)

Borrados `estados_espesador.py`, `reglas_espesador.py` y
`processes/espesador/permisivos_config.py`. El fallback de `cargar_reglas_json` a las 28
reglas del espesador era el peor: un `reglas.json` corrupto no detenía el motor, lo ponía a
operar con las reglas de otra planta. Hoy devuelve `[]`.

**Un archivo de configuración ausente o ilegible se responde con vacío, nunca con la
plantilla de otra planta.** Si agregás un `cargar_*` nuevo, seguí ese criterio.

### Las etiquetas son POR VARIABLE, y la derivación se declara una sola vez (2026-09-01)

`etiquetas_disponibles()` es la **unión** de todo: sirve para validar *"existe en alguna
parte"*, **nunca para ofrecer**. Para ofrecer y para validar una hoja concreta se usa
`etiquetas_por_variable()` / `etiquetas_validas_de(variable)`.

Ofrecer la unión dejaba pedir `LOW` a una pendiente de INC/DEC/STABLE, o `CERCA_ALTO` a un
fuzzy sin filas `OK` y `HIGH`. Se guardaba, no daba error, y **evaluaba 0 para siempre**.

La regla de qué etiquetas derivadas existen (`NO-<X>`, `CERCA_ALTO`, `CERCA_BAJO`) está en
**`core/fuzzy/evaluator.py :: etiquetas_derivadas()`**, y la consultan **las dos** partes: el
expansor que calcula los grados y el catálogo que la interfaz ofrece. Si cambiás cómo se
deriva algo, cambialo ahí y solo ahí — es lo que impide que la página ofrezca una etiqueta
que el motor no calcula.

En la página, un par variable+etiqueta se dibuja con **`_parVarLbl()`**, que redibuja el
selector de etiquetas cuando cambia la variable. No armes un selector de etiquetas suelto:
una etiqueta sin su variable no se puede validar.

Detalle en `AFINACION_PENDIENTE.md` A27.

### Historial de escrituras al DCS (2026-09-01)

`SEEngine._historial_escrituras` es un anillo de **200** escrituras
(`HISTORIAL_ESCRITURAS_MAX`), expuesto en `status()` y en `/api/se/trace` como
`historial_escrituras`, y renderizado en la etapa **9b** de la página de traza.

Existe porque la traza es un anillo de 60 ticks y el SE es **write-on-change**: la mayoría
de los ticks no escriben nada, así que mirando solo la traza es imposible responder *"qué le
mandó el experto a la planta en la última hora"*. Es el registro de auditoría de lo único
que sale del SE hacia el DCS.

Registra también las escrituras **rechazadas** por el DCS: "el experto quiso mover el SP y no
lo aceptaron" es justamente el evento que hay que poder reconstruir después.

> El valor anterior se fotografía **antes** de pisar `_sp_escritos`, o el historial mostraría
> "de X a X". Si tocás `_write_setpoints`, respetá ese orden.

### Configuración sobre código

El contrato, los filtros, los modelos difusos y los límites de SP se leen de JSON.
Nada de eso debería volver al código.

---

## Hallazgos (verificados ejecutando, no leyendo)

### 1. Las variables derivadas no existían en el motor en vivo — CERRADO (2026-08-21)

`calcular_variables_df()` se llamaba **solo** en el runner de CSV/batch.
`SEEngine._run_tick` nunca la llamaba: las definiciones de `variables.json` estaban
muertas en el camino online — se podían crear en la interfaz y el motor no las
producía nunca, así que una regla que nombrara una quedaba muda para siempre. Y el
`tz["derivadas"]` de la traza era engañoso: listaba los `_lmin`/`_lmax` y los
setpoints, que no son derivadas de nada.

Cerrado con `core/variables/online.py`. Ver *Las variables calculadas existen en vivo*.

### 2. `PEND_MODELOS` tumbaba el tick entero — CERRADO (2026-08-21)

Iteraba los modelos de pendiente (hardcodeados con las variables del espesador)
haciendo `inputs[var]` directo → `KeyError: 'torque'` en **todos** los ticks de
cualquier contrato distinto. Se le puso un guard, y el fondo quedó resuelto con
`pendientes.json` + `core/fuzzy/pendientes.py`. Ver *Las pendientes son configurables*.

### 3. Los tests estaban atados al contrato del espesador

El fixture generaba los tags desde el contrato vigente, pero los valores y las
aserciones estaban clavados a las 9 PV del espesador. Los tests solo pasaban mientras
el contrato fuera el original — se caían justo en la operación para la que existe el
producto. Ya derivan todo del contrato.

### 4. `start()` no devolvía nada en el camino feliz

`web/api/se.py:79` lo tapaba con `or {"ok": True}`, así que un arranque fallido podía
leerse como exitoso. Corregido.

### 5. El motor en vivo NUNCA leía `defuzzy.json` — CERRADO (2026-08-20)

`_init_state()` cargaba de disco reglas, permisivos, filtros y modelos difusos, pero
**no las tablas de defuzzificación**: usaba el `DEFUZZY_POR_FAMILIA` importado de
`defuzzy_actions`, con las tablas del espesador de fábrica.

Lo que se descubrió al cerrarlo es peor que el enunciado: el **único** lugar del repo
que reemplazaba ese global era `_ejecutar_simulacion()`. O sea, el SE en vivo empezaba
a mover setpoints recién cuando alguien pulsaba *Ejecutar Simulacion* en la web —
incluso si la simulación fallaba después, porque el parche ocurría antes del error. Y
el `clear()` + `update()` pisaba en caliente el dict que el hilo del motor estaba
usando.

**Arreglo:** `_init_state()` lee `cargar_defuzzy_json()` a `self._defuzzy` (copia de
la instancia) y el tick usa `apply_actions_tabla(..., self._defuzzy)`.
`correr_prueba_general()` acepta `defuzzy_por_familia` explícito, así que la
simulación ya no muta ningún global. Además el arranque avisa por nombre las acciones
que las reglas nombran y ninguna tabla sabe traducir.

### 6. Dos nomenclaturas para el mismo setpoint — CERRADO (2026-08-20)

El contrato decía `sp_vel_bomba` y la página de Defuzzificación creaba familias con el
identificador derivado del tag (`velocidad_salida_del_se`): la acción reventaba con
`KeyError: Familia ... no está en setpoints`.

Se resolvió eligiendo **una** fuente de verdad: el identificador derivado del tag. Hoy
el contrato, el defuzzy, el tracking y el mapeo hablan todos de `velocidad_sp`.

El caso hermano — el validador de **reglas** usaba `VARIABLES_VALIDAS`, congelada al
importar — quedó cerrado el 2026-08-21: hay `variables_validas()` y el contrato se
recarga en caliente.

---

## Afinación pendiente

El backlog vivo está en **`AFINACION_PENDIENTE.md`**: qué se cerró el 2026-08-20, qué
queda abierto por severidad, con reproducción y arreglo propuesto. Los titulares:

**No queda ningún pendiente de riesgo operativo (B1)**, y desde el 2026-08-25 tampoco
queda nada de la lista de titulares. **Lo que falta para operar ya no es software: son
las reglas, los permisivos y la calibración del defuzzy, que se definen con el experto
de planta.** Lo que sigue abierto en `AFINACION_PENDIENTE.md` es B2/B4 (funcionalidad
menor y ruido de diagnóstico).

| Prioridad | Tema |
|---|---|
| ~~Funcional~~ | ~~Interlock de habilitación~~ — ya existía: es el handshake DCS fail-closed |
| ~~Funcional~~ | ~~Retener el último valor bueno con timeout~~ — CERRADO 2026-08-25 |
| ~~Funcional~~ | ~~Rate limit por SP~~ — CERRADO 2026-08-25 |
| ~~Consistencia~~ | ~~`_save_*` sin lock y sin atomicidad~~ — CERRADO 2026-08-25 |
| ~~Operativo~~ | ~~La calidad OPC-UA es cosmética (no detecta valor congelado)~~ — CERRADO 2026-08-25 |
| ~~Operativo~~ | ~~`stop()` no comprueba `is_alive()` → dos motores escribiendo al DCS~~ — CERRADO 2026-08-25 |
| ~~Operativo~~ | ~~Sin resincronización con el DCS: revierte la intervención manual~~ — CERRADO 2026-08-25 |
| ~~Operativo~~ | ~~Sesión OPC-UA por tick sin timeout~~ — CERRADO 2026-08-25 |
| ~~Funcional~~ | ~~El wait tampoco debe reiniciarse por tracking~~ — CERRADO 2026-08-21 |
| ~~Funcional~~ | ~~Pendientes: solo existen offline y sin config editable~~ — CERRADO 2026-08-21 |
| ~~Funcional~~ | ~~Variables derivadas: solo existen offline~~ — CERRADO 2026-08-21 |
| ~~Funcional~~ | ~~`tracking.json` se configura pero no lo lee nadie en runtime~~ — CERRADO 2026-08-21 |
| ~~Consistencia~~ | ~~Contrato congelado al importar: guardar exige reiniciar~~ — CERRADO 2026-08-21 |
| ~~Consistencia~~ | ~~`or None` en `_init_state` cae a los modelos difusos del espesador~~ — CERRADO 2026-08-21 |

## Pendientes conocidos

### 1. Pendientes fuzzy — CERRADO (2026-08-21)

`pendientes.json` + `core/fuzzy/pendientes.py`. El editor de reglas ya no ofrece
`pend_<var>` para toda PV (eso era ofrecer una variable que el motor no podía
producir): ofrece exactamente las declaradas. Vivo y simulación usan el mismo objeto.
Ver *Las pendientes son configurables*.

### 2. Reloj del motor — CERRADO

`t_s` sale ahora de `time.monotonic()`, tomado una vez al inicio de cada tick. Los
waits de 900 s duran 900 s y la ventana de pendientes mide 60 s de verdad.
Ver "Ciclo libre" más abajo.

### 3. Política de calidad de dato — CERRADO (2026-08-25)

**2026-08-21:** `_read_tags` dejó de abortar el tick. Una PV o un límite con calidad mala
sacan a esa variable del `fuzzy_out`; el resto del pipeline corre y las reglas que la
nombran quedan `no_evaluable`. Las CRUDA degradan a `0.0` y avisan solo si alguien las usa.

**2026-08-25 (B1.4):** la calidad ya es **real**. Sale del `StatusCode` de OPC-UA, se
juzga en un solo lugar (`valor_utilizable()`), `Uncertain` se rechaza por defecto y es
configurable, y hay detección de valor congelado — por estancamiento del valor, no por
timestamp, que contra este servidor no funciona. Ver *La calidad de dato es real, y se
juzga en un solo lugar*.

**2026-08-25 (B1.5):** cerrado lo que faltaba — **retención del último valor bueno**. Ver
*Un parpadeo no saca al experto de servicio*.

### 4. Arranque sin salto de setpoint — CERRADO

Ver "Arranque bumpless".

### 5. Escritura de SP sin control — CERRADO salvo el interlock

`_write_setpoints()` ya hace **write-on-change**: compara contra el último valor
efectivamente escrito (`_sp_escritos`) y solo manda la diferencia. Si el write falla
no mueve la referencia, así que el tick siguiente reintenta. El primer tick tras
arrancar escribe todo, que es lo que sostiene el arranque bumpless.

Desde 2026-08-20 la escritura además es **honesta**: `write_float_batch` devuelve
`{escritos, fallidos}` en vez de tragarse el error por tag, solo se mueve la
referencia de los que el DCS aceptó, y el error sobrevive al final del tick (antes lo
borraba un `self._last_error = None` incondicional).

Desde 2026-08-25 tiene además **límite de velocidad** (`rate_sp` del contrato, ver *El
rate limit rampea, no descarta*) y **resincronización con el DCS**: si el operador mueve
el SP a mano, el SE adopta su valor en vez de revertirlo de un salto.

El **interlock de habilitación** ya existe y estaba mal anotado como pendiente: es
`_chequear_handshake_dcs`, que corre en cada tick antes de armar el lote y bloquea
**todas** las familias si el `Enable_FBK` del DCS no llega en `Good` con valor verdadero.
El handshake completo está provisionado (`PU009_Exp_Enable_Ext`, `Enable_FBK`, `Exp_HB`,
`LIC_Auto/Manual`, selectores). La calidad real (B1.4) fue lo que lo volvió efectivo: la
rama de `quality == "Good"` existía desde que se escribió, pero con la calidad inventada
nunca se había podido ejecutar.

### 6. `_save_tags` sin lock — CERRADO (2026-08-25)

`_tags_lock` cubre el read-modify-write completo y todos los `_save_*` escriben de forma
atómica. Ver *Guardar un JSON no puede dejarlo a medias*.

Lo que se había arreglado antes: suspender un tag ya no **borra** sus rangos del
generador; queda dormido con su configuración intacta.

### 7. Colisiones de pseudónimo — CERRADO

Los 27 tags tienen pseudónimo único, con el equipo en el nombre (PU-009, PU-0025,
AG-004, TK-004, PIC-2391).

---

## Cosas que quedaron sin confirmar

1. **¿AG-004 y TK-004 son el mismo estanque?** De eso depende si los límites
   `PU009_Exp_TK004_Lvl_MIN/MAX` acotan `nivel_ag004_a/b` o quedan huérfanos.
2. **¿AG-004 está aguas arriba o aguas abajo de la bomba?** Define el signo: si está
   aguas abajo, subir velocidad lo llena; si está aguas arriba, lo vacía.
3. **Los límites de escritura de `velocidad_salida_del_se` siguen siendo el número
   `[50, 100]` de `contrato.json`.** Desde 2026-09-03 se pueden cablear a
   `PU009_Speed_MIN/MAX` desde la página de Defuzzificación — que es lo correcto, porque
   así el SE sigue el límite de ingeniería sin que nadie edite un JSON, y esos mismos tags
   pueden seguir acotando a `velocidad_pv`. **Falta hacerlo y confirmar con el experto que
   ese rango sea el de la bomba.**
4. **Los 11 modelos difusos son plantillas, no ingeniería.** Todos `norm` en dominio
   `[0, 0.5, 1]` con HIGH/OK/LOW simétricos. Hacen que el registry cargue; no
   describen ninguna variable real. Igual con `q=0.15` en los 11 filtros.
5. **`velocidad_bomba_sp_local` quedó como PV.** Es el setpoint del lazo local de la
   PU-009, no una medición: se va a fuzzificar y exige sus dos límites.
6. **El tracking de `velocidad_sp` quedó con `pv_key: velocidad_pv` y `rango: 0.3`.** El
   readback ya está declarado, pero **el rango es un número de banco de pruebas**: 0.3
   sobre una velocidad en % es muy estrecho, y con el lag del filtro Exp-Q (50 s) retiene
   la familia varios segundos en cada movimiento. Se ve en la verificación del rate limit:
   el SP quedó retenido 5 tramos esperando al readback. Hay que decidir con el experto
   cuánto desvío es tolerable de verdad.
7. **`rate_sp` no está declarado**, así que `velocidad_sp` se escribe sin límite de
   velocidad. El mecanismo está listo y probado; el número es de proceso. La pregunta
   concreta para el experto: **¿cuántos % por segundo puede aceptar la PU-009?** Ese
   valor es además el paso máximo de un solo write.
8. **`retencion_s` quedó en el default de 5,0 s.** Es un valor razonable para cruzar un
   parpadeo, pero nadie confirmó cuánto tarde este KEPserver en reconectar en la práctica.
   Si se ven huecos de más de 5 s, conviene subirlo.
9. `PU009_Corriente` no tiene límites: ¿cuáles son sus rangos de ingeniería? Ahora
   importa más, porque `corriente` entró al contrato como PV.
10. El tag `PU009_Exp_Hesrt_Int` parece tener un dedazo ("Hesrt" por "Heart").

---

## Convenciones de trabajo

- **Verificar, no suponer.** Antes de afirmar algo del comportamiento, comprobarlo
  ejecutando código.
- **No adivinar mapeos de señal.** Asignar la señal equivocada a una variable no
  produce un error: produce un experto que controla mal en silencio.
- **Cambios aditivos en el núcleo.**
- **Correr los tests** después de cada cambio: `python -m pytest tests/ -q`.
 Al 2026-09-08: **270 pasan, 2 fallan, 3 skip**. Los dos que fallan
 (`test_sp_ilegible_se_inhibe_y_se_recupera_solo` y
 `test_el_historial_registra_lo_que_se_escribio_al_dcs`) fallan **igual en una copia
 limpia del proyecto**: están acoplados al `config/` vivo, que cambia al operar la app.
 Es la regla de abajo (A17) sin cerrar del todo — comprobar contra una copia antes de
 culpar a un cambio nuevo.
 Al 2026-09-03: **248 pasan, 0 fallan, 7 skip**. A22 quedó cerrado: `config_completa`
 escribe ahora un `contrato.json` temporal y devuelve un `_ajustar(**campos)` para
 cambiarlo desde el cuerpo de un test. `monkeypatch.setattr` sobre `LIMITES_SP_CONTRATO`
 o `RATE_SP_CONTRATO` **no funciona** — `recargar_contrato()` los vacía y los repuebla
 desde el archivo. El host necesita `pip install pytest` una vez; el resto de las
 dependencias ya está. **La imagen del contenedor no trae pytest** —
 `requirements.txt` no lo incluye, así que `docker compose exec ... pytest` falla.
- **Un test no debe leer la config viva de la planta.** `_leer_json(<ALGO>_JSON, ...)`
 sin monkeypatch es una dependencia con la calibración: la red de seguridad prueba el
 cableado y no puede caerse porque alguien afine una regla. Ya pasó dos veces
 (`AFINACION_PENDIENTE.md` A17).
- **Validar el JS** de `index.html` y de `graficos.html` tras editarlos — son archivos
  grandes y un error de sintaxis deja la página muerta sin aviso (cambiar el nombre del
  archivo en el comando según cuál se tocó):
  ```bash
  python3 -c "import re;s=open('web/templates/index.html',encoding='utf-8').read();open('/tmp/x.js','w').write(max(re.findall(r'<script>(.*?)</script>',s,re.S),key=len))" && node --check /tmp/x.js
  ```

---

## Cómo se levanta

### Contenedor

```bash
docker compose up -d --build
docker compose logs -f se-espesador
```

Queda en `http://<ip-del-host>:5000/`. `./config` es un volumen, así que la
configuración sobrevive a rebuilds. El entrypoint siembra los tags PCS7 de forma
idempotente; se salta con `SEED_TAGS_PLANTA=0`.

**El stack se llama `se-hutbay-espesador-3`** (2026-08-21). Antes el nombre del
proyecto lo derivaba Compose del nombre de la carpeta — por eso Docker Desktop mostraba
`se-hutbay-espesador-2`: venía del directorio anterior. Ahora está declarado con `name:`
en el compose, así que mover o renombrar el directorio ya no cambia cómo se llama el
stack ni deja stacks huérfanos con el nombre viejo. El `container_name` también pasó a
`se-hutbay-espesador-3`; el **servicio** sigue llamándose `se-espesador`, que es lo que
usan los `docker compose logs -f se-espesador`.

Al cambiar de nombre, el stack viejo no desaparece solo y **retiene el puerto 5000**:

```bash
docker compose -p se-hutbay-espesador-2 down    # desde la carpeta vieja: docker compose down
docker compose build && docker compose up -d
```

Para tener los dos a la vez hay que darle otro puerto al `-3` (`"5001:5000"`), porque
5000 no se comparte.

**`workers = 1` es obligatorio** (`gunicorn.conf.py`): el `SEEngine` mantiene estado
en memoria del proceso y una única conexión OPC-UA. Con N workers habría N motores
compitiendo por los mismos tags.

### Sin Docker (desarrollo)

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### Simulador de DCS (OPC-UA)

```bash
cd Simulador_DCS_OPC_UA
pip install -r requirements.txt
python dcs_opcua_server.py --host 0.0.0.0 --port 4840 --periodo 1.0
python test_client.py --handshake       # en otra consola
```

Endpoint `opc.tcp://<ip>:4840/dcs/`, seguridad None, anónimo. KEPserver se conecta a
él como **cliente** OPC-UA (driver "OPC UA Client", licencia aparte). Detalle completo
en `Simulador_DCS_OPC_UA/README.txt`.

---

## Documentación vieja

`README.txt` y `COMO_EJECUTAR.txt` describen el prototipo del espesador con tags
`RETO.*` y las 28 reglas. **Están desactualizados.** `PROJECT_CONTEXT.md` también.
`DESPLIEGUE_LINUX.txt` y `EXPORTAR_Y_DESPLEGAR.txt` siguen siendo válidos para la
parte de infraestructura.
