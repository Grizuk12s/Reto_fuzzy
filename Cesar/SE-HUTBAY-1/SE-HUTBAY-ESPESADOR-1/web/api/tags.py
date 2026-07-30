# -*- coding: utf-8 -*-
"""Blueprint bp_tags — CRUD de Tags KEPserver + Generador.

Rutas:
  GET/POST             /api/tags
  PUT/DELETE           /api/tags/<tag_id>
  POST                 /api/tags/<tag_id>/write
  POST                 /api/tags/refresh
  GET/PUT              /api/tags/simulation
  GET/PUT              /api/tags/generator
  POST                 /api/tags/generator/start
  POST                 /api/tags/generator/stop

IT-7: extraído de app.py.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from web.state import (
    _load_tags, _save_tags,
    _read_kepserver_tags_batch, _try_write_kepserver_tag, _enrich_tags_with_kepserver,
    _record_tag_values, _get_tag_history,
    _tag_generator,
)

bp_tags = Blueprint("tags", __name__)


@bp_tags.route("/api/tags", methods=["GET"])
def api_get_tags():
    data = _load_tags()
    return jsonify({
        "tags": _enrich_tags_with_kepserver(data["tags"]),
        "simulation_mode": data.get("simulation_mode", True),
    })


@bp_tags.route("/api/tags", methods=["POST"])
def api_create_tag():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "El nombre del tag es obligatorio."}), 400
    data_type = body.get("data_type", "Float")
    if data_type not in ("Float", "Int", "Boolean", "String"):
        return jsonify({"error": "Tipo de dato no soportado."}), 400
    store = _load_tags()
    for t in store["tags"]:
        if t["name"] == name:
            return jsonify({"error": f"Ya existe un tag con nombre '{name}'."}), 409
    new_tag = {"id": store["next_id"], "name": name, "data_type": data_type, "enabled": True}
    store["tags"].append(new_tag)
    store["next_id"] += 1
    _save_tags(store)
    live = _read_kepserver_tags_batch([name])
    return jsonify({"ok": True, "tag": {**new_tag, **live.get(name, {})}}), 201


@bp_tags.route("/api/tags/<int:tag_id>", methods=["PUT"])
def api_update_tag(tag_id: int):
    body = request.get_json(force=True)
    store = _load_tags()
    tag = next((t for t in store["tags"] if t["id"] == tag_id), None)
    if not tag:
        return jsonify({"error": "Tag no encontrado."}), 404
    if "name" in body:
        new_name = body["name"].strip()
        if new_name and new_name != tag["name"]:
            if any(t["name"] == new_name for t in store["tags"] if t["id"] != tag_id):
                return jsonify({"error": f"Ya existe un tag '{new_name}'."}), 409
            tag["name"] = new_name
    if "data_type" in body:
        tag["data_type"] = body["data_type"]
    if "enabled" in body:
        tag["enabled"] = bool(body["enabled"])
    for field in ("pseudonimo", "instrumento", "unidad_ing", "equipo"):
        if field in body:
            tag[field] = (body[field] or "").strip()
    _save_tags(store)
    live = _read_kepserver_tags_batch([tag["name"]]) if tag["enabled"] else {}
    info = live.get(tag["name"], {"connected": False, "exists": False, "value": None, "quality": "Suspended"})
    return jsonify({"ok": True, "tag": {**tag, **info}})


@bp_tags.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id: int):
    store = _load_tags()
    before = len(store["tags"])
    store["tags"] = [t for t in store["tags"] if t["id"] != tag_id]
    if len(store["tags"]) == before:
        return jsonify({"error": "Tag no encontrado."}), 404
    _save_tags(store)
    return jsonify({"ok": True})


@bp_tags.route("/api/tags/<int:tag_id>/write", methods=["POST"])
def api_write_tag(tag_id: int):
    body = request.get_json(force=True)
    store = _load_tags()
    tag = next((t for t in store["tags"] if t["id"] == tag_id), None)
    if not tag:
        return jsonify({"error": "Tag no encontrado."}), 404
    if not tag["enabled"]:
        return jsonify({"error": "El tag esta suspendido."}), 400
    value = body.get("value")
    result = _try_write_kepserver_tag(tag["name"], value, tag["data_type"])
    return jsonify(result)


@bp_tags.route("/api/tags/refresh", methods=["POST"])
def api_refresh_tags():
    store = _load_tags()
    return jsonify({"tags": _enrich_tags_with_kepserver(store["tags"])})


_CATALOG_TYPES = ("instrumentos", "unidades_ing", "equipos")
_CATALOG_FIELD = {"instrumentos": "instrumento", "unidades_ing": "unidad_ing", "equipos": "equipo"}


@bp_tags.route("/api/tags/catalogs", methods=["GET"])
def api_get_catalogs():
    store = _load_tags()
    return jsonify(store.get("catalogs", {t: [] for t in _CATALOG_TYPES}))


@bp_tags.route("/api/tags/catalogs/<catalog_type>", methods=["POST"])
def api_manage_catalog(catalog_type: str):
    if catalog_type not in _CATALOG_TYPES:
        return jsonify({"error": "Tipo de catalogo invalido."}), 400
    body = request.get_json(force=True)
    action = body.get("action", "add")
    value = (body.get("value") or "").strip()
    if not value:
        return jsonify({"error": "El valor es obligatorio."}), 400

    store = _load_tags()
    catalogs = store.setdefault("catalogs", {t: [] for t in _CATALOG_TYPES})
    items = catalogs.setdefault(catalog_type, [])

    if action == "add":
        if value in items:
            return jsonify({"error": f"'{value}' ya existe."}), 409
        items.append(value)
        _save_tags(store)
        return jsonify({"ok": True, "items": items}), 201

    if action == "remove":
        if value not in items:
            return jsonify({"error": "Item no encontrado."}), 404
        items.remove(value)
        field = _CATALOG_FIELD[catalog_type]
        for tag in store.get("tags", []):
            if tag.get(field) == value:
                tag[field] = ""
        _save_tags(store)
        return jsonify({"ok": True, "items": items})

    return jsonify({"error": "Accion invalida (add|remove)."}), 400


@bp_tags.route("/api/tags/simulation", methods=["GET"])
def api_get_simulation_mode():
    store = _load_tags()
    return jsonify({"simulation_mode": store.get("simulation_mode", True)})


@bp_tags.route("/api/tags/simulation", methods=["PUT"])
def api_set_simulation_mode():
    body = request.get_json(force=True)
    mode = bool(body.get("simulation_mode", True))
    store = _load_tags()
    store["simulation_mode"] = mode
    _save_tags(store)
    return jsonify({"ok": True, "simulation_mode": mode})


@bp_tags.route("/api/tags/generator", methods=["GET"])
def api_get_generator():
    return jsonify(_tag_generator.status())


@bp_tags.route("/api/tags/generator", methods=["PUT"])
def api_put_generator():
    body = request.get_json(force=True)
    _tag_generator.update_config(
        intervalo_s=body.get("intervalo_s"),
        n_ciclo=body.get("n_ciclo"),
        ranges=body.get("ranges"),
    )
    return jsonify({"ok": True, **_tag_generator.status()})


@bp_tags.route("/api/tags/generator/start", methods=["POST"])
def api_start_generator():
    _tag_generator.start()
    return jsonify({"ok": True, "running": True})


@bp_tags.route("/api/tags/generator/stop", methods=["POST"])
def api_stop_generator():
    _tag_generator.stop()
    return jsonify({"ok": True, "running": False})
