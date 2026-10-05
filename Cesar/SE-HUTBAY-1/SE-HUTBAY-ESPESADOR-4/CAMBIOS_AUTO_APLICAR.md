# Auto-aplicar configuración desde el servidor (2026-09-24)

Actualiza `CAMBIOS_REINICIO_CALIENTE.md`. Allí figuraba como límite conocido:
*"El reinicio lo sigue disparando la página principal, en el navegador"*. Ya no.

## Qué se revisó

Se contrastó todo lo que el motor lee al arrancar (`_init_state()`) contra la huella
de `/api/config/version`:

| Archivo | ¿Cómo aplica un Guardar? |
|---|---|
| contrato, reglas, permisivos, filtros, fuzzy, defuzzy, tracking (incluye arranque), pendientes, aceleraciones, variables, estados, waits | Huella → reinicio en caliente |
| `tags.json` (mapeo de roles + handshake) | Huella parcial → reinicio en caliente |
| `kepserver.json` (URL, timeout, uncertain, estancado, retención) | En vivo (caché por mtime) |
| `motor.json` (piso) | En vivo, a propósito fuera de la huella |
| `licencia.json` | En vivo (el motor la vigila) |
| `postgres.json` | En vivo (se lee en cada escritura) |

Ningún archivo quedaba sin aplicar. Los problemas estaban en **quién** disparaba el
reinicio.

## Problemas encontrados

1. **Dependía de una pestaña abierta.** El reinicio lo hacía `index.html`. Con esa
   pestaña cerrada, un Guardar en Traza, Aceleración, Reglas, etc. no se aplicaba.
2. **Un guardado podía perderse.** Si caía mientras se aplicaba el anterior, al terminar
   la pestaña releía la huella y la daba por aplicada.
3. **Un interruptor por navegador** (`localStorage`): apagado en un PC, encendido en otro.

## Qué se cambió

| Archivo | Qué |
|---|---|
| `web/state.py` | `AutoAplicador` (hilo daemon, revisa cada 0,5 s, debounce 1 s); `SEEngine._huella_cargada` tomada **al inicio** de `_init_state()`; `SEEngine.reiniciar_si_corre()` (chequeo + reinicio bajo `_ciclo_vida_lock`, no arranca un motor detenido); `motor.json` gana `auto_aplicar` (default `true`) y `guardar_motor_cfg` ya no borra otras claves; `registrar_huella_config()` / `huella_config_actual()` |
| `web/api/config.py` | `huella_config()` extraída del endpoint y registrada en `web.state` |
| `web/api/se.py` | `GET/PUT /api/se/auto-aplicar` |
| `app.py` | Arranca el vigilante (no bajo pytest) |
| `web/templates/index.html` | El toggle lee/escribe el servidor; la página solo **informa** (ya no llama a `/api/se/restart`) |
| `tests/test_auto_aplicar.py` | 11 tests |

`POST /api/se/restart` se mantiene para uso manual.

## Reglas

- Se compara la huella de disco contra la **cargada por el motor**, no contra "la última
  vista". Es lo que impide perder un guardado.
- La huella se toma **antes** de leer los archivos: un guardado durante el arranque
  provoca otro reinicio (≈5 ms) en vez de perderse.
- Un reinicio fallido no se reintenta en lazo: espera a que la configuración cambie otra vez.
- Motor detenido o auto-aplicar apagado → no hace nada. Al volver a encenderlo, aplica lo
  pendiente.

## Verificación

```bash
python -m pytest tests/ -q     # 473 pasan, 2 fallan (los de siempre), 3 skip
```

Contra la app levantada (sin KEPserver): guardar → reinicio en caliente en ~1,5 s con el
contador de ticks conservado; dos guardados seguidos → un solo reinicio; apagado → no
reinicia; al encender → aplica lo pendiente; motor detenido → no arranca nada.

## También en esta fecha

`web/templates/traza.html`, sección 4: barra de columnas (mostrar/ocultar Valor, Límites,
Fuente, Ventana, Pendiente, Ratificación, Rate, Aceleración, Dominio, Pertenencias,
Tarjetas acel.; presets Compacto/Todas), columnas propias para pendiente y aceleración, y
el texto de aceleraciones pasa al botón "?".

---

## Explorador de Series: anotaciones por instante (2026-09-24)

El tooltip mostraba dominio, pendiente y aceleración del **último tick**, aunque el
cursor estuviera sobre un punto de hace 20 min.

- `web/state.py`: `anotaciones_por_tag()` (movida desde `web/api/se.py`, una sola
  lógica para "ahora" y para el historial); `registrar_anotaciones()` graba 1
  muestra/s en un anillo de 3 h (formato compacto, solo tags con algo que decir);
  `historial_anotaciones()`. El motor graba después de `_traza_push`.
- `web/api/se.py`: `GET /api/se/grafico/anotaciones/historial?tags&from_ms&to_ms`.
- `web/templates/graficos.html`: con algún extra del tooltip activo, trae el historial
  (incremental) y el tooltip usa la muestra del instante bajo el cursor (tolerancia 5 s;
  donde el motor no corrió no muestra nada). Las tarjetas de resumen siguen mostrando
  "ahora".
- `tests/test_anotaciones_historial.py`: 5 tests.

Vive en RAM como el historial de tags: se pierde al reiniciar el servidor (no el motor).

## Reglas: orden entre iguales prioridades (2026-09-24)

A igual prioridad el motor evalúa en el orden del archivo (`sorted` estable), y eso
importa: si dos reglas comparten un wait, en el mismo tick gana la de arriba.

- `web/api/config.py`: `POST /api/reglas/<id>/mover {"direccion": "arriba"|"abajo"}`.
  Solo intercambia con la vecina **de la misma prioridad** (409 si no); el resto del
  archivo no cambia de orden. `orden_visible_reglas()`.
- `web/templates/index.html`: flechas ▲▼ a la derecha del ID (solo en reglas con vecina
  de igual prioridad); al mover, las dos filas se agrandan un poco, se cruzan y la
  tabla se repinta. Respeta *prefers-reduced-motion*.
- `tests/test_orden_reglas.py`: 6 tests.
- Mover guarda `reglas.json` → el auto-aplicar reinicia en caliente.

Verificación: `python -m pytest tests/ -q` → 484 pasan, 2 fallan (los de siempre).

## Fuzzy: orden manual de filas, botones homogéneos y Vaciar en todas las páginas (2026-09-24)

**Por qué se reordenaban solas las filas (HIGH, LOW, OK):** `jsonify` de Flask ordena
alfabéticamente las claves de todo dict. La página recibía las etiquetas ordenadas y, al
guardar, ese orden quedaba escrito en `fuzzy.json` / `pendientes.json`.

- `web/api/config.py`: `_jsonify_orden()` (serializa sin ordenar) en TODAS las rutas de
  `/api/fuzzy*` y `/api/pendientes*`. El resto de la API no cambia.
- `web/templates/index.html`:
  - Flechas ▲▼ en cada fila de etiqueta del fuzzy de PV y de las pendientes, con la
    misma animación de intercambio que Reglas (`_animarIntercambio`, compartida). El
    orden se guarda con **Guardar** / **Guardar pendientes**, como cualquier edición.
  - Botonera homogénea (`_botonesTabla`) en Fuzzy, Pendientes y Defuzzy: `+ Columna`
    (verde), `- Columna` (rojo), `+ Fila`/`+ Accion` (verde), separador, luego
    Guardar/Borrar. Quitar una fila dice **Quitar** en las tres tablas.
  - **Vaciar** en todas las páginas 2–8, mismo texto, mismo estilo, al final de la barra:
    nuevos en Variables, Waits, Permisivos y Reglas; "Vaciar (todas)" y "Vaciar estados"
    pasan a "Vaciar" (el alcance queda en el `title`).
- Endpoints nuevos: `POST /api/variables/reset`, `/api/waits/reset`,
  `/api/permisivos/reset`, `/api/reglas/reset`. Informan las reglas afectadas.
- `tests/test_orden_etiquetas_y_vaciar.py`: 7 tests.
- Página 1 (Entrada de Datos) queda **sin** Vaciar a propósito: pendiente de definir qué
  significa (ver conversación).

Verificación: `python -m pytest tests/ -q` → 491 pasan, 2 fallan (los de siempre).

## Export / Import: revisión de cobertura (2026-09-24)

Qué viajaba y qué no:

| Configuración | Antes | Ahora |
|---|---|---|
| tags (incl. handshake, generador, heartbeat, preferencias del gráfico), contrato, kepserver, variables, filtros, fuzzy, pendientes, aceleraciones, estados, tracking, permisivos, waits, reglas, defuzzy | Sí | Sí |
| Orden de las filas de fuzzy/pendientes (y de toda clave de dict) | **Se perdía**: el export salía ordenado alfabético | Se conserva |
| Orden de reglas de igual prioridad al importar en *Reemplazar* | Se conservaba el orden del destino | Se aplica el orden del paquete (las reglas que no vienen no se mueven) |
| `motor.json` (piso del lazo, auto-aplicar) | **No se exportaba** | Módulo nuevo **Motor** |

Fuera a propósito: `licencia.json` (es de cada instalación), `postgres.json` (credenciales),
el registro de actividad y los backups, lo que vive en RAM (historial del gráfico, trazas)
y las preferencias por navegador (columnas de la traza, refresco, etc., en `localStorage`).

- `web/api/export_import.py`: export sin ordenar claves; `_aplicar_reglas()`;
  módulo `motor` (`_aplicar_motor()`: *Agregar* solo completa lo que falte, *Copias* =
  *Reemplazar*; con el motor corriendo el piso se aplica en vivo).
- `tests/test_export_orden_motor.py`: 6 tests. Suite: 497 pasan, 2 fallan (los de siempre).
