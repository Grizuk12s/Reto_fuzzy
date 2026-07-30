# -*- coding: utf-8 -*-
"""Blueprint bp_config — CRUD de toda la configuración del SE.

Rutas:
  GET/POST/PUT/DELETE  /api/meta
  GET/POST/PUT/DELETE  /api/reglas[/<id>]
  GET/PUT/POST         /api/filtros[/reset]
  GET/PUT/POST         /api/defuzzy[/reset]
  GET/PUT/POST         /api/fuzzy[/reset]
  GET/PUT/POST         /api/variables[/reset]
  GET/PUT/POST         /api/permisivos[/reset]
  GET/POST/PUT/DELETE  /api/estados[/<nombre>][/reset]
  GET/POST/PUT/DELETE  /api/waits[/<wait_id>][/reset]

IT-7: extraído de app.py.
"""
from __future__ import annotations

import copy as _copy
import calendar
import json
import os
import re as _re
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from config import (
    SETPOINT_KEYS,
    VARIABLES_PROCESO,
    VARIABLES_CRUDAS_REQUERIDAS,
)
from core.filters.exp_q import CONFIG_FILTRO_ESPESADOR_DEFAULT
from fuzzys_models_espesador import FUZZY_MODELOS
from calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS
from permisivos import PERMISIVOS

from web.state import (
    _alerts,
    REGLAS_JSON, FILTROS_JSON, DEFUZZY_JSON, FUZZY_JSON, VARIABLES_JSON,
    PERMISIVOS_JSON, LICENCIA_JSON, ESTADOS_JSON_PATH, WAITS_JSON_PATH,
    TRACKING_JSON,
    VARIABLES_DISPONIBLES, ETIQUETAS_DISPONIBLES, ACCIONES_DISPONIBLES,
    BLOQUES_DISPONIBLES, PERMISIVOS_DISPONIBLES, WAITS_CATALOGO_DISPONIBLES,
    VARIABLES_VALIDAS, ETIQUETAS_VALIDAS, ACCIONES_VALIDAS, BLOQUES_VALIDOS,
    ESTADOS_SERIALIZADOS, DEFUZZY_POR_FAMILIA,
    _load_estados, _save_estados, _defaults_estados,
    _load_waits, _save_waits, _defaults_waits,
    _definiciones_lista_a_dict,
)

bp_config = Blueprint("config", __name__)


# ============================================================
# API — Meta
# ============================================================

@bp_config.route("/api/meta", methods=["GET"])
def api_meta():
    from web.state import _load_waits as _lw
    return jsonify({
        "variables":   VARIABLES_DISPONIBLES,
        "etiquetas":   ETIQUETAS_DISPONIBLES,
        "acciones":    ACCIONES_DISPONIBLES,
        "bloques":     BLOQUES_DISPONIBLES,
        "permisivos":  PERMISIVOS_DISPONIBLES,
        "setpoints":   list(SETPOINT_KEYS),
        "estados":     _load_estados(),
        "waits":       _lw(),
    })


# ============================================================
# Helpers — Licenciamiento (prototipo)
# ============================================================

LICENCIA_DURACIONES_VALIDAS = (3, 6, 9, 12)
LICENCIA_CODIGO_PROTOTIPO = "HUTBAY-LIC-PROTOTIPO-2026"


def _defaults_licencia() -> dict:
    return {
        "active": False,
        "months": None,
        "type": "Sin licencia activa",
        "activated_at": None,
        "expires_at": None,
    }


def _save_licencia(cfg: dict) -> None:
    with open(LICENCIA_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _load_licencia() -> dict:
    if not os.path.exists(LICENCIA_JSON):
        cfg = _defaults_licencia()
        _save_licencia(cfg)
        return cfg
    try:
        with open(LICENCIA_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_licencia()
    if not isinstance(data, dict):
        return _defaults_licencia()
    return {**_defaults_licencia(), **data}


def _add_months(base_date: date, months: int) -> date:
    month_idx = base_date.month - 1 + months
    year = base_date.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _fmt_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _parse_iso_date(value) -> date | None:
    if not value:
        return None
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _tipo_licencia(months: int | None) -> str:
    if months in LICENCIA_DURACIONES_VALIDAS:
        return f"Licencia prototipo {months} meses"
    return "Sin licencia activa"


def _serializar_licencia(cfg: dict) -> dict:
    today = date.today()
    months = cfg.get("months")
    activated_at = _parse_iso_date(cfg.get("activated_at"))
    expires_at = _parse_iso_date(cfg.get("expires_at"))
    active = bool(cfg.get("active")) and months in LICENCIA_DURACIONES_VALIDAS and activated_at is not None and expires_at is not None
    expired = bool(active and expires_at < today)
    remaining_days = None
    remaining_text = "Sin licencia activa"

    if active:
        delta_days = (expires_at - today).days
        remaining_days = max(delta_days, 0)
        if expired:
            remaining_text = "Expirada"
        elif delta_days == 0:
            remaining_text = "Vence hoy"
        else:
            remaining_text = f"{remaining_days} dia(s)"

    return {
        "active": active and not expired,
        "expired": expired,
        "months": months if months in LICENCIA_DURACIONES_VALIDAS else None,
        "type": _tipo_licencia(months if active else None),
        "activated_at": _fmt_date(activated_at),
        "expires_at": _fmt_date(expires_at),
        "remaining_days": remaining_days,
        "remaining_text": remaining_text,
        "status_label": "Activa" if active and not expired else ("Expirada" if expired else "Inactiva"),
        "requires_code": True,
    }


@bp_config.route("/api/licencia", methods=["GET"])
def api_get_licencia():
    return jsonify(_serializar_licencia(_load_licencia()))


@bp_config.route("/api/licencia/activar", methods=["POST"])
def api_activate_licencia():
    data = request.get_json(force=True) or {}
    license_code = str(data.get("license_code", "")).strip()
    try:
        months = int(data.get("months"))
    except (TypeError, ValueError):
        return jsonify({"error": "Campo 'months' requerido. Valores validos: 3, 6, 9, 12."}), 400
    if months not in LICENCIA_DURACIONES_VALIDAS:
        return jsonify({"error": "Duracion invalida. Valores validos: 3, 6, 9, 12 meses."}), 400
    if not license_code:
        return jsonify({"error": "Activacion fallida: ingresa un codigo de licencia para este prototipo."}), 400
    if license_code != LICENCIA_CODIGO_PROTOTIPO:
        return jsonify({"error": "Activacion fallida: codigo de licencia invalido para este prototipo."}), 400

    activated_at = date.today()
    expires_at = _add_months(activated_at, months)
    cfg = {
        "active": True,
        "months": months,
        "type": _tipo_licencia(months),
        "activated_at": _fmt_date(activated_at),
        "expires_at": _fmt_date(expires_at),
    }
    _save_licencia(cfg)
    return jsonify({"ok": True, "license": _serializar_licencia(cfg)})


# ============================================================
# Helpers — Reglas
# ============================================================

def _load_reglas() -> list[dict]:
    if not os.path.exists(REGLAS_JSON):
        return []
    try:
        with open(REGLAS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_reglas(reglas: list[dict]) -> None:
    with open(REGLAS_JSON, "w", encoding="utf-8") as f:
        json.dump(reglas, f, indent=2, ensure_ascii=False)


def _find_regla(reglas: list[dict], regla_id: str):
    for i, r in enumerate(reglas):
        if str(r.get("id")) == str(regla_id):
            return i, r
    return None, None


def _normalizar_regla_payload(data: dict, require_id: bool = True) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto JSON."
    regla = dict(data)
    if require_id:
        regla_id = str(regla.get("id", "")).strip()
        if not regla_id:
            return None, "Campo 'id' requerido."
        regla["id"] = regla_id

    bloque = str(regla.get("bloque", "")).strip().lower()
    if not bloque:
        return None, "Campo 'bloque' requerido."
    if bloque not in BLOQUES_VALIDOS:
        return None, f"Bloque invalido: '{bloque}'. Validos: {sorted(BLOQUES_VALIDOS)}."
    regla["bloque"] = bloque

    fuerza = regla.get("fuerza")
    if fuerza is not None:
        def _val_fuerza_leaf(leaf, ctx="Fuerza"):
            if not isinstance(leaf, (list, tuple)) or len(leaf) != 2:
                return None, f"{ctx}: formato invalido."
            v = str(leaf[0]).strip()
            l = str(leaf[1]).strip().upper()
            if v not in VARIABLES_VALIDAS:
                return None, f"{ctx}: variable invalida '{v}'."
            if l not in ETIQUETAS_VALIDAS:
                return None, f"{ctx}: etiqueta invalida '{l}'."
            return [v, l], None

        if isinstance(fuerza, dict) and "OR" in fuerza:
            items = fuerza.get("OR", [])
            norm_items = []
            for i, leaf in enumerate(items, 1):
                n, err = _val_fuerza_leaf(leaf, f"Fuerza OR #{i}")
                if err:
                    return None, err
                norm_items.append(n)
            regla["fuerza"] = {"OR": norm_items} if len(norm_items) > 1 else (norm_items[0] if norm_items else None)
        elif isinstance(fuerza, (list, tuple)) and len(fuerza) == 2 and isinstance(fuerza[0], str):
            n, err = _val_fuerza_leaf(fuerza)
            if err:
                return None, err
            regla["fuerza"] = n
        else:
            regla["fuerza"] = None

    condiciones = regla.get("if")
    if not isinstance(condiciones, list) or not condiciones:
        return None, "Campo 'if' debe ser una lista no vacia."

    items = condiciones
    if (len(condiciones) == 1
            and isinstance(condiciones[0], dict)
            and "AND" in condiciones[0]
            and isinstance(condiciones[0]["AND"], list)
            and not any(isinstance(x, dict) and "AND" in x for x in condiciones[0]["AND"])):
        items = condiciones[0]["AND"]

    def _norm_leaf(leaf, ctx: str):
        if not isinstance(leaf, (list, tuple)) or len(leaf) != 2:
            return None, f"{ctx}: debe tener formato [variable, etiqueta]."
        variable = str(leaf[0]).strip()
        etiqueta = str(leaf[1]).strip().upper()
        if variable not in VARIABLES_VALIDAS:
            return None, f"{ctx}: variable invalida '{variable}'."
        if etiqueta not in ETIQUETAS_VALIDAS:
            return None, f"{ctx}: etiqueta invalida '{etiqueta}'."
        return [variable, etiqueta], None

    def _norm_and_group(group, ctx: str):
        sub_items = group.get("AND", [])
        if not isinstance(sub_items, list):
            return None, f"{ctx}: AND debe ser una lista."
        norm = []
        for si, leaf in enumerate(sub_items, start=1):
            leaf_norm, err = _norm_leaf(leaf, f"{ctx} AND hoja #{si}")
            if err is not None:
                return None, err
            norm.append(leaf_norm)
        return {"AND": norm}, None

    condiciones_norm = []
    for idx, item in enumerate(items, start=1):
        if isinstance(item, dict) and "AND" in item:
            and_norm, err = _norm_and_group(item, f"Condicion #{idx}")
            if err is not None:
                return None, err
            condiciones_norm.append(and_norm)
            continue
        if isinstance(item, dict) and "OR" in item:
            sub_items = item.get("OR")
            if not isinstance(sub_items, list) or len(sub_items) < 2:
                return None, f"Condicion #{idx} (OR): debe contener al menos 2 hojas."
            sub_norm = []
            for sub_idx, leaf in enumerate(sub_items, start=1):
                leaf_norm, err = _norm_leaf(leaf, f"Condicion #{idx} OR hoja #{sub_idx}")
                if err is not None:
                    return None, err
                sub_norm.append(leaf_norm)
            condiciones_norm.append({"OR": sub_norm})
            continue
        leaf_norm, err = _norm_leaf(item, f"Condicion #{idx}")
        if err is not None:
            return None, err
        condiciones_norm.append(leaf_norm)

    regla["if"] = condiciones_norm

    acciones = regla.get("then")
    if not isinstance(acciones, list) or not acciones:
        return None, "Campo 'then' debe ser una lista no vacia."
    acciones_norm = []
    for idx, accion in enumerate(acciones, start=1):
        if isinstance(accion, dict) and "accion" in accion:
            accion_nombre = str(accion["accion"]).strip().upper()
            if accion_nombre not in ACCIONES_VALIDAS:
                return None, f"Accion invalida en then #{idx}: '{accion_nombre}'."
            accion["accion"] = accion_nombre
            acciones_norm.append(accion)
        else:
            accion_norm = str(accion).strip().upper()
            if accion_norm not in ACCIONES_VALIDAS:
                return None, f"Accion invalida en then #{idx}: '{accion_norm}'."
            acciones_norm.append(accion_norm)
    regla["then"] = acciones_norm

    for key in ("weight", "priority"):
        if key in regla and regla[key] is not None and regla[key] != "":
            try:
                regla[key] = float(regla[key])
            except (TypeError, ValueError):
                return None, f"Campo '{key}' debe ser numerico."
    regla.setdefault("weight", 1.0)
    regla.setdefault("priority", 50.0)
    return regla, None


# ============================================================
# API — Reglas
# ============================================================

@bp_config.route("/api/reglas", methods=["GET"])
def api_get_reglas():
    return jsonify(_load_reglas())


@bp_config.route("/api/reglas/<regla_id>", methods=["GET"])
def api_get_regla(regla_id: str):
    reglas = _load_reglas()
    _, r = _find_regla(reglas, regla_id)
    if r is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada."}), 404
    return jsonify(r)


@bp_config.route("/api/reglas", methods=["POST"])
def api_create_regla():
    data = request.get_json(force=True)
    regla, error = _normalizar_regla_payload(data, require_id=True)
    if error:
        return jsonify({"error": error}), 400
    reglas = _load_reglas()
    if any(str(r.get("id")) == str(regla["id"]) for r in reglas):
        return jsonify({"error": f"Ya existe una regla con id '{regla['id']}'."}), 409
    reglas.append(regla)
    _save_reglas(reglas)
    return jsonify({"ok": True, "regla": regla}), 201


@bp_config.route("/api/reglas/<regla_id>", methods=["PUT"])
def api_update_regla(regla_id: str):
    data = request.get_json(force=True)
    data["id"] = regla_id
    regla, error = _normalizar_regla_payload(data, require_id=True)
    if error:
        return jsonify({"error": error}), 400
    reglas = _load_reglas()
    idx, _ = _find_regla(reglas, regla_id)
    if idx is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada."}), 404
    reglas[idx] = regla
    _save_reglas(reglas)
    return jsonify({"ok": True, "regla": regla})


@bp_config.route("/api/reglas/<regla_id>", methods=["DELETE"])
def api_delete_regla(regla_id: str):
    reglas = _load_reglas()
    idx, _ = _find_regla(reglas, regla_id)
    if idx is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada."}), 404
    removed = reglas.pop(idx)
    _save_reglas(reglas)
    return jsonify({"ok": True, "eliminada": removed})


@bp_config.route("/api/reglas/<regla_id>/toggle", methods=["POST"])
def api_toggle_regla(regla_id: str):
    reglas = _load_reglas()
    idx, regla = _find_regla(reglas, regla_id)
    if idx is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada."}), 404
    regla["enabled"] = not regla.get("enabled", True)
    reglas[idx] = regla
    _save_reglas(reglas)
    return jsonify({"ok": True, "enabled": regla["enabled"]})


# ============================================================
# Helpers — Filtros Exp-Q
# ============================================================

VARIABLES_FILTRO = list(VARIABLES_PROCESO)
VARIABLES_FILTRO_SET = set(VARIABLES_FILTRO)


def _defaults_filtros() -> dict:
    return {k: dict(v) for k, v in CONFIG_FILTRO_ESPESADOR_DEFAULT.items()}


def _save_filtros(cfg: dict) -> None:
    with open(FILTROS_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _load_filtros() -> dict:
    if not os.path.exists(FILTROS_JSON):
        cfg = _defaults_filtros()
        _save_filtros(cfg)
        return cfg
    try:
        with open(FILTROS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_filtros()
    if not isinstance(data, dict) or not data:
        return _defaults_filtros()
    return data


def _normalizar_filtros_payload(data: dict) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict) or not data:
        return None, "El payload debe ser un objeto no vacio { var: {q, window_size}, ... }."
    norm: dict = {}
    for var, cfg in data.items():
        var_s = str(var).strip()
        if var_s not in VARIABLES_FILTRO_SET:
            return None, f"Variable '{var_s}' no es filtrable. Validas: {sorted(VARIABLES_FILTRO_SET)}."
        if not isinstance(cfg, dict):
            return None, f"'{var_s}': la entrada debe ser un objeto con campos 'q' y 'window_size'."
        try:
            q = float(cfg.get("q"))
            ws = int(cfg.get("window_size"))
        except (TypeError, ValueError):
            return None, f"'{var_s}': 'q' debe ser numerico y 'window_size' entero."
        if not (0.0 <= q <= 1.0):
            return None, f"'{var_s}': 'q' fuera de [0.0, 1.0] (valor recibido: {q})."
        if ws < 1 or ws > 1000:
            return None, f"'{var_s}': 'window_size' fuera de [1, 1000] (valor recibido: {ws})."
        norm[var_s] = {"q": q, "window_size": ws}
    faltantes = VARIABLES_FILTRO_SET - set(norm.keys())
    if faltantes:
        return None, f"Faltan variables en el payload: {sorted(faltantes)}."
    return norm, None


_load_filtros()  # seed perezoso


@bp_config.route("/api/filtros", methods=["GET"])
def api_get_filtros():
    return jsonify({"variables": VARIABLES_FILTRO, "defaults": _defaults_filtros(), "actual": _load_filtros()})


@bp_config.route("/api/filtros", methods=["PUT"])
def api_put_filtros():
    data = request.get_json(force=True)
    norm, error = _normalizar_filtros_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_filtros(norm)
    return jsonify({"ok": True, "actual": norm})


@bp_config.route("/api/filtros/reset", methods=["POST"])
def api_reset_filtros():
    cfg = _defaults_filtros()
    _save_filtros(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Helpers — Defuzzy
# ============================================================

DEFUZZY_FAMILIAS = ("sp_floculante", "sp_vel_bomba", "sp_tonelaje")
DEFUZZY_ACCIONES_KEYS = (
    "AUMENTAR_FUERTE", "AUMENTAR", "AUMENTAR_SUAVE",
    "DISMINUIR_SUAVE", "DISMINUIR", "DISMINUIR_FUERTE",
)


def _defaults_defuzzy() -> dict:
    out = {}
    for fam in DEFUZZY_FAMILIAS:
        tabla = DEFUZZY_POR_FAMILIA.get(fam, {})
        out[fam] = {
            "belief_axis": list(tabla.get("belief_axis", [])),
            "steps_por_accion": {k: list(v) for k, v in tabla.get("steps_por_accion", {}).items()},
        }
    return out


def _save_defuzzy(cfg: dict) -> None:
    with open(DEFUZZY_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _load_defuzzy() -> dict:
    if not os.path.exists(DEFUZZY_JSON):
        cfg = _defaults_defuzzy()
        _save_defuzzy(cfg)
        return cfg
    try:
        with open(DEFUZZY_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_defuzzy()
    if not isinstance(data, dict) or not data:
        return _defaults_defuzzy()
    return data


def _normalizar_defuzzy_payload(data: dict) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict) or not data:
        return None, "El payload debe ser un objeto no vacio { <familia>: {...} }."
    actual = _load_defuzzy()
    out = _defaults_defuzzy()
    for fam in DEFUZZY_FAMILIAS:
        if fam in actual:
            out[fam] = {
                "belief_axis": list(actual[fam].get("belief_axis", out[fam]["belief_axis"])),
                "steps_por_accion": {k: list(v) for k, v in actual[fam].get("steps_por_accion", out[fam]["steps_por_accion"]).items()},
            }
    for fam, tabla in data.items():
        if fam not in DEFUZZY_FAMILIAS:
            return None, f"Familia desconocida: '{fam}'. Validas: {list(DEFUZZY_FAMILIAS)}."
        if not isinstance(tabla, dict):
            return None, f"'{fam}': debe ser un objeto con 'belief_axis' y 'steps_por_accion'."
        axis = tabla.get("belief_axis")
        steps = tabla.get("steps_por_accion")
        if not isinstance(axis, list) or len(axis) < 2:
            return None, f"'{fam}': 'belief_axis' debe ser una lista con al menos 2 puntos."
        try:
            axis_f = [float(x) for x in axis]
        except (TypeError, ValueError):
            return None, f"'{fam}': 'belief_axis' contiene valores no numericos."
        if any(x < 0.0 or x > 1.0 for x in axis_f):
            return None, f"'{fam}': 'belief_axis' fuera de [0.0, 1.0]."
        if any(axis_f[i] >= axis_f[i+1] for i in range(len(axis_f)-1)):
            return None, f"'{fam}': 'belief_axis' no es estrictamente creciente."
        n = len(axis_f)
        if not isinstance(steps, dict):
            return None, f"'{fam}': 'steps_por_accion' debe ser un objeto."
        faltan = set(DEFUZZY_ACCIONES_KEYS) - set(steps.keys())
        sobran = set(steps.keys()) - set(DEFUZZY_ACCIONES_KEYS)
        if faltan:
            return None, f"'{fam}': faltan acciones: {sorted(faltan)}."
        if sobran:
            return None, f"'{fam}': acciones desconocidas: {sorted(sobran)}."
        steps_norm = {}
        for k in DEFUZZY_ACCIONES_KEYS:
            arr = steps[k]
            if not isinstance(arr, list) or len(arr) != n:
                return None, f"'{fam}.{k}': debe ser una lista de {n} valores."
            try:
                steps_norm[k] = [float(x) for x in arr]
            except (TypeError, ValueError):
                return None, f"'{fam}.{k}': contiene valores no numericos."
        out[fam] = {"belief_axis": axis_f, "steps_por_accion": steps_norm}
    return out, None


_load_defuzzy()


@bp_config.route("/api/defuzzy", methods=["GET"])
def api_get_defuzzy():
    return jsonify({"familias": list(DEFUZZY_FAMILIAS), "acciones": list(DEFUZZY_ACCIONES_KEYS),
                    "defaults": _defaults_defuzzy(), "actual": _load_defuzzy()})


@bp_config.route("/api/defuzzy", methods=["PUT"])
def api_put_defuzzy():
    data = request.get_json(force=True)
    norm, error = _normalizar_defuzzy_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_defuzzy(norm)
    return jsonify({"ok": True, "actual": norm})


@bp_config.route("/api/defuzzy/reset", methods=["POST"])
def api_reset_defuzzy():
    cfg = _defaults_defuzzy()
    _save_defuzzy(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Helpers — Fuzzy (membresías)
# ============================================================

FUZZY_VARIABLES    = tuple(FUZZY_MODELOS.keys())
FUZZY_TIPO_POR_VAR = {v: FUZZY_MODELOS[v]["type"] for v in FUZZY_VARIABLES}
FUZZY_LABEL_KEYS   = ("HIGH", "OK", "LOW")


def _defaults_fuzzy() -> dict:
    out = {}
    for var, entry in FUZZY_MODELOS.items():
        mdl = entry["model"]
        out[var] = {
            "type":   entry["type"],
            "offset": [float(x) for x in list(mdl.offset)],
            "labels": {k: [float(x) for x in list(mdl.conjuntos[k])] for k in FUZZY_LABEL_KEYS},
        }
    return out


def _save_fuzzy(cfg: dict) -> None:
    with open(FUZZY_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _load_fuzzy() -> dict:
    if not os.path.exists(FUZZY_JSON):
        cfg = _defaults_fuzzy()
        _save_fuzzy(cfg)
        return cfg
    try:
        with open(FUZZY_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_fuzzy()
    if not isinstance(data, dict) or not data:
        return _defaults_fuzzy()
    return data


def _normalizar_fuzzy_payload(data: dict) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict) or not data:
        return None, "El payload debe ser un objeto no vacio { <var>: {...} }."
    actual = _load_fuzzy()
    out = _defaults_fuzzy()
    for var in FUZZY_VARIABLES:
        if var in actual:
            a = actual[var]
            out[var] = {
                "type":   FUZZY_TIPO_POR_VAR[var],
                "offset": list(a.get("offset", out[var]["offset"])),
                "labels": {k: list(a.get("labels", out[var]["labels"]).get(k, out[var]["labels"][k]))
                           for k in FUZZY_LABEL_KEYS},
            }
    for var, cfg in data.items():
        if var not in FUZZY_VARIABLES:
            return None, f"Variable desconocida: '{var}'. Validas: {list(FUZZY_VARIABLES)}."
        if not isinstance(cfg, dict):
            return None, f"'{var}': debe ser un objeto con 'offset' y 'labels'."
        offset = cfg.get("offset")
        labels = cfg.get("labels")
        if not isinstance(offset, list) or len(offset) < 3:
            return None, f"'{var}': 'offset' debe ser una lista con al menos 3 puntos."
        try:
            offset_f = [float(x) for x in offset]
        except (TypeError, ValueError):
            return None, f"'{var}': 'offset' contiene valores no numericos."
        if any(offset_f[i] >= offset_f[i+1] for i in range(len(offset_f)-1)):
            return None, f"'{var}': 'offset' no es estrictamente creciente."
        n = len(offset_f)
        if not isinstance(labels, dict):
            return None, f"'{var}': 'labels' debe ser un objeto con HIGH/OK/LOW."
        faltan = set(FUZZY_LABEL_KEYS) - set(labels.keys())
        sobran = set(labels.keys()) - set(FUZZY_LABEL_KEYS)
        if faltan:
            return None, f"'{var}': faltan etiquetas: {sorted(faltan)}."
        if sobran:
            return None, f"'{var}': etiquetas desconocidas: {sorted(sobran)}."
        labels_norm = {}
        for k in FUZZY_LABEL_KEYS:
            arr = labels[k]
            if not isinstance(arr, list) or len(arr) != n:
                return None, f"'{var}.{k}': debe ser una lista de {n} valores."
            try:
                arr_f = [float(x) for x in arr]
            except (TypeError, ValueError):
                return None, f"'{var}.{k}': contiene valores no numericos."
            if any(x < 0.0 or x > 1.0 for x in arr_f):
                return None, f"'{var}.{k}': valores fuera de [0.0, 1.0]."
            labels_norm[k] = arr_f
        out[var] = {"type": FUZZY_TIPO_POR_VAR[var], "offset": offset_f, "labels": labels_norm}
    return out, None


_load_fuzzy()


@bp_config.route("/api/fuzzy", methods=["GET"])
def api_get_fuzzy():
    return jsonify({"variables": list(FUZZY_VARIABLES), "tipo_por_var": FUZZY_TIPO_POR_VAR,
                    "label_keys": list(FUZZY_LABEL_KEYS), "defaults": _defaults_fuzzy(), "actual": _load_fuzzy()})


@bp_config.route("/api/fuzzy", methods=["PUT"])
def api_put_fuzzy():
    data = request.get_json(force=True)
    norm, error = _normalizar_fuzzy_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_fuzzy(norm)
    return jsonify({"ok": True, "actual": norm})


@bp_config.route("/api/fuzzy/reset", methods=["POST"])
def api_reset_fuzzy():
    cfg = _defaults_fuzzy()
    _save_fuzzy(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Helpers — Variables (crudas + definiciones calculadas)
# ============================================================

VAR_TIPOS              = ("aritmetica", "rolling_delta", "rolling_std")
VAR_OPERACIONES        = ("suma", "resta", "multiplicacion", "division")
VAR_NOMBRE_RE          = _re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
VAR_NOMBRES_RESERVADOS = set(VARIABLES_PROCESO) | set(SETPOINT_KEYS) | {
    f"pend_{v}" for v in VARIABLES_PROCESO
} | {"t_s"} | {f"{v}_lmin" for v in VARIABLES_PROCESO} | {f"{v}_lmax" for v in VARIABLES_PROCESO}


def _defaults_variables() -> dict:
    crudas = {k: str(v) for k, v in VARIABLES_CRUDAS.items()}
    definiciones = []
    for nombre, cfg in DEFINICIONES_CALCULADAS.items():
        item = {"nombre": nombre, "descripcion": str(cfg.get("descripcion", "")), "tipo": cfg["tipo"]}
        if cfg["tipo"] == "aritmetica":
            item["operacion"] = cfg["operacion"]
            item["args"] = list(cfg["args"])
        else:
            item["arg"] = cfg["arg"]
            item["ventana_min"] = float(cfg["ventana_min"])
        definiciones.append(item)
    return {"crudas": crudas, "definiciones": definiciones}


def _save_variables(cfg: dict) -> None:
    with open(VARIABLES_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _load_variables() -> dict:
    if not os.path.exists(VARIABLES_JSON):
        cfg = _defaults_variables()
        _save_variables(cfg)
        return cfg
    try:
        with open(VARIABLES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_variables()
    if not isinstance(data, dict) or "definiciones" not in data:
        return _defaults_variables()
    return data


def _normalizar_variables_payload(data: dict) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto con 'crudas' y 'definiciones'."
    crudas = data.get("crudas", {})
    defs = data.get("definiciones", [])
    if not isinstance(crudas, dict):
        return None, "'crudas' debe ser un objeto {nombre: descripcion}."
    if not isinstance(defs, list):
        return None, "'definiciones' debe ser una lista ordenada."
    crudas_norm: dict[str, str] = {}
    for nombre, descr in crudas.items():
        if not isinstance(nombre, str) or not VAR_NOMBRE_RE.match(nombre):
            return None, f"crudas: nombre invalido '{nombre}'."
        if nombre in VAR_NOMBRES_RESERVADOS:
            return None, f"crudas: '{nombre}' choca con un nombre reservado del nucleo."
        crudas_norm[nombre] = str(descr) if descr is not None else ""
    refs_disponibles = set(crudas_norm.keys()) | set(VARIABLES_PROCESO) | {"t_s"}
    defs_norm: list[dict] = []
    nombres_vistos: set[str] = set()
    for i, item in enumerate(defs):
        if not isinstance(item, dict):
            return None, f"definiciones[{i}]: debe ser un objeto."
        nombre = item.get("nombre")
        if not isinstance(nombre, str) or not VAR_NOMBRE_RE.match(nombre):
            return None, f"definiciones[{i}]: 'nombre' invalido '{nombre}'."
        if nombre in VAR_NOMBRES_RESERVADOS:
            return None, f"definiciones[{i}]: '{nombre}' choca con un nombre reservado."
        if nombre in crudas_norm:
            return None, f"definiciones[{i}]: '{nombre}' ya existe como cruda."
        if nombre in nombres_vistos:
            return None, f"definiciones[{i}]: '{nombre}' duplicada."
        nombres_vistos.add(nombre)
        tipo = item.get("tipo")
        if tipo not in VAR_TIPOS:
            return None, f"definiciones[{i}].tipo: invalido '{tipo}'. Validos: {list(VAR_TIPOS)}."
        descripcion = str(item.get("descripcion", "") or "")
        out: dict = {"nombre": nombre, "descripcion": descripcion, "tipo": tipo}
        if tipo == "aritmetica":
            operacion = item.get("operacion")
            if operacion not in VAR_OPERACIONES:
                return None, f"definiciones[{i}].operacion: invalida '{operacion}'."
            args = item.get("args")
            if not isinstance(args, list) or len(args) != 2:
                return None, f"definiciones[{i}].args: debe ser una lista de 2 nombres."
            for j, a in enumerate(args):
                if a not in refs_disponibles:
                    return None, f"definiciones[{i}].args[{j}] = '{a}' no existe."
            out["operacion"] = operacion
            out["args"] = list(args)
        else:
            arg = item.get("arg")
            if arg not in refs_disponibles:
                return None, f"definiciones[{i}].arg = '{arg}' no existe."
            try:
                vmin = float(item.get("ventana_min"))
            except (TypeError, ValueError):
                return None, f"definiciones[{i}].ventana_min: debe ser numero > 0."
            if vmin <= 0.0:
                return None, f"definiciones[{i}].ventana_min: debe ser > 0."
            out["arg"] = arg
            out["ventana_min"] = vmin
        defs_norm.append(out)
        refs_disponibles.add(nombre)
    return {"crudas": crudas_norm, "definiciones": defs_norm}, None


_load_variables()


@bp_config.route("/api/variables", methods=["GET"])
def api_get_variables():
    return jsonify({"tipos": list(VAR_TIPOS), "operaciones": list(VAR_OPERACIONES),
                    "variables_proceso": list(VARIABLES_PROCESO), "defaults": _defaults_variables(),
                    "actual": _load_variables()})


@bp_config.route("/api/variables", methods=["PUT"])
def api_put_variables():
    data = request.get_json(force=True)
    norm, error = _normalizar_variables_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_variables(norm)
    return jsonify({"ok": True, "actual": norm})


@bp_config.route("/api/variables/reset", methods=["POST"])
def api_reset_variables():
    cfg = _defaults_variables()
    _save_variables(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Helpers — Permisivos
# ============================================================

PERM_OPERADORES = ("<", "<=", ">", ">=", "==", "=", "!=")
PERM_NOMBRE_RE  = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _defaults_permisivos() -> dict:
    return _copy.deepcopy(PERMISIVOS)


def _load_permisivos() -> dict:
    if not os.path.exists(PERMISIVOS_JSON):
        cfg = _defaults_permisivos()
        _save_permisivos(cfg)
        return cfg
    try:
        with open(PERMISIVOS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return _defaults_permisivos()


def _save_permisivos(cfg: dict) -> None:
    with open(PERMISIVOS_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _validar_condicion(cond, path: str) -> str | None:
    if not isinstance(cond, dict):
        return f"{path}: cada condicion debe ser un objeto dict."
    if "OR" in cond:
        if set(cond.keys()) != {"OR"}:
            return f"{path}: OR debe ser la unica clave."
        if not isinstance(cond["OR"], list) or len(cond["OR"]) < 1:
            return f"{path}.OR: debe ser una lista no vacia."
        for i, sub in enumerate(cond["OR"]):
            err = _validar_condicion(sub, f"{path}.OR[{i}]")
            if err:
                return err
        return None
    if "AND" in cond:
        if set(cond.keys()) != {"AND"}:
            return f"{path}: AND debe ser la unica clave."
        if not isinstance(cond["AND"], list) or len(cond["AND"]) < 1:
            return f"{path}.AND: debe ser una lista no vacia."
        for i, sub in enumerate(cond["AND"]):
            err = _validar_condicion(sub, f"{path}.AND[{i}]")
            if err:
                return err
        return None
    if "NOT" in cond:
        if set(cond.keys()) != {"NOT"}:
            return f"{path}: NOT debe ser la unica clave."
        return _validar_condicion(cond["NOT"], f"{path}.NOT")
    if "fuzzy_var" in cond:
        var = cond.get("fuzzy_var")
        label = cond.get("label")
        if not isinstance(var, str) or not var.strip():
            return f"{path}.fuzzy_var: debe ser string no vacio."
        if not isinstance(label, str) or not label.strip():
            return f"{path}.label: debe ser string no vacio."
        if "min_mu" in cond:
            try:
                mu = float(cond["min_mu"])
            except (TypeError, ValueError):
                return f"{path}.min_mu: debe ser numero."
            if not (0.0 <= mu <= 1.0):
                return f"{path}.min_mu: fuera de [0,1]."
        extras = set(cond.keys()) - {"fuzzy_var", "label", "min_mu"}
        if extras:
            return f"{path}: claves no soportadas: {sorted(extras)}."
        return None
    if "var" in cond:
        var = cond.get("var")
        op = cond.get("op")
        if not isinstance(var, str) or not var.strip():
            return f"{path}.var: debe ser string no vacio."
        if op not in PERM_OPERADORES:
            return f"{path}.op: invalido {op!r}. Validos: {list(PERM_OPERADORES)}."
        if "value" not in cond:
            return f"{path}: falta clave 'value'."
        try:
            float(cond["value"])
        except (TypeError, ValueError):
            return f"{path}.value: debe ser numero."
        extras = set(cond.keys()) - {"var", "op", "value"}
        if extras:
            return f"{path}: claves no soportadas: {sorted(extras)}."
        return None
    return f"{path}: condicion no reconocida."


def _normalizar_permisivos_payload(data) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto {NOMBRE: [condiciones]}."
    if not data:
        return None, "Debe declarar al menos un permisivo."
    out: dict[str, list] = {}
    for nombre_raw, conds in data.items():
        nombre = str(nombre_raw).strip()
        if not nombre:
            return None, "Nombre de permisivo vacio."
        if not PERM_NOMBRE_RE.match(nombre):
            return None, f"Nombre de permisivo invalido: {nombre!r}."
        nombre_up = nombre.upper()
        if nombre_up in out:
            return None, f"Nombre de permisivo duplicado: {nombre_up}."
        if not isinstance(conds, list):
            return None, f"{nombre_up}: el cuerpo debe ser una lista."
        if len(conds) == 0:
            return None, f"{nombre_up}: debe declarar al menos una condicion."
        for i, c in enumerate(conds):
            err = _validar_condicion(c, f"{nombre_up}[{i}]")
            if err is not None:
                return None, err
        out[nombre_up] = conds
    return out, None


_load_permisivos()


@bp_config.route("/api/permisivos", methods=["GET"])
def api_get_permisivos():
    return jsonify({"operadores": list(PERM_OPERADORES), "fuzzy_vars": sorted(FUZZY_MODELOS.keys()),
                    "defaults": _defaults_permisivos(), "actual": _load_permisivos()})


@bp_config.route("/api/permisivos", methods=["PUT"])
def api_put_permisivos():
    data = request.get_json(force=True)
    norm, error = _normalizar_permisivos_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_permisivos(norm)
    return jsonify({"ok": True, "actual": norm})


@bp_config.route("/api/permisivos/reset", methods=["POST"])
def api_reset_permisivos():
    cfg = _defaults_permisivos()
    _save_permisivos(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# API — Estados
# ============================================================

@bp_config.route("/api/estados", methods=["GET"])
def api_get_estados():
    return jsonify(_load_estados())


@bp_config.route("/api/estados/<nombre>", methods=["GET"])
def api_get_estado(nombre: str):
    estados = _load_estados()
    est = estados.get(nombre)
    if est is None:
        return jsonify({"error": f"Estado '{nombre}' no encontrado"}), 404
    return jsonify(est)


@bp_config.route("/api/estados", methods=["POST"])
def api_create_estado():
    data = request.get_json(force=True)
    nombre = str(data.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "Campo 'nombre' requerido"}), 400
    estados = _load_estados()
    if nombre in estados:
        return jsonify({"error": f"Estado '{nombre}' ya existe"}), 409
    nuevo = {"nombre": nombre, "tipo": data.get("tipo", "estado"), "condicion": data.get("condicion", [])}
    estados[nombre] = nuevo
    _save_estados(estados)
    return jsonify({"ok": True, "estado": nuevo}), 201


@bp_config.route("/api/estados/<nombre>", methods=["PUT"])
def api_update_estado(nombre: str):
    estados = _load_estados()
    if nombre not in estados:
        return jsonify({"error": f"Estado '{nombre}' no encontrado"}), 404
    data = request.get_json(force=True)
    estados[nombre]["tipo"]      = data.get("tipo", estados[nombre].get("tipo", "estado"))
    estados[nombre]["condicion"] = data.get("condicion", estados[nombre].get("condicion", []))
    _save_estados(estados)
    return jsonify({"ok": True, "estado": estados[nombre]})


@bp_config.route("/api/estados/<nombre>", methods=["DELETE"])
def api_delete_estado(nombre: str):
    estados = _load_estados()
    if nombre not in estados:
        return jsonify({"error": f"Estado '{nombre}' no encontrado"}), 404
    removed = estados.pop(nombre)
    _save_estados(estados)
    return jsonify({"ok": True, "eliminado": removed})


@bp_config.route("/api/estados/reset", methods=["POST"])
def api_reset_estados():
    cfg = _defaults_estados()
    _save_estados(cfg)
    return jsonify({"ok": True})


# ============================================================
# API — Waits Catálogo
# ============================================================

@bp_config.route("/api/waits", methods=["GET"])
def api_get_waits():
    return jsonify(_load_waits())


@bp_config.route("/api/waits/<path:wait_id>", methods=["GET"])
def api_get_wait(wait_id: str):
    waits = _load_waits()
    for w in waits:
        if w.get("wait_id") == wait_id:
            return jsonify(w)
    return jsonify({"error": f"Wait '{wait_id}' no encontrado"}), 404


@bp_config.route("/api/waits", methods=["POST"])
def api_create_wait():
    data = request.get_json(force=True)
    nombre = str(data.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "Campo 'nombre' requerido"}), 400
    from waits_catalogo import crear_wait
    new_wait = crear_wait(nombre, variable_controlada=data.get("variable_controlada"),
                          descripcion=data.get("descripcion", ""))
    waits = _load_waits()
    for w in waits:
        if w["wait_id"] == new_wait["wait_id"]:
            return jsonify({"error": f"Wait '{new_wait['wait_id']}' ya existe"}), 409
    waits.append(new_wait)
    _save_waits(waits)
    return jsonify({"ok": True, "wait": new_wait}), 201


@bp_config.route("/api/waits/<path:wait_id>", methods=["PUT"])
def api_update_wait(wait_id: str):
    waits = _load_waits()
    idx = next((i for i, w in enumerate(waits) if w.get("wait_id") == wait_id), None)
    if idx is None:
        return jsonify({"error": f"Wait '{wait_id}' no encontrado"}), 404
    data = request.get_json(force=True)
    waits[idx]["variable_controlada"] = data.get("variable_controlada", waits[idx].get("variable_controlada"))
    waits[idx]["descripcion"] = data.get("descripcion", waits[idx].get("descripcion", ""))
    _save_waits(waits)
    return jsonify({"ok": True, "wait": waits[idx]})


@bp_config.route("/api/waits/<path:wait_id>", methods=["DELETE"])
def api_delete_wait(wait_id: str):
    waits = _load_waits()
    idx = next((i for i, w in enumerate(waits) if w.get("wait_id") == wait_id), None)
    if idx is None:
        return jsonify({"error": f"Wait '{wait_id}' no encontrado"}), 404
    removed = waits.pop(idx)
    _save_waits(waits)
    return jsonify({"ok": True, "eliminado": removed})


@bp_config.route("/api/waits/reset", methods=["POST"])
def api_reset_waits():
    cfg = _defaults_waits()
    _save_waits(cfg)
    return jsonify({"ok": True})


# ============================================================
# Helpers — Tracking PV-SP
# ============================================================

TRACKING_FAMILIAS = list(SETPOINT_KEYS)


def _defaults_tracking() -> dict:
    return {
        "sp_tonelaje":   {"pv_key": "pv_tonelaje",   "rango": 1.0,  "habilitado": True},
        "sp_floculante": {"pv_key": "pv_floculante", "rango": 0.05, "habilitado": True},
        "sp_vel_bomba":  {"pv_key": "pv_vel_bomba",  "rango": 0.10, "habilitado": True},
    }


def _save_tracking(cfg: dict) -> None:
    with open(TRACKING_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _load_tracking() -> dict:
    if not os.path.exists(TRACKING_JSON):
        cfg = _defaults_tracking()
        _save_tracking(cfg)
        return cfg
    try:
        with open(TRACKING_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_tracking()
    if not isinstance(data, dict) or not data:
        return _defaults_tracking()
    return data


def _normalizar_tracking_payload(data: dict) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict) or not data:
        return None, "El payload debe ser un objeto no vacio { <familia>: {pv_key, rango, habilitado} }."
    out = {}
    for familia in TRACKING_FAMILIAS:
        if familia not in data:
            return None, f"Falta la familia '{familia}' en el payload."
        spec = data[familia]
        if not isinstance(spec, dict):
            return None, f"'{familia}': debe ser un objeto con pv_key, rango y habilitado."
        pv_key = spec.get("pv_key")
        if not isinstance(pv_key, str) or not pv_key.strip():
            return None, f"'{familia}': 'pv_key' debe ser un string no vacio."
        try:
            rango = float(spec.get("rango"))
        except (TypeError, ValueError):
            return None, f"'{familia}': 'rango' debe ser numerico."
        if rango < 0.0:
            return None, f"'{familia}': 'rango' debe ser >= 0 (valor recibido: {rango})."
        habilitado = bool(spec.get("habilitado", True))
        out[familia] = {"pv_key": pv_key.strip(), "rango": rango, "habilitado": habilitado}
    extras = set(data.keys()) - set(TRACKING_FAMILIAS)
    if extras:
        return None, f"Familias desconocidas: {sorted(extras)}. Validas: {TRACKING_FAMILIAS}."
    return out, None


_load_tracking()


@bp_config.route("/api/tracking", methods=["GET"])
def api_get_tracking():
    return jsonify({
        "familias": TRACKING_FAMILIAS,
        "defaults": _defaults_tracking(),
        "actual": _load_tracking(),
    })


@bp_config.route("/api/tracking", methods=["PUT"])
def api_put_tracking():
    data = request.get_json(force=True)
    norm, error = _normalizar_tracking_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_tracking(norm)
    return jsonify({"ok": True, "actual": norm})


@bp_config.route("/api/tracking/reset", methods=["POST"])
def api_reset_tracking():
    cfg = _defaults_tracking()
    _save_tracking(cfg)
    return jsonify({"ok": True, "actual": cfg})
