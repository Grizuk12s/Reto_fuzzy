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
    _get_trazas,
    grabador_start, grabador_stop, grabador_estado, grabador_historial,
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
    """Arranca el motor en ciclo libre.

    `piso_s` es el periodo MINIMO entre ticks: el motor vuelve a iterar apenas
    termina de escribir los setpoints, y el piso solo evita saturar CPU y
    KEPserver. `intervalo_s` se sigue aceptando como alias historico, de
    cuando el lazo tenia periodo fijo.
    """
    body = request.get_json(force=True) if request.content_length else {}
    body = body or {}
    if "piso_s" in body:
        piso = float(body["piso_s"])
    elif "intervalo_s" in body:
        piso = float(body["intervalo_s"])
    else:
        piso = _se_engine.PISO_S_DEFAULT
    res = _se_engine.start(piso_s=piso) or {"ok": True, "error": None}
    if not res.get("ok"):
        # Mapeo incompleto: no se arranca. El detalle va al front.
        return jsonify({"ok": False, "running": False, "error": res.get("error")}), 400
    return jsonify({"ok": True, "running": True})


@bp_se.route("/api/se/trace", methods=["GET"])
def api_se_trace():
    """Traza del pipeline: que paso en cada etapa del ultimo (o ultimos N) ticks.

    Query params:
      - n: cuantos ticks devolver (por defecto 1, el mas reciente)
    """
    try:
        n = int(request.args.get("n", 1))
    except ValueError:
        n = 1
    trazas = _get_trazas(n)
    estado = _se_engine.status()
    return jsonify({
        "trazas": trazas,
        "running": estado.get("running", False),
        "disponibles": len(_get_trazas(0)),
        # El anillo de trazas guarda pocos segundos en ciclo libre; el
        # historial de disparos sobrevive y es lo que permite ver que una
        # regla con wait largo si esta actuando.
        "ultimos_disparos": estado.get("ultimos_disparos", []),
    })


# ============================================================
# API — Grabadores por regla
#
# Seguir UNA regla durante minutos: la traza es un anillo de 60 ticks y no
# alcanza para ver por que una regla no actua. Se pueden grabar varias reglas
# a la vez; la pagina de historial muestra una.
# ============================================================

@bp_se.route("/api/se/grabador", methods=["GET"])
def api_grabador_estado():
    return jsonify(grabador_estado(request.args.get("regla")))


@bp_se.route("/api/se/grabador/<path:regla_id>/start", methods=["POST"])
def api_grabador_start(regla_id: str):
    """Arranca la grabacion. Vacia lo anterior: 'desde que pulso el boton'."""
    return jsonify(grabador_start(regla_id))


@bp_se.route("/api/se/grabador/<path:regla_id>/stop", methods=["POST"])
def api_grabador_stop(regla_id: str):
    """Detiene la grabacion; lo grabado se conserva para poder mirarlo."""
    return jsonify(grabador_stop(regla_id))


@bp_se.route("/api/se/grabador/<path:regla_id>", methods=["GET"])
def api_grabador_historial(regla_id: str):
    return jsonify(grabador_historial(regla_id))


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
        # El grupo sale de la categoria elegida a mano, NO del prefijo del
        # nombre. Deducirlo de `RETO.PV.` / `RETO.SP.` / ... solo funcionaba con
        # la nomenclatura del prototipo: con nombres de planta (PCS7.OS01.*)
        # ningun tag matcheaba y los 27 caian en "other", asi que los filtros
        # PV / SP / LIM del Explorador de Series no filtraban nada.
        categoria = (t.get("categoria") or "").strip().lower()
        group = categoria if categoria in ("pv", "cruda", "lim", "sp") else "other"
        direction = "output" if group == "sp" else "input"
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

def _rango_por_rol(mapeo: dict, ranges: dict) -> dict:
    """Rangos del generador de tags, reindexados por ROL del SE.

    El generador guarda min/max/ruido por TAG (asi los edita el operador en
    Tags KEPserver); el pipeline razona en roles del contrato. El puente es el
    mapeo tag->rol que ya usa el motor en vivo.
    """
    out = {}
    for tag, rol in (mapeo.get("tag_to_pv") or {}).items():
        if tag in ranges:
            out[rol] = ranges[tag]
    for tag, rol in (mapeo.get("tag_to_cruda") or {}).items():
        if tag in ranges:
            out[rol] = ranges[tag]
    for tag, (var, bound) in (mapeo.get("tag_to_lim") or {}).items():
        if tag in ranges:
            out[f"{var}_{bound}"] = ranges[tag]
    for rol, tag in (mapeo.get("sp_to_tag") or {}).items():
        if tag in ranges:
            out[rol] = ranges[tag]
    return out


def _serie_tres_fases(n: int, n_ciclo: int, vmin: float, vmax: float,
                      noise: float, rng) -> list[float]:
    """Misma curva 3-fases del generador en vivo, pero con ruido reproducible.

    El generador usa `random.gauss`; aca el ruido sale de un RNG sembrado con
    `seed` para que dos corridas con la misma semilla den el mismo resultado,
    que es lo que hace comparable una simulacion.
    """
    vmin, vmax = float(vmin), float(vmax)
    n_ciclo = max(1, int(n_ciclo))
    vals = []
    for tick in range(n):
        pos = (tick % n_ciclo) / n_ciclo
        if pos < 0.35:
            base = vmin + (vmax - vmin) * 0.2
        elif pos < 0.70:
            base = vmin + (vmax - vmin) * (0.2 + 0.7 * (pos - 0.35) / 0.35)
        else:
            base = vmin + (vmax - vmin) * (0.9 - 0.5 * (pos - 0.70) / 0.30)
        val = base + (float(rng.normal(0.0, float(noise))) if noise else 0.0)
        vals.append(max(vmin, min(vmax, val)))
    return vals


def _datos_desde_generador(n_muestras: int, dt_s: float, seed: int) -> tuple:
    """DataFrame sintetico armado desde el CONTRATO y los rangos del generador.

    Antes esto lo hacia `simulacion.generar_datos_proceso()`, que tiene las
    columnas del espesador original cableadas (torque, bed_level, densidad...).
    Con cualquier otro contrato la simulacion moria con un KeyError del nombre
    de la primera PV — no habia forma de probar reglas de un cliente nuevo.

    Ahora las columnas salen de VARIABLES_PROCESO / crudas / SP del contrato, y
    los valores de los mismos min/max/ruido que el operador ya configuro en
    Tags KEPserver para el generador en vivo. Una sola fuente de verdad.

    Devuelve (df, avisos, setpoints_base).
    """
    import numpy as np
    import pandas as pd
    from config import (VARIABLES_PROCESO, VARIABLES_CRUDAS_REQUERIDAS,
                        SETPOINT_KEYS, LIMITES_SP_CONTRATO)
    from web.state import construir_mapeo, _tag_generator

    if not VARIABLES_PROCESO:
        raise ValueError("El contrato no tiene variables de proceso: "
                         "define al menos una PV en Contrato de Variables.")

    estado = _tag_generator.status()
    ranges = estado.get("ranges") or {}
    n_ciclo = int(estado.get("n_ciclo") or 60)
    rangos = _rango_por_rol(construir_mapeo(), ranges)

    rng = np.random.default_rng(int(seed))
    n = int(n_muestras)
    avisos = []
    cols = {"t_s": np.arange(0, n * float(dt_s), float(dt_s), dtype=float)[:n]}

    def _mm(rol, defecto):
        """min/max/ruido del rol, o un default explicado en los avisos."""
        r = rangos.get(rol)
        if not r:
            avisos.append(f"'{rol}' no tiene rango en el generador de tags: "
                          f"se simula en {defecto[0]}-{defecto[1]}.")
            return defecto[0], defecto[1], defecto[2]
        return float(r.get("min", 0.0)), float(r.get("max", 0.0)), float(r.get("noise", 0.0))

    # --- Limites primero: acotan el rango en el que conviene mover la PV ---
    lim_vals = {}
    for var in VARIABLES_PROCESO:
        for bound, defecto in (("lmin", (0.0, 0.0, 0.0)), ("lmax", (100.0, 100.0, 0.0))):
            rol = f"{var}_{bound}"
            vmin, vmax, _ = _mm(rol, defecto)
            # Un limite es una constante: si min y max difieren, se toma el medio.
            lim_vals[rol] = (vmin + vmax) / 2.0
            cols[rol] = np.full(n, lim_vals[rol], dtype=float)

    # --- PV: la curva 3-fases entre su min y su max ---
    for var in VARIABLES_PROCESO:
        lo, hi = lim_vals[f"{var}_lmin"], lim_vals[f"{var}_lmax"]
        vmin, vmax, noise = _mm(var, (lo, hi if hi > lo else lo + 100.0, 1.0))
        if vmax <= vmin:
            vmax = vmin + 1.0
        cols[var] = np.array(_serie_tres_fases(n, n_ciclo, vmin, vmax, noise, rng))

    for var in VARIABLES_CRUDAS_REQUERIDAS:
        vmin, vmax, noise = _mm(var, (0.0, 100.0, 1.0))
        if vmax <= vmin:
            vmax = vmin + 1.0
        cols[var] = np.array(_serie_tres_fases(n, n_ciclo, vmin, vmax, noise, rng))

    # --- Setpoints: arrancan donde el generador los tiene y los mueve el SE ---
    setpoints_base = {}
    for sp in SETPOINT_KEYS:
        lims = LIMITES_SP_CONTRATO.get(sp) or ()
        defecto = (float(lims[0]), float(lims[1]), 0.0) if len(lims) == 2 else (0.0, 100.0, 0.0)
        vmin, vmax, _ = _mm(sp, defecto)
        setpoints_base[sp] = (vmin + vmax) / 2.0
        cols[sp] = np.full(n, setpoints_base[sp], dtype=float)

    return pd.DataFrame(cols), avisos, setpoints_base


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
    from config import (VARIABLES_PROCESO, COLUMNAS_ENTRADA, SETPOINT_KEYS,
                        LIMITES_SP_CONTRATO)
    from core.fuzzy.templates import construir_registry_fuzzy

    n_muestras = int(params.get("n_muestras", 240))
    dt_s       = float(params.get("dt_s", 60.0))
    seed       = int(params.get("seed", 42))

    df_data, avisos, setpoints_base = _datos_desde_generador(n_muestras, dt_s, seed)

    limites_sp = {sp: tuple(LIMITES_SP_CONTRATO[sp]) for sp in SETPOINT_KEYS
                  if sp in LIMITES_SP_CONTRATO}
    faltan_lim = [sp for sp in SETPOINT_KEYS if sp not in limites_sp]
    if faltan_lim:
        raise ValueError("Setpoints sin limites en el contrato (el defuzzy no "
                         "sabria hasta donde moverlos): " + ", ".join(faltan_lim))

    reglas        = cargar_reglas_json()
    config_filtro = cargar_filtros_json()

    # Defuzzy: las tablas vigentes, pasadas explicitamente. Antes esto hacia
    # clear()+update() sobre el dict global de defuzzy_actions — el MISMO que
    # usaba el motor en vivo en otro hilo. Simular cambiaba el comportamiento
    # del SE en produccion, y entre el clear y el update habia una ventana en
    # la que el motor veia la tabla vacia.
    defuzzy_cfg = cargar_defuzzy_json()

    # Fuzzy: se construye el registry igual que el motor en vivo. La version
    # anterior parcheaba FUZZY_MODELOS del espesador exigiendo filas llamadas
    # HIGH/OK/LOW — con etiquetas de nombre libre reventaba con KeyError.
    fuzzy_modelos = construir_registry_fuzzy(cargar_fuzzy_json()) or {}
    sin_fuzzy = [v for v in VARIABLES_PROCESO if v not in fuzzy_modelos]
    if sin_fuzzy:
        avisos.append("PV sin modelo difuso (no se fuzzifican, y las reglas que "
                      "las nombran no pueden disparar): " + ", ".join(sin_fuzzy))

    vars_cfg         = cargar_variables_json()
    definiciones_cfg = _definiciones_lista_a_dict(vars_cfg.get("definiciones", []))
    permisivos_cfg   = cargar_permisivos_json()

    resultados = correr_prueba_general(
        df_data=df_data,
        reglas=reglas,
        setpoints_base=setpoints_base,
        limites_sp=limites_sp,
        min_belief=0.05,
        verbose=False,
        calcular_vars=True,
        dt_s=dt_s,
        columnas_entrada=COLUMNAS_ENTRADA,
        config_filtro=config_filtro,
        definiciones_calculadas=definiciones_cfg,
        permisivos_config=permisivos_cfg,
        fuzzy_modelos=fuzzy_modelos,
        # Vacio a proposito: PEND_MODELOS sigue cableado al espesador viejo.
        pend_modelos={},
        variables_proceso=list(VARIABLES_PROCESO),
        defuzzy_por_familia=defuzzy_cfg,
    )
    resultados["avisos"] = avisos
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
            # Lo que se simulo con defaults o quedo sin fuzzificar: sin esto,
            # una corrida con 0 eventos parece un problema de las reglas.
            "avisos":                  resultados.get("avisos", []),
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
