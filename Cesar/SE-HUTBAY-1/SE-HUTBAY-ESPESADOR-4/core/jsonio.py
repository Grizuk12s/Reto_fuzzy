# -*- coding: utf-8 -*-
"""Escritura atomica de los JSON de configuracion (B3.4).

Todos los `_save_*` del proyecto escribian directo sobre el archivo destino:

    with open(RUTA, "w", encoding="utf-8") as f:
        json.dump(data, f, ...)

El `open(..., "w")` trunca el archivo ANTES de escribir nada. Si el proceso se
cae, el contenedor se reinicia o el disco se llena a mitad del `json.dump`, lo
que queda en disco es un JSON incompleto — y entonces el loader correspondiente
lo considera corrupto y **cae a la plantilla de Python**. O sea: el modo de
falla no es "se perdio la ultima edicion", es "la planta arranca con la
configuracion de otra planta".

`escribir_json_atomico` lo evita con el patron de siempre: se escribe un archivo
temporal COMPLETO en el mismo directorio y despues se lo renombra encima del
destino con `os.replace`, que en POSIX y en Windows es atomico. En cualquier
instante en que se corte, en el destino hay o la version vieja entera o la nueva
entera, nunca media.

El `.tmp` va en el **mismo directorio** a proposito: `os.replace` solo es
atomico dentro del mismo sistema de archivos, y `./config` es un volumen de
Docker. Un temporal en `/tmp` degradaria el rename a copiar+borrar.

Ademas hay un lock por ruta, para que dos hilos que guardan el mismo archivo no
se pisen los temporales ni el rename. **No cubre el read-modify-write**: eso es
responsabilidad de quien lee y modifica (ver `web/state._tags_lock`), porque el
lock tendria que estar tomado desde antes de leer.
"""
from __future__ import annotations

import json
import os
import threading

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_de(path: str) -> threading.Lock:
    """Lock compartido para una ruta. Misma ruta, mismo lock."""
    clave = os.path.abspath(path)
    with _locks_guard:
        lk = _locks.get(clave)
        if lk is None:
            lk = threading.Lock()
            _locks[clave] = lk
        return lk


def escribir_json_atomico(path: str, data, *, indent: int = 2,
                          ensure_ascii: bool = False) -> None:
    """Serializa `data` a `path` sin dejar nunca el archivo a medio escribir."""
    destino = os.path.abspath(path)
    carpeta = os.path.dirname(destino) or "."
    os.makedirs(carpeta, exist_ok=True)
    # El PID y el ident del hilo evitan que dos escritores concurrentes de
    # procesos distintos (gunicorn recargando, un script de siembra) compartan
    # el mismo temporal. Dentro del proceso ya los serializa el lock.
    tmp = os.path.join(
        carpeta, f".{os.path.basename(destino)}.{os.getpid()}.{threading.get_ident()}.tmp")
    with lock_de(destino):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
                # Sin el flush + fsync, `os.replace` puede publicar un inodo
                # cuyo contenido todavia esta en el cache de pagina: ante un
                # corte de energia el destino queda visible y vacio, que es
                # exactamente el escenario que este modulo evita.
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, destino)
        except BaseException:
            # El temporal no puede quedar de recuerdo: se acumularian en el
            # volumen de config y ensuciarian cualquier listado del directorio.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
