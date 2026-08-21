# Contexto del proyecto — SE HUTBAY Espesador

Archivo de continuidad. Léelo antes de tocar nada: explica qué es el proyecto, en qué
estado quedó y qué está pendiente.

**Guía operativa completa:** `PUESTA_EN_MARCHA.md`
**Backlog vivo de afinación:** `AFINACION_PENDIENTE.md` (qué se cerró, qué falta, con
reproducción y arreglo propuesto)

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

Se reemplazó la plantilla del espesador por el contrato de esta planta. **Al
2026-08-20 el contrato se recortó a lo que está instrumentado y el SE corre.**

| Archivo | Estado |
|---|---|
| `contrato.json` | **2 PV (`hopper_nvl_pv_a`, `velocidad_pv`) + 1 SP (`velocidad_sp`) + `limites_sp`** |
| `tags.json` | 27 tags; 6 habilitados alimentan el pipeline, el resto suspendido |
| `variables.json` | `crudas: {}` y `definiciones: []` — desde 2026-08-21 el motor **sí** las calcula |
| `filtros.json` | 1 entrada por PV (`q=0.15`, `ventana_s=50.0`) |
| `fuzzy.json` | `hopper_nvl_pv_a` (`high`). `velocidad_pv` sin fuzzy **a propósito**: es readback |
| `pendientes.json` | vacío. Desde 2026-08-21 los fuzzy de pendiente son **configurables**, uno o varios por variable |
| `tracking.json` | 1 familia (`velocidad_sp`) — **conectada al motor desde 2026-08-21** |
| `reglas.json` | 1 regla de prueba (`A1_Prueba_Hopper_Lmita`) |
| `estados.json` | `{}` |
| `waits.json` | 1 wait (`sp_velocidad_a_hopper`) |
| `permisivos.json` | `{}` |
| `defuzzy.json` | 1 familia (`velocidad_sp`) con la acción `AUMENTAR_SP_VEL` |

> **"Vacío" es un estado válido.** Los loaders solo caen a la plantilla de Python si
> el archivo **falta o está corrupto**, no si está vacío. Desde 2026-08-20 tampoco hay
> plantilla de espesador que sembrar: `_defaults_defuzzy()` y `_defaults_tracking()`
> devuelven `{}`, y el catálogo de acciones sale de `defuzzy.json`.

### El mapeo está completo y el SE decide

El pipeline corre de punta a punta: lee los 6 tags con `Good`, filtra, fuzzifica
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
| `core/` | Núcleo genérico, sin nada específico del espesador |
| `processes/espesador/` | Definiciones del proceso (plantilla) |
| `connectors/kepserver.py` | Toda la lógica OPC-UA |
| `scripts/seed_tags_planta.py` | Siembra los tags PCS7 (corre en el arranque del contenedor) |
| `web/templates/historial.html` | Historial grabado de UNA regla (`/espesador/historial?regla=<id>`) |
| `tests/test_pipeline.py` | 79 tests. Red de seguridad del cableado, no de la lógica difusa |

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
`VARIABLES_CRUDAS_REQUERIDAS`, `COLUMNAS_ENTRADA`, `LIMITES_FUZZY_POR_VARIABLE` y los dos
`ROLES_*`.

**Lo que NO se recarga en caliente es el motor.** Cambiar el contrato bajo un lazo de
control en marcha significaría fuzzificar contra otra escala y escribir a otro setpoint a
mitad de tick. El motor toma el contrato nuevo en su próximo `start()`, y `PUT
/api/contrato` devuelve `motor_corriendo` para que la página lo diga.

`VAR_NOMBRES_RESERVADOS` pasó a ser `var_nombres_reservados()` por el mismo motivo que
`etiquetas_disponibles()`: una foto al importar seguiría reservando las variables del
contrato viejo y dejando libres las del nuevo.

`estado_contrato()` se conserva: sigue siendo la única forma de detectar el caso raro de
alguien editando `contrato.json` a mano en el disco, sin pasar por la API.

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

| Prioridad | Tema |
|---|---|
| Operativo | `stop()` no comprueba `is_alive()` → dos motores escribiendo al DCS |
| Operativo | Sesión OPC-UA por tick sin timeout |
| Operativo | Sin resincronización con el DCS: revierte la intervención manual del operador |
| Operativo | La calidad OPC-UA es cosmética (no detecta valor congelado) |
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

### 3. Política de calidad de dato — sigue abierto

**Actualizado 2026-08-21:** `_read_tags` ya **no** aborta el tick. Una PV o un límite
con calidad mala sacan a esa variable del `fuzzy_out`; el resto del pipeline corre y las
reglas que la nombran quedan `no_evaluable`. O sea, lo de "inhibir las reglas que
dependen de una variable degradada en vez de tirar el tick" ya está. Las CRUDA degradan
a `0.0` y levantan alerta solo si alguien las usa.

Falta todavía: **retener el último valor bueno con timeout** (hoy la variable
desaparece de inmediato).

Relacionado: `connectors/kepserver.py` usa `get_value()`, que descarta el StatusCode
y el SourceTimestamp de OPC-UA. El campo `quality` que se muestra es inventado por el
código. Con `get_data_value()` se tendría calidad real y detección de congelados.

### 4. Arranque sin salto de setpoint — CERRADO

Ver "Arranque bumpless".

### 5. Escritura de SP sin control — PARCIALMENTE CERRADO

`_write_setpoints()` ya hace **write-on-change**: compara contra el último valor
efectivamente escrito (`_sp_escritos`) y solo manda la diferencia. Si el write falla
no mueve la referencia, así que el tick siguiente reintenta. El primer tick tras
arrancar escribe todo, que es lo que sostiene el arranque bumpless.

Desde 2026-08-20 la escritura además es **honesta**: `write_float_batch` devuelve
`{escritos, fallidos}` en vez de tragarse el error por tag, solo se mueve la
referencia de los que el DCS aceptó, y el error sobrevive al final del tick (antes lo
borraba un `self._last_error = None` incondicional).

**Sigue faltando** el límite de tasa por SP, el interlock de habilitación y la
resincronización con el DCS si el operador mueve el SP a mano (ver
`AFINACION_PENDIENTE.md` B1.3).

El DCS ya provisionó el handshake completo (`PU009_Exp_Enable_Ext`, `Enable_FBK`,
`Exp_HB`, `LIC_Auto/Manual`, selectores). Debería ser **fail-closed**: si el enable no
se puede leer o tiene calidad mala, no se escribe.

### 6. `_save_tags` sin lock — sigue abierto

Read-modify-write sin sincronizar, llamado por el generador, el heartbeat y la API.
Ya se corrompieron datos una vez por esto. Y ningún `_save_*` es atómico: un corte a
mitad de `json.dump` deja el archivo ilegible y el loader cae a la plantilla de Python
(ver `AFINACION_PENDIENTE.md` B3.4).

Lo que sí se arregló: suspender un tag ya no **borra** sus rangos del generador; queda
dormido con su configuración intacta.

### 7. Colisiones de pseudónimo — CERRADO

Los 27 tags tienen pseudónimo único, con el equipo en el nombre (PU-009, PU-0025,
AG-004, TK-004, PIC-2391).

---

## Cosas que quedaron sin confirmar

1. **¿AG-004 y TK-004 son el mismo estanque?** De eso depende si los límites
   `PU009_Exp_TK004_Lvl_MIN/MAX` acotan `nivel_ag004_a/b` o quedan huérfanos.
2. **¿AG-004 está aguas arriba o aguas abajo de la bomba?** Define el signo: si está
   aguas abajo, subir velocidad lo llena; si está aguas arriba, lo vacía.
3. **`limites_sp` de `sp_vel_bomba` quedó en `[20, 95]`**, heredado tal cual de
   `simulacion.LIMITES_SP`. No cambia el comportamiento anterior, pero **nadie
   confirmó que ese rango sea el correcto**. Contrastar con `PU009_Speed_MIN/MAX`.
4. **Los 11 modelos difusos son plantillas, no ingeniería.** Todos `norm` en dominio
   `[0, 0.5, 1]` con HIGH/OK/LOW simétricos. Hacen que el registry cargue; no
   describen ninguna variable real. Igual con `q=0.15` en los 11 filtros.
5. **`velocidad_bomba_sp_local` quedó como PV.** Es el setpoint del lazo local de la
   PU-009, no una medición: se va a fuzzificar y exige sus dos límites.
6. **El `rango` de tracking de `velocidad_sp` quedó en 0.3** y `pv_key` vacío. Con el
   readback sin declarar la familia no se verifica; hay que decidir qué PV es el readback
   real de la PU-009 y cuánto desvío tolerar.
7. `PU009_Corriente` no tiene límites: ¿cuáles son sus rangos de ingeniería?
8. El tag `PU009_Exp_Hesrt_Int` parece tener un dedazo ("Hesrt" por "Heart").

---

## Convenciones de trabajo

- **Verificar, no suponer.** Antes de afirmar algo del comportamiento, comprobarlo
  ejecutando código.
- **No adivinar mapeos de señal.** Asignar la señal equivocada a una variable no
  produce un error: produce un experto que controla mal en silencio.
- **Cambios aditivos en el núcleo.**
- **Correr los tests** después de cada cambio: `python -m pytest tests/ -q`
- **Validar el JS** de `index.html` tras editarlo — es un archivo grande y un error
  de sintaxis deja la página muerta sin aviso:
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
