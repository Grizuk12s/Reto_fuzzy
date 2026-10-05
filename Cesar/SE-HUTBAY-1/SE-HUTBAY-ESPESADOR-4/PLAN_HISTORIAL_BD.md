# Plan — Historial del Explorador de Series en PostgreSQL

**Estado:** propuesta, sin implementar (2026-09-24).
**Objetivo:** que el Explorador de Series se alimente de PostgreSQL en vez del
buffer en RAM (`_tag_history`), con valor, dominio, pendiente y aceleración
**guardados por instante**, grabando mientras el motor esté encendido, sin
depender de que haya una página abierta.

---

## 1. Por qué es mejor que el buffer en RAM

| Aspecto | Hoy (buffer en RAM) | Con la BD |
|---|---|---|
| Quién graba las PV de planta | El **navegador**, vía `/api/tags/lectura` | El **motor**, siempre que esté encendido |
| Página cerrada | Las PV no se graban: hueco | Se graban igual |
| Reinicio del servidor | Se pierde todo | Se conserva |
| Dominio / pendiente / aceleración | Solo el **último tick** | **Los de cada segundo** |
| Costo por refresco del gráfico | Lectura OPC al KEPserver + copia del buffer completo de todos los tags | 1–2 filas nuevas, solo de los tags elegidos |
| Varias pestañas abiertas | Multiplican las lecturas OPC | No tocan el KEPserver |
| Historia disponible | ≤ 3 h (y con huecos) | 22–24 h continuas mientras el motor corrió |

La ganancia depende de dos reglas del diseño (sección 5): **lectura
incremental** y **agrupación en ventanas anchas**. Sin ellas, pedir 3 h de JSON
cada segundo sería más pesado que hoy.

---

## 2. Decisiones de diseño

| Decisión | Valor |
|---|---|
| Cuándo se graba | Solo con el **motor encendido**. Da lo mismo si el SE tiene el lazo, si hay licencia del DCS o si hay familias inhibidas |
| Resolución | **1 fila por segundo** con todas las variables en JSONB |
| Retención | Máximo **24 h en filas**; al superarlas se recorta a **22 h** |
| Ventana del gráfico | 1 min a **3 h** (sin cambio) |
| Resolución mínima del gráfico | **1 s** (sin cambio) |
| Reloj de referencia del gráfico | El **navegador** (sin cambio) |
| Alineación de relojes | **Desfase configurable** en la configuración de Postgres, aplicado al escribir |

---

## 3. Tabla nueva

`espesadores_historial` (una por proceso, igual que las tablas actuales):

| Columna | Tipo | Contenido |
|---|---|---|
| `id` | `BIGSERIAL PK` | Orden de inserción. Sirve para recortar y para la lectura incremental |
| `ts` | `TIMESTAMPTZ NOT NULL` | Hora del motor **+ desfase configurado**. Es la que usa el gráfico |
| `ts_motor` | `TIMESTAMPTZ NOT NULL` | Hora del motor sin corregir, para auditoría |
| `valores` | `JSONB` | `{tag: valor}` — PV, crudas, límites, SP escritos y series internas `SE::` |
| `anot` | `JSONB` | `{tag: {dom, pend, pend_dom, pend_ventana_s, acel, acel_dom, signo}}` |

- **Índice:** B-tree sobre `ts`. `id` ya queda indexado por ser clave primaria.
- **`ts` se escribe siempre explícito.** No se usa `DEFAULT NOW()`, porque eso
  metería un tercer reloj, el del servidor de Postgres.
- **`valores` y `anot` van en columnas separadas.** Así el gráfico baja las
  anotaciones solo cuando el tooltip las pide.
- **Tamaño estimado:** con los 27 tags actuales, 4 KB por fila o menos antes de
  compresión, del orden de **100 a 400 MB** para 24 h. Se mide en la Fase 0.

---

## 4. Grabación

### 4.1 Grabador en su propio hilo
Hoy `persist_tick` corre **dentro del tick del motor** y abre una conexión nueva
en cada escritura. Si Postgres se pone lento, se atrasa el lazo de control.

La propuesta es un **grabador** separado:

1. En cada tick, el motor deja el **último fotograma** (valores y anotaciones)
   en una variable compartida. Eso cuesta una asignación, no una consulta.
2. El grabador despierta **cada 1 s**, toma ese fotograma y lo inserta. Usa una
   **conexión persistente** y reconecta si se cae.
3. Si la BD no responde, **se descarta el fotograma**, no se encola, y se
   levanta una alerta en la categoría PostgreSQL. El motor nunca espera a la BD.
4. El grabador arranca con `SEEngine.start()` y se detiene con `stop()`. Un
   **reinicio en caliente no lo detiene**.

### 4.2 Qué entra al fotograma
- **`valores`**: lo mismo que hoy va a `entrada` y `salida` (PV, crudas,
  límites, SP), más las series `SE::` del objetivo interno.
- **`anot`**: por cada tag mapeado, el dominio, la pendiente y la aceleración de
  la **traza de ese tick**, con la misma lógica que hoy usa
  `/api/se/grafico/anotaciones`. Esa lógica se mueve a una función compartida
  para que el tooltip y la BD no puedan dar resultados distintos.

### 4.3 Desfase de reloj
- Nuevo campo en `postgres.json`: `desfase_s` (segundos, con signo, por defecto `0`).
- `ts = hora_motor + desfase_s`.
- Nuevo botón en la página de Postgres: **"Alinear con la hora de este
  navegador"**. Calcula `navegador − motor` y lo guarda. La página muestra tres
  horas: la del motor, la del navegador y el desfase aplicado.
- **Limitación:** el desfase se calibra para **un** navegador. Otro PC con otra
  hora verá la ventana corrida.

---

## 5. Retención (24 h en filas)

- Filas por hora = `3600 / periodo_s`. Con 1 s son **3.600** filas por hora.
- Umbral = 24 h = **86.400** filas. Objetivo después de recortar = 22 h = **79.200** filas.
- Los dos valores son configurables en horas y se convierten a filas con el
  periodo vigente. Si el periodo pasa a 500 ms, las cuentas se ajustan solas.

**Cuándo se revisa:**
1. Al **encender el motor**, antes de la primera inserción.
2. Durante la ejecución, **cada 5 minutos**, en el hilo del grabador.

**Cómo se hace:**
- **Conteo barato:** `max(id) − min(id) + 1`, sin `COUNT(*)`. Puede
  sobrestimar un poco si hubo inserciones fallidas, y eso solo adelanta un
  recorte.
- **Borrado:** `DELETE ... WHERE id < min_id + exceso`, sobre la clave primaria.
  Se borran unas 7.200 filas por vez, lo que toma menos de un segundo.
- **Mantenimiento:** el autovacuum de Postgres recupera el espacio y la tabla
  se estabiliza en tamaño. No hacen falta particiones.

> Tu propuesta original: al arrancar con 23 h, borrar 6 h; en ejecución con
> 24 h, borrar 12 h. Esas dos reglas se reemplazan por **una sola**
> (> 24 h → dejar 22 h). Así siempre hay cerca de un día de datos y nunca se
> cae a 12 h.

---

## 6. Lectura para el gráfico

Nuevo endpoint: `GET /api/tags/historial`

| Parámetro | Uso |
|---|---|
| `tags` | CSV de los tags elegidos. Solo se extraen esas claves del JSONB |
| `from_ms`, `to_ms` | Ventana en hora del navegador |
| `desde_id` | Si viene, devuelve **solo filas nuevas**: modo incremental |
| `anot=1` | Incluye las anotaciones (solo si el tooltip las tiene activadas) |

**Dos modos:**

1. **Carga completa.** Se usa al abrir la página, al cambiar la ventana, al
   mover el deslizador o al agregar o quitar un tag.
   - Ventana **≤ 15 min**: se devuelven todas las filas, a 1 s.
   - Ventana **> 15 min**: la BD agrupa en cubetas de `ventana / 1500` segundos
     y devuelve **mínimo y máximo** de cada cubeta, para no perder picos. Son
     unos 1.500 puntos como máximo, que es lo que el canvas alcanza a mostrar.
2. **Incremental.** Se usa en cada refresco con "Seguir tiempo real". Pide
   `desde_id = último id recibido` y trae 1 o 2 filas. El navegador las agrega a
   la derecha y descarta lo que sale por la izquierda.

La respuesta incluye `ultimo_id` y `motor_grabando` (si hubo una fila en los
últimos 3 s).

**Punto del tooltip.** `GET /api/tags/historial/punto?ts_ms=...&tags=...`
devuelve la fila más cercana con sus anotaciones. Se pide solo al pasar el
cursor y se guarda en una caché del navegador.

---

## 7. Cambios en el Explorador de Series

1. **Deja de llamar a `/api/tags/lectura`.** Ya no le toca muestrear el
   KEPserver; eso lo hace el motor. La página de Tags sigue igual.
2. `fetchData()` pasa a manejar los dos modos: carga completa o incremental.
3. **Tooltip:** dominio, pendiente y aceleración **del instante bajo el
   cursor**. Se elimina el aviso "son del último tick".
4. **Nuevo indicador** junto a "EN VIVO": *"Motor detenido — sin grabación"*
   cuando `motor_grabando = false`. Evita confundir una línea plana con un
   proceso quieto.
5. **Respaldo:** si la BD no responde, el gráfico cae al buffer en RAM actual y
   lo avisa con *"BD sin conexión — mostrando memoria"*. Por eso el buffer se
   mantiene.
6. **Sin cambios:** franjas de handshake y de reinicio, normalización,
   preferencias de color y límites, y el rango de 1 min a 3 h.

---

## 8. Fases de implementación

| Fase | Contenido | Resultado verificable |
|---|---|---|
| **0. Medición** | Versión de Postgres (`date_bin` requiere ≥ 14; si no, se agrupa con `floor(epoch/n)`). Tamaño real de un fotograma con los 27 tags | Cifras de tamaño y de 24 h reales |
| **1. Tabla y desfase** | DDL en `ensure_tables`, campo `desfase_s`, botón de alineación en la página de Postgres | La tabla existe y la página muestra las tres horas |
| **2. Grabador** | Hilo propio, fotograma compartido, conexión persistente, arranque y parada con el motor, alertas | 1 fila/s con el motor encendido, 0 con el motor apagado; el lazo no cambia su ritmo (métricas de Fase 1) |
| **3. Retención** | Revisión al arrancar y cada 5 min, recorte por `id` | Con datos sembrados de 25 h, queda en 22 h |
| **4. Endpoints** | `/api/tags/historial` (completo, agrupado, incremental) y `/punto` | Respuestas correctas en los tres modos; tiempos medidos |
| **5. Gráfico** | Modos de carga, indicador de grabación, respaldo a RAM, se quita `muestrearKepserver` | El gráfico muestra datos de antes de abrir la página |
| **6. Tooltip histórico** | Anotaciones por instante | Pendiente y dominio cambian al mover el cursor |
| **7. Cierre** | Tests en `tests/`, `_verif_historial_bd.py`, `CAMBIOS_HISTORIAL_BD.md`, `CLAUDE.md`, versión 0.45 en `DESPLIEGUE.txt` | Suite verde |

Las fases 1 a 3 pueden ir a planta solas: graban sin tocar el gráfico, y así se
junta un día de datos reales antes de cambiar la interfaz.

---

## 9. Decisión pendiente: tablas `espesadores_entrada` / `espesadores_salida`

Hoy reciben **una fila por tag por segundo** y **no tienen retención**. Con 27
tags son unos 2,3 millones de filas por día, y crecen sin límite. Hay tres
opciones:

- **A.** Dejarlas como están (duplican lo que guarda la tabla nueva).
- **B.** Aplicarles la misma retención de 24 h.
- **C.** Dejar de escribirlas si nadie más las consume (un reporte, otro sistema).

Hay que confirmar si algún sistema externo las lee antes de elegir.

---

## 10. Riesgos y cómo se cubren

| Riesgo | Cobertura |
|---|---|
| Postgres lento o caído | El grabador descarta y alerta; el motor no espera; el gráfico usa la RAM |
| Relojes desalineados | `desfase_s` con botón de alineación; `ts_motor` guardado para auditar |
| Crecimiento del disco | Retención por filas cada 5 min + autovacuum |
| JSON más grande al agregar tags | Se mide en la Fase 0 y se vuelve a medir al crecer; la extracción por clave evita traer el JSON completo |
| Tooltip distinto de lo que vieron las reglas | Una sola función arma las anotaciones para la traza y para la BD |
