# Afinación pendiente — SE HUTBAY Espesador

**Fecha:** 2026-08-20 · **Alcance:** pipeline en vivo (`SEEngine`), simulación offline,
páginas Fuzzy / Defuzzy / Reglas / Traza / Historial / Tags.

**Fuera de alcance** (no se auditaron; no están acá porque no se miraron, no porque
estén limpios): persistencia PostgreSQL, licenciamiento, heartbeat y Explorador de
Series.

Todo lo de este documento está **verificado ejecutando el código**, no leyéndolo:
cada hallazgo tiene su escenario de reproducción. Lo que no se pudo confirmar está
marcado como *sospechado*.

Cobertura: `python -m pytest tests/ -q` → **79 pasan, 6 skip**.

---

## A · Cerrado en esta sesión

| # | Qué era | Dónde | Test que lo cubre |
|---|---|---|---|
| A1 | El tick llamaba a `apply_actions()` y **descartaba el retorno**: la regla disparaba, armaba su wait y el setpoint no se movía nunca | `web/state.py` `_run_tick` | `test_un_tick_con_regla_que_dispara_mueve_el_sp_y_lo_escribe` |
| A2 | El motor en vivo **nunca leía `defuzzy.json`**: usaba el dict global con las tablas del espesador de fábrica, y solo se corregía si alguien pulsaba *Ejecutar Simulacion* (que hacía `clear()`+`update()` sobre ese global compartido, con ventana de carrera incluida) | `web/state.py` `_init_state` + `web/api/se.py`, `runner.py` | `test_el_motor_en_vivo_usa_defuzzy_json_sin_pasar_por_la_simulacion` |
| A3 | `write_float_batch` se tragaba el error **por tag** (`except Exception: pass`): un SP rechazado por el DCS se marcaba como escrito y el write-on-change no lo reintentaba nunca | `connectors/kepserver.py` | `test_un_setpoint_rechazado_por_el_dcs_se_reintenta` |
| A4 | `self._last_error = None` incondicional al final del tick borraba el error de escritura que `_write_setpoints` acababa de registrar | `web/state.py` `_run_tick` | idem A3 |
| A5 | Un tag LIM ilegible se rellenaba con `0.0` y el tick seguía: con `lmin=lmax=0` un fuzzy `norm` da OK=1.0 permanente y un `high` mide contra una escala inventada | `web/state.py` `_read_tags` | `test_un_limite_no_legible_saca_la_pv_del_fuzzy_sin_abortar` |
| A6 | **Fail-open**: `NOT` sobre una variable que el pipeline no produjo valía `1.0` → la regla disparaba siempre con belief pleno y un permisivo mal referenciado quedaba concedido para siempre | `core/engine/motor.py` `evaluar_condicion` | `test_negar_una_variable_ausente_no_dispara_la_regla` |
| A7 | Una regla mal formada (típico: wait sin `duracion_s`) reventaba `evaluar_reglas` **entero** — se perdía el tick, las reglas críticas de otros bloques, la escritura al DCS y hasta la traza | `core/engine/motor.py` + validador en `web/api/config.py` | `test_una_regla_con_wait_mal_formado_no_mata_el_tick` |
| A8 | Las acciones del editor de reglas salían de un catálogo hardcodeado del espesador viejo (18 acciones inexistentes) | `web/state.py` `acciones_disponibles()` | `test_acciones_disponibles_sale_de_defuzzy_json` |
| A9 | Una PV sin fuzzy **bloqueaba el arranque**; ahora degrada con aviso | `web/state.py` `_init_state` | `test_arranca_con_aviso_si_una_pv_no_tiene_modelo_difuso` |
| A10 | La simulación offline moría con `KeyError` de la primera PV: generaba las columnas del espesador original | `web/api/se.py` `_datos_desde_generador` | `test_la_simulacion_genera_las_columnas_del_contrato` |
| A11 | Suspender un tag **borraba** sus rangos min/max/ruido del generador | `web/state.py` `sincronizar_con_tags` | `test_suspender_un_tag_conserva_sus_rangos` |

Observabilidad agregada:

- Panel **7b · Últimos disparos** en la traza: historial global de 50 disparos con el
  efecto real sobre el SP, independiente del anillo de 60 ticks.
- Aviso explícito de **setpoint saturado** en el paso 8.
- **Grabador por regla**: se elige una regla en el paso 6 y se graba su historial a
  demanda (500 eventos, por transición con contador de repeticiones; cada disparo es
  entrada propia). Página `/espesador/historial?regla=<id>`, una regla por pestaña,
  varias grabando a la vez. API: `GET/POST /api/se/grabador[/<regla_id>[/start|stop]]`.
  Detalle de diseño en `CLAUDE.md` → *Observabilidad: tres ventanas de tiempo*.

**Limitaciones conocidas del grabador** (a afinar si hace falta): vive en memoria —
sobrevive a stop/start del motor, se pierde al reiniciar el proceso Flask; no hay
exportación a archivo más allá de pedir el JSON de la API; y el buffer descarta lo más
viejo al llegar a 500 (la página lo avisa).

### A12 · Etapa 1 de la traza contra el estándar del supervisor (2026-08-21)

El estándar pide que la etapa de **Lectura** no bloquee: si falta una variable, alertar,
y solo si alguna etapa posterior la usa. Se implementó completo.

| Qué era | Dónde | Test |
|---|---|---|
| `start()` se negaba a arrancar con el mapeo tag↔rol incompleto — y el catálogo de roles sale del contrato, así que declarar una variable no instrumentada dejaba a la planta sin experto | `web/state.py` `start` | `test_motor_arranca_degradado_con_mapeo_incompleto` |
| No existía el criterio "se usa aguas abajo": se exigía todo el contrato | `web/state.py` `roles_en_uso()` | `test_en_uso_*` (6 tests) |
| Un fuzzy `high` exigía `lmin` además de `lmax` | `web/state.py` `limites_requeridos()` | `test_en_uso_solo_pide_el_limite_que_el_tipo_de_fuzzy_necesita` |
| Una PV sin filtro impedía arrancar | `web/state.py` `_init_state` | `test_pv_sin_filtro_recibe_el_default_y_arranca` |
| Un SP sin límites impedía arrancar; ahora se inhibe solo esa familia | `web/state.py` `_init_state` + `_write_setpoints` | `test_sp_sin_limites_se_inhibe_y_el_resto_corre` |
| Un SP ilegible impedía arrancar; ahora se inhibe y se reengancha solo (bumpless) | `web/state.py` `_reintentar_sp_inhibidos` | `test_sp_ilegible_se_inhibe_y_se_recupera_solo` |
| Una PV o un límite con calidad mala **abortaban el tick entero** | `web/state.py` `_read_tags` / `_run_tick`, `runner.py` | `test_pv_caida_no_aborta_el_tick_pero_no_se_fuzzifica`, `test_un_limite_no_legible_saca_la_pv_del_fuzzy_sin_abortar` |
| `pend_modelos=None` en el motor en vivo cargaba `PEND_MODELOS` del espesador | `web/state.py` `_run_tick` | cubierto por el suite completo |

Detalle de diseño en `CLAUDE.md` → *Degradar por partes, nunca adivinar*.

### A13 · Etapa 7 (waits) contra el estándar del supervisor (2026-08-21)

El estándar pide: *"si la regla no se puede activar por límites de SP o tracking o
porque no cumple las condiciones, el wait no se debe reiniciar"*. De los tres casos, el
único que ya funcionaba era el tercero (sin condiciones la regla ni llega a armar nada).

| Qué era | Dónde | Test |
|---|---|---|
| `_activar_waits` corría en el disparo, **antes** del defuzzy, del clipeo y de la escritura: un SP saturado en su límite armaba el wait igual y la regla quedaba muda minutos sin haber movido nada | `core/engine/motor.py` | `test_el_wait_no_arranca_si_el_sp_esta_saturado_en_su_limite` |
| Una acción que lanzaba excepción también dejaba el wait armado | `web/state.py` `_run_tick` | `test_una_accion_que_falla_no_reinicia_el_wait` |
| El vivo y la simulación podían diverger en el temporizador | `runner.py` | cubierto por el suite |
| `tz["waits_activos"]` se tomaba antes de aplicar las acciones: mostraba waits que después se revertían | `web/state.py` `_run_tick` | `test_el_wait_no_arranca_si_el_sp_esta_saturado_en_su_limite` |
| Un disparo efectivo y uno sin efecto se veían idénticos en traza, historial y grabador | traza, `historial.html`, `_grabar_evaluaciones` | — |

Propiedades que **no** cambiaron, y hay tests que las fijan: el wait sigue armándose en
el disparo (bloquea a las reglas que faltan evaluar en el mismo barrido —
`test_el_wait_sigue_bloqueando_dentro_del_mismo_barrido`) y revertir restaura el estado
exacto, así que un wait que ya venía corriendo por otra regla conserva su cuenta
(`test_revertir_conserva_la_cuenta_de_un_wait_que_ya_venia_corriendo`).

**Queda abierto del mismo punto del estándar:** el caso "por tracking", que depende de
B3.6 (`tracking.json` no lo lee nadie en runtime). El enganche ya está: cuando el
tracking inhiba una familia, esa acción no moverá el SP y el wait no arrancará.

Detalle de diseño en `CLAUDE.md` → *El wait cuenta solo si la regla actuó*.

### A14 · Etapa 3 (variables calculadas) contra el estándar del supervisor (2026-08-21)

El estándar pide: *"calcula variables a partir de uno o más PV, generando una variable
nueva que se puede usar en reglas y que puede asignarse como PV"*. No funcionaba nada de
eso en vivo: las definiciones existían solo en el runner de CSV.

| Qué era | Dónde | Test |
|---|---|---|
| `calcular_variables_df()` se llamaba **solo** en el runner batch; `_run_tick` nunca la llamaba, así que una calculada creada en la interfaz no existía para el motor | `web/state.py` + `core/variables/online.py` (nuevo) | `test_el_motor_en_vivo_calcula_las_variables_de_variables_json` |
| El evaluador batch cuenta la ventana en **muestras** con un `dt` fijo: en ciclo libre eso significa 30 min a una cadencia y 20 s a otra | `core/variables/online.py` | `test_online_la_ventana_se_mide_en_segundos_no_en_muestras` |
| Una fuente ausente lanzaba `KeyError`, que en vivo se llevaría el tick entero | `core/variables/online.py` | `test_online_una_fuente_ausente_no_tumba_las_demas`, `test_una_calculada_sin_su_fuente_no_se_calcula_y_el_tick_sigue` |
| El validador de reglas ofrecía `VARIABLES_EXTERNAS`, las calculadas **hardcodeadas del espesador**: una calculada nueva se podía definir y no se podía usar | `web/state.py` `variables_validas()` | `test_una_calculada_es_variable_valida_en_una_regla` |
| Una calculada no se podía fuzzificar: no aparecía en el catálogo del editor de fuzzy y no tenía de dónde sacar `lmin`/`lmax` | `web/api/config.py` `entradas_fuzzificables()`, `web/state.py` `limites_fijos_calculadas()` | `test_una_calculada_se_fuzzifica_con_sus_limites_de_respaldo`, `test_los_roles_lim_incluyen_a_las_calculadas` |
| `roles_en_uso()` no seguía la cadena: una PV que solo alimentaba a una calculada se veía como "no la usa nadie" | `web/state.py` | `test_una_calculada_en_uso_arrastra_a_sus_pv_de_origen` |
| `_defaults_variables()` sembraba las crudas y calculadas del espesador | `web/api/config.py` | — |
| Una ventana de 30 min a 10 tick/s son 18.000 muestras por variable | `core/variables/online.py` | `test_el_buffer_no_crece_con_la_velocidad_del_lazo` |
| El paso 3 de la traza mostraba los `_lmin`/`_lmax` y los setpoints: datos del DCS, no derivadas de nada | `traza.html` | — |

**Queda abierto:** una calculada no puede ser el **origen de una PV del contrato**
(`construir_mapeo` sigue exigiendo un tag OPC-UA por rol PV). Y el `rolling_std` se
calcula sobre la señal filtrada, así que mide menos dispersión que sobre la cruda; si
hiciera falta medir el ruido, habría que hacer la fuente elegible por definición.

Detalle de diseño en `CLAUDE.md` → *Las variables calculadas existen en vivo*.

### A15 · Etapa 4 (fuzzy de pendiente) contra el estándar del supervisor (2026-08-21)

El estándar pide tres cosas del fuzzy pendiente: que se pueda **crear**, que se le pueda
**asignar el tiempo** de cálculo, y que se puedan crear **uno o más con tiempos
distintos**. No cumplía ninguna.

| Qué era | Dónde | Test |
|---|---|---|
| `PEND_MODELOS` hardcodeado con las 4 variables calibradas del espesador + 5 placeholders; no había equivalente a `fuzzy.json` | `core/fuzzy/pendientes.py` (nuevo) + `pendientes.json` | `test_el_motor_en_vivo_evalua_las_pendientes_de_pendientes_json` |
| **El tiempo no era asignable**: `evaluar_pendiente_var(..., ventana_s=60.0)` era un default que nadie pasaba nunca, así que toda pendiente medía un minuto | `core/fuzzy/pendientes.py` | `test_la_ventana_de_la_pendiente_es_configurable` |
| **Una sola por variable**: la clave era `pend_<var>` | idem | `test_varias_pendientes_sobre_la_misma_variable` |
| Con la ventana a medio llenar reportaba pendiente 0 → la regla veía `STABLE` sin que el motor pudiera sostenerlo | idem | `test_la_pendiente_no_se_afirma_hasta_cubrir_la_ventana` |
| El editor de reglas ofrecía `pend_<var>` para toda PV: variables que el motor no podía producir | `web/state.py` `variables_disponibles()` | `test_una_pendiente_es_variable_valida_en_una_regla` |
| Las etiquetas de una pendiente no entraban al catálogo del editor | `web/state.py` `etiquetas_disponibles()` | `test_las_etiquetas_de_una_pendiente_entran_al_catalogo` |
| `roles_en_uso()` no veía la PV que solo alimentaba a una pendiente | `web/state.py` | `test_una_pendiente_en_uso_arrastra_a_su_variable` |
| Vivo (`pend_modelos=None` → espesador) y simulación (`{}`) no se comportaban igual | `runner.py`, `web/api/se.py` | cubierto por el suite |
| Una config a medio escribir podía romper el arranque | `construir_registry_pendientes` | `test_una_pendiente_mal_formada_no_rompe_el_registry` |

Interfaz: bloque **Fuzzy de pendiente** dentro de la página Fuzzy, con `+ Pendiente`,
`+ Fila`, `+ Columna`, selector de variable fuente (PV o calculada) y ventana en minutos.

**Nota de calibración:** la ventana tiene que ser **mayor que el período del lazo**. Con
una ventana más corta que el tiempo entre ticks, el buffer nunca junta los 3 puntos
mínimos y la pendiente no se produce nunca. Con ventanas en minutos —el caso real— esto
no aparece.

Detalle de diseño en `CLAUDE.md` → *Las pendientes son configurables*.

### A16 · Tracking conectado (B3.6) y contrato en caliente (2026-08-21)

Con esto el estándar del supervisor queda cubierto de punta a punta.

**Tracking (B3.6) — cierra también la mitad pendiente de la etapa 7.**

| Qué era | Dónde | Test |
|---|---|---|
| `tracking.json` se configuraba y **nadie lo leía en runtime**: la página prometía una verificación que no existía | `web/state.py` `_evaluar_tracking` | `test_el_tracking_retiene_la_familia_hasta_que_el_proceso_alcanza` |
| El wait se reiniciaba aunque el proceso no hubiera alcanzado el setpoint | idem | `test_el_tracking_impide_que_el_wait_se_reinicie` |
| Sin readback legible no había criterio | idem | `test_sin_readback_legible_el_tracking_es_fail_closed` |

La acción sobre una familia retenida **se descarta, no se acumula**: reteniendo solo la
escritura, el SP interno seguiría subiendo mientras el proceso quedó atrás y al liberarse
se mandaría un salto de varios pasos juntos. Y como el SP no se mueve, el mecanismo de
A13 ya no arma el wait — el caso "por tracking" del estándar sale gratis.

**Contrato en caliente.**

| Qué era | Dónde | Test |
|---|---|---|
| `config.py` leía `contrato.json` una sola vez al importarse: guardar exigía reiniciar el servicio, y mientras tanto la pantalla mostraba una lista y el catálogo de roles otra | `config.recargar_contrato()` | `test_recargar_contrato_muta_en_el_mismo_objeto`, `test_el_catalogo_de_roles_sigue_al_contrato_recargado` |
| `VAR_NOMBRES_RESERVADOS` era una foto al importar: reservaba las variables del contrato viejo y dejaba libres las del nuevo | `web/api/config.py` `var_nombres_reservados()` | `test_los_nombres_reservados_siguen_al_contrato` |

La clave es que se **muta en el mismo objeto**: medio proyecto hace `from config import
VARIABLES_PROCESO`, así que reasignar el nombre no llegaría a quien ya lo importó.

**Deliberadamente fuera de alcance:** el motor en marcha no recarga el contrato. Cambiarlo
bajo un lazo de control significaría fuzzificar contra otra escala a mitad de tick; se
toma en el próximo `start()` y la respuesta de la API trae `motor_corriendo`.

Detalle de diseño en `CLAUDE.md` → *El tracking espera al proceso* y *El contrato se
recarga en caliente*.

---

## B · Pendiente de afinación

### B1 · Riesgo operativo (tocan lo que llega al DCS)

**B1.1 — `stop()` no verifica que el hilo murió.** `web/state.py:1089`

```python
self._stop_event.set()
if self._thread:
    self._thread.join(timeout=3)   # sin comprobar is_alive()
self._running = False
```

Si el KEPserver está colgado, el `join` vence, `_running` pasa a `False` y `start()`
hace `_stop_event.clear()`: el hilo viejo despierta y **quedan dos motores escribiendo
setpoints al DCS** sobre el mismo estado. Arreglo: comprobar `is_alive()` tras el
join, negarse a arrancar mientras el anterior siga vivo, y dar `_stop_event` por
generación (un evento nuevo por arranque en vez de reutilizar el mismo).

**B1.2 — Sesión OPC-UA nueva por tick, sin timeout.** `connectors/kepserver.py`

Cada lectura y cada escritura abre `Client(get_url())`, conecta y desconecta. En ciclo
libre son decenas de sesiones por segundo, y `connect()` no tiene timeout: es
exactamente el escenario que dispara B1.1. Arreglo: cliente persistente con reconexión
y timeout explícito.

**B1.3 — Sin resincronización con el DCS después del arranque.**

El arranque es bumpless (parte del valor vivo del SP), pero después el motor solo
confía en su copia interna. Si el operador mueve el SP a mano, el SE no se entera:
al siguiente disparo escribe `su_valor + paso` y **revierte la intervención de un
salto**. Arreglo: releer el SP del DCS cada N ticks y, si difiere de `_sp_escritos`
más que la banda muerta, adoptar el valor del operador (y dejarlo en la traza).

**B1.4 — La calidad OPC-UA es cosmética.** `connectors/kepserver.py:160`

`read_tags_batch` usa `node.get_value()`, que descarta el `StatusCode`, y rellena
`"quality": "Good"` si no hubo excepción. Un instrumento congelado o en `Uncertain`
es indetectable, y la columna QUALITY de la UI dice siempre *Good*. Arreglo: usar
`get_data_value()` y propagar `StatusCode` + `SourceTimestamp`; con eso se puede
detectar valor congelado (timestamp que no avanza).

### B2 · Funcionalidad prometida que no existe en vivo

**B2.1 — Variables derivadas: solo offline.** `runner.py` `calcular_variables_df`

`SEEngine._run_tick` nunca la llama. Las definiciones de `variables.json` están
muertas en el camino online, y el panel *3 · Variables derivadas* de la traza en
realidad muestra los `_lmin`/`_lmax` y los setpoints. **Una PV no puede ser una
variable calculada.**

**B2.2 — Pendientes (`pend_<var>`) cableadas al espesador.**
`fuzzys_models_espesador.py` `PEND_MODELOS`

No hay equivalente a `fuzzy.json` para las pendientes: con este contrato, `fuzzy_out`
no contiene ningún `pend_*`. Agravante: el editor de reglas **ofrece y acepta**
`pend_hopper_nvl_pv_a`, que el motor no puede producir jamás → `no_evaluable`
permanente. Y la simulación pasa `pend_modelos={}` mientras el vivo pasa `None`: con
un contrato que sí tenga `torque`, una regla de tendencia **dispara en vivo y nunca
en la simulación**.

**B2.3 — Permisivos validados contra la planta equivocada.** `web/state.py:208`

`VARIABLES_VALIDAS` se arma con `PERMISIVOS.keys()` — los permisivos **hardcodeados
del espesador**, no los de `permisivos.json`. El validador de reglas rechaza cualquier
`__PERM_*` real y acepta los del espesador, que nunca se inyectan. Además el editor
de permisivos ofrece `fuzzy_vars` del espesador viejo.

**B2.4 — El validador de reglas es más pobre que el motor.** `web/api/config.py`

No soporta `NOT` ni OR anidado dentro de AND, que el motor sí evalúa: hay reglas
válidas para el motor que no se pueden crear ni editar por la UI, y editar una regla
legada con `NOT` la rechaza.

### B3 · Configuración y consistencia

**B3.1 — El contrato está congelado al importar.** `config.py:72,109,157`

`VARIABLES_PROCESO`, `SETPOINT_KEYS`, `LIMITES_SP_CONTRATO` y
`VARIABLES_CRUDAS_REQUERIDAS` se leen una vez al importar `config.py`. Guardar el
contrato reescribe el archivo pero **no toca la memoria del proceso**: hasta reiniciar,
el Mapeo de tags sigue mostrando las variables viejas y ampliar un `limites_sp` no
surte efecto ni con stop/start del motor. Ya está decidido el arreglo: banner de
"núcleo desincronizado" comparando disco contra memoria (pendiente de implementar).

**B3.2 — Roles huérfanos silenciosos.** `web/state.py` `normalizar_rol`

Al renombrar una variable en el contrato, los tags que la usaban quedan con un rol que
ya no existe y se descartan **sin avisar**: aparecen como *(sin asignar)*. Decidido:
marcarlos en rojo con el nombre viejo (pendiente de implementar).

**B3.3 — `or None` cae a los modelos del espesador.** `web/state.py:1565`

```python
self._fuzzy_modelos = construir_registry_fuzzy(cargar_fuzzy_json()) or None
```

Si `fuzzy.json` queda `{}` (botón *Vaciar todas*) o todas sus entradas son inválidas,
el registry vacío se convierte en `None` y `runner._evaluar_estado_fuzzy` importa
`FUZZY_MODELOS` **del espesador viejo** — mientras el arranque avisa "PV sin modelo
difuso", exactamente lo contrario de lo que pasa. El camino de simulación usa `or {}`.
**Es una diferencia de un carácter.**

**B3.4 — Escrituras de JSON sin atomicidad ni lock.**

`_save_tags` / `_save_defuzzy` / etc. escriben directo sobre el archivo. Un corte a
mitad de `json.dump` deja el archivo corrupto, y el loader entonces cae a la plantilla
de Python. Arreglo: escribir a `.tmp` + `os.replace`, con un lock por archivo.

**B3.5 — `construir_registry_fuzzy` no valida el eje.** `core/fuzzy/templates.py:162`

Solo exige `len(offset) >= 3`. La API sí valida crecimiento estricto, pero el camino
vivo lee el JSON directo: un `offset` desordenado (edición a mano) hace que `np.interp`
devuelva basura **en silencio**. Mismo riesgo ya documentado para `belief_axis`.

**B3.6 — `tracking.json` no lo lee nadie.** La página Tracking PV-SP configura, guarda
y valida, pero ningún módulo del runtime consulta el archivo: el bloqueo por
seguimiento PV↔SP **no existe todavía**.

### B4 · Menores (no bloquean, ensucian el diagnóstico)

- **Bloque desconocido = regla invisible.** `core/engine/motor.py`: `evaluar_reglas`
  solo itera los bloques de `config.BLOQUES`. Una regla con `bloque: "seguridad"`
  editada a mano no dispara **y no aparece en la traza**. Debería reportarse como
  `invalida`.
- **La prioridad no ordena lo que parece.** Ordena *dentro* del bloque, pero todas las
  reglas que pasan disparan. Su único efecto real es quién se queda el wait compartido:
  la de menor prioridad se reporta *"Bloqueada por wait activo"* por un wait que su
  hermana creó en el mismo tick, tres líneas antes. Entre bloques la prioridad no se
  compara nunca (lo decide `level`).
- **Los waits son globales por `wait_id`**, no "por familia SP + regla" como dice la
  documentación vieja. Dos reglas que comparten wait se bloquean entre sí cruzando
  bloques.
- **Una acción mala aborta las demás acciones de la regla**, pero los waits ya se
  armaron: un typo deja el lazo desatendido lo que dure el cooldown.
- **`CERCA_ALTO` / `CERCA_BAJO` dependen de que existan `OK` y `HIGH`/`LOW`
  literales** (`core/fuzzy/evaluator.py`). Con etiquetas libres (`CRITICO_ALTO`…) esas
  derivadas desaparecen, pero el editor las sigue ofreciendo: la regla se acepta y
  queda con μ=0 permanente, reportada como *descartada*, no como no evaluable.
- **`core/states/builder.py` `pertenencia()` lanza `ValueError`** con cualquier
  etiqueta fuera de ALTO/OK/BAJO. Es API de autoría, no la usa el tick.
- **`_defaults_fuzzy()` indexa `HIGH`/`OK`/`LOW`** en cada `GET /api/fuzzy`. Hoy no
  rompe porque los defaults son los del espesador; es una asunción muerta.
- **`belief_accion`** se calcula en `motor.py` y nadie lo consume.
- **`weight` sin rango validado**: `weight=5` da `belief=5.0` en la traza (el defuzzy
  lo clipea después, pero el número miente).
- **`dt_min_floor`** (`core/fuzzy/evaluator.py`) es un piso de *pendiente*
  (unidades/min), no de tiempo. Confunde al calibrar.
- **`min_belief` filtra el belief, no la activación.** Una pertenencia de cola de 0.02
  en el `if`, con una `fuerza` a 0.98 en otra etiqueta, dispara con belief 0.98 y mueve
  el SP casi el paso máximo. El único umbral del antecedente es `> 0.0`.

---

## C · Notas de calibración (no son bugs)

- Los límites del hopper están puestos **a propósito** fuera de rango para que la regla
  `A1_Prueba_Hopper_Lmita` dispare siempre; el wait está en 10 s en vez de 900 s.
- `velocidad_sp` llegó a **100.00 con límites 50–100**: está saturado. La regla sigue
  disparando y el paso se clipea a cero. Para verla actuar hay que bajar el SP en el
  DCS o ampliar el `lmax` del contrato.
- `velocidad_pv` no tiene fuzzy a propósito (es readback). Desde A9 eso ya no bloquea
  el arranque, solo avisa.

## D · Documentación desactualizada

`CLAUDE.md` y el resumen del pipeline que circula por fuera describen estado viejo:
`reglas.json` vacío, 18 tags de límite faltantes, el desajuste
`sp_vel_bomba` ↔ `velocidad_salida_del_se` (resuelto: hoy la familia es
`velocidad_sp`) y el defuzzy en vivo como bug abierto (cerrado en A2).

## E · Cómo verificar

```bash
python -m pytest tests/ -q                      # 79 pass, 6 skip
python -m pytest tests/ -q -k "defuzzy or dcs or limite or wait or grabador"
```
