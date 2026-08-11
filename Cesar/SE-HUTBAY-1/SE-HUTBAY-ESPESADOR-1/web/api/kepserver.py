# -*- coding: utf-8 -*-
"""Blueprint bp_kep — Configuracion y prueba de conexion KEPserver (OPC-UA).

Rutas:
  GET  /api/kepserver/config    Lee host + puerto guardados
  PUT  /api/kepserver/config    Guarda host + puerto
  POST /api/kepserver/test      Prueba la conexion (opcionalmente con host/puerto del body)
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
    cfg = _kep.set_config(host, port)
    return jsonify({"ok": True, **_serialize(cfg)})


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
