# Hopper — Fase 3: pagina Motor separada de Tags KEPserver (2026-10-02)

Igual que en el proyecto SAG, con una sola tarjeta (el Hopper es un solo motor).

- **Nueva pagina `/espesador/motor`** (`web/templates/motor.html`, ruta en `web/api/views.py`, `MOTOR_PAGE` en `web/state.py`):
  Iniciar / Detener, metricas del lazo, Auto-aplicar, piso del lazo, estado de la config
  ("Config al dia" / "Cambios sin aplicar" / ...), boton **Aplicar cambios**, y los desplegables
  Handshake, Heartbeat y Generador de datos. Usa las mismas APIs que antes (`/api/se/*`, `/api/tags/*`).
- **Menu lateral**: Motor en la seccion Conexion (debajo de Tags KEPserver). La pagina lleva el menu y esta alineada a la izquierda.
- **Tags KEPserver**: los paneles viejos (Iniciar Sistema, panel en vivo, generador, handshake, heartbeat) quedan ocultos
  (regla CSS en index.html; el HTML y su JS siguen ahi) y hay un aviso con el enlace a Motor.
- Textos que decian "Detenlo en Configuracion SE" / "Inicia el sistema" apuntan ahora a la pagina Motor.
- Arreglo: la hora "Ultimo" del heartbeat mostraba un numero sin sentido (cortaba un epoch como texto). Tambien en el SAG.
- Test: `tests/test_diagrama_hopper.py::test_motor_en_su_propia_pagina`.

Respaldo: `_to_delete/respaldo_fase3_20261002/`.
