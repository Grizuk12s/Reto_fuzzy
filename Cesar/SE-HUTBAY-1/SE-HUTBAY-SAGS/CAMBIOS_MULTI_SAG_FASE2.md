# Multi-SAG — Gestión de SAG y Fase 2 (tags por SAG) (2026-10-01)

## Gestión de SAG (`/gestion`)

Es la página a la que lleva la tarjeta **Molienda SAG** de la portada del Hopper, y la entrada
"Gestionar SAG…" del selector de cada página.

- **SAG 1 fijo:** existe siempre, no se puede eliminar. La migración ya **no** crea el SAG 2.
- **Crear SAG** (hasta `MAX_SAGS = 2`): nombre, color y punto de partida:
  - *Vacío*: solo hereda conexiones (KEPserver, PostgreSQL), licencia y piso del motor.
  - *Clonar la lógica de SAG N*: además copia reglas, filtros, fuzzy, pendientes, aceleraciones,
    estados, waits, permisivos, defuzzy, tracking, variables, contrato y los tags de **lectura**
    (PV, CRUDA, LIM). **Nunca** SP, handshake ni heartbeat.
  - En ambos casos el handshake queda encendido y sin tags: el SAG nuevo no escribe al DCS.
  - Su proceso arranca solo en ~4 s; su motor queda detenido.
- **Renombrar / color.**
- **Eliminar:** solo con el motor detenido. La configuración se **archiva** en
  `config/sags_archivo/<id>_<fecha>/`; las tablas de la BD se conservan.

API (router): `GET/POST /gestion/api/sags`, `PATCH/DELETE /gestion/api/sags/<id>`.

## Fase 2 — a qué SAG pertenece cada tag

**Entrada de datos** cambia la columna *Proceso* por **Pertenece a**: SAG 1 / SAG 2 / Compartido.
Los filtros de arriba filtran por dueño. Al final de la tabla aparecen los tags que están solo en
otro SAG, para poder traerlos o compartirlos.

Reglas (las aplica el servidor, no la página):
- Solo **PV, CRUDA y LIM** se pueden compartir. **SP y OTRO** pertenecen a un solo SAG.
- Un tag **no se quita** de un SAG que lo usa: rol asignado, límite de un fuzzy/defuzzy, tag de
  arranque del tracking, pendiente/aceleración, handshake o heartbeat. El mensaje dice qué lo usa.
- Los cambios se hacen a través de la API de tags de cada SAG (con su lock y sus validaciones):
  primero se agrega donde falta y solo después se quita donde sobra.

Flujo recomendado: cargar cada tag en **Tags KEPserver del SAG al que pertenece** (el selector de
arriba dice en cuál estás). Los de lectura que usan ambos SAG se cargan una vez y se marcan
*Compartido*. Ojo: al dar de alta un tag PV/SP el SE le asigna rol solo (entra al contrato), así
que moverlo después pide quitarle el rol primero.

API (router): `GET /gestion/api/tags`, `POST /gestion/api/tags/asignar {name, dueno}`.

## Otros cambios

- El script que fija la página a su SAG ahora se inserta al **inicio del `<head>`**: así también
  las primeras llamadas de la página (las que hace al cargar) salen con `X-SE-SAG`.
- Archivos: `sags.py` (crear/actualizar/eliminar), `tags_sag.py` (nuevo), `router.py`,
  `web/templates/gestion.html` (nuevo), `web/templates/entrada.html`, `bienvenida.html` (aquí y en
  ESPESADOR-4: la tarjeta SAG va a `/gestion`).

## Verificación

- `pytest tests/`: **531 pasan, 2 fallan (los de siempre), 3 skip** (30 tests en `test_multi_sag.py`).
- Router real con procesos reales: arranca solo con SAG 1; crear SAG 2 → en línea en ~4 s; alta de
  un PV y un SP en SAG 1 por la API real; compartir el PV lo deja en ambos `tags.json`; compartir el
  SP se rechaza; mover un tag con rol se rechaza con el motivo; Entrada de datos sale con el script
  en el `<head>`; eliminar SAG 2 con motor corriendo se rechaza, detenido se archiva y su proceso
  baja (4 → 2 procesos); eliminar SAG 1 se rechaza; al bajar el router quedan 0 procesos.
