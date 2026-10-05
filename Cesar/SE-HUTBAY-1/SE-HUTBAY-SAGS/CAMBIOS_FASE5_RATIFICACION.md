# Fase 5 — Ratificar la pendiente con la aceleracion (2026-09-22)

Cierra el punto 6 del listado de afinacion.

## El problema

Una pendiente mide sobre una ventana larga. Eso es lo que la hace robusta al
ruido **y** lo que la hace llegar tarde: sigue diciendo *"sube"* un rato
despues de que la variable dejo de subir, porque la mayor parte de su ventana
todavia esta subiendo. Es una propiedad del metodo, no un error de
calibracion — acortar la ventana no lo arregla, solo cambia el ruido por el
retardo.

La aceleracion ya miraba 5-10 s y producia `rate`: la derivada **en el extremo
derecho** de su ventana, o sea lo que la variable esta haciendo ahora. Los dos
numeros existian y **nadie los cruzaba**: para contrastarlos habia que escribir
la condicion a mano en cada regla.

## Que hace la ratificacion

Cada pendiente puede declarar, opcionalmente, con que aceleracion se ratifica.
Antes de fuzzificar:

| Situacion | Que pasa |
|---|---|
| `rate` y pendiente al mismo lado | la tendencia se sostiene: pasa como esta |
| `\|rate\|` dentro de su banda muerta | no alcanza para desmentir: pasa como esta |
| `rate` al lado contrario | **desmentida**: se fuzzifica como pendiente NULA |

### Por que cero y no "STABLE"

Las etiquetas las nombra el operador — `INC/DEC/STABLE` es solo la plantilla, y
una planta puede tener `SUBIENDO/QUIETO/CAE_RAPIDO`. Forzar una etiqueta
obligaria al nucleo a adivinar cual es la neutra de cada planta.

Evaluar el mismo modelo difuso en `0.0` no necesita saber ningun nombre: se
activa la etiqueta que el operador haya puesto sobre el cero de SU eje, con la
forma que le haya dado. **El SE deja de afirmar que se mueve, y no afirma nada
nuevo en su lugar.**

### El rate en banda muerta pasa, a proposito

Tratarlo como "no confirmado" dejaria la pendiente muda en operacion normal y
tranquila, que es justo cuando mas se la necesita. La banda muerta significa
"no se puede afirmar nada en ninguna direccion", no "la tendencia es falsa".

### Sin ratificador, la pendiente no se emite

Si se declaro ratificacion y la aceleracion no esta disponible en ese tick, la
pendiente **se omite** y las reglas que la nombran quedan `no_evaluable`, con
el motivo en la traza. Emitirla sin ratificar seria afirmar justamente lo que
el operador pidio comprobar. Es el mismo criterio fail-closed que el `NOT` del
motor y que una PV sin limites.

En la practica casi no ocurre: la ventana de la aceleracion es mas corta que la
de la pendiente (el validador lo exige), asi que cuando la pendiente esta lista
la aceleracion ya lo estaba. Y hay red de seguridad: **no se puede borrar ni
deshabilitar una aceleracion que esta ratificando** a alguna pendiente.

## Que cambio

| Archivo | Cambio |
|---|---|
| `core/fuzzy/pendientes.py` | `ratificar()` y el uso de `ratifica_con` en `actualizar()`; la traza recibe `slope_medido`, `ratifica_con` y `ratificacion` |
| `core/variables/aceleracion.py` | La salida expone `umbral_rate` (vivia solo en el registry) |
| `web/state.py` | **Las aceleraciones se calculan ANTES que las pendientes** y se les pasan; la traza propaga los campos nuevos |
| `web/api/config.py` | Valida `ratifica_con` (existe, habilitada, misma variable, ventana mas corta), lo ofrece en el GET, y protege el borrado/deshabilitado |
| `web/templates/index.html` | Selector *"ratificar con ..."* por pendiente, con solo las aceleraciones de esa variable |
| `web/templates/traza.html` | Badge **ratificada** / **desmentida**, y en ese caso la pendiente medida al lado de la fuzzificada |
| `tests/test_ratificacion_pendiente.py` | **Nuevo.** 22 tests: los cuatro cuadrantes, banda muerta, fail-closed y la validacion |

### El orden del tick cambio

Las aceleraciones ahora van **antes** que las pendientes. Con el orden anterior
una pendiente ratificada habria usado la aceleracion del tick **pasado** — que
en ciclo libre puede ser de hace 50 ms o de hace un segundo, segun la latencia
de OPC-UA. Es el unico cambio de orden en el pipeline.

### En la traza

Cuando la aceleracion desmiente, lo fuzzificado es `0` y lo medido es otra
cosa. Los dos se muestran: sin eso, el operador ve una pendiente nula y no
tiene forma de saber que el SE **si** midio movimiento y decidio no afirmarlo.

## Como activarlo en esta planta

`hopper_nvl_pv_a` ya tiene su aceleracion de 5 s. En *Fuzzy de pendiente*,
en la tarjeta de `pend_hopper_nvl_pv_a_5min`, elegir
**"ratificar con hopper_nvl_pv_a_Acceleration_5s (5 s)"** y guardar.

Antes de activarla conviene **calibrar `umbral_estable`** de esa aceleracion
mirando la traza: la segunda derivada amplifica el ruido, y una banda muerta
demasiado chica haria que el `rate` cambie de signo por ruido y desmienta
tendencias buenas. Si se quiere una segunda opinion a 10 s, se crea otra
aceleracion sobre la misma variable y se elige esa.

## Estado de los tests

`444 passed, 2 failed, 7 skipped`. Los dos que fallan son los mismos de antes
de la Fase 1 (nombre del SP del contrato, reincidencia de A17/A22).
