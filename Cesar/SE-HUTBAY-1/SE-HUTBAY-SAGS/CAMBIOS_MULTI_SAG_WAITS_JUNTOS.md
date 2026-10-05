# Multi-SAG — Waits: todos los SAG en la misma página (2026-10-02)

Prueba del formato "todos los SAG juntos", aplicado **solo a Waits**.

- Con varios SAG, la sección Waits muestra **un bloque por SAG**, en el orden del registro, cada
  uno con el color de su SAG (filo izquierdo, cabecera y borde). El SAG de la página dice
  "SAG de esta página".
- Cada bloque es **editable ahí mismo**: *+ Nuevo Wait*, *Vaciar*, *Editar* y *Borrar* actúan
  sobre **ese** SAG (`SE_SAG.fetchDe` → encabezado `X-SE-SAG`). Ya no hay enlace "Editar en SAG N".
- El modal de nuevo/editar muestra a qué SAG se guarda (color y nombre en el título y en el botón
  "Guardar en SAG N") y ofrece como *variable controlada* los **setpoints de ese SAG** (de su
  contrato, con su descripción).
- Al crear un SAG en la gestión aparece un bloque más, sin tocar la página.
- Las columnas están alineadas entre bloques (anchos fijos).
- El editor de reglas de la página sigue usando el catálogo de waits de **su** SAG; si se edita ese
  bloque, se actualiza también.
- Sin router (un solo equipo) la sección queda como antes. Tracking PV-SP sigue con "Otros SAG"
  en solo lectura.

Implementación: objeto `WaitsMulti` en `web/templates/index.html` (sección Waits).

## Verificación

- `pytest tests/`: 549 pasan, 2 fallan (los de siempre), 3 skip.
- Router real con 3 SAG (Chromium), desde la página del SAG 1: crear un wait en SAG 2 con su
  setpoint ("Velocidad SAG 2"), editarlo, crear uno en SAG 3 y borrar uno de SAG 1 → cada cambio
  quedó en el `waits.json` de su SAG; sin errores de JavaScript.
