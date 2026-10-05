# Fase 2 — Ventana de pendiente en segundos y relacion 2x-4x (2026-09-22)

Cierra el punto 8 del listado de afinacion.

## El problema

La ventana se declaraba en **minutos** (`ventana_min`). Pedir 20 s obligaba a
escribir `0.33`, y 20 s es justo el orden de magnitud que interesa cuando la
pendiente se sintoniza contra el filtro: si el filtro baja a 10 s, la pendiente
tiene que quedar entre 20 y 40 s.

Y esa sintonizacion **no la comprobaba nadie**. Bajar el filtro y dejar la
pendiente larga pasaba inadvertido; en planta eso se ve como *"el SE reacciona
tarde"*, no como un error de configuracion.

## La regla que ahora se comprueba

    ventana de la pendiente = 2x a 4x la ventana del filtro de su variable

- **Por debajo de 2x** la regresion cae dentro del transitorio del filtro: la
  pendiente mide el filtro, no el proceso.
- **Por encima de 4x** promedia cambios reales y la tendencia llega tarde.

Es un **aviso, no un rechazo**. El experto puede querer una ventana larga a
proposito — una deriva de turno se mira asi. El guardado nunca se bloquea por
esto.

## Que cambio

| Archivo | Cambio |
|---|---|
| `core/fuzzy/pendientes.py` | `ventana_s_de_spec()`: la ventana sale de `ventana_s`, y si no esta, de `ventana_min` x 60. El registry usa segundos |
| `web/api/config.py` | Validacion en segundos (tope 86400 s), `_migrar_pendientes()` al leer, `_relacion_filtro()`, y la relacion viaja en el GET, el PUT y el POST |
| `web/templates/index.html` | Campo en **segundos** y, al lado, *"2.4x el filtro (50 s) — dentro de 2x-4x"*, recalculado mientras se tipea. Una pendiente nueva **nace en 3x** el filtro de su fuente |
| `tests/test_pendiente_ventana_s.py` | **Nuevo.** 23 tests: compatibilidad, migracion, bordes exactos de 2x y 4x, y que el aviso no bloquee |

### Dos unidades que no hay que confundir

- La **ventana** es cuanto pasado mira: ahora en **segundos**.
- El **eje** (`x`) es la escala de la tendencia: sigue en **unidades de
  ingenieria por minuto**, y no cambio.

Son cosas distintas y la pagina ahora lo dice. La regresion devuelve la
pendiente por minuto midiendo sobre la ventana en segundos; cambiar la unidad
de la ventana **no invalida ninguna calibracion del eje**.

### Compatibilidad

`ventana_min` se sigue leyendo. Un `pendientes.json` viejo se migra **al leer**
y la migracion se persiste recien cuando el operador guarda desde la pagina —
el mismo criterio que `_migrar_filtros`, para que un rollback del codigo se
encuentre el archivo que dejo. Un paquete de export viejo tambien importa bien.

## Estado de esta planta

`pend_hopper_nvl_pv_a_5min` quedo leido como **120 s** sobre un filtro de
**50 s**: **2.4x, dentro del rango**. Pero los dos valores son grandes. Cuando
se baje el filtro (punto 7) hay que bajar la pendiente en la misma proporcion,
y ahora la pagina lo dice sola: con el filtro en 10 s, la pendiente tiene que
ir a **20 - 40 s**.

Ojo con el nombre: dice `5min` y la ventana son 2 min. El nombre **queda
congelado a proposito** (renombrarlo dejaria muda a toda regla que lo use), asi
que es solo una etiqueta historica, no la ventana vigente.

## Estado de los tests

`406 passed, 2 failed, 7 skipped`. Los dos que fallan son los mismos de antes
de la Fase 1 (nombre del SP del contrato, reincidencia de A17/A22).
