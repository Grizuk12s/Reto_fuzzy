# Afinación pendiente — SE HUTBAY Espesador

**Fecha:** 2026-08-20 · **Alcance:** pipeline en vivo (`SEEngine`), simulación offline,
páginas Fuzzy / Defuzzy / Reglas / Traza / Historial / Tags.

**Fuera de alcance** (no se auditaron; no están acá porque no se miraron, no porque
estén limpios): persistencia PostgreSQL, licenciamiento, heartbeat y Explorador de
Series.

Todo lo de este documento está **verificado ejecutando el código**, no leyéndolo:
cada hallazgo tiene su escenario de reproducción. Lo que no se pudo confirmar está
marcado como *sospechado*.

Cobertura: `python -m pytest tests/ -q` → **180 pasan, 2 skip** (al 2026-08-25).

> **No queda nada abierto en B1 (riesgo operativo).** Lo que sigue en B2/B3/B4 es
> funcionalidad y consistencia, y buena parte se cerró en el camino. Lo que falta de
> verdad para operar no es software: son las reglas, los permisivos y la calibración del
> defuzzy, que se definen con el experto de planta.

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

### A17 · La red de seguridad dependía de la calibración de la planta (2026-08-25)

Al ir a correr el suite para validar B1.2 estaba en **rojo: 6 fallos**, y ninguno tenía
que ver con el cambio. El fixture leía tres archivos de la config **viva**, así que el
resultado de los tests dependía de lo que el operador tuviera cargado. Es el mismo
problema que A3/*"los tests estaban atados al contrato del espesador"*, un nivel más
abajo: la red de seguridad prueba el **cableado** y no puede romperse porque alguien
calibre una regla.

| Qué era | Efecto |
|---|---|
| `TRACKING_JSON` apuntaba a la config viva. Cuando `tracking.json` ganó `pv_key: velocidad_pv` con `rango: 0.3`, el readback sintético del fixture (50.0) quedó lejísimo del SP (42.0) | El tracking **retenía la familia en todos los tests**: 4 fallos |
| `cargar_reglas_json` / `cargar_defuzzy_json` leían la config viva. `reglas.json` ganó una segunda regla de prueba con `BAJAR_SP_VEL` (paso −2,0) | El SP se movía solo (42 → 40) en tests que asumían que nadie lo tocaba |
| El DCS falso devolvía `sp_en_dcs` **fijo**, ignorando las escrituras | 2 fallos, y algo peor: ver abajo |

`config_completa` ahora parte de tracking, reglas y defuzzy **vacíos**. Los tests que los
necesitan se los declaran ellos (`_motor_con`, `_motor_con_tracking`) desde el cuerpo, que
corre después de los fixtures y por lo tanto gana.

**Lo que el DCS falso sin eco escondía.** No era solo un fixture pobre: era la premisa de
`_reconciliar_sp_con_dcs` (B1.3). La reconciliación decide que hubo intervención manual
cuando el DCS difiere de lo último que el SE escribió. Con un DCS que ignoraba las
escrituras y devolvía siempre el mismo número, **el SE leía su propia escritura como si el
operador la hubiera deshecho y la revertía en el tick siguiente**. Ahora `_write` asienta
lo aceptado y `_read` lo devuelve, que es lo que hace un DCS real. Lo rechazado no se
asienta — es lo que sostiene el reintento del write-on-change.

Con el eco en su lugar se pudo además **cubrir B1.3, que se había implementado sin ningún
test**: `test_el_se_adopta_el_sp_que_el_operador_movio_en_el_dcs` y
`test_no_se_reconcilia_un_sp_que_el_se_nunca_escribio`.

Y un fallo real quedó a la vista al destapar los otros: con el tracking retenido, el test
del reenganche bumpless (`test_sp_ilegible_se_inhibe_y_se_recupera_solo`) **pasaba por el
motivo equivocado** — la retención impedía la escritura, así que la referencia quedaba
intacta por accidente y no porque el reenganche funcionara.

> **Lección para la próxima:** `_leer_json(<ALGO>_JSON, ...)` en un test es una dependencia
> con la planta. Los que todavía la tienen son `PERMISIVOS_JSON`, `PENDIENTES_JSON`,
> `VARIABLES_JSON`, `ESTADOS_JSON_PATH` y `WAITS_JSON_PATH`: hoy están vacíos o casi, así
> que no molestan, pero van a morder igual que estos tres el día que se llenen.

### A18 · El aviso de calidad se borraba en el mismo tick que lo creaba (2026-08-25)

Salió al validar B1.4 en el contenedor: el detector marcaba las PV congeladas en la traza
y `GET /api/alerts` devolvía **la lista vacía**.

`_run_tick` cierra la categoría `kep` al final de cada tick que termina bien. Para un
error de **conexión** eso es correcto: un tick sano prueba que la conexión anda. Pero
`_read_tags` publicaba los avisos de **calidad** en esa misma categoría, y un tick sano no
prueba nada sobre los instrumentos — una PV en `Bad` no impide que el tick termine bien.
Resultado: el aviso se auto-resolvía a los pocos milisegundos de nacer y no llegaba nunca
a la pantalla.

Es **preexistente**: afectaba igual a los avisos de *"PV/LIM en uso que no se pudieron
leer"* y *"sensores crudos no legibles"*, que están desde antes. O sea que la degradación
por partes de 2026-08-21 nunca avisó por la interfaz de alertas; solo por la traza.

Arreglo: categoría propia `calidad`, con su propio ciclo de vida — se limpia cuando la
lectura del tick no tuvo nada que avisar, no cuando el tick terminó bien.

Dos detalles que se corrigieron en el camino:

- **El mensaje no lleva el número de segundos.** `AlertCollector.add` deduplica por
  mensaje **exacto**; con el número adentro cada tick generaba una alerta nueva — a
  ~6 tick/s son cientos de miles por día en una lista que se recorre entera en cada `add`.
  Con el mensaje estable se incrementa `count` (verificado: 1 alerta, `count: 61`) y el
  número que cambia vive en la traza, que es su lugar.
- El aviso nombra **solo las PV en uso**, mientras la traza lista todas las estancadas —
  el mismo criterio de `roles_en_uso()` que ya regía el resto.

Tests: `test_el_aviso_de_calidad_sobrevive_a_un_tick_sano`,
`test_el_aviso_de_calidad_se_limpia_cuando_la_senal_vuelve`.

### A19 · Se cerró la lista de JSON que el suite leía de la planta (2026-08-25)

La lección de A17 terminaba nombrando los cinco archivos que seguían apuntando a la
config viva: `PERMISIVOS_JSON`, `PENDIENTES_JSON`, `VARIABLES_JSON`, `ESTADOS_JSON_PATH`
y `WAITS_JSON_PATH`. Estaban vacíos, así que no molestaban — y por eso mismo convenía
cerrarlos antes de que alguien los llene y el suite empiece a fallar por una calibración.

`config_completa` los redirige ahora a temporales. **Los dos últimos importan más de lo
que parece**: `estados.json` y `waits.json` no son configuración, son **estado que el
motor escribe**. Un test que corriera un tick estaba armando waits reales sobre el
archivo de la planta.

Verificado con los mtime del directorio de config antes y después de correr el suite
completo: ningún archivo se tocó.

---

### A20 · El catálogo seguía ofreciendo los permisivos y los estados del espesador (2026-09-01)

Mismo defecto que A8 (`acciones_disponibles`), que `ETIQUETAS_DISPONIBLES` y que
`VARIABLES_EXTERNAS`, en el último lugar donde quedaba: **una foto al importar de la
plantilla de otra planta.**

`web/state.py:595` armaba las variables ofrecidas con
`nombre_variable_permisivo(p) for p in PERMISIVOS.keys()`, y ese `PERMISIVOS` viene por
`permisivos.py` de `processes/espesador/permisivos_config.py`. Con `permisivos.json` en
`{}`, el desplegable de condiciones —el de la página Estados y el del editor de reglas—
ofrecía cinco `__PERM_OPTIMIZAR_*` / `__PERM_PERMITIR_*` de otra planta. **El motor no las
produce**: `_run_tick` evalúa `cargar_permisivos_json()`, que lee el archivo. O sea que la
condición se podía escribir, se guardaba, y quedaba `no_evaluable` para siempre — el mismo
"agravante" que ya se había cerrado con las pendientes.

Arreglo: `permisivos_definidos()` / `nombres_permisivos()` en `web/state.py`, que releen
`permisivos.json` con `_leer_json` en cada llamada. **`_leer_json` y no
`cargar_permisivos_json()`** por el mismo motivo que `roles_en_uso()`: ese cae a la
plantilla del espesador cuando el archivo falta, y acá eso reproduce exactamente el ruido
que se quiere sacar. `/api/meta` lo recalcula por request.

Lo que arrastró:

- **`_defaults_estados()` devolvía `ESTADOS_SERIALIZADOS`**, los estados del espesador.
  Mientras `estados.json` existiera no se notaba, pero el archivo se siembra con ese
  default si falta **y el botón "Restaurar default" lo reescribía**. Ahora devuelve `{}` y
  el botón se llama **Vaciar estados**, que es lo que hace. Mismo criterio que
  `_defaults_defuzzy()` y `_defaults_tracking()`.
- **`_defaults_permisivos()`** (`web/api/config.py`) y **`_permisivos_defaults_deepcopy()`**
  (`runner.py`) devuelven `{}`.
- **`correr_prueba_general` caía a `PERMISIVOS`** cuando no se le pasaba
  `permisivos_config`. El motor en vivo usa `cargar_permisivos_json()`, así que ese default
  hacía divergir la simulación de la producción justo en los permisivos. Ahora llama al
  mismo loader.

Verificado: `/api/meta` devuelve `permisivos: []`, `estados: {}` y
`variables: ['hopper_nvl_pv_a', 'velocidad_pv']` — sin un solo `__PERM_`.

### A21 · Las etiquetas de una pendiente no podían llamarse INC, DEC ni STABLE (2026-09-01)

`FUZZY_LABELS_RESERVADAS` era **un solo conjunto para los dos validadores**, y tenía
adentro `INC/DEC/STABLE`. En un fuzzy de PV eso es correcto (ahí significarían
"tendencia" sin serlo); en un fuzzy de **pendiente** prohibía justo las tres etiquetas que
una tendencia quiere usar. Consecuencia concreta: se creaba una pendiente con la plantilla
—que trae INC/DEC/STABLE— y **el guardado se rechazaba**, sin manera obvia de salir salvo
renombrar las tres filas a mano.

Ahora hay `LABELS_RESERVADAS_NUCLEO` (`CERCA_ALTO`, `CERCA_BAJO`, `ON`, `OFF` — las que
`expandir_etiquetas_compuestas` e `inyectar_permisivos_en_fuzzy_out` generan pase lo que
pase), y encima de ella `FUZZY_LABELS_RESERVADAS = núcleo | {INC, DEC, STABLE}` para las
PV y `PENDIENTE_LABELS_RESERVADAS = núcleo` para las pendientes.

Lo que se cerró junto:

- **El nombre de fila de una pendiente se edita en la página**, como en el fuzzy de PV.
  Antes era un `<th>` de texto plano: la única forma de renombrar era Quitar + `+ Fila`.
  El `offset` queda fuera: es el eje, no una etiqueta.
- **El validador de pendiente aplica las mismas reglas de nombre que el de PV**
  (`FUZZY_LABEL_RE`, largo máximo, sin prefijo `NO_`/`NO-`, sin duplicados). Antes solo
  exigía "no vacío", así que una etiqueta con espacios se guardaba y ninguna regla podía
  nombrarla.
- **`_pendientes_labels_huerfanas()`**: renombrar o quitar una fila que una regla nombra
  se rechaza con **409**, con el escape `__forzar__`, igual que en el fuzzy de PV. Hacía
  falta *porque* ahora se puede renombrar: desde el JSON un renombre es un borrado + un
  alta, y sin esto la regla quedaba muda en silencio. `GET /api/pendientes` expone
  `uso_en_reglas` para que la página lo avise **antes** de guardar (borde celeste + tooltip
  con los ids de regla).
- **`- Columna`**, que faltaba. Quita una columna **interior** (los extremos definen el
  rango, sacarlos cambia lo que la tabla significa) y no baja de 3 puntos, que es el piso
  del validador.
- El botón **Quitar** de una fila identifica por **posición y no por nombre**: el commit
  de los inputs puede acabar de renombrar esa fila, y el `delete` por nombre viejo no
  borraba nada ni avisaba.

### A22 · Seis tests fallan por la calibración de la planta — reincidencia de A17 (2026-09-01)

Al 2026-09-01 `python -m pytest tests/ -q` da **194 pasan, 6 fallan, 7 skip**. Los seis
son de setpoints (`test_arranque_bumpless_parte_del_sp_del_dcs`,
`test_sp_sin_limites_se_inhibe_y_el_resto_corre`,
`test_el_wait_no_arranca_si_el_sp_esta_saturado_en_su_limite`,
`test_la_regla_saturada_puede_reintentar_al_tick_siguiente`,
`test_el_rate_limit_rampea_en_vez_de_descartar`,
`test_una_pausa_larga_no_compra_un_salto_grande`).

**Verificado que son previos**: se revirtieron los cambios de A20/A21 sobre una copia del
árbol y los seis siguen fallando igual. No los introdujo nadie de esta sesión.

El síntoma es el de A17 otra vez, por otra puerta: `config_completa` **sí** monkeypatchea
`cfg.LIMITES_SP_CONTRATO` a `(0.0, 100.0)`, pero el motor termina viendo los `[50, 100]`
de `contrato.json` — el arranque bumpless clipea 37,5 a 50,0 y el test espera 37,5. Es
decir, algo entre `_init_state` y `recargar_contrato()` vuelve a leer el archivo de la
planta después del monkeypatch. **Es un defecto del fixture, no del motor**, y hay que
cerrarlo con el mismo criterio de A19: la red de seguridad prueba el cableado y no puede
depender de que `limites_sp` valga `[50, 100]` en esta planta.

**CERRADO el 2026-09-03**, junto con A29. La causa concreta: `_init_state()` llama a
`recargar_contrato()`, que hace `LIMITES_SP_CONTRATO.clear()` + `update()` **sobre el mismo
objeto** leyendo `CONTRATO_JSON` — o sea, el monkeypatch sobrevivía como referencia pero
con los números de la planta. El arreglo es del fixture, no del motor: `config_completa`
escribe ahora un `contrato.json` temporal y apunta `cfg.CONTRATO_JSON` ahí, y devuelve un
`_ajustar(**campos)` que reescribe ese archivo y recarga. Los tests que cambiaban
`limites_sp` o `rate_sp` desde el cuerpo pasan por ese ajustador; `monkeypatch.setattr`
sobre esos dos dicts **no funciona** y no hay que volver a intentarlo.

Al 2026-09-03: **248 pasan, 0 fallan, 7 skip**.

---

### A23 · Tres errores silenciosos en el editor de reglas (2026-09-01)

Salieron auditando A20: sacar los `__PERM_*` del catálogo es correcto, pero **una regla ya
guardada que nombrara uno** habría caído en esto. Ninguna lo hacía (las 3 de `reglas.json`
usan `hopper_nvl_pv_a` con HIGH/LOW/OK), así que no hubo daño — pero el mecanismo estaba
armado y se dispara con cualquier borrado futuro: una PV que sale del contrato, una fila de
fuzzy renombrada, un estado borrado.

**1 · El `<select>` se autocompletaba con el primer valor de la lista.**
`_selVar` / `_selLbl` / `_selEstado` construían las opciones y marcaban `selected` la que
coincidiera con el valor guardado. Si el valor guardado ya no estaba en el catálogo,
**ninguna quedaba marcada y el navegador selecciona la primera**. El operador abría la
regla, veía `hopper_nvl_pv_a / LOW` donde el JSON decía otra cosa, guardaba, y la regla
cambiaba de significado sin que nadie lo pidiera. Silencioso, y en lo que llega al DCS.

Ahora todos pasan por `_selCatalogo()`, que **conserva el valor huérfano como opción
propia**, en rojo y con tooltip. Mismo criterio que `_selAccion` ya usaba con las acciones
que salen del defuzzy.

**2 · `saveRule` descartaba un estado inexistente sin decir nada.**
Era literalmente `if (est && est.condicion) ifItems.push(...)`. Un estado borrado hacía que
la condición **desapareciera del `if`**: la regla se guardaba más laxa que lo que el
operador tenía en pantalla — dispara en condiciones en las que antes no disparaba. Ahora
corta con un aviso.

**3 · No había forma de ver qué reglas quedaron colgadas sin abrirlas una por una.**
`_renderHoja()` pinta en rojo, con ⚠ y tooltip, la variable o la etiqueta que ya no existe,
en la lista de reglas y en la columna Fuerza — como `_tagAccion` con las acciones.

Encima de eso, `_huerfanosEnModal()` **impide guardar** mientras quede un huérfano, y
`saveEstadoTab` hace la misma comprobación en el modal de Estados.

> Al escribir esto apareció un cuarto, en el código nuevo: `_escapeHtml` **no escapa
> comillas**, y se estaba usando para `title="..."`. La comilla del propio mensaje cerraba
> el atributo antes de tiempo. Se agregó `_escapeAttr()` para texto dentro de atributos.
> Lo cazó el test en node, no la lectura.

---

### A24 · Estados: anidamiento, referencia por nombre y aviso de copia vieja (2026-09-01)

**El problema de fondo.** Una regla no guarda una referencia al estado: guarda la **copia**
de sus condiciones, porque eso es lo que `evaluar_condicion` sabe evaluar (baja por AND/OR
y no sabe que existen los estados). Editabas un estado y las reglas se quedaban con la
definición vieja. Peor: al reabrir la regla, `_matchEstado` intentaba **adivinar** de qué
estado salía esa copia comparando condiciones — y en cuanto el estado se editaba ya no
coincidía, así que la regla dejaba de reconocerlo.

**La decisión (opción A, elegida con el usuario).** Se sigue guardando la copia, pero al
lado va la etiqueta `ref_estado`:

```json
{"ref_estado": "HOPPER_LLENO",
 "AND": [["hopper_nvl_pv_a", "HIGH"], ["velocidad_pv", "OK"]]}
```

`evaluar_condicion` ve el `AND` y evalúa; **la clave de más la ignora**, así que el motor no
cambió ni una línea. Pero ya no hay que adivinar: se sabe exactamente de qué estado salió
cada copia, y se puede detectar cuál quedó vieja. `_matchEstado` queda de respaldo para las
reglas guardadas antes de esto.

Al editar un estado, `PUT /api/estados/<n>` devuelve `usos` con qué reglas y qué estados
quedaron con la definición anterior. **No se propaga solo**: la página pregunta, y con
`propagar: true` se reescriben. El criterio de siempre — nada cambia en silencio lo que
termina llegando al DCS. En la lista de reglas, esas copias aparecen marcadas
`[Estado X ⚠ copia vieja]` en ámbar, y `[Estado X ⚠ borrado]` en rojo si el estado ya no
existe.

**Anidamiento, un solo nivel.** Un estado puede nombrar a otro. La regla se aplica en las
dos direcciones, y por eso **no hay ciclos posibles**:

- el estado referido no puede a su vez contener referencias;
- un estado ya referido por otro no puede ganar referencias después.

La copia del estado anidado se **regenera** en el servidor desde la definición vigente, no
se confía en lo que mande el navegador.

**Grupo OR mixto.** Cada renglón de un grupo OR puede ser una condición suelta **o** un
estado entero. El motor ya sabía evaluar `(A y B) o (C y D)` desde siempre —
`evaluar_condicion` toma el máximo del OR y el mínimo de cada AND — el que no lo aceptaba
era el validador, que solo admitía hojas dentro de un OR y dentro de un AND.

**Dos errores silenciosos que aparecieron haciendo esto:**

1. **`api_create_estado` guardaba el payload sin validar NADA.** Se podía crear un estado
   con una variable inexistente o una etiqueta inventada; el error recién aparecía —callado—
   cuando una regla lo usaba y quedaba `no_evaluable`. Ahora hay
   `_normalizar_estado_payload`.
2. **El `if` de un solo grupo AND se desenvolvía siempre**, en el backend y en la página.
   Una regla cuya única condición era un estado se guardaba como hojas sueltas y **perdía la
   etiqueta**. Ahora el desenvoltorio se saltea cuando el grupo trae `ref_estado`.

Cobertura: `tests/test_estados.py`, 18 tests. Incluye que el motor **evalúe** bien el OR
mixto (0.4 = max(0.0, min(0.9, 0.4))) y el estado anidado, no solo que el JSON se guarde.

### A28 · Botones de "Restaurar default" y "Vaciar" (2026-09-01)

**Los waits del espesador habían vuelto solos.** Al auditar los botones de la interfaz
apareció que `waits.json` tenía los ocho waits de fábrica (`vel_bomba_critico`,
`tonelaje_*`, `floculante_*`...) — variables controladas `sp_vel_bomba`, `sp_tonelaje`,
`sp_floculante`, que este contrato no tiene. Los repuso el botón **"Restaurar default"** de
la página Waits, que llamaba a `_defaults_waits()` → `WAITS_CATALOGO_DISPONIBLES`. Es
exactamente el mismo mecanismo de A20 con los estados, en la última página donde quedaba.

Cambios:

- **`_defaults_waits()` devuelve `[]`** y `WAITS_CATALOGO_DISPONIBLES` queda vacía. Ya no se
  siembran cuando falta el archivo. (La constante se conserva porque `web/api/config.py` la
  importa.)
- **Eliminados los tres endpoints de reset**: `/api/waits/reset`, `/api/permisivos/reset` y
  `/api/variables/reset`, con sus botones y sus funciones JS. No se dejaron "por las dudas":
  un endpoint que repuebla un default es precisamente como volvieron los waits.
- `waits_catalogo.py` sigue existiendo porque `crear_wait()` se usa para dar de alta uno
  nuevo desde la página; lo que se fue son las ocho constantes de fábrica.

**"Vaciar (todas)" en Fuzzy ahora vacía también las pendientes.** Son la misma página y la
misma decisión — "empezar de cero con la fuzzificación" — y dejarlas vivas era peor que
inconsistente: una pendiente sobrevivía a la PV que le da origen y quedaba calculando la
tendencia de una variable sin modelo difuso.

El aviso se arma **antes** de borrar, con las reglas que hoy nombran alguna etiqueta
(`uso_en_reglas`), y dice cuántas pendientes se van. Avisar después de destruir no sirve.
No se niega el vaciado: es un botón explícito de "vaciar todo", y negarlo cuando hay reglas
lo volvería inservible justo cuando hace falta. Al terminar llama a `refrescarMeta()`, o los
desplegables del editor seguirían ofreciendo etiquetas que ya no existen.

**Regresión propia, encontrada y corregida en el momento.** El suite pasó de 6 a 9 rojos al
aplicar esto: A27 hizo que la validación de reglas dependa de `fuzzy.json`, y ni
`config_completa` ni dos tests sueltos apuntaban ese archivo a un temporal — leían el de la
planta, que el usuario acababa de vaciar. Es la lección A17/A19 otra vez, y la conclusión se
repite: **cada archivo nuevo del que dependa la validación hay que traerlo al fixture**.
`config_completa` escribe ahora el mismo fuzzy en disco que le da al loader de `runner`, y
los dos tests sueltos usan el helper `_fuzzy_en_disco()`. Vuelta a 6 rojos, los de siempre.

### A27 · Las etiquetas se ofrecian sin mirar la variable (2026-09-01)

**La causa raíz de tres pedidos distintos** del usuario: "las etiquetas deben corresponder
al tipo de fuzzy", "que CERCA_ALTO esté bien asociado" y "algunas en español y otras en
inglés". Los tres eran el mismo defecto.

`etiquetas_disponibles()` devuelve la **unión** de todas las etiquetas de todos los fuzzy,
más `ETIQUETAS_BASE`. Eso está bien para validar *"esta etiqueta existe en alguna parte"* —
pero era también lo que se **ofrecía**, igual para todas las variables. Consecuencias, las
tres verificadas contra la config viva de la planta:

- `pend_x = LOW` sobre una pendiente cuyas filas son INC/DEC/STABLE
- `nivel = INC` sobre una PV
- `CERCA_ALTO` sobre cualquier variable, cuando el motor solo lo genera si el fuzzy tiene
  literalmente las filas `OK` y `HIGH` (`expandir_etiquetas_compuestas`)

En los tres casos la regla **se guardaba, no daba error, y evaluaba 0 para siempre**. Y con
un grupo OR es peor: el estado muerto no rompe nada visible, el OR devuelve siempre el otro
— creés que tenés dos caminos y tenés uno.

**El arreglo.** `etiquetas_por_variable()` en `web/state.py` arma el catálogo por variable:
las filas de su fuzzy o de su pendiente, `ON`/`OFF` si es un pseudo-permisivo, más las
derivadas. Las derivadas salen de **`etiquetas_derivadas()`**, una función nueva en
`core/fuzzy/evaluator.py` que **también usa el expansor**. Esa es la parte que importa: la
condición de CERCA_ALTO / CERCA_BAJO está declarada **una sola vez**, así que el catálogo de
la interfaz y lo que el motor calcula **no pueden divergir**. Si algún día se deriva de otra
forma (por posición, para soportar filas en español), se cambia en un lugar y las dos cosas
siguen de acuerdo.

Se aplica en tres capas:

1. **Validación** — `_norm_leaf` (if y fuerza) y `_normalizar_estado_payload` preguntan
   `etiquetas_validas_de(variable)`. El mensaje distingue tres casos: la etiqueta no existe
   en ninguna parte; existe pero no en esa variable (y lista las que sí); la variable no
   tiene fuzzy definido.
2. **Ofrecimiento** — `_parVarLbl()` en la página dibuja el par variable+etiqueta y
   **redibuja el selector de etiquetas al cambiar la variable**, conservando la etiqueta si
   la variable nueva también la tiene.
3. **Aviso** — `_renderHoja()` pinta en rojo, en la lista de reglas, la combinación que no
   evalúa, con las válidas en el tooltip.

**Efecto sobre el español:** ya no hace falta ningún trabajo extra. Si las filas de un fuzzy
se llaman `BAJO/NORMAL/ALTO`, el desplegable ofrece eso. Lo que **deja de ofrecerse** es
`CERCA_ALTO`/`CERCA_BAJO`, porque con esos nombres el motor no los calcula — que es la
verdad, dicha en voz alta en vez de en silencio.

**Lo que encontró al aplicarse, en la config real de la planta:** de 7 estados, 3 evaluaban
0 para siempre — `SSSS` (`pend_..._5min = LOW`), `A1` y `1SUB` (ambos sobre `velocidad_pv`,
que no tiene fuzzy creado). Ninguno había dado error nunca.

Cobertura: 8 tests nuevos en `tests/test_estados.py`, incluido
`test_el_catalogo_y_el_motor_no_pueden_divergir`, que compara el catálogo contra la salida
real del expansor para cuatro juegos de etiquetas.

### A26 · `refrescarMeta()` existia y no la llamaba nadie (2026-09-01)

`refrescarEtiquetas()` / `refrescarMeta()` (son la misma funcion) relee `/api/meta` y
actualiza `VARS`, `LABELS` y `ACTIONS`. El unico call site era `refrescarAcciones()`, que
**solo** actualiza `ACTIONS`. Resultado: `VARS` y `LABELS` se quedaban con la foto del
arranque de la pagina.

Consecuencia directa: una variable calculada o un fuzzy de pendiente recien creado **no
aparecia** en el editor de reglas ni en el modal de Estados hasta recargar con F5 — que es
exactamente lo que el usuario reporto como "verifica que se puedan seleccionar los ultimos
agregados".

Y con el aviso de huerfanos de A23 se volvia peor: una variable perfectamente valida pero
ausente de esa lista vieja se marcaba en rojo como "ya no existe" y **bloqueaba el
guardado**. El aviso es correcto; el catalogo contra el que comparaba, no.

Ahora `openModal` (reglas), `openEstadoModal` y `loadEstadosTab` llaman `refrescarMeta()`.
La pestana Estados ademas se recarga en cada visita y no solo la primera:
`_seccionLoaded.estados` hacia que la tabla mostrara la foto de la primera vez.

### A25 · Se borraron las plantillas del espesador (2026-09-01)

Cierre de A20. Eliminados: `estados_espesador.py`, `reglas_espesador.py` y
`processes/espesador/permisivos_config.py`. `permisivos.py` quedó como shim del motor, sin
el dict `PERMISIVOS`.

El que más importaba era el fallback de `cargar_reglas_json`: caía a las **28 reglas del
espesador** cuando `reglas.json` faltaba o estaba corrupto. O sea que un archivo ilegible
no detenía el motor — lo ponía a operar con las reglas de OTRA planta sobre los setpoints de
esta. Ahora devuelve `[]`: "este contrato todavía no tiene reglas", y el motor no mueve nada.

`ESTADOS_SERIALIZADOS` se conserva como `{}` para no romper imports de fuera.

---

### A29 · Los límites se declaran donde se usan, no en el rol del tag (2026-09-03)

El disparador fue el cartel *"Setpoints inhibidos — `velocidad_salida_del_se` sin límites
en `contrato.json`"*: correcto, pero apuntaba a un archivo que **ninguna página editaba**.
Auditándolo aparecieron dos mecánicas distintas y las dos escondidas:

| | Dónde vivía | Quién lo editaba |
|---|---|---|
| Límite de una **PV** | campo `rol` de un tag LIM (`hopper_nvl_pv_a_lmax`) | la página **Tags**, sin decir a qué fuzzy pertenecía |
| Límite de un **SP** | `contrato.json → limites_sp`, números fijos | **nadie** |

Y una restricción que decidía la arquitectura: **un tag tiene un solo rol**, así que
`PU009_Speed_MIN/MAX` no podía ser a la vez la escala de `velocidad_pv` y el tope de
escritura de `velocidad_salida_del_se`. Había que elegir uno o duplicar el tag en
KEPserver.

#### Qué cambió

El cableado se mudó a **quien consume el límite**: `fuzzy.json` para las PV,
`defuzzy.json` para los SP, bajo la clave `limites` y por **nombre de tag**. Un mismo tag
puede acotar varias variables. `bindings_limites()` (en `web/state.py`) es la única fuente
de verdad, y `construir_mapeo()` arma `tag_to_lim` desde ahí — ya no desde `t["rol"]`.

- **`tag_to_lim` cambió de tipo**: `dict[tag, (var, bound)]` → `dict[tag, [(var, bound), …]]`.
  Consumidores tocados: `_read_tags`, `_traza_lectura`, la persistencia, `web/api/se.py`,
  `web/api/tags.py` y dos aserciones del suite.
- **Los SP entraron a `catalogo_roles()["lim"]`**, con una resta: un bound de SP que tiene
  respaldo numérico en `limites_sp` **no cuenta como faltante**. Sin esa resta cualquier
  planta que use los números —o sea, todas hoy— vería el mapeo incompleto para siempre.
- **`_resolver_limites_sp()`** recalcula `self._limites_sp` en cada tick: tag si está
  cableado y se pudo leer, número si no hay tag, y **bloqueo si hay tag y no se pudo
  leer** (fail-closed: no se cae al número, porque el DCS acaba de decir que ese límite no
  es confiable). `lmin >= lmax` también bloquea.
- **La falta de límites dejó de ser un veredicto de arranque.** Antes se inhibía la familia
  en `_init_state()` y quedaba pegada toda la corrida; con límites leídos de un tag eso
  sería falso apenas el tag conteste. Ahora es `_sp_sin_limite`, una cuarta fuente de
  `_familias_sin_escritura()`, y se libera sola.
- **Migración única** (`migrar_roles_lim_a_bindings()`, en `_startup_checks`): adopta los
  roles LIM viejos como bindings. **Es aditiva: no borra el rol.** La primera versión sí lo
  borraba, y el suite de tests —que hace `from app import app`, o sea `_startup_checks()`,
  contra la config **viva** de la planta— dejó los dos tags del hopper sin rol y sin binding
  en una corrida intermedia. Se recuperó a mano, pero la lección queda: **importar `app` no
  puede destruir configuración**. Un rol cuya variable todavía no tiene fuzzy ni tabla queda
  pendiente hasta que la membresía exista, o `crear fuzzy` lo hereda al vuelo
  (`_limites_heredados_del_rol`).
- **`PUT /api/contrato/limites-sp`** edita solo el respaldo numérico. `PUT /api/contrato`
  reemplaza la lista de variables y corre el análisis de impacto: demasiada maquinaria para
  cambiar dos números, y con riesgo de recortar el contrato desde la página equivocada.
- En **Tags**, el rol de un tag LIM pasó a ser derivado y de solo lectura, y el `PUT`
  rechaza asignarlo (permite borrarlo, para limpiar restos sin migrar).

#### Lo que hay que respetar al tocar esto

- **Un binding colgado NUNCA se reemplaza solo.** Es A23 otra vez: un `<select>` cuyo valor
  no está entre sus opciones selecciona la primera, y guardar reescribiría el cableado con
  otro tag en silencio. `limites_huerfanos()` lo reporta, la página lo pinta en rojo y
  bloquea el guardado hasta que alguien elija reemplazo.
- **Los límites viajan con la tabla al guardar.** `_readFuzzyTable()` y
  `_leerFamiliaDelDOM()` los devuelven; si no, agregar una columna o guardar borraría el
  cableado recién elegido — el mismo riesgo que ya documenta `_sincronizarDefuzzy()`.
- **Un binding de un bound que el `type` actual no usa se CONSERVA.** Cambiar un fuzzy a
  `high` y volver a `norm` no puede perder el `lmin`.
- **`bindings_limites()` lee con `_leer_json`**, no con los `cargar_*_json()` de
  `runner.py`: esos caen a la plantilla del espesador y cablearían límites de otra planta.
- **Todo test que toque el motor necesita `config_completa`**, que ahora también apunta
  `DEFUZZY_JSON` y `CONTRATO_JSON` al temporal. Es A17/A19 una vez más: cada archivo nuevo
  del que dependa el motor hay que traerlo al fixture.

#### Segunda vuelta (mismo día): respaldo numérico, handshake y export

- **`limites_num` en las dos páginas.** Debajo de los selectores de tag hay ahora un
  respaldo numérico con la misma forma en Fuzzy y en Defuzzy:
  `{"habilitado": false, "lmin": null, "lmax": null}`. **Nace apagado y se enciende a
  mano** — un respaldo que valiera solo por existir es lo que hacía invisible al viejo
  `limites_sp`. El tag siempre gana; el número solo cubre el bound que no tiene tag. Un
  bound *con* tag que no se pudo leer **no** cae al número (fail-closed, igual que el SP).
  Los números se guardan aunque esté apagado: apagarlo y volver a encenderlo no tiene por
  qué hacer reescribirlos. Un bound con respaldo encendido sale de `faltantes` y de
  `roles_en_uso`.
- **`contrato.json → limites_sp` quedó como legado.** `migrar_limites_sp_del_contrato()`
  lo adopta una vez como `limites_num` de la familia de defuzzy, **encendido** (esos
  números están clipeando hoy; apagarlos al actualizar dejaría el SP sin tope, o sea sin
  escritura). El motor ya no lee el contrato para esto, y `PUT /api/contrato/limites-sp`
  —que había durado una hora— se borró: el respaldo se guarda con el botón Guardar de la
  familia, en el mismo PUT que la tabla.
- **Handshake configurable.** `ENABLE_FBK`/`ENABLE_EXT` vivían en `tags.json` desde
  siempre, sin endpoint ni página: se editaban a mano. Ahora hay
  `GET/PUT /api/tags/handshake` y un panel en Tags con el estado en vivo. Se rechaza
  exigir el handshake sin FBK (sería fail-closed permanente) y que FBK y EXT sean el mismo
  tag.
- **Export/import.** `handshake` entró a `_TAG_STORE_KEYS`: **no viajaba**, así que una
  planta clonada arrancaba sin el permiso configurado y no escribía nada sin decir por qué.
  Y el preview del import ahora avisa cuándo un cableado de límite apunta a un tag que esta
  planta no va a tener (cierra el pendiente que quedaba de la primera vuelta).

#### Una trampa nueva para los tests de API

`web/api/tags.py` hace `from web.state import _load_tags`, así que el nombre ya está
ligado: **parchear `web.state._load_tags` no alcanza** y el endpoint escribe el `tags.json`
de la planta. Los tests de handshake usan `_client_tags()`, que parchea
`web.api.tags._load_tags` / `_save_tags`. Vale para cualquier test que pegue a un endpoint.

---

## B · Pendiente de afinación

### B1 · Riesgo operativo (tocan lo que llega al DCS)

**B1.1 — `stop()` no verifica que el hilo murió.** — CERRADO 2026-08-25

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

**Implementado en `SEEngine`, `HeartbeatManager` y `Generator`:**

- **`_worker()` con `try/finally`** — al terminar el hilo (por lo que sea, incluida
  una excepción no manejada) el `finally` pone `_running = False`. Antes un crash del
  worker dejaba `_running=True` mintiendo, y `status()` decía "corriendo".
- **`start()` detecta hilo zombie** — si `_running=True` pero `_thread.is_alive()`
  es False, resetea el estado y arranca limpio en vez de devolver `ok=True` sin
  arrancar nada. Ese caso pasaba cuando el worker crasheaba entre ticks.
- **`stop()` no miente** — si tras `join(timeout=3)` el hilo sigue vivo, se conserva
  `_running=True` y se reporta el error. Con la marca `False`, un `start()` posterior
  arrancaba un segundo motor mientras el primero seguía escribiendo SP al DCS. Es el
  bug de dos motores.
- **`status()` expone `thread_alive`** — la UI puede distinguir "corriendo" (running
  + hilo vivo) de "colgado" (running pero hilo muerto).
- **`api_se_stop()` propaga `ok`/`error`** — antes devolvía `{ok: True}` incondicional.

Lo del `_stop_event` por generación no hizo falta: la combinación finally + zombie
check + stop honesto cierra la ventana sin cambiar el patrón del evento.

**B1.2 — Sesión OPC-UA nueva por tick, sin timeout.** — CERRADO 2026-08-25

Cada lectura y cada escritura abría `Client(get_url())`, conectaba y desconectaba. En
ciclo libre son decenas de sesiones por segundo.

**Corrección al enunciado original:** *"`connect()` no tiene timeout"* es falso, y
conviene que quede escrito para que nadie vuelva a buscar ese bug. `python-opcua` trae
`Client(url, timeout=4)` por defecto, y ese valor viaja tanto al
`socket.create_connection` como al `future.result(timeout)` de **cada** request.
Verificado contra una IP que no responde: falla a los 4,01 s, no se cuelga.

El riesgo real era otro y es peor: el timeout es **por request**, y
`read_tags_batch` iteraba los tags capturando la excepción de cada uno para seguir con
el siguiente. Si el servidor aceptaba el socket y después dejaba de contestar, el lote
de 27 tags costaba **27 × 4 s = 108 s en UN tick** — con `stop()` haciendo
`join(timeout=3)`, o sea el motor imposible de parar. Ese sí era el escenario que
disparaba B1.1.

**Implementado** (`connectors/kepserver.py`, reescrito el manejo de sesión):

| Qué | Cómo |
|---|---|
| Sesión persistente | Una por hilo, en un registro de módulo. `_obtener_sesion()` la crea al primer uso |
| Timeout explícito | `timeout_s` en `kepserver.json` (default 2,0 s, acotado a 0,2–30). Por debajo del `join(timeout=3)` de `stop()` a propósito |
| Reconexión lazy | Un fallo de transporte descarta la sesión; el próximo uso reconecta |
| Corte de lote | Un fallo de transporte **aborta el lote** en vez de seguir preguntando |
| Cambio de host | `_obtener_sesion` compara la URL; `set_config` cierra todo |
| Cierre ordenado | `close_thread_client()` en el `finally` de los tres workers |
| Cosecha | Sesiones de hilos muertos o con >120 s sin uso |

**Por qué una por hilo y no una sola con lock.** El cliente OPC-UA no es reentrante, así
que una sesión compartida obliga a serializar a todos sus usuarios — y no son solo el
motor: el heartbeat pulsa cada 2 s y las rutas HTTP releen todos los tags en cada carga
de la página de Tags. Con lock global, abrir esa página esperaría al tick del lazo de
control y el tick esperaría a la página. La cuenta queda acotada porque los hilos que
llaman son de vida larga (los tres workers y el pool `gthread` de gunicorn, 4 hilos
reutilizados); la cosecha cubre el caso del servidor de desarrollo de Flask, que sí crea
un hilo por request.

**Transporte roto vs. tag malo.** Distinción nueva que la sesión persistente obliga a
hacer. Con una sesión por llamada, un socket roto se manifestaba como fallo del
`connect()` y el lote entero salía con `connected=False`. Compartiendo la conexión, si se
cae a mitad del lote los tags que faltaban se reportarían como
`exists=False, quality="Bad"`: el SE vería **"todos los instrumentos rotos"** en vez de
"me quedé sin KEPserver", y no reconectaría nunca. El criterio es que si el servidor
**respondió** con un StatusCode, la conexión está viva y el problema es de ese tag. Un
error que no es ninguna de las dos cosas se trata como problema del tag, porque cerrar la
sesión por un `ValueError` recurrente la haría reconectar en cada tick, en silencio.

Lo mismo en la escritura: los SP que no se llegaron a intentar salen en `fallidos` con
"Conexion OPC-UA caida antes de escribirlo". Marcarlos como escritos dejaría al DCS con
el valor viejo y al write-on-change convencido de haberlo mandado.

**Medido, no supuesto** (contra el KEPserver real, 6 tags):

| | Antes | Ahora |
|---|---|---|
| Sesiones TCP en 191 ticks | 191+ | **1** |
| Latencia por lote | 114,4 ms | 92,3 ms (**1,2×**) |
| Sesión del motor | nueva cada tick | una, viva 32 s y contando |

La ganancia de latencia es modesta a propósito de reportarla así: manda el round-trip de
las 6 lecturas secuenciales, no el armado de sesión. Lo que se gana de verdad es no
quemar sesiones en un servidor con conexiones licenciadas, el timeout acotado, y no
confundir una caída con instrumentos rotos.

**Observabilidad:** `GET /api/kepserver/sesiones` responde la pregunta operativa — la
sesión se está reusando o se está reabriendo.

Tests: `tests/test_kepserver.py` (16).

**B1.3 — Sin resincronización con el DCS después del arranque.** — CERRADO 2026-08-25

El arranque es bumpless (parte del valor vivo del SP), pero después el motor solo
confía en su copia interna. Si el operador mueve el SP a mano, el SE no se entera:
al siguiente disparo escribe `su_valor + paso` y **revierte la intervención de un
salto**. Arreglo: releer el SP del DCS cada N ticks y, si difiere de `_sp_escritos`
más que la banda muerta, adoptar el valor del operador (y dejarlo en la traza).

**Implementado.** `_read_tags()` agrega los tags SP al batch principal (piggyback,
sin sesión OPC-UA extra). `_reconciliar_sp_con_dcs()` se llama en cada tick antes
del pipeline: si el valor leído del DCS difiere del último que el SE escribió más
que `SP_DEADBAND`, se adopta como `_setpoints[sp_key]` y `_sp_escritos[tag]`. No se
clipea al adoptar (la intervención puede ser deliberada fuera del rango de reglas;
el defuzzy siguiente clipea al escribir). Los eventos aparecen en `tz["resync_sp"]`
y la traza muestra un banner "Intervención manual del operador" con `antes → dcs`.
Las familias inhibidas o retenidas por tracking quedan fuera — no las estamos
escribiendo, así que "intervención externa" no aplica: el valor en el DCS es libre.

**B1.4 — La calidad OPC-UA es cosmética.** — CERRADO 2026-08-25

`read_tags_batch` usaba `node.get_value()`, que descarta el `StatusCode`, y rellenaba
`"quality": "Good"` si no hubo excepción. La columna QUALITY de la UI decía siempre
*Good*, dijera lo que dijera el servidor.

**Era cosmética dos veces, y la segunda no estaba en el enunciado:** además de que el
conector inventaba el `"Good"`, `_read_tags` **tampoco miraba la calidad** — su `_ok()`
solo preguntaba si había llegado un número. O sea que aunque el conector hubiera dicho
la verdad, el motor habría decidido igual. Se arreglaron las dos mitades.

Ahora `get_data_value()` trae el DataValue completo y el conector publica, por tag:
`quality` (severidad OPC-UA: Good / Uncertain / Bad / Unknown), `status_code` (el nombre
exacto, `BadNotConnected` vs `BadUserAccessDenied` son dos viajes distintos),
`source_ts` y `estancado_s`. `valor_utilizable()` en `web/state.py` es el único criterio,
y lo usan las tres lecturas que importan: PV/CRUDA/LIM, el valor inicial de un SP y la
reconciliación con el DCS (B1.3 — adoptar un readback dudoso es peor que no reconciliar).

| Severidad | Qué hace el motor |
|---|---|
| `Good` | se usa |
| `Uncertain` | **no** se usa; la variable sale del `fuzzy_out` y las reglas que la nombran quedan `no_evaluable`. Configurable con `aceptar_uncertain` |
| `Bad` (con valor o sin él) | no se usa. El valor se conserva solo para diagnóstico |
| `Unknown` (StatusCode ilegible) | no se usa — fail-closed |

`Uncertain` se rechaza por defecto porque algunos de esos códigos son literalmente un
dato viejo (`UncertainLastUsableValue` significa que la fuente se cayó). Es configurable
porque la alternativa no es gratis: `UncertainEngineeringUnitsExceeded` es solo "fuera de
rango", y en una planta que los emita seguido rechazarlos deja al experto mudo.

**Corrección al enunciado original, verificada:** *"con eso se puede detectar valor
congelado (timestamp que no avanza)"* **no funciona contra este servidor.** El
`SourceTimestamp` avanza en **cada lectura** aunque el valor no se mueva — mismo
`66.90608978271484` con estampas `18.509 → 20.011 → 21.516`. Estampa el momento de la
lectura, no el del último cambio, así que como detector de congelado da siempre "fresco".
(`ServerTimestamp` viene `None`.)

El detector mide entonces el estancamiento **del valor**, que es la mejor señal
disponible: un float analógico real jitterea en los últimos bits. Con dos límites
deliberados:

- **Avisa, no inhibe.** No puede distinguir un scan congelado de un proceso genuinamente
  quieto — un nivel en un tanque lleno o un readback clavado en su setpoint no se mueven
  y están perfectos. Inhibir por esto dejaría al experto mudo justo en régimen
  estacionario, que es cuando más se lo necesita. Es un diagnóstico para el
  instrumentista, no un interlock.
- **Solo PV.** Un tag LIM vale 90.0 para siempre y eso es correcto.

Umbral en `estancado_alerta_s` (default 60 s, 0 lo desactiva). Ambos parámetros se editan
en la página de KEPserver.

**Efecto colateral bueno:** `_chequear_handshake_dcs` ya exigía `quality == "Good"` desde
que se escribió, pero como la calidad era siempre `"Good"` por construcción, esa rama
**nunca se había podido ejecutar**. Ahora el fail-closed del handshake es real.

Verificado en el contenedor contra el servidor: 8 tags en `Good` con su `status_code`, el
pipeline corriendo, y el detector marcando las 4 PV congeladas del banco a los 60 s con
`hopper_nvl_pv_a` todavía dentro del fuzzy.

Tests: `tests/test_kepserver.py` (7 nuevos), `tests/test_pipeline.py` (9 nuevos).

Detalle de diseño en `CLAUDE.md` → *La calidad de dato es real, y se juzga en un solo
lugar*.

**B1.5 — Un parpadeo de calidad sacaba al experto de servicio.** — CERRADO 2026-08-25

Era la mitad que B1.4 dejó abierta. Con la calidad ya real, una PV que llegaba `Bad`
desaparecía del `fuzzy_out` **en el mismo tick**, y las reglas que la nombraban quedaban
`no_evaluable` de inmediato. Un parpadeo de un tick del KEPserver —o una reconexión, que
después de B1.2 dura lo que dura un `connect()`— dejaba al SE mudo por algo que ya se
había resuelto solo.

**El reloj era la decisión de diseño, y no podía ser el `source_ts`.** B1.4 dejó
verificado que este servidor lo estampa en el momento de la lectura, así que como "edad
del dato" da siempre cero. Se cuenta desde el **último tick en que ese tag estuvo
`Good`** (`_ultimo_bueno[tag] = (valor, self._t_s)`), igual que se mide `estancado_s`.

- **`retencion_s` en `kepserver.json`**, default **5,0 s**, editable en la página. A
  ~6 tick/s son ~30 ticks: alcanza para cruzar un parpadeo y es corto frente a la ventana
  del filtro Exp-Q (50 s) y a cualquier wait. `0` lo desactiva.
- **Lo aplica `_read_tags`, no el conector.** El conector reporta lo que el servidor dijo
  en *esta* lectura y no debe mentir sobre eso: la página de Tags tiene que seguir
  mostrando `Bad` mientras el motor usa el valor retenido.
- **Una PV que nunca estuvo `Good` no se retiene.** No hay último valor bueno que retener,
  y rellenarla sería inventar.
- **`_init_state()` olvida lo retenido.** Arrancar el motor con el valor de hace media hora
  es exactamente lo que el arranque bumpless existe para evitar.
- **El handshake no retiene nada.** Su fail-closed es más duro a propósito: un permiso que
  no se puede verificar es un permiso denegado, y ahí no hay parpadeo tolerable.
- **Un LIM retenido sostiene a su PV**, porque si no la PV se caía del fuzzy igual.

**Interacción con el tracking, que hubo que arbitrar.** El test
`test_sin_readback_legible_el_tracking_es_fail_closed` empezó a fallar: la retención
mantenía vivo el readback y el fail-closed no se disparaba. No era un bug, era el test
afirmando algo que dejó de ser cierto. Quedaron los dos casos separados y probados: sin
readback legible **nunca** → se retiene la familia; readback que parpadea → la retención
lo cubre y el tracking sigue.

Se ve en el paso 1 de la traza con la antigüedad, el valor retenido y la calidad real, más
un cartel propio. Alerta en la categoría `calidad`.

Tests: 7 en `tests/test_pipeline.py`. Verificado en el contenedor con lectura falsa.

**B1.6 — Escritura de SP sin límite de velocidad.** — CERRADO 2026-08-25

`_write_setpoints` mandaba el paso completo del defuzzy de un salto. Con una tabla mal
calibrada, o con un belief pleno tras un rato retenido, el DCS podía recibir un escalón
de varias unidades de ingeniería en un solo write.

**`rate_sp` en `contrato.json`**, en unidades de ingeniería **por segundo** y por familia
de SP. Vacío = sin límite, que es el comportamiento anterior.

**Rampea, no descarta, y ahí está toda la decisión.** El tracking sí descarta el paso —a
propósito, porque el proceso quedó atrás y acumular mandaría un salto de varios pasos
juntos al liberarse. Un rate limit es lo contrario: la acción es válida y el objetivo es
correcto, lo único que no se acepta es llegar de un salto. Descartar acá perdería la
decisión del experto; lo que se hace es entregarla en varios ticks.

Se aplica **al escribir, no al calcular**, y eso tiene tres consecuencias buscadas:

| | Por qué |
|---|---|
| `_setpoints` (objetivo interno) avanza completo | El mecanismo de *el wait cuenta solo si la regla actuó* ve que el SP se movió y **no** revierte el wait. Correcto: la regla actuó, su efecto viaja en rampa. Si el rate limit tocara `_setpoints`, la regla volvería a disparar en cada tramo |
| `_sp_escritos` guarda lo que el DCS aceptó | Es lo que compara `_reconciliar_sp_con_dcs` (B1.3): un SP a mitad de rampa **no** se lee como intervención manual del operador |
| El write-on-change reintenta solo | Mientras quede diferencia entre objetivo y escrito, el tag sigue apareciendo como cambiado. La rampa avanza sin código extra |

El primer tick **no rampea**: es el arranque bumpless, que parte del valor vigente en el
DCS, y rampear hacia un valor que el DCS ya tiene no tiene sentido.

**El tope del presupuesto se descubrió verificando, no diseñando.** La primera versión
calculaba `paso_max = rate × (ahora − última escritura de este tag)`. Medido en el
contenedor: tras 5 s retenido por tracking, el SP saltaba **3,94 unidades en un solo
write** — exactamente lo que el límite existe para impedir, y en el peor momento, porque
el equipo llevaba un rato quieto. El presupuesto se acumulaba durante toda pausa
(tracking, handshake, write rechazado, motor recién arrancado). Ahora está acotado por
`RATE_DT_MAX_S = 1.0 s`, lo que le da a `rate_sp` una segunda lectura fácil de explicar:
es también **el paso máximo de un solo write**. Con el mismo escenario, el tramo de
liberación avanza 1,00.

Se ve en el paso 8 de la traza: objetivo, escrito, cuánto falta y el rate aplicado.

Tests: 6 en `tests/test_pipeline.py`.

Con esto se cierra B1 completo. El otro item que el titular listaba junto al rate limit
—el **interlock de habilitación**— ya existía y no se había anotado: es el handshake DCS
fail-closed de `_chequear_handshake_dcs`, que se consulta en cada tick antes de armar el
lote y bloquea **todas** las familias si el `Enable_FBK` no llega en `Good` con valor
verdadero. B1.4 fue lo que lo volvió real: la rama de calidad existía desde que se
escribió, pero con la calidad inventada nunca se había podido ejecutar.

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

**B3.4 — Escrituras de JSON sin atomicidad ni lock.** — CERRADO 2026-08-25

`_save_tags` / `_save_defuzzy` / etc. escribían directo sobre el archivo. El
`open(..., "w")` trunca **antes** de escribir nada, así que un corte a mitad de
`json.dump` no dejaba "la edición anterior": dejaba un JSON incompleto, que el loader
considera corrupto y por el que **cae a la plantilla de Python**. O sea que el modo de
falla real era *la planta arranca con la configuración de otra planta*.

**`core/jsonio.escribir_json_atomico`** (nuevo): `.tmp` completo + `flush` + `fsync` +
`os.replace`, que es atómico en POSIX y en Windows. En cualquier instante en que se corte,
en el destino hay o la versión vieja entera o la nueva entera.

- **El `.tmp` va en el mismo directorio** del destino, no en `/tmp`: `os.replace` solo es
  atómico dentro del mismo sistema de archivos, y `./config` es un volumen de Docker. Un
  temporal fuera degradaría el rename a copiar+borrar.
- **El `fsync` no es decorativo.** Sin él, `os.replace` puede publicar un inodo cuyo
  contenido sigue en el cache de página: ante un corte de energía el destino queda visible
  y vacío, que es justo el escenario que el módulo evita.
- **El temporal se borra si algo falla**, o se acumularían en el volumen de config.
- **Lock por ruta** (`lock_de`), para que dos hilos que guardan el mismo archivo no se
  pisen el temporal ni el rename.

Migrados **todos** los `_save_*`: `web/state.py`, `web/api/config.py`,
`web/api/contrato.py`, `web/api/postgres.py`, `connectors/kepserver.py` y
`scripts/seed_tags_planta.py`.

**El read-modify-write es un problema aparte y también se cerró.** El lock por ruta
protege la escritura, no el ciclo leer→modificar→escribir: para eso el lock tiene que
estar tomado **desde antes de leer**. `tags.json` lo necesita porque tiene cuatro
escritores concurrentes (el generador, el heartbeat, las rutas de la API y la
sincronización del contrato) y ya se corrompió una vez por esto. Hay un `_tags_lock`
(`RLock`) en `web/state.py` que envuelve el ciclo completo en `TagGenerator._save_config`,
`HeartbeatManager._save_config` y todas las rutas de `web/api/tags.py` y
`web/api/contrato.py` que tocan tags.

Tests: 3. Uno inyecta un fallo a mitad del `json.dump` y comprueba que el destino sigue
leyéndose; otro que el temporal vive en el directorio del destino; el tercero corre el
generador y el heartbeat en paralelo **por el camino de producción** contra un
`tags.json` real y verifica que ninguna de las dos configuraciones se perdió.

> El primer intento de ese tercer test no probaba nada: el fixture `store` monkeypatchea
> `_save_tags` a un no-op, así que la carrera ocurría sobre un dict en memoria. Es la
> misma clase de error que A17 — un test que pasa por el motivo equivocado.

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
python -m pytest tests/ -q                      # 180 pass, 2 skip
python -m pytest tests/test_kepserver.py -q     # 23: sesion OPC-UA + calidad de dato
python -m pytest tests/ -q -k "calidad or estanca or uncertain"   # B1.4
python -m pytest tests/ -q -k "retencion or retenido or parpadeo" # B1.5
python -m pytest tests/ -q -k "rampa or rate"                     # B1.6
python -m pytest tests/ -q -k "atomico or temporal or pisan"      # B3.4
python -m pytest tests/ -q -k "defuzzy or dcs or limite or wait or grabador"
```

Contra el servidor, con el stack levantado:

```bash
curl -s localhost:5000/api/kepserver/sesiones   # B1.2: la sesion se reusa?
curl -s localhost:5000/api/se/trace?n=1         # paso 1: quality, status_code, estancado_s
curl -s localhost:5000/api/alerts               # categoria "calidad" (A18)
```

**El Python del host necesita `pip install pytest`** (el resto de las dependencias ya
está). La imagen del contenedor **no** trae pytest: `requirements.txt` no lo incluye, así
que `docker compose exec se-espesador python -m pytest` falla con *No module named
pytest*. Correrlos en el host es lo práctico.

Para ver el pool de sesiones OPC-UA con el motor corriendo:

```bash
curl http://localhost:5000/api/kepserver/sesiones
```
