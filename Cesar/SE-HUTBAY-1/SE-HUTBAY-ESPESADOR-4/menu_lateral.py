# -*- coding: utf-8 -*-
"""Menu lateral comun del SE (2026-10-02, igual que en el proyecto SAG).

El editor, PostgreSQL y Export / Import comparten el mismo menu lateral. Vive
una sola vez en web/templates/_sidebar.html y se pega en cada pagina donde
esta tenga la marca:

  <!--SE_SIDEBAR_EDITOR-->  en el editor: las secciones son #hash de la misma pagina
  <!--SE_SIDEBAR-->         en las demas: las secciones apuntan a /espesador#...

Las paginas se sirven como texto (sin Jinja), por eso es un reemplazo simple.
"""
from __future__ import annotations

import os

RAIZ = os.path.dirname(os.path.abspath(__file__))
MARCA = "<!--SE_SIDEBAR-->"
MARCA_EDITOR = "<!--SE_SIDEBAR_EDITOR-->"
PARCIAL = os.path.join(RAIZ, "web", "templates", "_sidebar.html")


def menu(en_editor: bool = False) -> str:
    with open(PARCIAL, encoding="utf-8") as f:
        html = f.read()
    return html.replace("__SB_BASE__", "" if en_editor else "/espesador")


def incrustar(html: str) -> str:
    """Pega el menu en la marca de la pagina (si la tiene)."""
    if MARCA_EDITOR in html:
        html = html.replace(MARCA_EDITOR, menu(en_editor=True), 1)
    elif MARCA in html:
        html = html.replace(MARCA, menu(en_editor=False), 1)
    return html
