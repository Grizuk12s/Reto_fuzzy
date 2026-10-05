# Editor — limpieza visual (2026-10-02)

## Sin selector de SAG en la barra lateral

El editor muestra todos los SAG en cada sección, así que la pastilla "SAG 1 ▾" de la barra
lateral sobraba. Se quitó. La plantilla declara `data-se-sag="oculto"` y el script del router no
pinta en esta página ni el selector, ni la franja de color, ni la pastilla flotante, ni el nombre
del SAG en la pestaña. Las demás páginas (Entrada de datos, Explorador, Traza, PostgreSQL,
Export/Import…) siguen con su selector, porque muestran un SAG a la vez.

## Tracking PV-SP

"Cómo funciona el Tracking" pasa de recuadro abierto a **desplegable**, igual que las demás
ayudas. "Ejemplo de evaluación" (Tracking) y "Formato de condiciones" (Permisivos) usan el mismo
estilo de desplegable que el resto.

## Fuzzy: sin una alerta roja por cada PV

Una PV sin fuzzy no es un error: no todas las PV necesitan membresía.
- Se quitó el aviso rojo "N PV sin fuzzy: … quedan muertas".
- Las PV sin fuzzy ya no tienen una tarjeta roja cada una. Hay **una sola fila**
  "+ Crear fuzzy para [PV] [tipo] + Crear fuzzy", con el conteo discreto "N PV sin fuzzy
  (opcional)". La fila desaparece cuando todas tienen fuzzy.
- Solo se muestran tarjetas de las variables que **tienen** fuzzy (y de los fuzzy sin tag PV,
  que hay que poder borrar).
- Al crear uno, aparece su tarjeta y la PV sale del selector.

## Verificación

- `pytest tests/`: **554 pasan, 2 fallan (los de siempre), 3 skip**.
- Navegador (2 SAG, 4 PV sin fuzzy en SAG 1): sin pastilla ni franja; la fila de creación lista
  las 4 PV; al crear una aparece su tarjeta y quedan 3 en el selector; Tracking con "Cómo
  funciona el Tracking" desplegable; sin errores de JavaScript.
