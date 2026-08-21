# -*- coding: utf-8 -*-
"""Blueprint bp_views — Vistas HTML del Sistema Experto Espesador.

Rutas:
  GET  /                   Bienvenida (selector de proceso)
  GET  /espesador          Editor de reglas + simulacion
  GET  /espesador/graficos Graficos en tiempo real
  GET  /espesador/diagrama Diagrama de flujo del SE
  GET  /espesador/entrada  Entrada de datos / live tags

IT-7: extraido de app.py.
"""
from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify

from config import SETPOINT_KEYS
from web.state import (
    AlertCollector,
    VARIABLES_DISPONIBLES, ETIQUETAS_DISPONIBLES, BLOQUES_DISPONIBLES,
    etiquetas_disponibles, acciones_disponibles,
    BIENVENIDA_PAGE, HTML_PAGE, DIAGRAM_PAGE, ENTRADA_PAGE, POSTGRES_PAGE, GRAFICOS_PAGE,
    TRAZA_PAGE, HISTORIAL_PAGE,
    _load_estados, _load_waits,
)

bp_views = Blueprint("views", __name__)


@bp_views.route("/")
def bienvenida():
    return Response(BIENVENIDA_PAGE, mimetype="text/html")


@bp_views.route("/espesador")
def index():
    page = HTML_PAGE
    page = page.replace("VARIABLES_JSON",       json.dumps(VARIABLES_DISPONIBLES))
    # Las etiquetas del fuzzy son configurables: se resuelven por request,
    # no al importar, o una fila recien creada no aparece hasta reiniciar.
    page = page.replace("LABELS_JSON",          json.dumps(etiquetas_disponibles()))
    # Las acciones salen de defuzzy.json, no de un catalogo en codigo: se
    # resuelven por request para que una columna recien creada ya este en el
    # selector de la regla (y una borrada, ya no).
    page = page.replace("ACTIONS_JSON",         json.dumps(acciones_disponibles()))
    page = page.replace("BLOCKS_JSON",          json.dumps(BLOQUES_DISPONIBLES))
    page = page.replace("SETPOINTS_JSON",       json.dumps(list(SETPOINT_KEYS)))
    page = page.replace("ESTADOS_JSON",         json.dumps(_load_estados()))
    page = page.replace("WAITS_JSON",           json.dumps(_load_waits()))
    page = page.replace("ALERT_CATEGORIES_JSON", json.dumps(AlertCollector.CATEGORIES))
    return Response(page, mimetype="text/html")


@bp_views.route("/espesador/graficos")
def graficos():
    return Response(GRAFICOS_PAGE, mimetype="text/html")


@bp_views.route("/espesador/traza")
def traza():
    """Traza del pipeline: que pasa desde que llegan los datos hasta el SP."""
    return Response(TRAZA_PAGE, mimetype="text/html")


@bp_views.route("/espesador/historial")
def historial():
    """Historial grabado de UNA regla (?regla=<id>). Una pagina por regla."""
    return Response(HISTORIAL_PAGE, mimetype="text/html")


@bp_views.route("/espesador/diagrama")
def diagrama():
    return Response(DIAGRAM_PAGE, mimetype="text/html")


@bp_views.route("/espesador/entrada")
def entrada():
    page = ENTRADA_PAGE.replace("ALERT_CATEGORIES_JSON", json.dumps(AlertCollector.CATEGORIES))
    return Response(page, mimetype="text/html")


@bp_views.route("/espesador/postgres")
def postgres():
    return Response(POSTGRES_PAGE, mimetype="text/html")


@bp_views.route("/health")
def health():
    """Liveness probe para Docker / orquestadores."""
    return jsonify(status="ok"), 200
