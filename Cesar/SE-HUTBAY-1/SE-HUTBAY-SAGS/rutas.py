# -*- coding: utf-8 -*-
"""Dónde vive la configuración de ESTE proceso del SE.

Un contenedor SAG corre varios procesos del SE, uno por SAG. Cada uno sabe
cuál es por la variable de entorno ``SE_SAG_ID`` (la pone el router al
lanzarlo) y lee/escribe SOLO su carpeta:

    SE_SAG_ID=sag1  ->  config/sags/sag1/
    SE_SAG_ID=sag2  ->  config/sags/sag2/
    (sin variable)  ->  config/espesador/      (modo de un solo equipo, como el Hopper)

Todo módulo que necesite la carpeta de configuración la toma de aquí. No
volver a escribir ``os.path.join(..., "config", "espesador")`` en otro lado:
ese era el motivo por el que un proceso solo podía ser un equipo.
"""
from __future__ import annotations

import os
import re

RAIZ = os.path.dirname(os.path.abspath(__file__))
CONFIG_RAIZ = os.path.join(RAIZ, "config")
SAGS_DIR = os.path.join(CONFIG_RAIZ, "sags")
CFG_DIR_LEGADO = os.path.join(CONFIG_RAIZ, "espesador")


_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def id_valido(sag_id: str) -> bool:
    """Un id de SAG va dentro de rutas y de nombres de tabla SQL: solo
    minúsculas ASCII, dígitos y guion bajo, empezando por letra."""
    return bool(_ID_RE.fullmatch(sag_id or ""))


def _sag_desde_entorno() -> str:
    sag = os.environ.get("SE_SAG_ID", "").strip().lower()
    if sag and not id_valido(sag):
        raise RuntimeError(f"SE_SAG_ID invalido: {sag!r}")
    return sag


SAG_ID = _sag_desde_entorno()
CFG_DIR = os.path.join(SAGS_DIR, SAG_ID) if SAG_ID else CFG_DIR_LEGADO
