# Multi-SAG — Fase 3: el SAG integrado en cada página (2026-10-01)

## Qué cambió

- **Máximo 4 SAG** (`MAX_SAGS = 4` en `sags.py`). El tope lo manda el código, no `sags.json`:
  subirlo no exige editar el archivo en cada planta. Puertos internos 5101–5104.
- La pastilla flotante de la Fase 1 se reemplazó por el **SAG dentro de la propia página**:
  - Barra lateral del editor (donde decía "SAG"): nombre del SAG en su color; colapsada, `S1`, `S2`…
  - Barra superior de Entrada de datos, Explorador de Series, PostgreSQL, Export/Import (y Diagrama).
  - Título de Traza, Historial de regla, Historial de escrituras e Historial de alertas.
  - El título "Entrada de datos **SAG 2**" toma el color del SAG.
  - Clic en cualquiera → menú *Cambiar de SAG* con el estado en vivo de cada uno ("sin conexión"
    si su proceso no responde) y *Gestionar SAG…*. Se cierra con Esc o clic afuera.
- **Franja de 3 px del color del SAG** arriba de toda página, y el título de la pestaña del
  navegador empieza con el nombre del SAG.
- Una página sin lugar reservado sigue mostrando la pastilla flotante (no queda ninguna así).
- La gestión propone para un SAG nuevo el primer color de la paleta que no esté en uso.

Los lugares se marcan en las plantillas con `data-se-sag="full" | "short" | "texto"`; el script
del router (`/gestion/sag.js`) los rellena. Corriendo sin router (un solo equipo) quedan como antes.

## Preparado para ver varios SAG en la misma página

El script expone `window.SE_SAG`:

```js
SE_SAG.actual        // {id, nombre, color} del SAG de la página
SE_SAG.todos         // todos los SAG
SE_SAG.fetchDe('sag2', '/api/waits')   // pide datos de OTRO SAG explícitamente
```

Con eso una página puede mostrar, por ejemplo, los waits del SAG 1 arriba y los de los demás SAG
abajo, cada tabla con su color, sin tocar el backend (cada SAG ya responde su propia API). Lo que
la página guarde sigue yendo a su SAG salvo que use `fetchDe` a propósito.

## Verificación

- `pytest tests/`: **533 pasan, 2 fallan (los de siempre), 3 skip** (32 en `test_multi_sag.py`,
  incluido uno que exige el lugar del SAG en las 9 páginas).
- Router real con 3 SAG y Chromium: gestión con 3 tarjetas y colores; barra lateral con "SAG 2 ▾"
  violeta y franja superior; menú con los 3 SAG; Traza con "SAG 3 ▾" rosa; Entrada de datos con
  "SAG 2" en el título y filtros por dueño; sin errores en la consola.
