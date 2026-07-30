# -*- coding: utf-8 -*-
"""Blueprint bp_se — Alerts, SE Engine, Entrada de datos, Simulación.

Rutas:
  GET                  /api/alerts
  POST                 /api/alerts/<alert_id>/resolve
  POST                 /api/alerts/clear
  GET                  /api/se/status
  POST                 /api/se/start
  POST                 /api/se/stop
  GET                  /api/entrada
  GET                  /api/entrada/history
  POST                 /api/simulacion
  POST                 /api/simulacion/start
  GET                  /api/simulacion/next
  POST                 /api/simulacion/reset

IT-7: extraído de app.py.
"""
from __future__ import annotations

import traceback

from flask import Blueprint, jsonify, request

from config import SETPOINT_KEYS, VARIABLES_PROCESO, COLUMNAS_ENTRADA
from web.state import (
    AlertCollector,
    _alerts,
    _se_engine,
    _load_tags,
    _read_kepserver_tags_batch,
    _get_tag_history,
    _sim_state,
    _definiciones_lista_a_dict,
)

bp_se = Blueprint("se", __name__)


# ============================================================
# API — Alertas
# ============================================================

@bp_se.route("/api/alerts", methods=["GET"])
def api_alerts():
    show = request.args.get("show", "active")
    if show == "all":
        return jsonify({"alerts": _alerts.get_all(), "categories": AlertCollector.CATEGORIES})
    return jsonify({"alerts": _alerts.get_active(), "categories": AlertCollector.CATEGORIES})


@bp_se.route("/api/alerts/<int:alert_id>/resolve", methods=["POST"])
def api_resolve_alert(alert_id: int):
    ok = _alerts.resolve(alert_id)
    return jsonify({"ok": ok})


@bp_se.route("/api/alerts/clear", methods=["POST"])
def api_clear_alerts():
    _alerts.clear_resolved()
    return jsonify({"ok": True})


# ============================================================
# API — SE Engine
# ============================================================

@bp_se.route("/api/se/status", methods=["GET"])
def api_se_status():
    return jsonify(_se_engine.status())


@bp_se.route("/api/se/start", methods=["POST"])
def api_se_start():
    body = request.get_json(force=True) if request.content_length else {}
    intervalo = float(body.get("intervalo_s", 5.0)) if body else 5.0
    _se_engine.start(intervalo_s=intervalo)
    return jsonify({"ok": True, "running": True})


@bp_se.route("/api/se/stop", methods=["POST"])
def api_se_stop():
    _se_engine.stop()
    return jsonify({"ok": True, "running": False})


# ============================================================
# API — Entrada de Datos (all tags + history)
# ============================================================

@bp_se.route("/api/entrada", methods=["GET"])
def api_entrada():
    store = _load_tags()
    tags = store.get("tags", [])
    enabled_names = [t["name"] for t in tags if t.get("enabled", True)]

    live = _read_kepserver_tags_batch(enabled_names) if enabled_names else {}
    hist = _get_tag_history()

    result = []
    for t in tags:
        name = t["name"]
        is_enabled = t.get("enabled", True)
        info = live.get(name, {}) if is_enabled else {}
        direction = "output" if name.startswith("RETO.SP.") else "input"
        if name.startswith("RETO.PV."):
            group = "pv"
        elif name.startswith("RETO.CRUDA."):
            group = "cruda"
        elif name.startswith("RETO.LIM."):
            group = "lim"
        elif name.startswith("RETO.SP."):
            group = "sp"
        else:
            group = "other"
        result.append({
            "id":          t["id"],
            "name":        name,
            "value":       info.get("value"),
            "exists":      info.get("exists", False),
            "direction":   direction,
            "group":       group,
            "enabled":     is_enabled,
            "processes":   t.get("processes", ["espesadores"]),
            "pseudonimo":  t.get("pseudonimo", ""),
            "instrumento": t.get("instrumento", ""),
            "unidad_ing":  t.get("unidad_ing", ""),
            "equipo":      t.get("equipo", ""),
            "history":     hist.get(name, []),
        })
    return jsonify(result)


@bp_se.route("/api/entrada/history", methods=["GET"])
def api_entrada_history():
    return jsonify(_get_tag_history())


# ============================================================
# Simulación — helper compartido
# ============================================================

def _ejecutar_simulacion(params: dict) -> dict:
    from runner import (
        correr_prueba_general,
        cargar_reglas_json,
        cargar_filtros_json,
        cargar_defuzzy_json,
        cargar_fuzzy_json,
        cargar_variables_json,
        cargar_permisivos_json,
    )
    from simulacion import LIMITES_SP, SETPOINTS_BASE, generar_datos_proceso
    import defuzzy_actions as _dfz_mod
    import fuzzys_models_espesador as _fz_mod
    from core.fuzzy import templates as _fz_tpl

    n_muestras = int(params.get("n_muestras", 240))
    dt_s       = float(params.get("dt_s", 60.0))
    seed       = int(params.get("seed", 42))

    df_data      = generar_datos_proceso(n_muestras=n_muestras, dt_s=dt_s, seed=seed)
    reglas       = cargar_reglas_json()
    config_filtro = cargar_filtros_json()

    defuzzy_cfg = cargar_defuzzy_json()
    _dfz_mod.DEFUZZY_POR_FAMILIA.clear()
    _dfz_mod.DEFUZZY_POR_FAMILIA.update(defuzzy_cfg)

    fuzzy_cfg = cargar_fuzzy_json()
    _factories = {
        "high": _fz_tpl.crear_clase_fuzzy_high,
        "low":  _fz_tpl.crear_clase_fuzzy_Low,
        "norm": _fz_tpl.crear_clase_fuzzy_norm,
    }
    for _var, _cfg in fuzzy_cfg.items():
        if _var not in _fz_mod.FUZZY_MODELOS:
            continue
        _tipo = _fz_mod.FUZZY_MODELOS[_var]["type"]
        _factory = _factories.get(_tipo)
        if _factory is None:
            continue
        _Klass = _factory(
            f"{_var}_dyn",
            list(_cfg["offset"]),
            HIGH=list(_cfg["labels"]["HIGH"]),
            OK=list(_cfg["labels"]["OK"]),
            LOW=list(_cfg["labels"]["LOW"]),
        )
        _fz_mod.FUZZY_MODELOS[_var] = {"type": _tipo, "model": _Klass()}

    vars_cfg         = cargar_variables_json()
    definiciones_cfg = _definiciones_lista_a_dict(vars_cfg.get("definiciones", []))
    permisivos_cfg   = cargar_permisivos_json()

    resultados = correr_prueba_general(
        df_data=df_data,
        reglas=reglas,
        setpoints_base=SETPOINTS_BASE,
        limites_sp=LIMITES_SP,
        min_belief=0.05,
        verbose=False,
        calcular_vars=True,
        dt_s=dt_s,
        config_filtro=config_filtro,
        definiciones_calculadas=definiciones_cfg,
        permisivos_config=permisivos_cfg,
    )
    return resultados


# ============================================================
# API — Simulación (síncrona)
# ============================================================

@bp_se.route("/api/simulacion", methods=["POST"])
def api_simulacion():
    try:
        params = request.get_json(silent=True) or {}
        resultados = _ejecutar_simulacion(params)

        df_res = resultados["resultados"]
        df_ev  = resultados["eventos"]

        sp_final = {}
        if not df_res.empty:
            ultima = df_res.iloc[-1]
            for sp in SETPOINT_KEYS:
                if sp in df_res.columns:
                    sp_final[sp] = round(float(ultima[sp]), 3)

        eventos_list = []
        if not df_ev.empty:
            for _, row in df_ev.iterrows():
                eventos_list.append({
                    "t_s":      round(float(row["t_s"]), 1),
                    "regla_id": str(row["regla_id"]),
                    "bloque":   str(row.get("bloque", "")),
                    "acciones": str(row.get("acciones", "")),
                    "belief":   round(float(row["belief"]), 4),
                })

        activaciones_por_regla = {}
        activaciones_por_bloque = {}
        if not df_ev.empty:
            activaciones_por_regla = {
                str(k): int(v) for k, v in df_ev["regla_id"].value_counts().to_dict().items()
            }
            if "bloque" in df_ev.columns:
                activaciones_por_bloque = {
                    str(k): int(v) for k, v in df_ev["bloque"].value_counts().to_dict().items()
                }

        return jsonify({
            "ok":                      True,
            "muestras":                len(df_res),
            "total_eventos":           len(df_ev),
            "setpoints_finales":       sp_final,
            "activaciones_por_regla":  activaciones_por_regla,
            "activaciones_por_bloque": activaciones_por_bloque,
            "eventos":                 eventos_list[:50],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ============================================================
# API — Simulación streaming
# ============================================================

@bp_se.route("/api/simulacion/start", methods=["POST"])
def api_sim_start():
    try:
        params = request.get_json(silent=True) or {}
        resultados = _ejecutar_simulacion(params)
        _sim_state["df_resultados"] = resultados["resultados"]
        _sim_state["df_eventos"]    = resultados["eventos"]
        _sim_state["cursor"]        = 0
        _sim_state["running"]       = True
        _sim_state["batch_size"]    = int(params.get("batch_size", 5))
        return jsonify({"ok": True, "total": len(resultados["resultados"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@bp_se.route("/api/simulacion/next", methods=["GET"])
def api_sim_next():
    if not _sim_state["running"] or _sim_state["df_resultados"] is None:
        return jsonify({"ok": False, "error": "No hay simulacion activa."}), 400

    df     = _sim_state["df_resultados"]
    cursor = _sim_state["cursor"]
    batch  = _sim_state["batch_size"]
    total  = len(df)

    if cursor >= total:
        _sim_state["running"] = False
        return jsonify({"ok": True, "done": True, "points": [], "eventos": [],
                        "cursor": cursor, "total": total})

    end   = min(cursor + batch, total)
    chunk = df.iloc[cursor:end]
    pv_keys = list(VARIABLES_PROCESO)

    points = []
    for _, row in chunk.iterrows():
        pt = {
            "t_s":      round(float(row["t_s"]), 1),
            "t_min":    round(float(row["t_min"]), 2),
            "n_reglas": int(row.get("n_reglas_activadas", 0)),
            "reglas":   str(row.get("reglas_activadas", "")),
        }
        for var in pv_keys:
            if var in df.columns:
                pt[var] = round(float(row[var]), 3)
        for sp in SETPOINT_KEYS:
            if sp in df.columns:
                pt[sp] = round(float(row[sp]), 3)
        points.append(pt)

    df_ev  = _sim_state["df_eventos"]
    ev_list = []
    if df_ev is not None and not df_ev.empty:
        t_start = float(chunk.iloc[0]["t_s"])
        t_end   = float(chunk.iloc[-1]["t_s"])
        mask    = (df_ev["t_s"] >= t_start) & (df_ev["t_s"] <= t_end)
        for _, row in df_ev[mask].iterrows():
            ev_list.append({
                "t_s":      round(float(row["t_s"]), 1),
                "regla_id": str(row["regla_id"]),
                "bloque":   str(row.get("bloque", "")),
                "acciones": str(row.get("acciones", "")),
                "belief":   round(float(row["belief"]), 4),
            })

    _sim_state["cursor"] = end
    return jsonify({"ok": True, "done": False, "points": points, "eventos": ev_list,
                    "cursor": end, "total": total})


@bp_se.route("/api/simulacion/reset", methods=["POST"])
def api_sim_reset():
    _sim_state["cursor"]  = 0
    _sim_state["running"] = _sim_state["df_resultados"] is not None
    return jsonify({"ok": True})
