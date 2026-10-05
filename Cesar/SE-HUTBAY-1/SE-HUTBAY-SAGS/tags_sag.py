# -*- coding: utf-8 -*-
"""A qué SAG pertenece cada tag (Fase 2).

Cada SAG sigue teniendo su propio ``tags.json`` (es lo que lee su motor). Lo
que se agrega es una vista de TODOS juntos y una forma segura de moverlos:

    dueño = "sag1"        -> el tag está solo en el SAG 1
    dueño = "sag2"        -> solo en el SAG 2
    dueño = "compartido"  -> está en todos los SAG (cada uno lo LEE)

Reglas:
- Cualquier tag se puede compartir (desde 2026-10-01 también SP y OTRO): estar
  en varios SAG significa que todos lo VEN. Lo que no se comparte es la
  ESCRITURA: un SP con rol lo escribe un solo SAG, y un tag de handshake o
  heartbeat lo usa un solo SAG. Eso lo hace cumplir cada SAG (exclusividad.py)
  al asignar roles, al configurar handshake/heartbeat y al importar.
- Un tag no se QUITA de un SAG mientras ese SAG lo use (rol asignado, límite
  de un fuzzy/defuzzy, tag de arranque del tracking, handshake o heartbeat).
  Primero se libera allá; así ninguna regla queda apuntando a la nada.

Este módulo solo LEE archivos y arma el plan. Los cambios se aplican a través
de la API de cada SAG (ver ``router.py``), que es quien tiene el lock de su
``tags.json``.
"""
from __future__ import annotations

import json
import os

from sags import ErrorSag, dir_sag

COMPARTIDO = "compartido"
CATEGORIAS_LECTURA = ("pv", "cruda", "lim")
CAMPOS_CATALOGO = ("data_type", "categoria", "pseudonimo", "instrumento", "unidad_ing", "equipo")
# Archivos donde un tag puede estar referenciado por nombre.
_ARCHIVOS_REF = ("fuzzy.json", "defuzzy.json", "tracking.json", "pendientes.json",
                 "aceleraciones.json", "variables.json", "permisivos.json")


def _leer(path: str, defecto):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def tags_de(sag_id: str, config_raiz: str) -> dict:
    return _leer(os.path.join(dir_sag(sag_id, config_raiz), "tags.json"), {"tags": []})


def union(sags: list[dict], config_raiz: str) -> list[dict]:
    """Todos los tags de todos los SAG, uno por nombre, con su dueño."""
    ids = [s["id"] for s in sags]
    por_nombre: dict[str, dict] = {}
    for sid in ids:
        for t in tags_de(sid, config_raiz).get("tags", []):
            nombre = t.get("name")
            if not nombre:
                continue
            fila = por_nombre.setdefault(nombre, {"name": nombre, "en": {}})
            for c in CAMPOS_CATALOGO:
                if c not in fila and t.get(c) not in (None, ""):
                    fila[c] = t.get(c)
            fila["en"][sid] = {"id": t.get("id"), "rol": t.get("rol") or "",
                               "enabled": t.get("enabled", True)}
        store = tags_de(sid, config_raiz)
        for k, campos in (("handshake", ("enable_fbk_tag", "enable_ext_tag")),
                          ("heartbeat", ("tag_out", "tag_in"))):
            for c in campos:
                n = (store.get(k) or {}).get(c)
                if n:
                    por_nombre.setdefault(n, {"name": n, "en": {}}).setdefault("dcs", {})[sid] = k
    salida = []
    for fila in por_nombre.values():
        presentes = [sid for sid in ids if sid in fila["en"]]
        if not presentes:
            continue                    # citado en un handshake pero sin tag
        fila["dueno"] = presentes[0] if len(presentes) == 1 else COMPARTIDO
        fila["categoria"] = str(fila.get("categoria") or "otro").lower()
        # Quien lo ESCRIBE (SP con rol). Informativo: la regla la aplica cada SAG.
        fila["escribe"] = next((sid for sid in presentes
                                if fila["categoria"] == "sp" and fila["en"][sid]["rol"]), None)
        fila["dcs"] = fila.get("dcs", {})
        fila["exclusivo"] = False       # compatibilidad: ya nada impide compartir
        salida.append(fila)
    salida.sort(key=lambda f: (f["categoria"], f.get("pseudonimo") or f["name"]))
    return salida


def usos(sag_id: str, nombre: str, config_raiz: str) -> list[str]:
    """Por qué el SAG todavía necesita este tag (vacío = se puede quitar)."""
    carpeta = dir_sag(sag_id, config_raiz)
    store = _leer(os.path.join(carpeta, "tags.json"), {})
    motivos = []
    t = next((x for x in store.get("tags", []) if x.get("name") == nombre), None)
    if t and str(t.get("rol") or "").strip():
        motivos.append(f"tiene el rol '{t['rol']}' (es una variable de su contrato; "
                       "quitale el rol o borra el tag en Tags KEPserver de ese SAG)")
    hs = store.get("handshake") or {}
    if nombre in (hs.get("enable_fbk_tag"), hs.get("enable_ext_tag")):
        motivos.append("es un tag del handshake")
    hb = store.get("heartbeat") or {}
    if nombre in (hb.get("tag_out"), hb.get("tag_in")):
        motivos.append("es un tag del heartbeat")
    clave = json.dumps(nombre)          # el nombre tal como aparece en un JSON
    for archivo in _ARCHIVOS_REF:
        try:
            with open(os.path.join(carpeta, archivo), encoding="utf-8") as f:
                if clave in f.read():
                    motivos.append(f"se usa en {archivo}")
        except OSError:
            pass
    return motivos


def plan_asignacion(sags: list[dict], nombre: str, dueno: str, config_raiz: str) -> dict:
    """Qué hay que agregar y quitar en cada SAG para que ``nombre`` quede con
    ``dueno``. Lanza ErrorSag si la asignación no está permitida."""
    ids = [s["id"] for s in sags]
    nombres = {s["id"]: s["nombre"] for s in sags}
    fila = next((f for f in union(sags, config_raiz) if f["name"] == nombre), None)
    if fila is None:
        raise ErrorSag(f"No existe el tag '{nombre}' en ningun SAG.")
    if dueno != COMPARTIDO and dueno not in ids:
        raise ErrorSag(f"Destino invalido: '{dueno}'.")
    destino = ids if dueno == COMPARTIDO else [dueno]
    agregar = [sid for sid in destino if sid not in fila["en"]]
    quitar = [sid for sid in fila["en"] if sid not in destino]
    bloqueos = []
    for sid in quitar:
        motivo = usos(sid, nombre, config_raiz)
        if motivo:
            bloqueos.append(f"{nombres.get(sid, sid)}: " + "; ".join(motivo))
    if bloqueos:
        raise ErrorSag("No se puede quitar el tag de un SAG que lo esta usando. "
                       "Liberalo primero alla. " + " | ".join(bloqueos))
    fuente_sid = next(iter(fila["en"]))
    fuente = next(t for t in tags_de(fuente_sid, config_raiz)["tags"] if t.get("name") == nombre)
    return {"name": nombre, "dueno": dueno, "agregar": agregar,
            "quitar": [(sid, fila["en"][sid]["id"]) for sid in quitar], "fuente": fuente}
