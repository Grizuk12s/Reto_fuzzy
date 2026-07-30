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

from flask import Blueprint, Response

from config import SETPOINT_KEYS
from web.state import (
    AlertCollector,
    VARIABLES_DISPONIBLES, ETIQUETAS_DISPONIBLES, ACCIONES_DISPONIBLES, BLOQUES_DISPONIBLES,
    BIENVENIDA_PAGE, HTML_PAGE, DIAGRAM_PAGE, ENTRADA_PAGE, POSTGRES_PAGE, CHART_VARS,
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
    page = page.replace("LABELS_JSON",          json.dumps(ETIQUETAS_DISPONIBLES))
    page = page.replace("ACTIONS_JSON",         json.dumps(ACCIONES_DISPONIBLES))
    page = page.replace("BLOCKS_JSON",          json.dumps(BLOQUES_DISPONIBLES))
    page = page.replace("SETPOINTS_JSON",       json.dumps(list(SETPOINT_KEYS)))
    page = page.replace("ESTADOS_JSON",         json.dumps(_load_estados()))
    page = page.replace("WAITS_JSON",           json.dumps(_load_waits()))
    page = page.replace("ALERT_CATEGORIES_JSON", json.dumps(AlertCollector.CATEGORIES))
    return Response(page, mimetype="text/html")


@bp_views.route("/espesador/graficos")
def graficos():
    from flask import current_app
    page = current_app.charts_page
    page = page.replace("CHART_VARS_JSON", json.dumps(CHART_VARS))
    page = page.replace("SP_KEYS_JSON",    json.dumps(list(SETPOINT_KEYS)))
    return Response(page, mimetype="text/html")


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
