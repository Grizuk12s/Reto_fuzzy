# Multi-SAG — Fases 0 y 1 (2026-10-01)

Plan completo y decisiones: `PLAN_MULTI_SAG.md`.

## Qué quedó

Un contenedor SAG con **2 SAG**, cada uno con su **proceso propio del SE** (motor, auto-aplicar,
heartbeat, handshake, OPC-UA), su carpeta de configuración y sus tablas.

```
navegador --:5001--> router (:5000 en el contenedor) --+--> SE sag1  127.0.0.1:5101  config/sags/sag1/  tablas sag1_*
                                                       +--> SE sag2  127.0.0.1:5102  config/sags/sag2/  tablas sag2_*
```

| Archivo | Qué |
|---|---|
| `rutas.py` | **Nuevo.** La carpeta de config sale de `SE_SAG_ID`. Sin variable: `config/espesador/` (modo un solo equipo, como el Hopper) |
| `web/state.py`, `config.py`, `web/api/contrato.py`, `runner.py`, `connectors/kepserver.py`, `scripts/*.py` | Leen la carpeta de `rutas.py` en vez de `config/espesador` fijo |
| `web/api/postgres.py` | `PROCESO_BD` = id del SAG → `sag1_entrada`, `sag2_entrada`… |
| `sags.py` | **Nuevo.** Registro `config/sags.json` (id, nombre, color, puerto interno; `MAX_SAGS = 2`), creación de config vacía y migración |
| `router.py`, `router_gunicorn.conf.py` | **Nuevos.** Supervisor de procesos + reenvío de peticiones + fijación de la página a su SAG |
| `Dockerfile` | `CMD` arranca el router |
| `tests/test_multi_sag.py` | **Nuevo.** 17 tests |

## Migración (automática, al primer arranque del router)

- `config/espesador/` se **copia** a `config/sags/sag1/` (el original queda).
- (Actualizado en Fase 2: la migración solo crea el SAG 1; el SAG 2 se crea desde `/gestion`.)
- Si `config/sags.json` ya existe no se toca nada.

## Cómo se elige el SAG

`?_sag=<id>` > encabezado `X-SE-SAG` > cookie `se_sag` > primer SAG. Un SAG pedido explícitamente
que no existe devuelve 404: no se adivina a dónde mandar un Guardar.

Cada página HTML recibe un script (`/gestion/sag.js`) que:
- agrega `X-SE-SAG` a todo `fetch()` de la página → los Guardar van al SAG con el que se abrió;
- agrega `_sag` a los enlaces internos y a la URL (recargar vuelve al mismo SAG);
- muestra arriba a la derecha el **selector de SAG** con su color (mínimo; el definitivo es la Fase 3).

Rutas propias del router: `/health`, `/gestion/api/sags`, `/gestion/seleccionar/<id>?volver=/ruta`, `/gestion/sag.js`.

## Supervisor

- Lanza un `gunicorn app:app` por SAG con `SE_SAG_ID` (o `python app.py` donde no hay gunicorn).
- Si un SAG muere, lo relanza con espera creciente (2, 4, 8… hasta 60 s). El otro SAG no se entera.
- En Linux cada SAG muere solo si muere el router (`PR_SET_PDEATHSIG`): no quedan motores huérfanos.

## Verificación

- `pytest tests/`: **518 pasan, 2 fallan (los de siempre), 3 skip** (501 + 17 nuevos).
- Router real con gunicorn y 2 SAG: los dos levantan en ~3 s; el piso guardado en sag2 solo cambia
  `config/sags/sag2/motor.json`; arrancar el motor de sag1 no arranca sag2; matar el proceso de sag2
  da 503 en sag2 mientras sag1 sigue corriendo, y sag2 vuelve solo en ~3 s; al bajar el router (o
  matarlo con SIGKILL) no quedan procesos huérfanos.

## Límites conocidos

- La navegación hecha por JavaScript con `location.href = '/...'` usa la cookie, no la página. Las
  **llamadas a la API** (lo que guarda) siempre van al SAG de la página.
- No probado en Windows (`python router.py` lanza los SAG con `app.py`). En planta se usa Docker.
- Tags, generador, heartbeat y handshake siguen siendo por SAG en su `tags.json` (Fases 2 y 4).
