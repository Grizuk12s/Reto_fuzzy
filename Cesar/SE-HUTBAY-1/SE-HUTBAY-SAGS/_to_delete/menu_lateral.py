# -*- coding: utf-8 -*-
"""Menu lateral comun del SE (2026-10-02).

El editor, PostgreSQL, Export / Import y Motor comparten el mismo menu
lateral. Vive una sola vez en web/templates/_sidebar.html y se pega en cada
pagina donde esta tenga la marca:

  <!--SE_SIDEBAR_EDITOR-->  en el editor: las secciones son #hash de la misma pagina
  <!--SE_SIDEBAR-->         en las demas: las secciones apuntan a /espesador#...

Las paginas del SE se sirven como texto (sin Jinja) y Motor la sirve el
router, por eso es un reemplazo simple y no un include de plantillas.
"""
from __future__ import annotations

import os

from rutas import RAIZ

MARCA = "<!--SE_SIDEBAR-->"
MARCA_EDITOR = "<!--SE_SIDEBAR_EDITOR-->"
PARCIAL = os.path.join(RAIZ, "web", "templates", "_sidebar.html")


def menu(en_editor: bool = False) -> str:
    with open(PARCIAL, encoding="utf-8") as f:
        html = f.read()
    return html.replace("__SB_BASE__", "" if en_editor else "/espesador")


def incrustar(html: str, desde_router: bool = False) -> str:
    """Pega el menu en la marca de la pagina (si la tiene).

    desde_router=True para las paginas que sirve el router directamente
    (Motor): ahi no corre /gestion/sag.js, que es quien muestra los enlaces
    data-se-solo-router, asi que se dejan visibles desde ya.
    """
    if MARCA_EDITOR in html:
        bloque = menu(en_editor=True)
        html = html.replace(MARCA_EDITOR, bloque, 1)
    elif MARCA in html:
        bloque = menu(en_editor=False)
        if desde_router:
            bloque = bloque.replace('data-se-solo-router style="display:none"', "data-se-solo-router")
        html = html.replace(MARCA, bloque, 1)
    return html
