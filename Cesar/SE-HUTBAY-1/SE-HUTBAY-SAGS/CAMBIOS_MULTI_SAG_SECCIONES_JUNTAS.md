# Multi-SAG — Todas las secciones con todos los SAG en la misma página (2026-10-02)

Extiende lo de Waits (`CAMBIOS_MULTI_SAG_WAITS_JUNTOS.md`) a **Contrato de Variables, Variables
calc., Tracking PV-SP, Filtros Exp-Q, Fuzzificación, Aceleración, Estados, Permisivos, Motor de
Reglas y Defuzzificación**.

## Cómo se ve

En cada sección, debajo del título: **un bloque por SAG**, cada uno con el color de su SAG
(filo, borde y cabecera), en el orden del registro. Todos **editables ahí mismo**.

- **Bloque del SAG de la página**: el editor de siempre. Sus botones de sección (Guardar, Vaciar,
  + Nueva…) pasan a la cabecera del bloque para que quede claro de qué SAG son.
- **Bloques de los demás SAG**: cargan **el mismo editor** de esa sección, embebido y fijado a su
  SAG (`/espesador?_sag=<id>&_embed=1#<seccion>`). Todo lo que se guarde ahí va a ese SAG: el
  script del router le pone `X-SE-SAG` a cada llamada. Botón **↻ Recargar** por bloque.
- Al crear un SAG en la gestión aparece un bloque más en cada sección.
- Tracking PV-SP deja el "Otros SAG" de solo lectura: ahora es editable como las demás.
- Waits mantiene su versión propia (tablas alineadas entre SAG).

## Por qué embebido y no reescrito

Las 10 secciones suman ~8.000 líneas de editores (fuzzy con gráficos, reglas con modales,
defuzzy…). Reescribir cada una para N SAG duplicaría ese código y sus errores. Embebiendo el
mismo editor, cada SAG usa exactamente el código probado, y un arreglo en un editor vale para
todos los bloques.

Detalles del modo embebido (`static/multi-seccion.js`):
- Oculta barra lateral, alertas, barra de actividad y el título de la sección (ya está arriba).
- El alto del bloque sigue al contenido (sin scroll anidado).
- Los modales (Nueva regla, etc.) se ubican en la parte del bloque que estás viendo y el bloque
  crece mientras el modal está abierto.
- Los enlaces a otras páginas se abren en la ventana principal.
- No se anida: un bloque embebido nunca embebe otros.

Sin router (un solo equipo) nada cambia.

## Verificación

- `pytest tests/`: **550 pasan, 2 fallan (los de siempre), 3 skip**.
- Router real con 2 SAG (Chromium): Contrato, Filtros y Reglas muestran SAG 1 (editor propio, con
  sus botones en la cabecera) y SAG 2 (embebido); "Guardar" en el bloque de SAG 2 envió
  `PUT /api/contrato` con `X-SE-SAG: sag2`; "+ Nueva Regla" en el bloque de SAG 2 abre el modal
  visible y el bloque vuelve a su alto al cerrarlo; sin errores de JavaScript.

## Ajuste: la ayuda va una sola vez (2026-10-02)

Los textos que explican cómo funciona una sección son iguales para todos los SAG y se repetían
dentro de cada bloque. Ahora se marcan con `data-se-ayuda` en la plantilla y se muestran **una
sola vez, entre el título de la sección y los bloques**; dentro de los bloques embebidos se ocultan.

| Sección | Texto movido arriba |
|---|---|
| Contrato | "¿Para qué sirve el contrato?" y el aviso "Quitar una variable no es borrar una fila…" |
| Tracking PV-SP | "Cómo funciona el Tracking" y "Ejemplo de evaluación" |
| Aceleración | "Cómo se calcula y cómo se calibra" |
| Permisivos | "Formato de condiciones (sintaxis admitida)" |
| Fuzzy | La explicación de "Fuzzy de pendiente" (como desplegable "Fuzzy de pendiente: cómo funciona"); el subtítulo "Fuzzy de pendiente" y sus botones quedan en cada bloque |

Variables calc., Filtros, Estados, Reglas y Defuzzy no tenían textos repetidos (su explicación ya
estaba solo en el título de la sección). Para marcar otro texto en el futuro basta con agregarle
`data-se-ayuda` (o `data-se-ayuda="Título"` para mostrarlo como desplegable).
