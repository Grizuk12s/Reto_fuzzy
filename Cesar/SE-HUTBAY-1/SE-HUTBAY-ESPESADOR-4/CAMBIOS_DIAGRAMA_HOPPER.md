# Diagrama del Hopper, estadistica del nivel y generador senoidal (2026-10-02)

## Pagina Diagrama (`/espesador/diagrama`)
- Reemplaza el diagrama de flujo (respaldo en `_backup_rediseno_20260922/antes_diagrama_hopper_20261002/`).
- Hopper 3D en SVG: nivel, limites MIN/MAX, llenado, bomba PU-009 con velocidad PV, SP DCS, SP SE y corriente.
- Refresco cada 1 s con el motor encendido (`GET /api/diagrama/hopper`, usa `_last_read` del motor; con el motor
  apagado no toca el KEPserver y marca "ultimos valores").

## Estadistica del nivel (`core/variables/estadistica.py`)
- Promedio y desviacion estandar MUESTRAL (n-1) del nivel del Hopper (rol `hopper_nvl_pv_a`).
- Muestrea 1/s solo con el motor encendido. **Calculo periodico** cada `recalculo_min` (30) sobre los ultimos
  `ventana_min` (30) minutos, mas una lectura **en vivo** de la misma ventana.
- Rango y periodo editables en la pagina (y por `PUT /api/diagrama/estadistica`); `POST .../calcular` = "Calcular ahora".
- Config en `config/espesador/estadistica.json` (se crea al guardar). `ventana_max_min` (tope del rango) por defecto
  1440 min; no hay un maximo decidido. Tope duro de memoria: 1440 min (`TOPE_DURO_MIN`).

## Generador de datos: senoidal (revision 2026-10-02 tarde)
- Por tag (no LIM): `sin_enabled` y `sin_periodo_s` en `generator.ranges` de `tags.json`. **El rango de la onda es el
  Min/Max del random del tag** (ya no hay `sin_min`/`sin_max`; si aparecen en un tags.json viejo se descartan).
- Onda NO perfecta: cada medio periodo se sortea el extremo (valles en el 45 % inferior del rango, crestas en el 45 %
  superior), con suavizado coseno; ademas se suma el ruido del tag y se acota a Min/Max. Reproducible por reloj+tag.
- Columna **Sinusoidal** en la tabla (Configuracion SE): casilla + `Periodo [ ] s` (input mas ancho, sin datalist: el
  valor quedaba tapado).
- El nivel del Hopper A nace senoidal (30 s) la primera vez que se ve sin la clave `sin_enabled`
  (`SIN_POR_DEFECTO_ROLES` en `web/state.py`); si el operador la apaga, no se vuelve a sembrar.
- La tabla ya no se reconstruye mientras haya ediciones sin guardar.

## Diagrama: burbujas y derrame
- Nivel 95-100 %: burbujitas flotando sobre la superficie del tanque (mas con mas nivel).
- Nivel > 100 %: insignia "DERRAME" y burbujas que se desprenden por el borde y bajan por la pared EXTERIOR, 7 s hasta
  el 43 % del tanque; ahi se desvanecen en 3 s. Cada burbuja vive su ciclo: si el nivel baja a 90 %, las ya soltadas
  terminan de caer y desaparecer. Solo se generan con el motor encendido.

## Tests
`tests/test_diagrama_hopper.py`, `tests/test_estadistica_y_senoidal.py`.
OJO: `tests/` escribe en `config/espesador/` (tags.json, activity_log.json, .backup/): correr la suite sobre una
copia del proyecto, no sobre la carpeta viva.
