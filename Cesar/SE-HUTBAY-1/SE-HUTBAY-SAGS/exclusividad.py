# -*- coding: utf-8 -*-
"""Un tag puede estar en varios SAG; ESCRIBIRLO, solo uno.

Desde 2026-10-01 cualquier tag se puede compartir (estar en el tags.json de
varios SAG), incluidos SP y OTRO: así un SAG puede VER el setpoint de otro, y
"importar en todos" no tiene que partir el paquete. Lo que no puede repetirse
es lo que escribe al DCS:

- un tag **SP con rol** (es la salida de ese SAG): un solo SAG por tag;
- un tag usado como **handshake** (Enable_Ext / Enable_FBK) o **heartbeat**:
  un solo SAG por tag (si no, detener un SAG le quitaría el control al otro).

Cada proceso del SE consulta aquí los tags.json de los OTROS SAG (solo lee).
Sin ``SE_SAG_ID`` (un solo equipo, como el Hopper) todo devuelve vacío y el
comportamiento es exactamente el de antes.
"""
from __future__ import annotations

import json
import os

from rutas import SAG_ID, SAGS_DIR


def _otros_tags(propio: str | None = None, sags_dir: str | None = None) -> dict[str, dict]:
    """{sag_id: contenido de su tags.json} de todos los SAG menos el propio."""
    propio = SAG_ID if propio is None else propio
    sags_dir = sags_dir or SAGS_DIR
    if not propio or not os.path.isdir(sags_dir):
        return {}
    out = {}
    for sid in sorted(os.listdir(sags_dir)):
        if sid == propio:
            continue
        ruta = os.path.join(sags_dir, sid, "tags.json")
        try:
            with open(ruta, encoding="utf-8") as f:
                out[sid] = json.load(f)
        except (OSError, ValueError):
            continue
    return out


def nombre_sag(sid: str) -> str:
    """Nombre visible del SAG (del registro), o el id si no se encuentra."""
    try:
        with open(os.path.join(os.path.dirname(SAGS_DIR), "sags.json"), encoding="utf-8") as f:
            for s in json.load(f).get("sags", []):
                if s.get("id") == sid:
                    return str(s.get("nombre") or sid)
    except (OSError, ValueError):
        pass
    return sid


def escritores_sp(propio: str | None = None, sags_dir: str | None = None) -> dict[str, str]:
    """{tag: sag_id} de los tags SP que OTRO SAG escribe (tienen rol allá)."""
    out = {}
    for sid, store in _otros_tags(propio, sags_dir).items():
        for t in store.get("tags", []):
            if (str(t.get("categoria", "")).lower() == "sp" and str(t.get("rol") or "").strip()
                    and t.get("name")):
                out.setdefault(t["name"], sid)
    return out


def usos_dcs_otros(propio: str | None = None, sags_dir: str | None = None) -> dict[str, tuple[str, str]]:
    """{tag: (sag_id, 'handshake'|'heartbeat')} de los tags que OTRO SAG usa
    para hablar con el DCS."""
    out = {}
    for sid, store in _otros_tags(propio, sags_dir).items():
        hs = store.get("handshake") or {}
        for k in ("enable_fbk_tag", "enable_ext_tag"):
            if hs.get(k):
                out.setdefault(hs[k], (sid, "handshake"))
        hb = store.get("heartbeat") or {}
        for k in ("tag_out", "tag_in"):
            if hb.get(k):
                out.setdefault(hb[k], (sid, "heartbeat"))
    return out


def motivo_sp(nombre: str) -> str | None:
    """Mensaje si este SAG NO puede escribir el SP ``nombre``; None si puede."""
    sid = escritores_sp().get(nombre)
    if not sid:
        return None
    return (f"El setpoint '{nombre}' ya lo escribe {nombre_sag(sid)}. Un SP tiene un solo "
            f"SAG que lo escribe: quitale el rol alla antes de asignarlo aqui.")


def motivo_dcs(nombre: str) -> str | None:
    """Mensaje si ``nombre`` ya es handshake/heartbeat de otro SAG."""
    uso = usos_dcs_otros().get(nombre)
    if not uso:
        return None
    sid, que = uso
    return (f"El tag '{nombre}' ya es el {que} de {nombre_sag(sid)}. Cada SAG necesita sus "
            f"propios tags de handshake y heartbeat: si compartieran uno, detener un SAG le "
            f"quitaria el control al otro.")
