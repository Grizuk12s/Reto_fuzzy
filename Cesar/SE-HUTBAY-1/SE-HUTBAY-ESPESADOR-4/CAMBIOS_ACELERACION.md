# Aceleración — implementación completa (2026-09-08)

Función nueva pedida por el cliente para el estándar del producto: poder preguntarle a
cualquier PV **si está aumentando o disminuyendo la magnitud de su cambio**, y usar esa
respuesta en las reglas, los estados y los subestados.

A diferencia de `CAMBIOS_2026-09-08.md` (que fue sólo interfaz), esto **sí toca el
pipeline**: hay una etapa nueva en el tick, entre las pendientes y la fuzzificación.

El detalle de las decisiones de diseño está en `CLAUDE.md` → *La aceleración es un estado
nítido, no un fuzzy*. Este archivo es el mapa del cambio y el orden en que se hizo.

---

## Qué se pidió y qué se entregó

| Requisito del cliente | Dónde quedó |
|---|---|
| Sección/página nueva llamada "Aceleración" | `/espesador#aceleracion` |
| Seleccionar cualquier variable PV del sistema | Selector de fuente: PV **y** variables calculadas |
| Ventana de tiempo configurable en segundos | Campo `ventana_s`, tope 3600 s |
| Usar **todos** los datos de la ventana | Buffer decimado a 240 muestras: con 5 s el paso es 0,02 s, o sea todas |
| Rate de la variable | `2a·t_final + b` — la derivada **al final** de la ventana |
| Aceleración | `2a` |
| Estado Acelerando / Desacelerando / Estable | Estado nítido de tres etiquetas |
| Variable calculada nueva (`..._Acceleration_5s`) | Nombre propuesto por el servidor, congelado al crear |
| Disponible en CONDICIONES de reglas | Sí — `variables_disponibles()` |
| Disponible en Estados y Subestados | Sí — usan el mismo catálogo |
| Negación No-Acelerando / No-Desacelerando | Sale de `expandir_etiquetas_compuestas`, sin código propio |
| Genérica, para cualquier PV | Sí; una o varias por variable, con ventanas distintas |
| Ajuste cuadrático por mínimos cuadrados | `np.linalg.lstsq` con el tiempo centrado |

**Lo único que no se entregó es la calibración**, porque no es software: `umbral_estable`
depende del ruido de cada señal y se elige con el proceso corriendo. Ver *Lo que queda*.

---

## Archivos

### Núcleo

| Archivo | Qué cambió |
|---|---|
| `core/variables/aceleracion.py` | **Nuevo.** Ajuste cuadrático, criterio de estado, `AceleracionesOnline` |
| `config/espesador/aceleraciones.json` | **Nuevo.** Vacío — como todo desde 2026-09-01, no hay plantilla que sembrar |
| `web/state.py` | Ruta del JSON, `aceleraciones_definidas()`, los tres catálogos, `roles_en_uso()`, `_init_state`, el tick, la traza |
| `runner.py` | `aceleraciones_config` + mismo objeto que el motor, para que simulación y producción no difieran |

### API e interfaz

| Archivo | Qué cambió |
|---|---|
| `web/api/config.py` | CRUD completo + `sugerir-nombre` + `vaciar`. `_pendientes_huerfanas_por_guardar` → `_variables_huerfanas_por_guardar` (genérica, alias conservado) |
| `web/templates/index.html` | Sección **Aceleración** (5a) con su panel *En vivo* |
| `web/templates/traza.html` | Paso 4 renombrado; badge `acel` en la tabla de fuzzy; tabla propia con rate, aceleración, umbral y **margen** |
| `web/templates/graficos.html` | Botón *Aceleración* en el tooltip + fila en las tarjetas de estadística |
| `web/api/se.py` | `/api/se/grafico/anotaciones` devuelve además la aceleración por tag |
| `web/api/export_import.py` | Módulo `aceleraciones` (13 → 14) |
| `static/activity-summary.js` | Resumen de cambios para la barra de actividad |

### Tests

| Archivo | Qué cubre |
|---|---|
| `tests/test_aceleracion.py` | **Nuevo. 43 tests**: el ajuste, el criterio de estado, el registry, los catálogos y la API |
| `tests/test_pipeline.py` | 6 tests nuevos: el tick, la negación, la traza, la degradación y las anotaciones del gráfico. Y `config_completa` apunta `ACELERACIONES_JSON` a un temporal |

---

## El orden en que se hizo, y por qué

Se implementó en tres fases, no de una sola vez. La razón es que las dos primeras son el
mismo cambio conceptual (una variable nueva que el motor produce y los catálogos ofrecen)
y la tercera es volumen de interfaz. Partirlo así permitió tener la función **usable en
reglas y verificada** antes de escribir una línea de HTML.

1. **Núcleo y catálogos.** El módulo, el enganche en el tick, y las cuatro funciones de
   `web/state.py` que hacen que la variable exista para las reglas, los estados y los
   subestados. Al terminar esta fase la función ya servía; se configuraba editando el
   JSON a mano.
2. **API y página.** Los cinco endpoints y la sección, con el panel *En vivo*.
3. **Traza y gráficos.** Los dos lugares donde ya se miraba el pipeline y ahora también
   se ve la aceleración.

---

## Sobre el rendimiento — medido, no estimado

El ajuste cuadrático sobre 240 muestras cuesta **~26 µs** (`np.linalg.lstsq`, medido en
el contenedor). Con el piso de 0,05 s el motor da como mucho 20 tick/s, así que **diez
aceleraciones declaradas cuestan ~5 ms de CPU por segundo: 0,5 %**. Al lado del
round-trip de la lectura OPC-UA (decenas a cientos de ms) es ruido.

Era la duda de fondo antes de empezar y por eso está medida y no supuesta. No hace falta
ninguna implementación incremental de las sumas del ajuste.

---

## Verificación

```bash
python -m pytest tests/ -q
```

→ **315 pasan, 2 fallan, 7 skip**.

Los dos que fallan son `test_sp_ilegible_se_inhibe_y_se_recupera_solo` y
`test_el_historial_registra_lo_que_se_escribio_al_dcs`, y **fallan igual en una copia
limpia del proyecto**: están acoplados al `config/` vivo, que cambia al operar la app.
Es el pendiente A17 sin cerrar del todo, no una regresión de este cambio.

JS validado con `node --check` en los tres templates tocados (`index.html`,
`traza.html`, `graficos.html`) y en `static/activity-summary.js`.

---

## Lo que queda

1. **Calibrar `umbral_estable` por variable, con el proceso corriendo.** El default
   (0,01 u/s²) es una plantilla neutra, igual que los 11 modelos difusos y que `q=0.15`
   en los filtros. El método: abrir la página de Aceleración con el motor andando y subir
   el umbral hasta que una variable en régimen se quede quieta en ESTABLE. La columna
   **Margen** de la traza (`|2a| / umbral`) es la que dice cuánto falta.
2. **Decidir con el experto de planta qué variables lo necesitan y con qué ventana.**
   Cinco segundos detecta un escape; sesenta, un cambio de régimen. Se pueden declarar
   las dos sobre la misma variable.
3. **Nada más.** Export/import, traza, gráficos y tests ya están.

---

# Actualización 2026-09-14 — el signo, y ESTABLE más estricto

El cliente pidió poder preguntarle a una aceleración **hacia qué lado empuja**, no sólo si
el movimiento se agranda. Son dos preguntas y hasta ahora el módulo contestaba una sola.

## Qué se pidió y qué se entregó

| Requisito del cliente | Dónde quedó |
|---|---|
| `POSITIVE_ACCELERATION` / `NEGATIVE_ACCELERATION` / `ZERO_ACCELERATION` | `ACELERACION_POSITIVA` / `_NEGATIVA` / `_NULA` — en español, como el resto del catálogo |
| Mantener ACELERANDO / DESACELERANDO / ESTABLE | Sí, intactas: son una familia aparte |
| La clasificación considera Rate **y** Aceleración | `estado_aceleracion()` (dinámica) + `signo_aceleracion()` (signo), las dos sobre el mismo par |
| Las cuatro combinaciones rate×acel de la tabla | Test parametrizado `test_las_dos_familias_son_independientes` |
| STABLE sólo con Rate **y** Aceleración en banda muerta | `estado_aceleracion()`; el caso de velocidad constante no lleva etiqueta dinámica |
| Disponibles en ESTADOS y en CONDICIONES de reglas | Sí — salen de `ETIQUETAS_ACELERACION`, que los catálogos ya recorrían |
| Que se vean en el gráfico | Tooltip y tarjeta de estadística del Explorador de Series |

## Las dos familias

Cada aceleración publica ahora **dos etiquetas a la vez** de dos familias independientes,
y las seis conviven en el mismo `pert` — que es lo que deja escribir una regla por
dinámica y otra por signo sobre la misma variable, y lo que hace que
`expandir_etiquetas_compuestas` genere las seis `NO-<X>` sin código propio.

`dom` siguió siendo la dinámica y el signo viaja en una clave nueva, `signo`: nada de lo
que ya leía `dom` (el CSS de las tarjetas, el tooltip del gráfico, la traza) cambió de
contrato.

## El cambio de comportamiento, que es el que hay que mirar

**ESTABLE pasó a exigir que las dos magnitudes estén en banda muerta.** Antes bastaba
`|2a| < umbral`, así que una variable subiendo a velocidad constante salía ESTABLE: es
correcto leído como "la magnitud del cambio no cambia" y engañoso leído —que es como se
lee, y como se escriben reglas— como "el proceso está quieto".

Ese caso **ya no lleva etiqueta dinámica**: `dom` sale vacío, las tres `NO-<dinámica>`
quedan en 1, y la variable se describe sólo con `ACELERACION_NULA`. Devolver `''` en vez
de inventar una cuarta etiqueta es deliberado: es exactamente lo que una regla necesita
preguntar y no agrega nada al catálogo.

> **Al revisar reglas existentes:** una que usara `ESTABLE` para decir "no se está
> escapando" ya no dispara con la variable moviéndose parejo. Es el único cambio de
> comportamiento de esta versión, y se resuelve con el experto de planta — no bajando la
> banda muerta.

## Archivos

| Fase | Archivo | Qué cambió |
|---|---|---|
| 1 | `core/variables/aceleracion.py` | `ETIQUETAS_DINAMICAS` + `ETIQUETAS_SIGNO`, `signo_aceleracion()`, criterio nuevo de ESTABLE, `pert` de 6 claves, `signo` en la salida |
| 1 | `web/state.py` | Las seis etiquetas y sus `NO-` en `ETIQUETAS_BASE`; `signo` en las dos capas de la traza |
| 1 | `tests/test_aceleracion.py` | 43 → 55 tests |
| 1 | `tests/test_pipeline.py` | `_acel_fija` deriva el signo; test nuevo de regla por signo |
| 2 | `web/api/config.py` | `etiquetas_dinamicas` y `etiquetas_signo` en `GET /api/aceleraciones` |
| 2 | `web/templates/index.html` | Dos pastillas por tarjeta, fila `signo` en el panel *En vivo*, `_acelUsada` sobre las seis, tabla de ayuda con los seis casos |
| 3 | `web/api/se.py` | `signo` en `/api/se/grafico/anotaciones` |
| 3 | `web/templates/traza.html` | Badge de signo en la tabla de fuzzy y fila `signo` en la celda de aceleración |
| 3 | `web/templates/graficos.html` | `acelSigno()`, tooltip y tarjeta de estadística |

## Decisiones

- **Los nombres en español**, como el resto del catálogo (`ACELERANDO`, `CERCA_ALTO`,
  `NO-INC`). El pedido llegó en inglés; mezclar las dos familias en dos idiomas sobre la
  misma variable habría sido peor que traducir una vez.
- **El signo se mide contra la MISMA banda muerta** que separa la dinámica. Un segundo
  umbral sería un segundo número que calibrar por variable, y el criterio es el mismo:
  cuánto vale `|2a|` para que el ruido no decida.
- **Dos pastillas y no una cadena `"ACELERANDO + POSITIVA"`.** Una sola cadena obliga a
  reescribir todo lo que compara contra el estado (CSS de las tarjetas y de la traza,
  colores del gráfico) y no deja preguntar por una familia sin la otra.
- **La abreviatura `2a > 0` es sólo de pantalla.** El nombre completo —el que se escribe
  en la regla— va siempre en el `title` de la pastilla y sin abreviar en la fila `signo`
  del panel en vivo y de la traza.
- **`_acelUsada()` recorre las seis.** Con la lista corta, borrar una aceleración que una
  regla usaba por `ACELERACION_NEGATIVA` no avisaba antes de guardar y el PUT se rechazaba
  con un 409 sin preaviso, que es justo lo que ese aviso existe para evitar.
- **Los colores del signo van en otra gama** (ámbar / celeste / gris) que los de la
  dinámica (rojo / verde): con los mismos colores la segunda pastilla se leería como una
  repetición de la primera.

## Verificación

```bash
python -m pytest tests/ -q
```

→ **321 pasan, 2 fallan, 7 skip**. Los dos que fallan son los de siempre
(`test_sp_ilegible_se_inhibe_y_se_recupera_solo` y
`test_el_historial_registra_lo_que_se_escribio_al_dcs`): están acoplados al `config/`
vivo y fallan igual en una copia limpia. Es el pendiente A17.

JS validado con `node --check` en los tres templates tocados (`index.html`, `traza.html`,
`graficos.html`).

## Lo que queda

**Nada de software.** Sigue pendiente la misma calibración de siempre —la banda muerta
por variable, con el proceso corriendo— y ahora además **revisar con el experto las reglas
que usen `ESTABLE`**, por el cambio de comportamiento de arriba.
