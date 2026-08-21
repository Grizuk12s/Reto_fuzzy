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
from core.filters.exp_q import CONFIG_FILTRO_ESPESADOR_DEFAULT, PERIODO_LEGACY_S
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
    etiquetas_disponibles, etiquetas_validas,
    acciones_disponibles, acciones_validas,
    ESTADOS_SERIALIZADOS,
    _load_estados, _save_estados, _defaults_estados,
    _load_waits, _save_waits, _defaults_waits,
    _definiciones_lista_a_dict,
    _license_check, _license_sign_and_save, _license_now_iso,
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
        # Incluye las filas de fuzzy.json: el editor de reglas tiene que
        # ofrecer las etiquetas que el operador acaba de crear.
        "etiquetas":   etiquetas_disponibles(),
        # Mismo criterio que las etiquetas: salen de defuzzy.json en cada
        # request, para que una accion recien creada aparezca sin reiniciar.
        "acciones":    acciones_disponibles(),
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
# Tope duro de vigencia: ninguna licencia puede expirar despues de esta fecha.
LICENCIA_FECHA_MAXIMA = date(2026, 10, 12)


def _defaults_licencia() -> dict:
    return {
        "active": False,
        "months": None,
        "type": "Sin licencia activa",
        "activated_at": None,
        "expires_at": None,
        "history": [],
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
    merged = {**_defaults_licencia(), **data}
    if not isinstance(merged.get("history"), list):
        merged["history"] = []
    return merged


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
    # _license_check() actualiza checkpoint + consumo y persiste. Recargar para
    # tomar los valores frescos.
    check = _license_check()
    cfg = _load_licencia()

    today = date.today()
    months = cfg.get("months")
    activated_at = _parse_iso_date(cfg.get("activated_at"))
    expires_at = _parse_iso_date(cfg.get("expires_at"))
    tampered = bool(check.get("tampered"))
    active = (
        bool(cfg.get("active"))
        and not tampered
        and months in LICENCIA_DURACIONES_VALIDAS
        and activated_at is not None
        and expires_at is not None
    )
    expired = bool(check.get("expired")) or bool(active and expires_at < today)
    remaining_days = None
    remaining_text = "Sin licencia activa"

    if active and not expired:
        delta_days = (expires_at - today).days
        remaining_days = max(delta_days, 0)
        if delta_days == 0:
            remaining_text = "Vence hoy"
        else:
            remaining_text = f"{remaining_days} dia(s)"
    elif expired:
        remaining_text = "Expirada"
    elif tampered:
        remaining_text = "Manipulada"

    capped = bool(active and expires_at == LICENCIA_FECHA_MAXIMA and activated_at
                  and _add_months(activated_at, months or 0) > LICENCIA_FECHA_MAXIMA)

    history = cfg.get("history") or []
    if not isinstance(history, list):
        history = []

    duration_seconds = float(cfg.get("duration_seconds") or 0.0)
    consumed_seconds = float(cfg.get("consumed_seconds") or 0.0)
    remaining_seconds = check.get("remaining_seconds")
    if remaining_seconds is None and duration_seconds:
        remaining_seconds = max(0.0, duration_seconds - consumed_seconds)
    usage_percent = (
        round(min(100.0, 100.0 * consumed_seconds / duration_seconds), 2)
        if duration_seconds > 0 else None
    )

    if tampered:
        status_label = "Manipulada"
    elif active and not expired:
        status_label = "Activa"
    elif expired:
        status_label = "Expirada"
    else:
        status_label = "Inactiva"

    return {
        "active": active and not expired,
        "expired": expired,
        "tampered": tampered,
        "months": months if months in LICENCIA_DURACIONES_VALIDAS else None,
        "type": _tipo_licencia(months if active else None),
        "activated_at": _fmt_date(activated_at),
        "expires_at": _fmt_date(expires_at),
        "remaining_days": remaining_days,
        "remaining_text": remaining_text,
        "status_label": status_label,
        "requires_code": True,
        "max_expires_at": _fmt_date(LICENCIA_FECHA_MAXIMA),
        "capped": capped,
        "history": history,
        "duration_seconds": duration_seconds or None,
        "consumed_seconds": consumed_seconds if duration_seconds else None,
        "remaining_seconds": remaining_seconds,
        "usage_percent": usage_percent,
        "last_seen_at": cfg.get("last_seen_at"),
        "signed": bool(cfg.get("signature")),
        "reason": check.get("reason") or "",
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
    if activated_at > LICENCIA_FECHA_MAXIMA:
        return jsonify({
            "error": f"No se pueden activar nuevas licencias despues de {LICENCIA_FECHA_MAXIMA.isoformat()}."
        }), 400

    solicited_expires = _add_months(activated_at, months)
    effective_expires = min(solicited_expires, LICENCIA_FECHA_MAXIMA)
    capped = solicited_expires > LICENCIA_FECHA_MAXIMA

    prev = _load_licencia()
    history = list(prev.get("history") or [])
    if prev.get("active") and prev.get("expires_at"):
        for h in history:
            if h.get("status") == "Activa":
                h["status"] = "Reemplazada"
        history.append({
            "activated_at": prev.get("activated_at"),
            "months": prev.get("months"),
            "expires_at": prev.get("expires_at"),
            "capped": bool(prev.get("capped")),
            "status": "Reemplazada",
            "code_masked": "HUTBAY-***-2026",
        })

    history.append({
        "activated_at": _fmt_date(activated_at),
        "months": months,
        "expires_at": _fmt_date(effective_expires),
        "capped": capped,
        "status": "Activa",
        "code_masked": "HUTBAY-***-2026",
    })

    if len(history) > 50:
        history = history[-50:]

    duration_seconds = float((effective_expires - activated_at).days) * 86400.0

    cfg = {
        "active": True,
        "months": months,
        "type": _tipo_licencia(months),
        "activated_at": _fmt_date(activated_at),
        "expires_at": _fmt_date(effective_expires),
        "capped": capped,
        "history": history,
        "duration_seconds": duration_seconds,
        "consumed_seconds": 0.0,
        "last_seen_at": _license_now_iso(),
    }
    _license_sign_and_save(cfg)
    payload = _serializar_licencia(cfg)
    return jsonify({
        "ok": True,
        "license": payload,
        "capped": capped,
        "max_expires_at": _fmt_date(LICENCIA_FECHA_MAXIMA),
    })


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
            if l not in etiquetas_validas():
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
        if etiqueta not in etiquetas_validas():
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
    # Las acciones se validan contra defuzzy.json en cada guardado, no contra
    # una foto del import: si el operador acaba de crear la columna, la regla
    # que la usa tiene que poder guardarse sin reiniciar el servicio.
    validas = acciones_validas()

    def _validar_accion(nombre_crudo, idx: int) -> tuple[str | None, str | None]:
        nombre = str(nombre_crudo or "").strip().upper()
        if not nombre:
            return None, (f"Accion sin asignar en then #{idx}. Elegi una accion del "
                          "defuzzy antes de guardar la regla.")
        if nombre not in validas:
            if not validas:
                return None, (f"Accion invalida en then #{idx}: '{nombre}'. No hay ninguna "
                              "accion definida: creala primero en la pagina Defuzzy.")
            return None, (f"Accion invalida en then #{idx}: '{nombre}'. No existe en el "
                          f"defuzzy. Disponibles: {sorted(validas)}.")
        return nombre, None

    def _validar_waits(accion: dict, idx: int) -> str | None:
        """Un wait sin wait_id o sin duracion revienta el motor en cada tick.

        El motor ahora aisla la regla rota, pero igual queda muerta. Se valida
        aca para que no se pueda guardar de entrada.
        """
        for campo in ("waits", "reiniciar_waits"):
            lista = accion.get(campo) or []
            if isinstance(lista, dict):
                lista = [lista]
            if not isinstance(lista, list):
                return f"then #{idx}: '{campo}' debe ser una lista de waits."
            for j, w in enumerate(lista, start=1):
                if not isinstance(w, dict):
                    return f"then #{idx}, {campo} #{j}: cada wait es un objeto."
                if not str(w.get("wait_id") or "").strip():
                    return f"then #{idx}, {campo} #{j}: falta 'wait_id'."
                try:
                    dur = float(w.get("duracion_s"))
                except (TypeError, ValueError):
                    return (f"then #{idx}, {campo} #{j} ('{w.get('wait_id')}'): "
                            "falta 'duracion_s' o no es numerica. Sin duracion el "
                            "motor no puede evaluar la regla.")
                if dur < 0:
                    return (f"then #{idx}, {campo} #{j}: 'duracion_s' debe ser >= 0 "
                            f"(valor recibido: {dur}).")
        return None

    acciones_norm = []
    for idx, accion in enumerate(acciones, start=1):
        if isinstance(accion, dict) and "accion" in accion:
            accion_nombre, err = _validar_accion(accion["accion"], idx)
            if err is not None:
                return None, err
            err = _validar_waits(accion, idx)
            if err is not None:
                return None, err
            accion["accion"] = accion_nombre
            acciones_norm.append(accion)
        else:
            accion_norm, err = _validar_accion(accion, idx)
            if err is not None:
                return None, err
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

# La ventana del filtro se mide en SEGUNDOS DE PROCESO, no en muestras: con
# el SEEngine en ciclo libre el numero de muestras por segundo lo fija la
# latencia de OPC-UA y cambia tick a tick.
VENTANA_S_MIN = 0.1
VENTANA_S_MAX = 3600.0


def _defaults_filtros() -> dict:
    return {k: dict(v) for k, v in CONFIG_FILTRO_ESPESADOR_DEFAULT.items()}


def _save_filtros(cfg: dict) -> None:
    with open(FILTROS_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _load_filtros() -> dict:
    """Config de filtros. Un objeto vacio es un estado VALIDO (sin PV aun)."""
    if not os.path.exists(FILTROS_JSON):
        cfg = _defaults_filtros()
        _save_filtros(cfg)
        return cfg
    try:
        with open(FILTROS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_filtros()
    if not isinstance(data, dict):
        return _defaults_filtros()
    return _migrar_filtros(data)


def _migrar_filtros(data: dict) -> dict:
    """Traduce entradas viejas {q, window_size} a {q, ventana_s}.

    No reescribe el archivo: la migracion se persiste recien cuando el
    operador guarda desde la pagina. Asi un rollback del codigo se sigue
    encontrando el filtros.json que dejo.
    """
    out: dict = {}
    for var, cfg in data.items():
        if not isinstance(cfg, dict):
            out[var] = cfg
            continue
        if "ventana_s" in cfg or "window_size" not in cfg:
            out[var] = cfg
            continue
        nuevo = {k: v for k, v in cfg.items() if k != "window_size"}
        try:
            nuevo["ventana_s"] = int(cfg["window_size"]) * PERIODO_LEGACY_S
        except (TypeError, ValueError):
            out[var] = cfg
            continue
        nuevo["migrado_de_window_size"] = int(cfg["window_size"])
        out[var] = nuevo
    return out


def _normalizar_filtros_payload(data: dict) -> tuple[dict | None, str | None]:
    """Valida la config de filtros.

    Las variables filtrables salen de los tags de categoria PV, que cambian
    por cliente; ya no se exigen contra una lista fija del espesador.
    Un payload vacio es valido.
    """
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto { var: {q, ventana_s}, ... }."

    items, _ = entradas_pv_disponibles()
    validas = {i["identificador"] for i in items} | set(VARIABLES_FILTRO_SET)

    norm: dict = {}
    for var, cfg in data.items():
        var_s = str(var).strip()
        if validas and var_s not in validas:
            return None, (f"'{var_s}' no corresponde a ningun tag de categoria PV. "
                          f"Validas: {sorted(validas)}.")
        if not isinstance(cfg, dict):
            return None, f"'{var_s}': la entrada debe ser un objeto con campos 'q' y 'ventana_s'."
        try:
            q = float(cfg.get("q"))
            # Se sigue aceptando `window_size` para que un payload viejo no
            # rompa; se traduce a segundos con el periodo de aquel lazo.
            if cfg.get("ventana_s") is not None:
                ventana_s = float(cfg.get("ventana_s"))
            elif cfg.get("window_size") is not None:
                ventana_s = int(cfg.get("window_size")) * PERIODO_LEGACY_S
            else:
                return None, f"'{var_s}': falta 'ventana_s' (segundos de proceso)."
        except (TypeError, ValueError):
            return None, f"'{var_s}': 'q' y 'ventana_s' deben ser numericos."
        if not (0.0 <= q <= 1.0):
            return None, f"'{var_s}': 'q' fuera de [0.0, 1.0] (valor recibido: {q})."
        if not (VENTANA_S_MIN <= ventana_s <= VENTANA_S_MAX):
            return None, (f"'{var_s}': 'ventana_s' fuera de "
                          f"[{VENTANA_S_MIN}, {VENTANA_S_MAX}] segundos "
                          f"(valor recibido: {ventana_s}).")
        norm[var_s] = {"q": q, "ventana_s": ventana_s}
    return norm, None


_load_filtros()  # seed perezoso


@bp_config.route("/api/filtros", methods=["GET"])
def api_get_filtros():
    """Filtros + catalogo de PV, para no escribir nombres a mano."""
    items, _ = entradas_pv_disponibles()
    actual = _load_filtros()
    idents = {i["identificador"] for i in items}
    return jsonify({
        "variables": [i["identificador"] for i in items],
        "pv": items,
        "huerfanas": [v for v in actual if v not in idents],
        "defaults": _defaults_filtros(),
        "actual": actual,
    })


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
    """Vacia los filtros. Ya no repuebla la plantilla del espesador."""
    _save_filtros({})
    return jsonify({"ok": True, "actual": {}})


# Sintonizacion inicial de un filtro nuevo: balance entre suavizado y lag.
# El operador la ajusta despues viendo crudo vs filtrado en la traza.
FILTRO_NUEVO_DEFAULT = {"q": 0.15, "ventana_s": 50.0}


@bp_config.route("/api/filtros/sincronizar", methods=["POST"])
def api_sincronizar_filtros():
    """Alinea las variables filtradas con los tags PV.

    Importante: el filtro Exp-Q lanza KeyError si recibe una variable que no
    esta configurada, o sea que una PV sin filtro rompe el tick. Por eso las
    nuevas entran con una sintonizacion por defecto y no vacias.
    """
    actual = _load_filtros()
    items, _ = entradas_pv_disponibles()
    idents = [i["identificador"] for i in items]

    agregadas = [i for i in idents if i not in actual]
    quitadas = [v for v in actual if v not in idents]

    nuevo = {v: c for v, c in actual.items() if v in idents}
    for i in agregadas:
        nuevo[i] = dict(FILTRO_NUEVO_DEFAULT)

    _save_filtros(nuevo)
    return jsonify({"ok": True, "actual": nuevo,
                    "agregadas": agregadas, "quitadas": quitadas,
                    "default_aplicado": FILTRO_NUEVO_DEFAULT})


# ============================================================
# Helpers — Defuzzy
# ============================================================

# Ya no hay familias precargadas. Las tablas del espesador antiguo
# (sp_floculante / sp_vel_bomba / sp_tonelaje) pertenecen a otra operacion y
# se sembraban solas cada vez que faltaba defuzzy.json, arrastrando 18
# acciones inexistentes al editor de reglas. Las familias salen ahora de los
# tags de categoria SP y las acciones, de las columnas que el operador crea.
DEFUZZY_FAMILIAS: tuple = ()
DEFUZZY_ACCIONES_KEYS = (
    "AUMENTAR_FUERTE", "AUMENTAR", "AUMENTAR_SUAVE",
    "DISMINUIR_SUAVE", "DISMINUIR", "DISMINUIR_FUERTE",
)


def _defaults_defuzzy() -> dict:
    """Sin plantilla: arrancar vacio es el estado correcto para un cliente nuevo."""
    return {}


def _save_defuzzy(cfg: dict) -> None:
    with open(DEFUZZY_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _load_defuzzy() -> dict:
    """Tablas Sugeno. Un objeto vacio es un estado VALIDO (sin familias aun)."""
    if not os.path.exists(DEFUZZY_JSON):
        cfg = _defaults_defuzzy()
        _save_defuzzy(cfg)
        return cfg
    try:
        with open(DEFUZZY_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_defuzzy()
    if not isinstance(data, dict):
        return _defaults_defuzzy()
    return data


# Nombre de accion: mayusculas, numeros y guion bajo. Es la etiqueta que
# usan las reglas en su `then`, asi que se valida con cuidado.
ACCION_RE = _re.compile(r"^[A-Z][A-Z0-9_]{1,60}$")

# Tabla de arranque de una familia nueva: eje de belief y una accion neutra.
DEFUZZY_NUEVA = {
    "belief_axis": [0.0, 0.5, 1.0],
    "steps_por_accion": {"AUMENTAR": [0.0, 0.5, 1.0]},
}


def _normalizar_defuzzy_payload(data: dict) -> tuple[dict | None, str | None]:
    """Valida las tablas Sugeno.

    Las familias salen de los tags de categoria SP y los nombres de accion
    son libres: el motor los busca directo en la tabla, ya no los parsea.
    Un payload vacio es valido.
    """
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto { <familia>: {belief_axis, steps_por_accion} }."

    validas = {s["identificador"] for s in salidas_sp_disponibles()} | set(DEFUZZY_FAMILIAS)

    out = {}
    for fam, tabla in data.items():
        if validas and fam not in validas:
            return None, (f"'{fam}' no corresponde a ningun tag de categoria SP. "
                          f"Validas: {sorted(validas)}.")
        if not isinstance(tabla, dict):
            return None, f"'{fam}': debe ser un objeto con 'belief_axis' y 'steps_por_accion'."

        # --- Eje del belief: acotado a [0,1] y creciente ---
        # El belief es la CONVICCION con que disparo la regla, no una magnitud
        # de proceso: por definicion va de 0.0 a 1.0. Y tiene que crecer de
        # izquierda a derecha porque el motor interpola sobre el (np.interp
        # con un eje desordenado devuelve basura en silencio).
        axis = tabla.get("belief_axis")
        if not isinstance(axis, list) or len(axis) < 2:
            return None, f"'{fam}': 'belief_axis' debe ser una lista con al menos 2 puntos."
        try:
            axis_f = [float(x) for x in axis]
        except (TypeError, ValueError):
            return None, f"'{fam}': 'belief_axis' contiene valores no numericos."
        for i, x in enumerate(axis_f):
            if x < 0.0 or x > 1.0:
                return None, (f"'{fam}': el belief va de 0.0 a 1.0 y P{i + 1} vale {x}. "
                              "Es una conviccion, no una magnitud de proceso.")
        for i in range(len(axis_f) - 1):
            if axis_f[i] >= axis_f[i + 1]:
                return None, (f"'{fam}': los puntos del belief van de menor a mayor, de "
                              f"izquierda a derecha: P{i + 1}={axis_f[i]} y "
                              f"P{i + 2}={axis_f[i + 1]} rompen el orden.")

        steps = tabla.get("steps_por_accion")
        if not isinstance(steps, dict) or not steps:
            return None, f"'{fam}': 'steps_por_accion' debe tener al menos una accion."

        steps_norm = {}
        for accion, arr in steps.items():
            acc = str(accion).strip().upper()
            if not ACCION_RE.match(acc):
                return None, (f"'{fam}': nombre de accion invalido '{accion}'. "
                              "Usa mayusculas, numeros y guion bajo (ej: SUBIR_VELOCIDAD).")
            if acc in steps_norm:
                return None, f"'{fam}': accion duplicada '{acc}'."
            if not isinstance(arr, list) or len(arr) != len(axis_f):
                return None, f"'{fam}.{acc}': debe tener {len(axis_f)} valores, uno por punto del belief."
            try:
                # A DIFERENCIA del eje, los pasos NO tienen tope: estan en
                # unidades de ingenieria del setpoint (%, t/h, g/t) y pueden
                # ser negativos (bajar el SP). Quien acota el resultado es
                # `limites_sp` del contrato, al clipear la escritura al DCS.
                steps_norm[acc] = [float(x) for x in arr]
            except (TypeError, ValueError):
                return None, f"'{fam}.{acc}': contiene valores no numericos."
        out[fam] = {"belief_axis": axis_f, "steps_por_accion": steps_norm}

    # Una misma accion en dos familias haria ambiguo a que SP afecta.
    vistos = {}
    for fam, tabla in out.items():
        for acc in tabla["steps_por_accion"]:
            vistos.setdefault(acc, []).append(fam)
    dup = {a: f for a, f in vistos.items() if len(f) > 1}
    if dup:
        detalle = "; ".join(f"'{a}' en {sorted(f)}" for a, f in dup.items())
        return None, ("Una accion no puede estar en dos familias: el motor no sabria "
                      f"que setpoint mover. {detalle}.")

    return out, None


def enlaces_defuzzy() -> dict:
    """Por familia: que reglas usan sus acciones y que fuzzys leen esas reglas.

    La tabla de defuzzy es el punto donde una decision difusa se convierte en
    un movimiento de setpoint, pero la pagina no mostraba ninguno de los dos
    extremos del enlace: ni el tag SP que termina moviendo, ni las variables
    difusas que lo disparan. Esto arma ese mapa:

        familia (SP)  <--  accion  <--  regla  <--  variables fuzzy

    Es solo para mostrar; no participa de ninguna decision.
    """
    from core.engine.motor import pares_de_regla

    cfg = _load_defuzzy()
    try:
        reglas = _load_reglas()
    except Exception:
        reglas = []

    # accion -> familia. El validador ya garantiza que una accion no este en
    # dos familias, asi que este mapeo es univoco.
    familia_de_accion = {}
    for fam, tabla in cfg.items():
        for acc in (tabla or {}).get("steps_por_accion", {}):
            familia_de_accion[str(acc).upper()] = fam

    out = {fam: {"reglas": [], "fuzzys": []} for fam in cfg}
    for r in reglas or []:
        rid = str(r.get("id", "?"))
        acciones = {(a.get("accion") if isinstance(a, dict) else a)
                    for a in (r.get("then") or [])}
        acciones = {str(a).upper() for a in acciones if a}
        familias = {familia_de_accion[a] for a in acciones if a in familia_de_accion}
        if not familias:
            continue
        try:
            variables = sorted({v for v, _ in pares_de_regla(r)})
        except Exception:
            variables = []
        for fam in familias:
            entrada = out.setdefault(fam, {"reglas": [], "fuzzys": []})
            entrada["reglas"].append({
                "id": rid,
                "bloque": r.get("bloque", ""),
                "acciones": sorted(a for a in acciones if familia_de_accion.get(a) == fam),
                "variables": variables,
            })
            for v in variables:
                if v not in entrada["fuzzys"]:
                    entrada["fuzzys"].append(v)
    for entrada in out.values():
        entrada["fuzzys"].sort()
    return out


_load_defuzzy()


@bp_config.route("/api/defuzzy", methods=["GET"])
def api_get_defuzzy():
    """Tablas Sugeno + catalogo de familias SP, para no escribir nombres a mano."""
    from config import LIMITES_SP_CONTRATO

    salidas = salidas_sp_disponibles()
    actual = _load_defuzzy()
    idents = {s["identificador"] for s in salidas}
    # El tag SP concreto al que escribe cada familia. La familia ES el
    # identificador del SP, pero el operador razona en tags y pseudonimos.
    sp_por_familia = {s["identificador"]: dict(s, limites=list(
        LIMITES_SP_CONTRATO.get(s["identificador"], []) or []))
        for s in salidas}
    return jsonify({
        "familias": sorted(actual.keys()),
        "salidas": salidas,
        "sin_tabla": [s["identificador"] for s in salidas if s["identificador"] not in actual],
        "huerfanas": [f for f in actual if f not in idents],
        "plantilla": DEFUZZY_NUEVA,
        "acciones": list(DEFUZZY_ACCIONES_KEYS),   # sugerencias, ya no obligatorias
        "sp_por_familia": sp_por_familia,
        "enlaces": enlaces_defuzzy(),
        "defaults": _defaults_defuzzy(),
        "actual": actual,
    })


@bp_config.route("/api/defuzzy/<familia>", methods=["POST"])
def api_crear_defuzzy(familia: str):
    """Crea la tabla de una familia SP con una plantilla minima."""
    if familia not in {s["identificador"] for s in salidas_sp_disponibles()}:
        return jsonify({"error": f"'{familia}' no corresponde a ningun tag de categoria SP."}), 400
    cfg = _load_defuzzy()
    if familia in cfg:
        return jsonify({"error": f"'{familia}' ya tiene tabla."}), 409
    cfg[familia] = {
        "belief_axis": list(DEFUZZY_NUEVA["belief_axis"]),
        "steps_por_accion": {k: list(v) for k, v in DEFUZZY_NUEVA["steps_por_accion"].items()},
    }
    _save_defuzzy(cfg)
    return jsonify({"ok": True, "familia": familia, "actual": cfg,
                    "aviso": "Creada con una accion de ejemplo. Renombrala y calibra los pasos."}), 201


@bp_config.route("/api/defuzzy/<familia>", methods=["DELETE"])
def api_borrar_defuzzy(familia: str):
    """Borra la tabla de una familia, informando que reglas usan sus acciones."""
    from runner import cargar_reglas_json

    cfg = _load_defuzzy()
    if familia not in cfg:
        return jsonify({"error": f"'{familia}' no tiene tabla."}), 404
    acciones = set(cfg[familia].get("steps_por_accion") or {})
    try:
        reglas = []
        for r in cargar_reglas_json():
            usadas = {a["accion"] if isinstance(a, dict) else a for a in (r.get("then") or [])}
            if usadas & acciones:
                reglas.append(str(r.get("id", "?")))
    except Exception:
        reglas = []
    del cfg[familia]
    _save_defuzzy(cfg)
    return jsonify({"ok": True, "actual": cfg, "reglas_afectadas": reglas})


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
    """Vacia las tablas. Ya no repuebla la plantilla del espesador."""
    _save_defuzzy({})
    return jsonify({"ok": True, "actual": {}})


# ============================================================
# Helpers — Fuzzy (membresías)
# ============================================================

FUZZY_VARIABLES    = tuple(FUZZY_MODELOS.keys())
FUZZY_TIPO_POR_VAR = {v: FUZZY_MODELOS[v]["type"] for v in FUZZY_VARIABLES}

# Etiquetas de la PLANTILLA, no un requisito. Cada variable define sus propias
# filas: una planta puede querer CRITICO_ALTO/ALTO/NORMAL/BAJO, o solo dos
# etiquetas. La factory de core/fuzzy/templates.py siempre acepto conjuntos
# arbitrarios; el limite a HIGH/OK/LOW vivia aca y en la UI.
FUZZY_LABEL_KEYS = ("HIGH", "OK", "LOW")

FUZZY_LABEL_RE = _re.compile(r"^[A-Z][A-Z0-9_]*$")
FUZZY_LABEL_MAX = 30

# Nombres que el nucleo genera o interpreta solo: si una fila se llamara asi,
# pisaria una etiqueta derivada y la regla que la nombre no querria decir lo
# que parece.
#   NO-<X>                    -> lo genera expandir_etiquetas_compuestas
#   CERCA_ALTO / CERCA_BAJO   -> idem, desde OK+HIGH y OK+LOW
#   INC / DEC / STABLE        -> etiquetas de pendiente (pend_<var>)
#   ON / OFF                  -> estados de permisivo
FUZZY_LABELS_RESERVADAS = {
    "CERCA_ALTO", "CERCA_BAJO",
    "INC", "DEC", "STABLE",
    "ON", "OFF",
}


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
    """Config difusa. Un objeto vacio es un estado VALIDO (sin fuzzys aun)."""
    if not os.path.exists(FUZZY_JSON):
        cfg = _defaults_fuzzy()
        _save_fuzzy(cfg)
        return cfg
    try:
        with open(FUZZY_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_fuzzy()
    if not isinstance(data, dict):
        return _defaults_fuzzy()
    return data


FUZZY_TIPOS = ("high", "low", "norm")

# Plantilla de arranque para un fuzzy nuevo: tres puntos, triangulo simetrico.
# Es deliberadamente neutra; se calibra despues mirando la traza.
FUZZY_NUEVO = {
    "offset": [0.0, 0.5, 1.0],
    "labels": {"HIGH": [1.0, 0.5, 0.0],
               "OK":   [0.0, 1.0, 0.0],
               "LOW":  [0.0, 0.5, 1.0]},
}


def _validar_fuzzy_spec(var: str, cfg: dict) -> tuple[dict | None, str | None]:
    """Valida un fuzzy suelto: type, offset creciente y labels 0..1 del mismo largo."""
    if not isinstance(cfg, dict):
        return None, f"'{var}': debe ser un objeto con 'type', 'offset' y 'labels'."
    tipo = str(cfg.get("type", "")).lower()
    if tipo not in FUZZY_TIPOS:
        return None, f"'{var}': 'type' invalido '{tipo}'. Validos: {list(FUZZY_TIPOS)}."
    offset = cfg.get("offset")
    if not isinstance(offset, list) or len(offset) < 3:
        return None, f"'{var}': 'offset' debe ser una lista con al menos 3 puntos."
    try:
        offset_f = [float(x) for x in offset]
    except (TypeError, ValueError):
        return None, f"'{var}': 'offset' contiene valores no numericos."
    if any(offset_f[i] >= offset_f[i + 1] for i in range(len(offset_f) - 1)):
        return None, f"'{var}': 'offset' no es estrictamente creciente."

    # --- Etiquetas: nombres libres, una fila cada una ---
    labels = cfg.get("labels")
    if not isinstance(labels, dict):
        return None, f"'{var}': 'labels' debe ser un objeto {{ETIQUETA: [grados]}}."
    if not labels:
        return None, (f"'{var}': hace falta al menos una etiqueta. "
                      "Una variable sin filas no se puede evaluar.")

    labels_norm = {}
    vistas = set()
    for k_raw, arr in labels.items():
        k = str(k_raw).strip().upper()
        if not FUZZY_LABEL_RE.match(k):
            return None, (f"'{var}': nombre de etiqueta invalido '{k_raw}'. "
                          "Usa mayusculas, digitos y guion bajo, empezando por letra "
                          "(ej. ALTO, CRITICO_ALTO).")
        if len(k) > FUZZY_LABEL_MAX:
            return None, (f"'{var}': la etiqueta '{k}' supera "
                          f"{FUZZY_LABEL_MAX} caracteres.")
        if k.startswith("NO_") or k.startswith("NO-"):
            return None, (f"'{var}': '{k}' choca con las etiquetas NO-<X>, que el "
                          "nucleo genera solo para cada etiqueta que definas.")
        if k in FUZZY_LABELS_RESERVADAS:
            return None, (f"'{var}': '{k}' es una etiqueta reservada del nucleo "
                          f"(reservadas: {sorted(FUZZY_LABELS_RESERVADAS)}).")
        if k in vistas:
            return None, f"'{var}': etiqueta duplicada '{k}'."
        vistas.add(k)

        if not isinstance(arr, list) or len(arr) != len(offset_f):
            return None, (f"'{var}.{k}': debe tener {len(offset_f)} valores, "
                          "uno por punto del eje.")
        try:
            arr_f = [float(x) for x in arr]
        except (TypeError, ValueError):
            return None, f"'{var}.{k}': contiene valores no numericos."
        if any(x < 0.0 or x > 1.0 for x in arr_f):
            return None, f"'{var}.{k}': los grados deben estar en [0.0, 1.0]."
        labels_norm[k] = arr_f

    return {"type": tipo, "offset": offset_f, "labels": labels_norm}, None


def etiquetas_usadas_por_reglas() -> dict:
    """{(variable, etiqueta): [ids de regla]} segun reglas.json.

    Es lo que permite negarse a borrar o renombrar una fila que alguna regla
    nombra, en vez de dejarla muerta en silencio.
    """
    from core.engine.motor import pares_de_regla

    uso: dict = {}
    try:
        # `_load_reglas` y no `cargar_reglas_json`: hay que mirar el MISMO
        # archivo que esta API escribe, sin el fallback a las reglas del
        # espesador que aplica el runner cuando reglas.json no existe.
        reglas = _load_reglas()
    except Exception:
        return uso
    for r in reglas or []:
        rid = str(r.get("id", "?"))
        try:
            pares = pares_de_regla(r)
        except Exception:
            continue
        for var, etiqueta in pares:
            uso.setdefault((var, etiqueta), []).append(rid)
    return uso


def _huerfanas_por_guardar(actual: dict, nuevo: dict) -> list[str]:
    """Etiquetas que dejarian reglas muertas si se guardara `nuevo`.

    Una regla que dice ("nivel_hopper", "OK") deja de ser evaluable si el
    fuzzy de nivel_hopper pierde la fila OK — la haya perdido por borrado o
    por renombre, que desde el JSON es lo mismo. Solo se miran variables que
    el payload realmente toca.
    """
    uso = etiquetas_usadas_por_reglas()
    if not uso:
        return []
    problemas = []
    for var, spec in nuevo.items():
        if var not in actual:
            continue
        antes = set((actual.get(var) or {}).get("labels", {}).keys())
        despues = set((spec or {}).get("labels", {}).keys())
        for etiqueta in sorted(antes - despues):
            reglas = uso.get((var, etiqueta)) or []
            # Tambien cuenta el uso via NO-<X>, que es derivada de la etiqueta.
            reglas = reglas + (uso.get((var, f"NO-{etiqueta}")) or [])
            if reglas:
                problemas.append(
                    f"'{var}.{etiqueta}' la usan las reglas: "
                    + ", ".join(sorted(set(reglas))))
    return problemas


def _normalizar_fuzzy_payload(data: dict) -> tuple[dict | None, str | None]:
    """Valida la config difusa completa.

    Las variables ya no se exigen contra la lista del espesador: son las que
    tu definas (identificadores de tags PV). El `type` viaja en el payload,
    porque con variables arbitrarias no hay tipo hardcodeado que consultar.
    Un payload vacio es valido.
    """
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto { <var>: {type, offset, labels} }."

    items, _ = entradas_pv_disponibles()
    validas = {i["identificador"] for i in items} | set(FUZZY_VARIABLES)

    out = {}
    for var, cfg in data.items():
        var_s = str(var).strip()
        if validas and var_s not in validas:
            return None, (f"'{var_s}' no corresponde a ningun tag de categoria PV. "
                          f"Validas: {sorted(validas)}.")
        spec, error = _validar_fuzzy_spec(var_s, cfg)
        if error is not None:
            return None, error
        out[var_s] = spec
    return out, None


_load_fuzzy()


@bp_config.route("/api/fuzzy", methods=["GET"])
def api_get_fuzzy():
    """Config difusa + catalogo de PV, para no escribir nombres a mano."""
    items, _ = entradas_pv_disponibles()
    actual = _load_fuzzy()
    idents = {i["identificador"] for i in items}
    return jsonify({
        "variables": sorted(actual.keys()),
        "pv": items,
        "sin_fuzzy": [i["identificador"] for i in items if i["identificador"] not in actual],
        "huerfanas": [v for v in actual if v not in idents],
        "tipos": list(FUZZY_TIPOS),
        "plantilla": FUZZY_NUEVO,
        # `label_keys` queda como la PLANTILLA para filas nuevas. Las
        # etiquetas reales son por variable: cada fuzzy define las suyas.
        "label_keys": list(FUZZY_LABEL_KEYS),
        "labels_por_variable": {v: list((c or {}).get("labels", {}).keys())
                                for v, c in actual.items()},
        "reservadas": sorted(FUZZY_LABELS_RESERVADAS),
        "uso_en_reglas": {f"{var}|{et}": ids
                          for (var, et), ids in etiquetas_usadas_por_reglas().items()},
        "defaults": _defaults_fuzzy(),
        "actual": actual,
    })


@bp_config.route("/api/fuzzy/<var>", methods=["POST"])
def api_crear_fuzzy(var: str):
    """Crea el fuzzy de una PV con una plantilla neutra y el tipo elegido.

    El tipo define contra que limite se mide el offset:
      high -> lmax - pv   |  low -> pv - lmin  |  norm -> (pv-lmin)/(lmax-lmin)
    """
    body = request.get_json(force=True) or {}
    tipo = str(body.get("type", "norm")).lower()
    if tipo not in FUZZY_TIPOS:
        return jsonify({"error": f"'type' invalido. Validos: {list(FUZZY_TIPOS)}."}), 400

    items, _ = entradas_pv_disponibles()
    if var not in {i["identificador"] for i in items}:
        return jsonify({"error": f"'{var}' no corresponde a ningun tag de categoria PV."}), 400

    cfg = _load_fuzzy()
    if var in cfg:
        return jsonify({"error": f"'{var}' ya tiene fuzzy definido."}), 409

    cfg[var] = {"type": tipo,
                "offset": list(FUZZY_NUEVO["offset"]),
                "labels": {k: list(v) for k, v in FUZZY_NUEVO["labels"].items()}}
    _save_fuzzy(cfg)
    return jsonify({"ok": True, "var": var, "actual": cfg,
                    "aviso": "Creado con una plantilla neutra. Calibralo antes de usarlo."}), 201


@bp_config.route("/api/fuzzy/<var>", methods=["DELETE"])
def api_borrar_fuzzy(var: str):
    """Borra el fuzzy de una variable, informando que reglas la nombran."""
    from core.engine.motor import variables_de_regla
    from runner import cargar_reglas_json

    cfg = _load_fuzzy()
    if var not in cfg:
        return jsonify({"error": f"'{var}' no tiene fuzzy definido."}), 404
    try:
        reglas = [str(r.get("id", "?")) for r in cargar_reglas_json()
                  if var in variables_de_regla(r) or f"pend_{var}" in variables_de_regla(r)]
    except Exception:
        reglas = []
    del cfg[var]
    _save_fuzzy(cfg)
    return jsonify({"ok": True, "actual": cfg, "reglas_afectadas": reglas})


@bp_config.route("/api/fuzzy", methods=["PUT"])
def api_put_fuzzy():
    data = request.get_json(force=True)
    # Se saca ANTES de normalizar: el normalizador rechaza cualquier clave que
    # no sea una variable PV valida.
    forzar = bool(data.pop("__forzar__", False)) if isinstance(data, dict) else False

    norm, error = _normalizar_fuzzy_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400

    # Fallar ruidoso, nunca adivinar: si el guardado dejaria reglas muertas
    # porque desaparece una etiqueta que nombran, no se guarda. `__forzar__`
    # existe para el caso en que se quieran borrar reglas y fuzzy a la vez.
    if not forzar:
        problemas = _huerfanas_por_guardar(_load_fuzzy(), norm)
        if problemas:
            return jsonify({
                "error": ("No se guardo: el cambio dejaria reglas sin la etiqueta "
                          "que nombran. " + " | ".join(problemas)),
                "huerfanas": problemas,
            }), 409

    _save_fuzzy(norm)
    return jsonify({"ok": True, "actual": norm})


@bp_config.route("/api/fuzzy/reset", methods=["POST"])
def api_reset_fuzzy():
    """Vacia la config difusa. Ya no repuebla la plantilla del espesador."""
    _save_fuzzy({})
    return jsonify({"ok": True, "actual": {}})


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
    # Las entradas se eligen del catalogo de PV registradas, asi que llamarse
    # como una PV es lo esperado, no un choque. Lo que si sigue prohibido son
    # los nombres derivados que el nucleo fabrica solo: pend_x, x_lmin, x_lmax
    # y t_s. Si el usuario definiera uno de esos, pisaria un valor calculado.
    _reservados_duros = VAR_NOMBRES_RESERVADOS - set(VARIABLES_PROCESO)
    for nombre, descr in crudas.items():
        if not isinstance(nombre, str) or not VAR_NOMBRE_RE.match(nombre):
            return None, f"entradas: nombre invalido '{nombre}'."
        if nombre in _reservados_duros:
            return None, f"entradas: '{nombre}' choca con un nombre reservado del nucleo."
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


def slug_identificador(texto: str) -> str:
    """Convierte una etiqueta libre en un identificador valido para el nucleo.

    El identificador es lo que ven las reglas, los filtros y los modelos
    difusos. Se genera UNA VEZ al crear la variable y despues queda
    congelado: si siguiera al pseudonimo, cambiar una etiqueta para que se
    lea mejor romperia en silencio todas las reglas que la nombran.
    """
    s = _re.sub(r"[^a-z0-9]+", "_", (texto or "").strip().lower()).strip("_")
    if not s:
        return ""
    if s[0].isdigit():
        s = "v_" + s
    return s[:49]


def entradas_pv_disponibles() -> tuple[list[dict], dict]:
    """Tags de categoria PV, con su identificador derivado del pseudonimo.

    Devuelve (items, colisiones). Dos senales distintas con el mismo
    identificador se fusionarian silenciosamente en una sola variable, asi
    que las colisiones se reportan para avisar antes de elegir.
    """
    from web.state import _load_tags

    items, vistos = [], {}
    for t in _load_tags().get("tags", []):
        if t.get("categoria") != "pv" or not t.get("enabled", True):
            continue
        nombre = t["name"]
        ultimo = nombre.split(".")[-1]
        pseudo = (t.get("pseudonimo") or "").strip() or ultimo
        ident = slug_identificador(pseudo) or slug_identificador(ultimo)
        vistos.setdefault(ident, []).append(nombre)
        items.append({
            "tag": nombre,
            "pseudonimo": pseudo,
            "unidad": t.get("unidad_ing", ""),
            "equipo": t.get("equipo", ""),
            "identificador": ident,
        })

    colisiones = {k: v for k, v in vistos.items() if len(v) > 1}
    for it in items:
        it["colision"] = it["identificador"] in colisiones
    return items, colisiones


@bp_config.route("/api/variables/entradas-disponibles", methods=["GET"])
def api_entradas_disponibles():
    """Tags de categoria PV que pueden usarse como entrada del SE."""
    items, colisiones = entradas_pv_disponibles()
    ya_usados = set((_load_variables().get("crudas") or {}).keys())
    for it in items:
        it["ya_usado"] = it["identificador"] in ya_usados
    return jsonify({"entradas": items, "colisiones": colisiones})


@bp_config.route("/api/variables/renombrar", methods=["POST"])
def api_renombrar_variable():
    """Renombra el identificador de una entrada, con reporte de impacto.

    Renombrar SI tiene consecuencias (a diferencia de editar el pseudonimo),
    asi que es una accion aparte y exige confirmacion explicita.
    """
    from core.engine.motor import variables_de_regla
    from runner import cargar_reglas_json

    body = request.get_json(force=True) or {}
    viejo = str(body.get("viejo") or "").strip()
    nuevo = slug_identificador(body.get("nuevo") or "")

    cfg = _load_variables()
    crudas = cfg.get("crudas") or {}
    if viejo not in crudas:
        return jsonify({"error": f"'{viejo}' no existe entre las entradas."}), 404
    if not nuevo:
        return jsonify({"error": "El nuevo identificador es invalido."}), 400
    if nuevo != viejo and nuevo in crudas:
        return jsonify({"error": f"'{nuevo}' ya esta en uso."}), 409
    if nuevo in VAR_NOMBRES_RESERVADOS - set(VARIABLES_PROCESO):
        return jsonify({"error": f"'{nuevo}' es un nombre reservado del nucleo."}), 400

    # Quien lo referencia hoy
    defs_afectadas = []
    for d in cfg.get("definiciones", []) or []:
        refs = set(d.get("args") or [])
        if d.get("arg"):
            refs.add(d["arg"])
        if viejo in refs:
            defs_afectadas.append(d.get("nombre", "?"))
    try:
        reglas_afectadas = [
            str(r.get("id", "?")) for r in cargar_reglas_json()
            if viejo in variables_de_regla(r) or f"pend_{viejo}" in variables_de_regla(r)
        ]
    except Exception:
        reglas_afectadas = []

    impacto = {
        "definiciones": defs_afectadas,
        "reglas": reglas_afectadas,
        "requiere_confirmacion": bool(defs_afectadas or reglas_afectadas),
    }
    if not body.get("confirmar"):
        return jsonify({"impacto": impacto, "aplicado": False,
                        "aviso": "Reenvia con confirmar=true para aplicar."}), 200

    # Aplicar: se renombra en las entradas y en las definiciones. Las REGLAS
    # no se reescriben solas -- tocar condiciones de reglas automaticamente es
    # demasiado riesgo; se reportan para que las ajustes tu.
    nuevas = {(nuevo if k == viejo else k): v for k, v in crudas.items()}
    for d in cfg.get("definiciones", []) or []:
        if d.get("args"):
            d["args"] = [nuevo if a == viejo else a for a in d["args"]]
        if d.get("arg") == viejo:
            d["arg"] = nuevo
    cfg["crudas"] = nuevas
    _save_variables(cfg)

    return jsonify({
        "aplicado": True, "impacto": impacto, "nuevo": nuevo,
        "aviso": ("Renombrado. Las definiciones se actualizaron solas; "
                  "las reglas listadas hay que ajustarlas a mano.")
        if reglas_afectadas else "Renombrado.",
    })


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
    """Sin plantilla: las familias se sincronizan desde los tags de categoria SP.

    Igual que en defuzzy, las familias del espesador antiguo se sembraban
    solas al faltar el archivo y aparecian como huerfanas en la pagina.
    """
    return {}


def _save_tracking(cfg: dict) -> None:
    with open(TRACKING_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _load_tracking() -> dict:
    """Config de tracking. Un objeto vacio es un estado VALIDO (sin familias)."""
    if not os.path.exists(TRACKING_JSON):
        cfg = _defaults_tracking()
        _save_tracking(cfg)
        return cfg
    try:
        with open(TRACKING_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _defaults_tracking()
    if not isinstance(data, dict):
        return _defaults_tracking()
    return data


def salidas_sp_disponibles() -> list[dict]:
    """Tags de categoria SP, con su identificador derivado del pseudonimo.

    Mismo criterio que las entradas PV: el identificador sale del pseudonimo
    (o del ultimo segmento del tag) y es lo que ven el motor y el defuzzy.
    """
    from web.state import _load_tags

    items, vistos = [], {}
    for t in _load_tags().get("tags", []):
        if t.get("categoria") != "sp" or not t.get("enabled", True):
            continue
        nombre = t["name"]
        ultimo = nombre.split(".")[-1]
        pseudo = (t.get("pseudonimo") or "").strip() or ultimo
        ident = slug_identificador(pseudo) or slug_identificador(ultimo)
        vistos.setdefault(ident, []).append(nombre)
        items.append({
            "identificador": ident,
            "tag": nombre,
            "pseudonimo": pseudo,
            "unidad": t.get("unidad_ing", ""),
            "equipo": t.get("equipo", ""),
        })
    colisiones = {k for k, v in vistos.items() if len(v) > 1}
    for it in items:
        it["colision"] = it["identificador"] in colisiones
    return items


def _normalizar_tracking_payload(data: dict) -> tuple[dict | None, str | None]:
    """Valida la config de tracking.

    Las familias ya no se exigen contra una lista fija: salen de los tags de
    categoria SP, que cambian por cliente. Un payload vacio es valido (sin
    tracking configurado todavia).
    """
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto { <familia>: {pv_key, rango, habilitado} }."

    validas = {s["identificador"] for s in salidas_sp_disponibles()} | set(SETPOINT_KEYS)
    out = {}
    for familia, spec in data.items():
        if not VAR_NOMBRE_RE.match(str(familia)):
            return None, f"'{familia}': identificador de familia invalido."
        if validas and familia not in validas:
            return None, (f"'{familia}' no corresponde a ningun tag de categoria SP. "
                          f"Validas: {sorted(validas)}.")
        if not isinstance(spec, dict):
            return None, f"'{familia}': debe ser un objeto con pv_key, rango y habilitado."
        # pv_key vacio = sin readback: el tracking no bloquea esa familia.
        pv_key = str(spec.get("pv_key") or "").strip()
        try:
            rango = float(spec.get("rango"))
        except (TypeError, ValueError):
            return None, f"'{familia}': 'rango' debe ser numerico."
        if rango < 0.0:
            return None, f"'{familia}': 'rango' debe ser >= 0 (valor recibido: {rango})."
        if pv_key and rango == 0.0:
            return None, (f"'{familia}': con readback definido, 'rango' 0 bloquearia la "
                          "familia para siempre. Usa un rango > 0 o quita el readback.")
        out[familia] = {"pv_key": pv_key, "rango": rango,
                        "habilitado": bool(spec.get("habilitado", True))}
    return out, None


_load_tracking()


@bp_config.route("/api/tracking", methods=["GET"])
def api_get_tracking():
    """Config de tracking + catalogos para no escribir nada a mano.

    Las familias se sincronizan con los tags de categoria SP, y el readback
    se elige entre las entradas PV ya declaradas en el catalogo de variables.
    """
    salidas = salidas_sp_disponibles()
    entradas_pv = list((_load_variables().get("crudas") or {}).keys())
    actual = _load_tracking()

    idents = {s["identificador"] for s in salidas}
    huerfanas = [f for f in actual if f not in idents]

    return jsonify({
        "familias": [s["identificador"] for s in salidas],
        "salidas": salidas,
        "entradas_pv": entradas_pv,
        "huerfanas": huerfanas,
        "en_contrato": list(SETPOINT_KEYS),
        "defaults": _defaults_tracking(),
        "actual": actual,
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
    """Vacia el tracking. Ya no repuebla la plantilla del espesador."""
    _save_tracking({})
    return jsonify({"ok": True, "actual": {}})


@bp_config.route("/api/tracking/sincronizar", methods=["POST"])
def api_sincronizar_tracking():
    """Alinea las familias con los tags SP: agrega las que faltan, saca huerfanas.

    Lo que ya estaba configurado se respeta; las nuevas entran sin readback
    y deshabilitadas, para que nadie quede bloqueado por una config a medias.
    """
    actual = _load_tracking()
    salidas = salidas_sp_disponibles()
    idents = [s["identificador"] for s in salidas]

    agregadas = [i for i in idents if i not in actual]
    quitadas = [f for f in actual if f not in idents]

    nuevo = {f: v for f, v in actual.items() if f in idents}
    for i in agregadas:
        nuevo[i] = {"pv_key": "", "rango": 0.0, "habilitado": False}

    _save_tracking(nuevo)
    return jsonify({"ok": True, "actual": nuevo,
                    "agregadas": agregadas, "quitadas": quitadas})
