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
| A5 | Un tag LIM ilegible se rellenaba con `0.0` y el tick seguía: con `lmin=lmax=0` un fuzzy `norm` da OK=1.0 permanente y un `high` mide contra una escala inventada | `web/state.py` `_read_tags` | `test_un_limite_no_legible_aborta_el_tick` |
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
