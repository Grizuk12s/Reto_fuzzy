# Multi-SAG — Fase 5: vistas con varios SAG (2026-10-01)

Todo se apoya en `SE_SAG.fetchDe(id, url)` (Fase 3): una página pide datos de otro SAG con el
encabezado `X-SE-SAG` explícito. **El backend no cambió**: cada SAG ya responde su propia API.
Sin router (un solo equipo) las páginas quedan como antes.

## Explorador de Series

- Nuevo filtro de SAG junto a los grupos: **SAG actual (este)**, cada uno de los otros SAG, o
  **Todos los SAG**.
- Los tags de otro SAG aparecen con una etiqueta de su color (`SAG 2`) y, en el gráfico y en las
  tarjetas de resumen, con su nombre al lado ("Potencia · SAG 2"). Se pueden superponer series de
  distintos SAG en el mismo gráfico.
- Internamente una serie de otro SAG se llama `sag2::TAG`; el historial se pide a ese SAG con el
  nombre crudo y vuelve con el prefijo. Si hay series de otro SAG, la página también hace que
  **ese** SAG muestree el KEPserver (su buffer se llena con su propia lectura en vivo).
- Dominio, pendiente y aceleración en el tooltip siguen siendo solo los del SAG de la página.

## Waits y Tracking PV-SP: "Otros SAG"

Debajo de la tabla del SAG actual aparece **Otros SAG** — una tabla por cada otro SAG, con su
color, en **solo lectura** y con el enlace *Editar en SAG N →* (lleva a esa misma sección del otro
SAG). Si un SAG no responde, su bloque lo dice.

Es un componente reutilizable (`static/otros-sag.js`, `OtrosSag.montar({...})`): para sumar otra
sección (reglas, filtros, fuzzy…) basta con llamarlo al cargarla, indicando endpoint y columnas.

## Motor

Cada tarjeta suma la métrica **Alertas** (activas de ese SAG, en ámbar si hay; el detalle va en el
tooltip).

## Verificación

- `pytest tests/`: **540 pasan, 2 fallan (los de siempre), 3 skip**.
- Router real con 2 SAG y Chromium: Waits y Tracking de SAG 1 muestran debajo los de SAG 2
  ("Otros SAG", 1 fila cada uno); el Explorador lista con "Todos los SAG" los tags de ambos y al
  elegir uno de SAG 2 pide catálogo e historial a SAG 2 (`X-SE-SAG: sag2`, nombre crudo); Motor
  muestra las alertas por SAG; sin errores de JavaScript.
