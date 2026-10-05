# Multi-SAG — Export/Import por SAG y tags compartidos sin trabas (2026-10-01)

## Regla nueva: un tag en varios SAG, una sola escritura

Antes solo PV, CRUDA y LIM se podían compartir: cambiar un SP u OTRO a *Compartido* estaba
bloqueado ("no deja cambiar"). Ahora **cualquier tag se puede compartir** — estar en varios SAG
significa que todos lo **ven**. Lo que tiene un solo dueño es la **escritura al DCS**:

| Qué | Regla | Dónde se aplica |
|---|---|---|
| SP con rol (la salida) | Un solo SAG por tag | Asignar rol en Tags, auto-rol del contrato, importar |
| Tag de handshake (EXT/FBK) | Un solo SAG por tag | Guardar handshake, importar |
| Tag de heartbeat (OUT/IN) | Un solo SAG por tag | Guardar heartbeat, importar |

Un SP compartido llega a los demás SAG **sin rol**: se ve (Tags, Explorador), pero no entra a su
contrato ni a su defuzzy. Para pasar la escritura a otro SAG: quitar el rol en el SAG que lo
escribe y asignarlo en el otro. Los mensajes de rechazo dicen qué SAG lo tiene.

Implementado en `exclusividad.py` (cada SAG lee los `tags.json` de los demás, solo lectura). Sin
`SE_SAG_ID` (un solo equipo, como el Hopper) no aplica nada: el comportamiento es el de antes.
Esto deja preparado el caso futuro de "los mismos tags en múltiples SAG".

## Export / Import

- **Exportar desde:** cualquier SAG (por defecto el de la página). El archivo se llama
  `se-export_<sag>_<fecha>.json` y lleva `origen_sag`.
- **Importar en:** un SAG concreto o **Todos los SAG**.
  - En un SAG: los tags del archivo quedan en ese SAG → en Entrada de datos aparecen asignados a
    ese SAG (si ya estaban en otro, quedan *Compartido*).
  - En todos: se aplica SAG por SAG, **el de la página primero**. Los tags quedan **Compartido**.
    Setpoints con rol, handshake y heartbeat quedan en el SAG de la página; a los demás les llegan
    los SP sin rol y no se les importa un handshake/heartbeat cuyos tags ya usa otro SAG.
  - Antes de aplicar se revisa el motor de **cada** destino: si alguno corre, no se importa nada.
  - El resultado se muestra por SAG (tags agregados/actualizados, SP sin escritura, bloques del
    DCS omitidos). Cada SAG guarda su propio respaldo: deshacer se hace desde cada SAG.
- La vista previa se calcula contra el primer destino (con "Todos", el SAG de la página).

### Corrección: el import emparejaba tags por id

Un paquete de otro SAG trae ids propios: el id 1 del archivo podía ser **otro tag** en el destino,
y el import le pisaba el nombre. Ahora se empareja por **nombre** (el id solo si la fila no trae
nombre) y un id ocupado se renumera.

## Tags KEPserver

Los tags que están en más de un SAG llevan la marca **COMPARTIDO** (celeste, con un punto del
color de cada otro SAG que lo tiene, y la lista en el tooltip) y un filo celeste en la fila. Además:
**"lo escribe SAG N"** en un SP que escribe otro SAG, y **"handshake/heartbeat de SAG N"** en los
tags del DCS de otro SAG.

## Verificación

- `pytest tests/`: **549 pasan, 2 fallan (los de siempre), 3 skip** (nuevo
  `tests/test_import_multi_sag.py` + casos en `test_multi_sag.py`).
- Router real con 2 SAG (Chromium): compartir un SP de SAG 1 → llega a SAG 2 sin rol; darle rol en
  SAG 2 → rechazado ("ya lo escribe SAG 1"); usar en SAG 2 el FBK de SAG 1 → rechazado; exportar
  SAG 1 e importar en **Todos los SAG** desde la página → "SAG 1: 3 tag(s) actualizados · SAG 2:
  +2 tag(s) · 1 actualizado · sin escritura: Vel_SP"; los 3 tags quedan Compartido y el SP lo
  sigue escribiendo solo SAG 1; Tags KEPserver de SAG 2 muestra COMPARTIDO; sin errores de JS.
