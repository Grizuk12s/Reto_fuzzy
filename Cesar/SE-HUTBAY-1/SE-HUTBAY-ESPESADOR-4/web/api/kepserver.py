# -*- coding: utf-8 -*-
"""Blueprint bp_kep — Configuracion y prueba de conexion KEPserver (OPC-UA).

Rutas:
  GET  /api/kepserver/config    Lee host + puerto + timeout + politica de calidad
  PUT  /api/kepserver/config    Guarda host + puerto (+ timeout_s,
                                aceptar_uncertain, estancado_alerta_s,
                                retencion_s opcionales)
  POST /api/kepserver/test      Prueba la conexion (opcionalmente con host/puerto del body)
  GET  /api/kepserver/sesiones  Estado del pool de sesiones OPC-UA (B1.2)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import connectors.kepserver as _kep

bp_kep = Blueprint("kepserver", __name__)


def _serialize(cfg: dict) -> dict:
    return {
        "host":         cfg.get("host", "127.0.0.1"),
        "port":         int(cfg.get("port", 49320)),
        "url":          _kep.build_url(cfg.get("host", "127.0.0.1"), cfg.get("port", 49320)),
        "last_status":  cfg.get("last_status", "unconfigured"),
        "last_message": cfg.get("last_message", ""),
        "timeout_s":    _kep.get_timeout_s(),
        # Politica de calidad de dato (B1.4)
        "aceptar_uncertain":  _kep.get_aceptar_uncertain(),
        "estancado_alerta_s": _kep.get_estancado_alerta_s(),
        "retencion_s":        _kep.get_retencion_s(),
    }


@bp_kep.route("/api/kepserver/config", methods=["GET"])
def api_get_config():
    return jsonify(_serialize(_kep.get_config()))


@bp_kep.route("/api/kepserver/config", methods=["PUT"])
def api_put_config():
    body = request.get_json(force=True) or {}
    host = str(body.get("host", "")).strip()
    if not host:
        return jsonify({"error": "El host es obligatorio."}), 400
    try:
        port = int(body.get("port", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Puerto invalido."}), 400
    if not (1 <= port <= 65535):
        return jsonify({"error": "Puerto fuera de rango (1-65535)."}), 400

    timeout_s = None
    if body.get("timeout_s") is not None:
        try:
            timeout_s = float(body["timeout_s"])
        except (TypeError, ValueError):
            return jsonify({"error": "Timeout invalido."}), 400

    estancado_alerta_s = None
    if body.get("estancado_alerta_s") is not None:
        try:
            estancado_alerta_s = float(body["estancado_alerta_s"])
        except (TypeError, ValueError):
            return jsonify({"error": "Umbral de estancamiento invalido."}), 400
        if estancado_alerta_s < 0:
            return jsonify({"error": "El umbral de estancamiento no puede ser negativo."}), 400

    retencion_s = None
    if body.get("retencion_s") is not None:
        try:
            retencion_s = float(body["retencion_s"])
        except (TypeError, ValueError):
            return jsonify({"error": "Retencion invalida."}), 400
        if retencion_s < 0:
            return jsonify({"error": "La retencion no puede ser negativa."}), 400
        # Un tope duro: la retencion decide sobre un dato viejo, y a partir de
        # cierto punto eso deja de ser "cruzar un parpadeo" y pasa a ser
        # controlar a ciegas. El filtro Exp-Q tiene ventanas de decenas de
        # segundos; retener minutos daria un experto convencido de un proceso
        # que ya no existe.
        if retencion_s > 300:
            return jsonify({"error": "La retencion no puede pasar de 300 s."}), 400

    aceptar_uncertain = None
    if body.get("aceptar_uncertain") is not None:
        aceptar_uncertain = bool(body["aceptar_uncertain"])

    cfg = _kep.set_config(host, port, timeout_s=timeout_s,
                          aceptar_uncertain=aceptar_uncertain,
                          estancado_alerta_s=estancado_alerta_s,
                          retencion_s=retencion_s)
    return jsonify({"ok": True, **_serialize(cfg)})


@bp_kep.route("/api/kepserver/sesiones", methods=["GET"])
def api_sesiones():
    """Estado del pool de sesiones OPC-UA.

    Responde la pregunta operativa que dejo B1.2: la sesion se esta REUSANDO o
    se esta reabriendo. Si `abiertas` sube con el tiempo o se ve una sesion con
    `viva_s` que se reinicia sin parar, el motor esta reconectando en cada tick
    y hay que mirar el log del KEPserver.
    """
    return jsonify(_kep.pool_status())


@bp_kep.route("/api/kepserver/test", methods=["POST"])
def api_test_connection():
    body = request.get_json(silent=True) or {}
    host = str(body.get("host", "")).strip()
    try:
        port = int(body.get("port", 0)) if body.get("port") is not None else 0
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Puerto invalido."}), 400

    saved = _kep.get_config()
    target_host = host or saved.get("host", "127.0.0.1")
    target_port = port if (1 <= port <= 65535) else int(saved.get("port", 49320))
    target_url = _kep.build_url(target_host, target_port)

    ok, err = _kep.check_connection(url=target_url)

    if ok:
        cfg = _kep.set_config(
            target_host, target_port,
            last_status="connected",
            last_message=f"Conexion verificada en {target_url}",
        )
        return jsonify({"ok": True, "detail": f"Conexion exitosa a {target_url}", **_serialize(cfg)})

    # Persistir el ultimo error sin cambiar host/puerto guardados si el usuario
    # solo estaba probando un valor distinto al salvado.
    cfg = _kep.get_config()
    cfg["last_status"] = "error"
    cfg["last_message"] = err
    if host and (1 <= port <= 65535):
        _kep.set_config(target_host, target_port, last_status="error", last_message=err)
    else:
        try:
            _kep.set_config(cfg["host"], cfg["port"], last_status="error", last_message=err)
        except Exception:
            pass
    return jsonify({"ok": False, "error": err, "url": target_url, **_serialize(cfg)})
