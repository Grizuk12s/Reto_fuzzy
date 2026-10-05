# Multi-SAG — Fase 4: página Motor y fuera el Diagrama de flujo (2026-10-01)

## Página Motor (`/motor`)

Una tarjeta por SAG (con su color), servida por el router — no pertenece a ningún SAG: cada
llamada va con `X-SE-SAG` explícito.

- Estado del proceso (en línea / arrancando / caído) y del motor (corriendo / detenido).
- **Iniciar / Detener** por SAG. Iniciar arranca también el heartbeat si está configurado;
  Detener lo corta (sin pulso el DCS pasa a Manual por watchdog), igual que antes en Tags.
- **Iniciar todos / Detener todos** (este último pide confirmación).
- Métricas del lazo: tick, ticks/s, período, duración del tick, dormido, escrituras al DCS.
- **Auto-aplicar** y **piso del lazo** por SAG (el piso se aplica en vivo).
- Errores y avisos del arranque del motor.
- Tres secciones desplegables por SAG:
  - **Handshake con el DCS**: exigir permiso, tags FBK y EXT (solo tags OTRO habilitados de ese
    SAG). El estado sale de la última lectura con el motor corriendo, o de la configuración
    guardada con el motor detenido ("Sin tags: no escribe" en un SAG recién creado).
  - **Heartbeat**: tags OUT/IN, período, tipo, valores A/B, iniciar/detener pulso, valores en vivo.
  - **Generador de datos** — marcado *SOLO PRUEBAS*: intervalo, ticks por ciclo, tabla de tags a
    simular (los LIM con valor fijo), iniciar con confirmación.

Se llega desde la gestión de SAG (barra superior), desde el menú de cada página ("Motor") y desde
la barra lateral del editor ("Motor →").

## Página de Tags KEPserver

Con router, los paneles *Iniciar/Detener sistema*, *Sistema Experto en vivo*, *Generador*,
*Handshake* y *Heartbeat* se **ocultan** y aparece un aviso con enlace a Motor. Están marcados
`data-se-solo-local`; el script del router los oculta. **Sin router (un solo equipo) la página
queda igual que antes**, así que este mismo código sigue sirviendo para un despliegue de un equipo.

## Diagrama de flujo

Eliminada la página (`/espesador/diagrama` responde 404) y sus enlaces. La plantilla quedó en
`_to_delete/diagrama.html` por si se quiere recuperar.

## Verificación

- `pytest tests/`: **537 pasan, 2 fallan (los de siempre), 3 skip**.
- Router real con 2 SAG y Chromium: Iniciar en SAG 1 arranca solo ese motor (SAG 2 sigue
  detenido) y las métricas se mueven (~20 ticks/s con piso 50 ms); Detener lo baja; el handshake
  de ambos muestra "Sin tags: no escribe"; Tags KEPserver muestra el aviso y oculta los paneles
  viejos; sin errores de JavaScript.
