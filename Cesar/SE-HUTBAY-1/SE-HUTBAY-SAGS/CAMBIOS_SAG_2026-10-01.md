# SAG — contenedor propio a partir del Hopper (2026-10-01)

Esta carpeta (SE-HUTBAY-SAGS) es el SE del **SAG**. Mismo código que el Hopper
(SE-HUTBAY-ESPESADOR-4), con su propia configuración, puerto, tablas y motor.

| Qué | Hopper | SAG |
|---|---|---|
| Carpeta | SE-HUTBAY-ESPESADOR-4 | SE-HUTBAY-SAGS |
| Contenedor / imagen | se-espesador-A0-45 | `se-sag` / `se-sag:0.1` |
| Puerto | 5000 (portada común) | 5001 |
| Tablas PostgreSQL | `espesadores_entrada/_salida` | `sag_entrada/_salida` |
| Seed de tags PCS7 | sí | **no** (`SEED_TAGS_PLANTA=0`) |

Levantar: `docker compose build` y `docker compose up -d` en esta carpeta.

## Cambios

- `docker-compose.yml`: stack, servicio, contenedor e imagen `se-sag`; `5001:5000`; sin seed.
- `config/espesador/*.json`: **vaciados** (tags, contrato, reglas, filtros, fuzzy, pendientes,
  aceleraciones, defuzzy, tracking, estados, waits, permisivos, variables, log de actividad).
  Se conservaron: conexión KEPserver, conexión PostgreSQL, licencia y `motor.json`.
  Handshake **encendido y sin tags**: fail-closed, el SAG no escribe ningún SP hasta que se
  configuren sus propios `Enable_Ext` / `Enable_FBK`.
  Respaldo de lo que había: `_respaldo_config_hopper_20261001/`.
- `config.py`, `web/api/contrato.py`: un contrato con listas **vacías** es válido (antes se
  rellenaba con la plantilla del espesador). Solo una clave ausente cae a la plantilla.
- `web/api/postgres.py`: `PROCESO_BD` (default `sag`, override `SE_PROCESO_BD`) define el prefijo
  de las tablas; la página de Postgres solo lista las de este proceso. `web/state.py` y
  `postgres.html` usan ese valor.
- Plantillas: títulos y badges "Prueba/Demo" / "SE Espesador" → "SAG".
- `static/portada.js` (cargado en todas las páginas internas): el logo / "Inicio" vuelve a la
  portada común en el puerto 5000, mismo host.
- Portada (`bienvenida.html`, aquí y en ESPESADOR-4): tarjeta **Molienda SAG** activa → `:5001`,
  con estado Disponible / Sin conexión.

## Verificación

- Con la config vacía: la app levanta, `/health` y las páginas responden 200, el motor arranca y
  se detiene sin errores, `ensure_tables` crea `sag_entrada` / `sag_salida`.
- `pytest` con el código nuevo y la config del Hopper: 501 pasan, 2 fallan (los de siempre) —
  igual que antes del cambio. Con la config vacía fallan ~138 tests más: están acoplados al
  `config/` vivo (pendiente A17), no son regresiones.
