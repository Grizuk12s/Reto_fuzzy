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
| `variables.json` | `crudas: {}` y `definiciones: []` |
| `filtros.json` | 1 entrada por PV (`q=0.15`, `ventana_s=50.0`) |
| `fuzzy.json` | `hopper_nvl_pv_a` (`high`). `velocidad_pv` sin fuzzy **a propósito**: es readback |
| `tracking.json` | 1 familia (`velocidad_sp`) — **configurada pero sin efecto**, ver `AFINACION_PENDIENTE.md` B3.6 |
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
  disparando y el paso se clipea a cero. El paso 8 de la traza ahora lo dice
  explícitamente en vez de mostrar un delta de 0 sin explicación.

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
      ├─ fuzzificación         core/fuzzy/evaluator.py + templates.py ← fuzzy.json
      ├─ permisivos            core/engine/permisivos.py
      ├─ motor de reglas       core/engine/motor.py
      └─ defuzzificación       core/engine/defuzzy.py
      │
      ▼  escribe SP
KEPserver → DCS
```

> **Ojo con este diagrama:** el paso de *variables derivadas* NO existe en el motor
> en vivo. Ver "Hallazgos" más abajo.

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

### Fallar ruidoso, nunca adivinar

**El SE debe negarse a correr antes que correr sobre datos inventados.** `start()`
valida y reporta todo junto:

- mapeo tag↔rol incompleto
- PV sin config de filtro en `filtros.json`
- PV sin modelo difuso en `fuzzy.json`
- SP sin límites en `contrato.json`
- SP cuyo valor actual no se pudo leer del DCS

### Los límites de SP viven en el contrato

`limites_sp` en `contrato.json` es lo que clipea la escritura al DCS.
`apply_actions_tabla` solo recorre las familias presentes en ese dict: **un SP sin
límites no se clipea y se escribe sin tope.** Por eso el motor se niega a arrancar
así, y `_normalizar()` conserva el campo cuando el payload no lo trae (la UI todavía
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

### 1. Las variables derivadas no existen en el motor en vivo

`calcular_variables_df()` se llama **solo** en `runner.py:436`, el runner de CSV/batch.
`SEEngine._run_tick` nunca la llama: las definiciones de `variables.json` están
muertas en el camino online. El `tz["derivadas"]` de la traza es engañoso — solo
lista los `_lmin`/`_lmax` y los setpoints, no derivadas reales.

**Consecuencia práctica:** una PV no puede ser una variable calculada. Además de que
`construir_mapeo()` exige un tag por PV, el valor nunca se computaría.

### 2. `PEND_MODELOS` tumbaba el tick entero

`runner.py:355` iteraba los modelos de pendiente (hardcodeados con las variables del
espesador) haciendo `inputs[var]` directo → `KeyError: 'torque'` en **todos** los
ticks de cualquier contrato distinto. Se le puso un guard.

**No está resuelto el fondo:** `PEND_MODELOS` sigue hardcodeado en
`fuzzys_models_espesador.py` y sigue faltando el equivalente a `fuzzy.json` para las
pendientes. Una regla que use tendencias (`("pend_nivel_hopper", "NO-INC")`) quedará
`no_evaluable`; el motor lo explica en la traza.

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

Queda vivo un caso hermano, en `## Afinación pendiente`: el validador de **reglas**
sigue usando `VARIABLES_VALIDAS` (contrato congelado al importar) mientras el de
**fuzzy** usa los tags — se puede definir el fuzzy de una variable y no poder escribir
la regla que la usa hasta reiniciar.

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
| Funcional | Variables derivadas y pendientes: solo existen offline |
| Funcional | `tracking.json` se configura pero no lo lee nadie en runtime |
| Consistencia | Contrato congelado al importar: guardar exige reiniciar |
| Consistencia | `or None` en `_init_state` cae a los modelos difusos del espesador |

## Pendientes conocidos

### 1. Pendientes fuzzy (`pend_<var>`) — sigue abierto

Ver Hallazgo 2. Falta la config editable y el constructor de registry, equivalente a
lo que se hizo con `fuzzy.json`. Agravante detectado el 2026-08-20: el editor de
reglas **ofrece y acepta** `pend_<var>` que el motor no puede producir, y el vivo
(`pend_modelos=None` → espesador) y la simulación (`pend_modelos={}`) no se comportan
igual.

### 2. Reloj del motor — CERRADO

`t_s` sale ahora de `time.monotonic()`, tomado una vez al inicio de cada tick. Los
waits de 900 s duran 900 s y la ventana de pendientes mide 60 s de verdad.
Ver "Ciclo libre" más abajo.

### 3. Política de calidad de dato — sigue abierto

`_read_tags` aborta el tick si falla un PV **o un límite** (desde 2026-08-20: antes el
límite se rellenaba con `0.0` y se seguía decidiendo sobre una escala inventada). Las
CRUDA degradan a `0.0` pero ahora levantan alerta.

Falta: retener último valor bueno con timeout, e **inhibir las reglas que dependen de
una variable degradada** en vez de tirar el tick completo.

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
6. **`tracking.json` no está conectado al motor.** `tracking` solo aparece en
   `web/state.py:71` como constante de ruta y en la API; el `SEEngine` nunca lo lee.
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
