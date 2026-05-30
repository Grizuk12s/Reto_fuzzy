# -*- coding: utf-8 -*-
"""Interfaz web Flask del sistema experto Espesador (v2) -- Prototipo_2.

Endpoints API:
  GET    /api/meta                 -- Catalogos (variables, etiquetas, acciones,
                                      bloques, permisivos) para construir la UI.
  GET    /api/reglas               -- Listar todas las reglas de reglas.json.
  GET    /api/reglas/<id>          -- Obtener una regla.
  POST   /api/reglas               -- Crear una regla nueva.
  PUT    /api/reglas/<id>          -- Actualizar una regla existente.
  DELETE /api/reglas/<id>          -- Eliminar una regla.
  GET    /api/filtros              -- Config Exp-Q vigente (filtros.json).
  PUT    /api/filtros              -- Reemplazar la config Exp-Q completa.
  POST   /api/filtros/reset        -- Restaurar defaults de core.
  GET    /api/defuzzy              -- Tablas Sugeno vigentes (defuzzy.json).
  PUT    /api/defuzzy              -- Reemplazar tablas defuzzy (1 o varias familias).
  POST   /api/defuzzy/reset        -- Restaurar defaults de core.
  GET    /api/fuzzy                -- Membresias fuzzy vigentes (fuzzy.json).
  PUT    /api/fuzzy                -- Reemplazar membresias (1 o varias variables).
  POST   /api/fuzzy/reset          -- Restaurar defaults de core.
  GET    /api/variables            -- Variables crudas + definiciones calculadas vigentes.
  PUT    /api/variables            -- Reemplazar el catalogo de variables (full payload).
  POST   /api/variables/reset      -- Restaurar defaults de core.
  GET    /api/permisivos           -- Permisivos vigentes (permisivos.json).
  PUT    /api/permisivos           -- Reemplazar el catalogo completo de permisivos.
  POST   /api/permisivos/reset     -- Restaurar defaults de core.
  POST   /api/simulacion           -- Ejecutar simulacion completa (sincrona).
  POST   /api/simulacion/start     -- Inicializar streaming.
  GET    /api/simulacion/next      -- Devolver siguiente lote (streaming).
  POST   /api/simulacion/reset     -- Reiniciar streaming.

Interfaz:
  GET    /          -- Editor de reglas + simulacion.
  GET    /graficos  -- Graficos en tiempo real (PV + SPs del Espesador).
"""

from __future__ import annotations

import json
import os
import traceback

from flask import Flask, Response, jsonify, request

from config import (
    SETPOINT_KEYS,
    VARIABLES_CRUDAS_REQUERIDAS,
    VARIABLES_EXTERNAS,
    VARIABLES_PROCESO,
)
from defuzzy_actions import DEFUZZY_POR_FAMILIA
from exp_q_filter import CONFIG_FILTRO_ESPESADOR_DEFAULT
from fuzzys_models_espesador import FUZZY_MODELOS
from calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS
from permisivos import PERMISIVOS, nombre_variable_permisivo

app = Flask(__name__)

REGLAS_JSON     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reglas.json")
FILTROS_JSON    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filtros.json")
DEFUZZY_JSON    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defuzzy.json")
FUZZY_JSON      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuzzy.json")
VARIABLES_JSON  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "variables.json")
PERMISIVOS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "permisivos.json")


# ============================================================
# Catalogos
# ============================================================

BLOQUES_DISPONIBLES = ["critico", "estabilidad", "optimizacion"]

ETIQUETAS_DISPONIBLES = [
    "LOW", "OK", "HIGH",
    "NO-LOW", "NO-OK", "NO-HIGH",
    "CERCA_BAJO", "CERCA_ALTO",
    "INC", "DEC", "STABLE",
    "NO-INC", "NO-DEC",
    "ON", "OFF",
]


def _build_variables_disponibles() -> list[str]:
    nombres: list[str] = []
    nombres.extend(VARIABLES_PROCESO)
    nombres.extend(f"pend_{v}" for v in VARIABLES_PROCESO)
    nombres.extend(v for v in VARIABLES_CRUDAS_REQUERIDAS if v not in nombres)
    nombres.extend(v for v in VARIABLES_EXTERNAS if v not in nombres)
    nombres.extend(nombre_variable_permisivo(p) for p in PERMISIVOS.keys())
    return nombres


def _build_acciones_disponibles() -> list[str]:
    nombres: list[str] = []
    familias_a_sufijo = {
        "sp_vel_bomba":  "VEL_BOMBA",
        "sp_tonelaje":   "TONELAJE",
        "sp_floculante": "FLOCULANTE",
    }
    for familia_sp, sufijo in familias_a_sufijo.items():
        tabla = DEFUZZY_POR_FAMILIA.get(familia_sp, {})
        keys = list(tabla.get("steps_por_accion", {}).keys())
        # AUMENTAR_FUERTE / AUMENTAR / AUMENTAR_SUAVE / DISMINUIR_SUAVE / DISMINUIR / DISMINUIR_FUERTE
        for key in keys:
            partes = key.split("_")
            direccion = partes[0]                       # AUMENTAR | DISMINUIR
            intensidad = "_".join(partes[1:]) if len(partes) > 1 else ""
            nombre = f"{direccion}_{sufijo}"
            if intensidad:
                nombre += f"_{intensidad}"
            nombres.append(nombre)
    return nombres


VARIABLES_DISPONIBLES = _build_variables_disponibles()
ACCIONES_DISPONIBLES = _build_acciones_disponibles()
PERMISIVOS_DISPONIBLES = list(PERMISIVOS.keys())

VARIABLES_VALIDAS = set(VARIABLES_DISPONIBLES)
ETIQUETAS_VALIDAS = set(ETIQUETAS_DISPONIBLES)
ACCIONES_VALIDAS = set(ACCIONES_DISPONIBLES)
BLOQUES_VALIDOS = set(BLOQUES_DISPONIBLES)


# ============================================================
# Helpers para reglas.json
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

    condiciones = regla.get("if")
    if not isinstance(condiciones, list) or not condiciones:
        return None, "Campo 'if' debe ser una lista no vacia."

    # Aceptamos dos formatos en el payload entrante:
    #   (a) Plano:   [<item>, ...]                  -> se envuelve en AND.
    #   (b) Canon:   [{"AND": [<item>, ...]}]       -> se valida tal cual.
    # donde <item> es:
    #   - hoja: [variable, etiqueta]
    #   - grupo OR: {"OR": [[v,l], [v,l], ...]}  (>= 2 hojas)
    # Persistimos siempre como (b) para evaluar como AND a top-level en el motor.
    items = condiciones
    if (len(condiciones) == 1
            and isinstance(condiciones[0], dict)
            and "AND" in condiciones[0]
            and isinstance(condiciones[0]["AND"], list)):
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

    condiciones_norm = []
    for idx, item in enumerate(items, start=1):
        # Grupo OR
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

        # Hoja simple
        leaf_norm, err = _norm_leaf(item, f"Condicion #{idx}")
        if err is not None:
            return None, err
        condiciones_norm.append(leaf_norm)

    regla["if"] = [{"AND": condiciones_norm}]

    acciones = regla.get("then")
    if not isinstance(acciones, list) or not acciones:
        return None, "Campo 'then' debe ser una lista no vacia."

    acciones_norm = []
    for idx, accion in enumerate(acciones, start=1):
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
# API REST -- Reglas
# ============================================================

@app.route("/api/meta", methods=["GET"])
def api_meta():
    return jsonify({
        "variables":  VARIABLES_DISPONIBLES,
        "etiquetas":  ETIQUETAS_DISPONIBLES,
        "acciones":   ACCIONES_DISPONIBLES,
        "bloques":    BLOQUES_DISPONIBLES,
        "permisivos": PERMISIVOS_DISPONIBLES,
        "setpoints":  SETPOINT_KEYS,
        "pv":         VARIABLES_PROCESO,
    })


@app.route("/api/reglas", methods=["GET"])
def api_get_reglas():
    return jsonify(_load_reglas())


@app.route("/api/reglas/<regla_id>", methods=["GET"])
def api_get_regla(regla_id: str):
    reglas = _load_reglas()
    _, regla = _find_regla(reglas, regla_id)
    if regla is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada"}), 404
    return jsonify(regla)


@app.route("/api/reglas", methods=["POST"])
def api_create_regla():
    data = request.get_json(force=True)
    regla_norm, error = _normalizar_regla_payload(data, require_id=True)
    if error is not None:
        return jsonify({"error": error}), 400
    reglas = _load_reglas()
    _, existing = _find_regla(reglas, regla_norm["id"])
    if existing is not None:
        return jsonify({"error": f"Regla '{regla_norm['id']}' ya existe"}), 409
    reglas.append(regla_norm)
    _save_reglas(reglas)
    return jsonify({"ok": True, "regla": regla_norm}), 201


@app.route("/api/reglas/<regla_id>", methods=["PUT"])
def api_update_regla(regla_id: str):
    reglas = _load_reglas()
    idx, _ = _find_regla(reglas, regla_id)
    if idx is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada"}), 404
    data = request.get_json(force=True)
    data["id"] = regla_id
    regla_norm, error = _normalizar_regla_payload(data, require_id=True)
    if error is not None:
        return jsonify({"error": error}), 400
    reglas[idx] = regla_norm
    _save_reglas(reglas)
    return jsonify({"ok": True, "regla": regla_norm})


@app.route("/api/reglas/<regla_id>", methods=["DELETE"])
def api_delete_regla(regla_id: str):
    reglas = _load_reglas()
    idx, _ = _find_regla(reglas, regla_id)
    if idx is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada"}), 404
    removed = reglas.pop(idx)
    _save_reglas(reglas)
    return jsonify({"ok": True, "eliminada": removed})


# ============================================================
# API REST -- Filtros Exp-Q
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
        _save_filtros(cfg)  # seed inicial
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


# Seed perezoso en el arranque: si filtros.json no existe, crearlo con defaults.
_load_filtros()


@app.route("/api/filtros", methods=["GET"])
def api_get_filtros():
    return jsonify({
        "variables": VARIABLES_FILTRO,
        "defaults":  _defaults_filtros(),
        "actual":    _load_filtros(),
    })


@app.route("/api/filtros", methods=["PUT"])
def api_put_filtros():
    data = request.get_json(force=True)
    norm, error = _normalizar_filtros_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_filtros(norm)
    return jsonify({"ok": True, "actual": norm})


@app.route("/api/filtros/reset", methods=["POST"])
def api_reset_filtros():
    cfg = _defaults_filtros()
    _save_filtros(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# API REST -- Defuzzy
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
            "steps_por_accion": {
                k: list(v) for k, v in tabla.get("steps_por_accion", {}).items()
            },
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
    """Acepta un dict completo con las 3 familias o un sub-dict de 1+ familias.
    Persiste un dict completo (mergeando con lo actual + defaults).
    """
    if not isinstance(data, dict) or not data:
        return None, "El payload debe ser un objeto no vacio { <familia>: {...} }."

    actual = _load_defuzzy()
    out = _defaults_defuzzy()
    for fam in DEFUZZY_FAMILIAS:
        if fam in actual:
            out[fam] = {
                "belief_axis": list(actual[fam].get("belief_axis", out[fam]["belief_axis"])),
                "steps_por_accion": {
                    k: list(v) for k, v in actual[fam].get("steps_por_accion", out[fam]["steps_por_accion"]).items()
                },
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
                return None, f"'{fam}.{k}': debe ser una lista de {n} valores (igual longitud que belief_axis)."
            try:
                steps_norm[k] = [float(x) for x in arr]
            except (TypeError, ValueError):
                return None, f"'{fam}.{k}': contiene valores no numericos."
        out[fam] = {"belief_axis": axis_f, "steps_por_accion": steps_norm}

    return out, None


# Seed perezoso en el arranque.
_load_defuzzy()


@app.route("/api/defuzzy", methods=["GET"])
def api_get_defuzzy():
    return jsonify({
        "familias": list(DEFUZZY_FAMILIAS),
        "acciones": list(DEFUZZY_ACCIONES_KEYS),
        "defaults": _defaults_defuzzy(),
        "actual":   _load_defuzzy(),
    })


@app.route("/api/defuzzy", methods=["PUT"])
def api_put_defuzzy():
    data = request.get_json(force=True)
    norm, error = _normalizar_defuzzy_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_defuzzy(norm)
    return jsonify({"ok": True, "actual": norm})


@app.route("/api/defuzzy/reset", methods=["POST"])
def api_reset_defuzzy():
    cfg = _defaults_defuzzy()
    _save_defuzzy(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Fuzzy (membresias por variable)
# ------------------------------------------------------------
# Schema fuzzy.json: { "<var>": {"offset":[..>=3 puntos..], "labels":{"HIGH":[..], "OK":[..], "LOW":[..]}} }
# El campo "type" (high/low/norm) viene fijo desde el core y NO es editable.
# ============================================================

FUZZY_VARIABLES   = tuple(FUZZY_MODELOS.keys())
FUZZY_TIPO_POR_VAR = {v: FUZZY_MODELOS[v]["type"] for v in FUZZY_VARIABLES}
FUZZY_LABEL_KEYS  = ("HIGH", "OK", "LOW")


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
    """Acepta dict con 1+ variables; persiste dict completo mergeando con actual+defaults.

    El campo 'type' es ignorado: se conserva el del nucleo.
    """
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
                return None, f"'{var}.{k}': debe ser una lista de {n} valores (igual longitud que offset)."
            try:
                arr_f = [float(x) for x in arr]
            except (TypeError, ValueError):
                return None, f"'{var}.{k}': contiene valores no numericos."
            if any(x < 0.0 or x > 1.0 for x in arr_f):
                return None, f"'{var}.{k}': valores fuera de [0.0, 1.0]."
            labels_norm[k] = arr_f
        out[var] = {
            "type":   FUZZY_TIPO_POR_VAR[var],
            "offset": offset_f,
            "labels": labels_norm,
        }

    return out, None


# Seed perezoso en el arranque.
_load_fuzzy()


@app.route("/api/fuzzy", methods=["GET"])
def api_get_fuzzy():
    return jsonify({
        "variables":     list(FUZZY_VARIABLES),
        "tipo_por_var":  FUZZY_TIPO_POR_VAR,
        "label_keys":    list(FUZZY_LABEL_KEYS),
        "defaults":      _defaults_fuzzy(),
        "actual":        _load_fuzzy(),
    })


@app.route("/api/fuzzy", methods=["PUT"])
def api_put_fuzzy():
    data = request.get_json(force=True)
    norm, error = _normalizar_fuzzy_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_fuzzy(norm)
    return jsonify({"ok": True, "actual": norm})


@app.route("/api/fuzzy/reset", methods=["POST"])
def api_reset_fuzzy():
    cfg = _defaults_fuzzy()
    _save_fuzzy(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Variables (crudas + definiciones calculadas)
# ------------------------------------------------------------
# Schema variables.json:
#   { "crudas": {<nombre>: <descripcion>},
#     "definiciones": [ { nombre, descripcion, tipo, ... }, ... ] }
# ============================================================
import re as _re

VAR_TIPOS                 = ("aritmetica", "rolling_delta", "rolling_std")
VAR_OPERACIONES           = ("suma", "resta", "multiplicacion", "division")
VAR_NOMBRE_RE             = _re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
VAR_NOMBRES_RESERVADOS    = set(VARIABLES_PROCESO) | set(SETPOINT_KEYS) | {
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
    """Valida un payload completo (crudas + definiciones).

    Reglas:
      - crudas: dict {nombre: descripcion}; nombres validos no reservados.
      - definiciones: lista ordenada; nombres unicos no reservados ni en crudas.
      - args/arg deben referenciar: crudas | VARIABLES_PROCESO | una definicion previa.
      - ventana_min > 0; operacion en VAR_OPERACIONES; args exactamente 2.
    """
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
            return None, f"crudas: nombre invalido '{nombre}' (use letras/digitos/_ y empiece por letra)."
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
            return None, f"definiciones[{i}]: '{nombre}' choca con un nombre reservado del nucleo."
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
                return None, f"definiciones[{i}].operacion: invalida '{operacion}'. Validas: {list(VAR_OPERACIONES)}."
            args = item.get("args")
            if not isinstance(args, list) or len(args) != 2:
                return None, f"definiciones[{i}].args: debe ser una lista de 2 nombres."
            for j, a in enumerate(args):
                if a not in refs_disponibles:
                    return None, f"definiciones[{i}].args[{j}] = '{a}' no existe (use crudas, PV o una definicion previa)."
            out["operacion"] = operacion
            out["args"] = list(args)
        else:
            arg = item.get("arg")
            if arg not in refs_disponibles:
                return None, f"definiciones[{i}].arg = '{arg}' no existe (use crudas, PV o una definicion previa)."
            try:
                vmin = float(item.get("ventana_min"))
            except (TypeError, ValueError):
                return None, f"definiciones[{i}].ventana_min: debe ser un numero > 0."
            if vmin <= 0.0:
                return None, f"definiciones[{i}].ventana_min: debe ser > 0 (recibido {vmin})."
            out["arg"] = arg
            out["ventana_min"] = vmin
        defs_norm.append(out)
        refs_disponibles.add(nombre)

    return {"crudas": crudas_norm, "definiciones": defs_norm}, None


def _definiciones_lista_a_dict(definiciones_lista: list) -> dict:
    out: dict = {}
    for item in definiciones_lista:
        nombre = item["nombre"]
        cfg = {"descripcion": item.get("descripcion", ""), "tipo": item["tipo"]}
        if item["tipo"] == "aritmetica":
            cfg["operacion"] = item["operacion"]
            cfg["args"] = list(item["args"])
        else:
            cfg["arg"] = item["arg"]
            cfg["ventana_min"] = float(item["ventana_min"])
        out[nombre] = cfg
    return out


# Seed perezoso en el arranque.
_load_variables()


@app.route("/api/variables", methods=["GET"])
def api_get_variables():
    return jsonify({
        "tipos":             list(VAR_TIPOS),
        "operaciones":       list(VAR_OPERACIONES),
        "variables_proceso": list(VARIABLES_PROCESO),
        "defaults":          _defaults_variables(),
        "actual":            _load_variables(),
    })


@app.route("/api/variables", methods=["PUT"])
def api_put_variables():
    data = request.get_json(force=True)
    norm, error = _normalizar_variables_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_variables(norm)
    return jsonify({"ok": True, "actual": norm})


@app.route("/api/variables/reset", methods=["POST"])
def api_reset_variables():
    cfg = _defaults_variables()
    _save_variables(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Permisivos (operadores logicos OR / AND / NOT)
# ------------------------------------------------------------
# Schema permisivos.json:
#   { "<NOMBRE>": [ <condicion>, ... ] }   (top-level AND implicito)
# Cada <condicion> puede ser:
#   {"var": <str>, "op": <str>, "value": <num>}
#   {"fuzzy_var": <str>, "label": <str>, "min_mu": <num>}
#   {"OR":  [<condicion>, ...]}
#   {"AND": [<condicion>, ...]}
#   {"NOT": <condicion>}
# ============================================================
import copy as _copy

PERM_OPERADORES   = ("<", "<=", ">", ">=", "==", "=", "!=")
PERM_NOMBRE_RE    = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    """Valida recursivamente una condicion. Devuelve None si OK, o str con el error."""
    if not isinstance(cond, dict):
        return f"{path}: cada condicion debe ser un objeto dict (recibido {type(cond).__name__})."

    # Operadores logicos: clave exclusiva
    if "OR" in cond:
        if set(cond.keys()) != {"OR"}:
            return f"{path}: OR debe ser la unica clave del objeto."
        if not isinstance(cond["OR"], list) or len(cond["OR"]) < 1:
            return f"{path}.OR: debe ser una lista no vacia."
        for i, sub in enumerate(cond["OR"]):
            err = _validar_condicion(sub, f"{path}.OR[{i}]")
            if err:
                return err
        return None

    if "AND" in cond:
        if set(cond.keys()) != {"AND"}:
            return f"{path}: AND debe ser la unica clave del objeto."
        if not isinstance(cond["AND"], list) or len(cond["AND"]) < 1:
            return f"{path}.AND: debe ser una lista no vacia."
        for i, sub in enumerate(cond["AND"]):
            err = _validar_condicion(sub, f"{path}.AND[{i}]")
            if err:
                return err
        return None

    if "NOT" in cond:
        if set(cond.keys()) != {"NOT"}:
            return f"{path}: NOT debe ser la unica clave del objeto."
        return _validar_condicion(cond["NOT"], f"{path}.NOT")

    # Primitivas
    if "fuzzy_var" in cond:
        var = cond.get("fuzzy_var")
        label = cond.get("label")
        if not isinstance(var, str) or not var.strip():
            return f"{path}.fuzzy_var: debe ser string no vacio."
        if not isinstance(label, str) or not label.strip():
            return f"{path}.label: debe ser string no vacio (etiqueta fuzzy)."
        if "min_mu" in cond:
            try:
                mu = float(cond["min_mu"])
            except (TypeError, ValueError):
                return f"{path}.min_mu: debe ser numero."
            if not (0.0 <= mu <= 1.0):
                return f"{path}.min_mu: fuera de [0,1] (recibido {mu})."
        extras = set(cond.keys()) - {"fuzzy_var", "label", "min_mu"}
        if extras:
            return f"{path}: claves no soportadas en primitiva fuzzy: {sorted(extras)}."
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
            return f"{path}: claves no soportadas en primitiva numerica: {sorted(extras)}."
        return None

    return (
        f"{path}: condicion no reconocida. Use OR/AND/NOT o primitiva "
        "(var/op/value, fuzzy_var/label/min_mu)."
    )


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
            return None, (
                f"Nombre de permisivo invalido: {nombre!r}. Solo letras, digitos y _ ; "
                "no puede empezar con digito."
            )
        nombre_up = nombre.upper()
        if nombre_up in out:
            return None, f"Nombre de permisivo duplicado: {nombre_up}."
        if not isinstance(conds, list):
            return None, f"{nombre_up}: el cuerpo debe ser una lista (AND implicito)."
        if len(conds) == 0:
            return None, f"{nombre_up}: debe declarar al menos una condicion."
        for i, c in enumerate(conds):
            err = _validar_condicion(c, f"{nombre_up}[{i}]")
            if err is not None:
                return None, err
        out[nombre_up] = conds
    return out, None


_load_permisivos()


@app.route("/api/permisivos", methods=["GET"])
def api_get_permisivos():
    return jsonify({
        "operadores": list(PERM_OPERADORES),
        "fuzzy_vars": sorted(FUZZY_MODELOS.keys()),
        "defaults":   _defaults_permisivos(),
        "actual":     _load_permisivos(),
    })


@app.route("/api/permisivos", methods=["PUT"])
def api_put_permisivos():
    data = request.get_json(force=True)
    norm, error = _normalizar_permisivos_payload(data)
    if error is not None:
        return jsonify({"error": error}), 400
    _save_permisivos(norm)
    return jsonify({"ok": True, "actual": norm})


@app.route("/api/permisivos/reset", methods=["POST"])
def api_reset_permisivos():
    cfg = _defaults_permisivos()
    _save_permisivos(cfg)
    return jsonify({"ok": True, "actual": cfg})


# ============================================================
# Simulacion
# ============================================================

def _ejecutar_simulacion(params: dict) -> dict:
    """Helper compartido entre /api/simulacion y /api/simulacion/start."""
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
    import fuzzys_templates as _fz_tpl

    n_muestras = int(params.get("n_muestras", 240))
    dt_s = float(params.get("dt_s", 60.0))
    seed = int(params.get("seed", 42))

    df_data = generar_datos_proceso(n_muestras=n_muestras, dt_s=dt_s, seed=seed)
    reglas = cargar_reglas_json()           # reglas.json o REGLAS_ESPESADOR por defecto
    config_filtro = cargar_filtros_json()   # filtros.json o CONFIG_FILTRO_ESPESADOR_DEFAULT

    # Monkey-patch de defuzzy: el motor y otras rutinas leen el dict global.
    defuzzy_cfg = cargar_defuzzy_json()
    _dfz_mod.DEFUZZY_POR_FAMILIA.clear()
    _dfz_mod.DEFUZZY_POR_FAMILIA.update(defuzzy_cfg)

    # Monkey-patch de membresias fuzzy: reconstruir cada clase con la fabrica
    # adecuada segun el tipo (high/low/norm) y reemplazar el modelo en-place.
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

    # Variables calculadas: convertir la lista del JSON a dict ordenado.
    vars_cfg = cargar_variables_json()
    definiciones_cfg = _definiciones_lista_a_dict(vars_cfg.get("definiciones", []))

    # Permisivos: dict {NOMBRE: [condiciones]}.
    permisivos_cfg = cargar_permisivos_json()

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


@app.route("/api/simulacion", methods=["POST"])
def api_simulacion():
    """Ejecuta la simulacion con las reglas actuales y devuelve un resumen."""
    try:
        params = request.get_json(silent=True) or {}
        resultados = _ejecutar_simulacion(params)

        df_res = resultados["resultados"]
        df_ev = resultados["eventos"]

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
            "ok": True,
            "muestras":               len(df_res),
            "total_eventos":          len(df_ev),
            "setpoints_finales":      sp_final,
            "activaciones_por_regla": activaciones_por_regla,
            "activaciones_por_bloque": activaciones_por_bloque,
            "eventos":                eventos_list[:50],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ============================================================
# Simulacion streaming
# ============================================================

_sim_state: dict = {
    "running": False,
    "cursor": 0,
    "df_resultados": None,
    "df_eventos": None,
    "batch_size": 5,
}


@app.route("/api/simulacion/start", methods=["POST"])
def api_sim_start():
    try:
        params = request.get_json(silent=True) or {}
        resultados = _ejecutar_simulacion(params)
        _sim_state["df_resultados"] = resultados["resultados"]
        _sim_state["df_eventos"] = resultados["eventos"]
        _sim_state["cursor"] = 0
        _sim_state["running"] = True
        _sim_state["batch_size"] = int(params.get("batch_size", 5))
        return jsonify({"ok": True, "total": len(resultados["resultados"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/simulacion/next", methods=["GET"])
def api_sim_next():
    if not _sim_state["running"] or _sim_state["df_resultados"] is None:
        return jsonify({"ok": False, "error": "No hay simulacion activa. Llama a /api/simulacion/start primero."}), 400

    df = _sim_state["df_resultados"]
    cursor = _sim_state["cursor"]
    batch = _sim_state["batch_size"]
    total = len(df)

    if cursor >= total:
        _sim_state["running"] = False
        return jsonify({"ok": True, "done": True, "points": [], "eventos": [], "cursor": cursor, "total": total})

    end = min(cursor + batch, total)
    chunk = df.iloc[cursor:end]

    pv_keys = list(VARIABLES_PROCESO)

    points = []
    for _, row in chunk.iterrows():
        pt = {
            "t_s":   round(float(row["t_s"]), 1),
            "t_min": round(float(row["t_min"]), 2),
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

    df_ev = _sim_state["df_eventos"]
    ev_list = []
    if df_ev is not None and not df_ev.empty:
        t_start = float(chunk.iloc[0]["t_s"])
        t_end = float(chunk.iloc[-1]["t_s"])
        mask = (df_ev["t_s"] >= t_start) & (df_ev["t_s"] <= t_end)
        for _, row in df_ev[mask].iterrows():
            ev_list.append({
                "t_s":      round(float(row["t_s"]), 1),
                "regla_id": str(row["regla_id"]),
                "bloque":   str(row.get("bloque", "")),
                "acciones": str(row.get("acciones", "")),
                "belief":   round(float(row["belief"]), 4),
            })

    _sim_state["cursor"] = end

    return jsonify({
        "ok":      True,
        "done":    False,
        "points":  points,
        "eventos": ev_list,
        "cursor":  end,
        "total":   total,
    })


@app.route("/api/simulacion/reset", methods=["POST"])
def api_sim_reset():
    _sim_state["cursor"] = 0
    _sim_state["running"] = _sim_state["df_resultados"] is not None
    return jsonify({"ok": True})


# ============================================================
# UI -- Editor de reglas
# ============================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espesador -- Editor de Reglas</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;min-height:100vh}
.sidebar{width:220px;flex-shrink:0;background:#0b1322;border-right:1px solid #1e293b;padding:18px 12px;display:flex;flex-direction:column;gap:4px;position:sticky;top:0;height:100vh;overflow-y:auto}
.sidebar .brand{font-size:.95rem;font-weight:700;color:#38bdf8;padding:4px 8px 14px 8px;border-bottom:1px solid #1e293b;margin-bottom:8px}
.sidebar a{display:block;color:#94a3b8;text-decoration:none;padding:8px 10px;border-radius:6px;font-size:.85rem;border-left:3px solid transparent;transition:.15s}
.sidebar a:hover{background:#1e293b;color:#e2e8f0}
.sidebar a.active{background:#1e293b;color:#38bdf8;border-left-color:#38bdf8;font-weight:600}
.sidebar a.external{color:#64748b;font-size:.78rem;margin-top:4px}
.sidebar hr.sep{border:none;border-top:1px solid #1e293b;margin:12px 4px}
.main{flex:1;padding:20px;overflow-x:auto}
.seccion{display:none}
.seccion.active{display:block}
.placeholder-card{background:#1e293b;border:1px dashed #475569;border-radius:10px;padding:24px;color:#94a3b8;text-align:center}
.placeholder-card h3{color:#e2e8f0;margin-bottom:8px}
h1{color:#38bdf8;margin-bottom:8px}
h2{color:#94a3b8;font-size:1rem;margin-bottom:20px;font-weight:400}
.top-bar{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:20px}
button{cursor:pointer;border:none;border-radius:6px;padding:8px 16px;font-size:.875rem;font-weight:600;transition:.15s}
.btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}
.btn-success{background:#22c55e;color:#fff}.btn-success:hover{background:#16a34a}
.btn-danger{background:#ef4444;color:#fff}.btn-danger:hover{background:#dc2626}
.btn-sm{padding:4px 10px;font-size:.75rem}
table{width:100%;border-collapse:collapse;background:#1e293b;border-radius:8px;overflow:hidden;margin-bottom:20px}
th{background:#334155;color:#94a3b8;text-align:left;padding:10px 12px;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em}
td{padding:8px 12px;border-top:1px solid #334155;font-size:.85rem;vertical-align:top}
tr:hover{background:#2d3a4f}
.tag{display:inline-block;background:#334155;border-radius:4px;padding:2px 6px;margin:1px;font-size:.75rem}
.tag-var{border-left:3px solid #38bdf8}
.tag-label{border-left:3px solid #a78bfa}
.tag-action{border-left:3px solid #fb923c}
.tag-block{border-left:3px solid #22c55e}
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.6);z-index:100;justify-content:center;align-items:center}
.modal-overlay.active{display:flex}
.modal{background:#1e293b;border-radius:12px;padding:24px;width:700px;max-width:95vw;max-height:90vh;overflow-y:auto;box-shadow:0 20px 40px rgba(0,0,0,.5)}
.modal h3{color:#38bdf8;margin-bottom:16px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group.full{grid-column:1/-1}
label{font-size:.75rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.03em}
input,select,textarea{background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 10px;color:#e2e8f0;font-size:.85rem}
input:focus,select:focus,textarea:focus{outline:none;border-color:#3b82f6}
.cond-row{display:flex;gap:6px;align-items:center;margin-bottom:4px}
.cond-row select{flex:1}
.cond-row button{flex-shrink:0}
#sim-results{background:#1e293b;border-radius:8px;padding:16px;margin-top:20px;display:none}
#sim-results h3{color:#22c55e;margin-bottom:12px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}
.stat-card{background:#334155;border-radius:8px;padding:12px;text-align:center}
.stat-card .val{font-size:1.5rem;font-weight:700;color:#38bdf8}
.stat-card .lbl{font-size:.7rem;color:#94a3b8;text-transform:uppercase;margin-top:4px}
.ev-table{max-height:300px;overflow-y:auto}
.loading{color:#94a3b8;font-style:italic}
.hint{font-size:.7rem;color:#64748b;margin-top:2px}
</style>
</head>
<body>
<aside class="sidebar">
  <div class="brand">Espesador v2</div>
  <a href="#reglas"     data-sec="reglas">Reglas</a>
  <a href="#filtros"    data-sec="filtros">Filtros Exp-Q</a>
  <a href="#defuzzy"    data-sec="defuzzy">Defuzzy</a>
  <a href="#fuzzy"      data-sec="fuzzy">Fuzzy</a>
  <a href="#variables"  data-sec="variables">Variables calc.</a>
  <a href="#permisivos" data-sec="permisivos">Permisivos</a>
  <hr class="sep">
  <a href="/graficos" class="external">Graficos en Vivo →</a>
</aside>
<main class="main">
<section class="seccion" id="seccion-reglas">
<div class="top-bar">
  <div>
    <h1>Sistema Experto Espesador (v2)</h1>
    <h2>Editor de reglas en vivo -- las reglas se aplican al ejecutar la simulacion.
        Si reglas.json esta vacio se usan las reglas por defecto del experto.</h2>
  </div>
  <div style="display:flex;gap:8px">
    <button class="btn-primary" onclick="openModal()">+ Nueva Regla</button>
    <button class="btn-success" onclick="runSim()">&#9654; Ejecutar Simulacion</button>
  </div>
</div>

<table id="rules-table">
<thead><tr>
  <th>ID</th><th>Bloque</th><th>Prioridad</th>
  <th>Condiciones (IF -- AND de items; cada item: hoja o grupo OR)</th><th>Acciones (THEN)</th>
  <th>Weight</th><th style="width:110px">Opciones</th>
</tr></thead>
<tbody id="rules-body"></tbody>
</table>

<div id="sim-results">
  <h3>Resultados de la Simulacion</h3>
  <div class="stat-grid" id="stat-grid"></div>
  <h4 style="color:#94a3b8;margin-bottom:8px">Eventos (max. 50)</h4>
  <div class="ev-table">
    <table>
      <thead><tr><th>t (s)</th><th>Regla</th><th>Bloque</th><th>Acciones</th><th>Belief</th></tr></thead>
      <tbody id="ev-body"></tbody>
    </table>
  </div>
</div>
</section>

<section class="seccion" id="seccion-filtros">
  <div class="top-bar">
    <div>
      <h1>Filtros Exp-Q</h1>
      <h2>Suavizado (q, window_size) por variable de proceso. Se aplica al iniciar la simulacion.
          Si <code>filtros.json</code> falta o esta vacio, se usan los defaults del nucleo.</h2>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="saveFiltros()">Guardar</button>
      <button class="btn-danger"  onclick="resetFiltros()">Restaurar default</button>
    </div>
  </div>
  <div id="filtros-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>
  <table id="filtros-table">
    <thead><tr>
      <th>Variable</th>
      <th style="width:170px">q (0.0 - 1.0)</th>
      <th style="width:170px">window_size (1 - 1000)</th>
      <th style="width:160px">Default core</th>
    </tr></thead>
    <tbody id="filtros-body"></tbody>
  </table>
</section>

<section class="seccion" id="seccion-defuzzy">
  <div class="top-bar">
    <div>
      <h1>Defuzzy</h1>
      <h2>Tablas Sugeno por familia de setpoint. Filas = acciones (6 fijas), columnas = puntos del belief.
          Cada tabla se aplica al iniciar la simulacion.</h2>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="saveDefuzzy()">Guardar familia actual</button>
      <button class="btn-danger"  onclick="resetDefuzzy()">Restaurar default (todas)</button>
    </div>
  </div>
  <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
    <label style="text-transform:none;letter-spacing:0">Familia:</label>
    <select id="dfz-familia" onchange="_renderDefuzzy()"></select>
    <button class="btn-sm btn-success" onclick="addDefuzzyCol()">+ Columna</button>
    <button class="btn-sm btn-danger"  onclick="delDefuzzyCol()">- Columna</button>
  </div>
  <div id="defuzzy-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>
  <div style="overflow-x:auto"><table id="defuzzy-table"></table></div>
</section>

<section class="seccion" id="seccion-fuzzy">
  <div class="top-bar">
    <div>
      <h1>Fuzzy</h1>
      <h2>Membresias por variable. Filas: offset (eje), HIGH, OK, LOW. El tipo (high/low/norm) es fijo desde el nucleo.
          Se aplica al iniciar la simulacion.</h2>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="saveFuzzy()">Guardar variable actual</button>
      <button class="btn-danger"  onclick="resetFuzzy()">Restaurar default (todas)</button>
    </div>
  </div>
  <div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
    <label style="text-transform:none;letter-spacing:0">Variable:</label>
    <select id="fz-variable" onchange="_renderFuzzy()"></select>
    <span style="font-size:.85rem;color:#94a3b8">Tipo:</span>
    <span id="fz-tipo" style="font-size:.85rem;color:#facc15;font-weight:600"></span>
    <button class="btn-sm btn-success" onclick="addFuzzyCol()">+ Columna</button>
    <button class="btn-sm btn-danger"  onclick="delFuzzyCol()">- Columna</button>
  </div>
  <div id="fuzzy-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>
  <div style="overflow-x:auto"><table id="fuzzy-table"></table></div>
</section>

<section class="seccion" id="seccion-variables">
  <div class="top-bar">
    <div>
      <h1>Variables</h1>
      <h2>Catalogo de variables crudas (sensores) y definiciones calculadas. Las definiciones se procesan en orden;
          una calculada puede referenciar crudas, PVs o una definicion previa.</h2>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="saveVariables()">Guardar catalogo</button>
      <button class="btn-danger"  onclick="resetVariables()">Restaurar default</button>
    </div>
  </div>
  <div id="variables-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>

  <h3 style="margin-top:6px">Variables crudas (sensores)</h3>
  <div style="display:flex;gap:8px;margin-bottom:6px">
    <button class="btn-sm btn-success" onclick="addCruda()">+ Cruda</button>
  </div>
  <div style="overflow-x:auto"><table id="crudas-table"></table></div>

  <h3 style="margin-top:14px">Definiciones calculadas (orden importa)</h3>
  <div style="display:flex;gap:8px;margin-bottom:6px">
    <button class="btn-sm btn-success" onclick="addDefinicion('aritmetica')">+ Aritmetica</button>
    <button class="btn-sm btn-success" onclick="addDefinicion('rolling_delta')">+ Rolling delta</button>
    <button class="btn-sm btn-success" onclick="addDefinicion('rolling_std')">+ Rolling std</button>
  </div>
  <div style="overflow-x:auto"><table id="definiciones-table"></table></div>
</section>

<section class="seccion" id="seccion-permisivos">
  <div class="top-bar">
    <div>
      <h1>Permisivos</h1>
      <h2>Condiciones logicas de habilitacion (AND/OR/NOT) expuestas al motor como
          <code>__PERM_&lt;NOMBRE&gt;</code> con etiquetas ON/OFF. Las reglas que dependan
          de un permisivo deben referenciarlo asi en su <code>if</code>.</h2>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="savePermisivos()">Guardar permisivos</button>
      <button class="btn-danger"  onclick="resetPermisivos()">Restaurar default</button>
    </div>
  </div>
  <div id="permisivos-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>

  <div style="display:flex;gap:8px;margin-bottom:10px">
    <input id="perm-new-nombre" type="text" placeholder="NOMBRE_NUEVO_PERMISIVO" style="min-width:280px">
    <button class="btn-sm btn-success" onclick="addPermisivo()">+ Permisivo</button>
  </div>

  <div id="permisivos-list"></div>

  <details style="margin-top:18px">
    <summary style="cursor:pointer;color:#94a3b8">Formato de condiciones (sintaxis admitida)</summary>
    <pre style="background:#0f172a;color:#cbd5e1;padding:10px;border-radius:6px;font-size:.78rem;overflow:auto">
Primitivas:
  {"var": "tonelaje_sag_delta_30min", "op": ">", "value": 300}
  {"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5}

Operadores logicos (anidables):
  {"OR":  [cond1, cond2, ...]}
  {"AND": [cond1, cond2, ...]}
  {"NOT": cond}

Top-level del permisivo = AND implicito (lista de condiciones).
Operadores numericos validos: &lt;  &lt;=  &gt;  &gt;=  ==  =  !=
</pre>
  </details>
</section>

<!-- Modal edicion -->
<div class="modal-overlay" id="modal-overlay">
<div class="modal">
  <h3 id="modal-title">Nueva Regla</h3>
  <div class="form-grid">
    <div class="form-group"><label>ID</label><input id="f-id" placeholder="ej: CRIT.01 o EST.10"></div>
    <div class="form-group"><label>Bloque</label>
      <select id="f-bloque"></select>
      <span class="hint">critico bloquea estabilidad; optimizacion corre independiente.</span>
    </div>
    <div class="form-group"><label>Prioridad</label><input id="f-priority" type="number" step="1" value="50"></div>
    <div class="form-group"><label>Weight</label><input id="f-weight" type="number" step="0.1" value="1.0"></div>
    <div class="form-group full">
      <label>Condiciones (IF) -- items en AND; cada item puede ser una hoja o un grupo OR</label>
      <div id="conds-container"></div>
      <div style="display:flex;gap:6px;margin-top:4px">
        <button class="btn-primary btn-sm" onclick="addCond()">+ Condicion</button>
        <button class="btn-sm" style="background:#facc15;color:#1e293b" onclick="addOrGroup()">+ Grupo OR</button>
      </div>
    </div>
    <div class="form-group full">
      <label>Acciones (THEN)</label>
      <div id="actions-container"></div>
      <button class="btn-primary btn-sm" onclick="addAction()" style="margin-top:4px">+ Accion</button>
    </div>
  </div>
  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:20px">
    <button class="btn-danger" onclick="closeModal()">Cancelar</button>
    <button class="btn-success" onclick="saveRule()">Guardar</button>
  </div>
</div>
</div>

<script>
const VARS    = VARIABLES_JSON;
const LABELS  = LABELS_JSON;
const ACTIONS = ACTIONS_JSON;
const BLOCKS  = BLOCKS_JSON;

let editingId = null;

// Las reglas se persisten como [{"AND": [<item>,...]}] donde cada <item> es:
//   - hoja: [var, lbl]
//   - grupo OR: {"OR": [[v,l], [v,l], ...]} con >=2 hojas
// La UI editor acepta esos dos kinds en el top-level (AND implicito).
function _isLeaf(x) {
  return Array.isArray(x) && x.length === 2 && typeof x[0] === 'string' && typeof x[1] === 'string';
}
function _isOrGroup(x) {
  return x && typeof x === 'object' && !Array.isArray(x)
      && Array.isArray(x.OR) && x.OR.length >= 2 && x.OR.every(_isLeaf);
}
function _condsToItems(ifList) {
  // Desempaqueta el wrapper AND y devuelve los items top-level.
  if (!Array.isArray(ifList)) return [];
  if (ifList.length === 1 && ifList[0] && Array.isArray(ifList[0].AND)) {
    return ifList[0].AND;
  }
  return ifList;
}
function _isComplexIf(ifList) {
  const items = _condsToItems(ifList);
  if (!items.length) return false;
  return !items.every(it => _isLeaf(it) || _isOrGroup(it));
}
function _renderItemHTML(it) {
  if (_isLeaf(it)) {
    return `<span class="tag tag-var">${it[0]}</span> <span class="tag tag-label">${it[1]}</span>`;
  }
  if (_isOrGroup(it)) {
    const inner = it.OR.map(l =>
      `<span class="tag tag-var">${l[0]}</span> <span class="tag tag-label">${l[1]}</span>`
    ).join(' <b style="color:#facc15">OR</b> ');
    return `<span style="color:#94a3b8">(</span> ${inner} <span style="color:#94a3b8">)</span>`;
  }
  return `<span class="tag" style="border-left:3px solid #f87171">?</span>`;
}

function _populateBlockSelect() {
  const s = document.getElementById('f-bloque');
  s.innerHTML = BLOCKS.map(b => `<option value="${b}">${b}</option>`).join('');
}
_populateBlockSelect();

async function loadRules() {
  const res = await fetch('/api/reglas');
  const rules = await res.json();
  const tbody = document.getElementById('rules-body');
  tbody.innerHTML = '';
  if (!rules || rules.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#64748b;padding:24px">
      reglas.json vacio. Se aplicaran las reglas por defecto del experto.</td></tr>`;
    return;
  }
  rules.sort((a,b) => (b.priority||0) - (a.priority||0));
  for (const r of rules) {
    const tr = document.createElement('tr');
    const items = _condsToItems(r['if']);
    const complex = _isComplexIf(r['if']);
    const conds = complex
      ? `<span class="tag" style="border-left:3px solid #f87171">expresion compleja (no editable desde UI)</span>`
      : items.map(_renderItemHTML).join('<br><b style="color:#22c55e">AND</b><br>');
    const acts  = (r['then']||[]).map(a => `<span class="tag tag-action">${a}</span>`).join('<br>');
    tr.innerHTML = `<td><b>${r.id}</b></td>
      <td><span class="tag tag-block">${r.bloque||'-'}</span></td>
      <td>${r.priority ?? '-'}</td>
      <td>${conds}</td><td>${acts}</td>
      <td>${r.weight ?? 1.0}</td>
      <td>
        <button class="btn-primary btn-sm" onclick="editRule('${r.id}')" ${complex?'disabled title=\"No editable desde UI\"':''}>Editar</button>
        <button class="btn-danger btn-sm" onclick="deleteRule('${r.id}')">Borrar</button>
      </td>`;
    tbody.appendChild(tr);
  }
}

function _selVar(v)   { return `<select class="cv">${VARS.map(x=>`<option ${x===v?'selected':''}>${x}</option>`).join('')}</select>`; }
function _selLbl(l)   { return `<select class="cl">${LABELS.map(x=>`<option ${x===l?'selected':''}>${x}</option>`).join('')}</select>`; }

function addCond(v, l) {
  if (v === undefined) v = VARS[0];
  if (l === undefined) l = 'OK';
  const c = document.getElementById('conds-container');
  const row = document.createElement('div');
  row.className = 'cond-row';
  row.dataset.kind = 'leaf';
  row.innerHTML = `
    ${_selVar(v)}
    ${_selLbl(l)}
    <button class="btn-danger btn-sm" onclick="this.parentElement.remove()">x</button>`;
  c.appendChild(row);
}

function _addOrLeafToGroup(container, v, l) {
  if (v === undefined) v = VARS[0];
  if (l === undefined) l = 'OK';
  const sub = document.createElement('div');
  sub.className = 'cond-row or-leaf';
  sub.innerHTML = `
    <span style="color:#facc15;font-size:.7rem;width:22px;text-align:center">OR</span>
    ${_selVar(v)}
    ${_selLbl(l)}
    <button class="btn-danger btn-sm" onclick="this.parentElement.remove()">x</button>`;
  container.appendChild(sub);
}

function addOrGroup(leaves) {
  // leaves: array de [v,l]; si esta vacio crea 2 por defecto.
  const c = document.getElementById('conds-container');
  const group = document.createElement('div');
  group.className = 'or-group';
  group.dataset.kind = 'or';
  group.style.cssText = 'border:1px dashed #facc15;border-radius:6px;padding:6px;margin-bottom:6px;background:#1a2436';
  group.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
      <b style="font-size:.7rem;color:#facc15">GRUPO OR (>=2 hojas)</b>
      <div>
        <button class="btn-primary btn-sm" onclick="_addOrLeafToGroup(this.closest('.or-group').querySelector('.or-leaves'))">+ hoja</button>
        <button class="btn-danger btn-sm" onclick="this.closest('.or-group').remove()">eliminar grupo</button>
      </div>
    </div>
    <div class="or-leaves"></div>`;
  c.appendChild(group);
  const inner = group.querySelector('.or-leaves');
  const initial = (leaves && leaves.length >= 2) ? leaves : [[VARS[0],'OK'],[VARS[0],'OK']];
  for (const [v,l] of initial) _addOrLeafToGroup(inner, v, l);
}

function addAction(a=ACTIONS[0]) {
  const c = document.getElementById('actions-container');
  const row = document.createElement('div');
  row.className = 'cond-row';
  row.innerHTML = `
    <select class="ca">${ACTIONS.map(x=>`<option ${x===a?'selected':''}>${x}</option>`).join('')}</select>
    <button class="btn-danger btn-sm" onclick="this.parentElement.remove()">x</button>`;
  c.appendChild(row);
}

function openModal(rule=null) {
  editingId = null;
  document.getElementById('modal-title').textContent = 'Nueva Regla';
  document.getElementById('f-id').value = '';
  document.getElementById('f-id').disabled = false;
  document.getElementById('f-priority').value = '50';
  document.getElementById('f-weight').value = '1.0';
  document.getElementById('f-bloque').value = BLOCKS[1] || BLOCKS[0];
  document.getElementById('conds-container').innerHTML = '';
  document.getElementById('actions-container').innerHTML = '';
  if (rule) {
    editingId = rule.id;
    document.getElementById('modal-title').textContent = 'Editar Regla ' + rule.id;
    document.getElementById('f-id').value = rule.id;
    document.getElementById('f-id').disabled = true;
    document.getElementById('f-priority').value = rule.priority ?? 50;
    document.getElementById('f-weight').value   = rule.weight ?? 1.0;
    document.getElementById('f-bloque').value   = rule.bloque || BLOCKS[1] || BLOCKS[0];
    for (const it of _condsToItems(rule['if'])) {
      if (_isOrGroup(it))      addOrGroup(it.OR);
      else if (_isLeaf(it))    addCond(it[0], it[1]);
    }
    for (const a of (rule['then']||[])) addAction(a);
  } else {
    addCond(); addAction();
  }
  document.getElementById('modal-overlay').classList.add('active');
}

function closeModal() { document.getElementById('modal-overlay').classList.remove('active'); }

async function editRule(id) {
  const res = await fetch('/api/reglas/' + encodeURIComponent(id));
  if (!res.ok) return alert('Error cargando regla');
  const rule = await res.json();
  openModal(rule);
}

async function deleteRule(id) {
  if (!confirm('Eliminar regla ' + id + '?')) return;
  await fetch('/api/reglas/' + encodeURIComponent(id), {method:'DELETE'});
  loadRules();
}

async function saveRule() {
  const id = document.getElementById('f-id').value.trim();
  if (!id) return alert('ID requerido');

  // Recolecta items top-level en el orden visual del contenedor.
  const container = document.getElementById('conds-container');
  const items = [];
  for (const child of container.children) {
    if (child.dataset.kind === 'leaf') {
      items.push([child.querySelector('.cv').value, child.querySelector('.cl').value]);
    } else if (child.dataset.kind === 'or') {
      const leaves = [...child.querySelectorAll('.or-leaves .or-leaf')].map(r => [
        r.querySelector('.cv').value, r.querySelector('.cl').value
      ]);
      if (leaves.length < 2) return alert('Cada grupo OR debe tener al menos 2 hojas.');
      items.push({OR: leaves});
    }
  }
  if (items.length === 0) return alert('Agrega al menos una condicion.');

  const acts = [...document.querySelectorAll('#actions-container .cond-row')]
                  .map(r => r.querySelector('.ca').value);

  const rule = {
    id:       id,
    bloque:   document.getElementById('f-bloque').value,
    'if':     items,
    'then':   acts,
    weight:   parseFloat(document.getElementById('f-weight').value)   || 1.0,
    priority: parseFloat(document.getElementById('f-priority').value) || 0,
  };

  const url = editingId
    ? '/api/reglas/' + encodeURIComponent(editingId)
    : '/api/reglas';
  const method = editingId ? 'PUT' : 'POST';
  const res = await fetch(url, {
    method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(rule)
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Error');
  closeModal();
  loadRules();
}

async function runSim() {
  const panel = document.getElementById('sim-results');
  panel.style.display = 'block';
  document.getElementById('stat-grid').innerHTML = '<p class="loading">Ejecutando simulacion...</p>';
  document.getElementById('ev-body').innerHTML = '';

  const res = await fetch('/api/simulacion', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})
  });
  const data = await res.json();
  if (!data.ok) {
    document.getElementById('stat-grid').innerHTML =
      '<p style="color:#ef4444">Error: ' + (data.error || 'desconocido') + '</p>';
    return;
  }

  const sp = data.setpoints_finales || {};
  const cards = [
    {val: data.muestras,      lbl: 'Muestras'},
    {val: data.total_eventos, lbl: 'Eventos'},
  ];
  for (const k of Object.keys(sp)) cards.push({val: sp[k], lbl: k});
  document.getElementById('stat-grid').innerHTML = cards.map(c =>
    `<div class="stat-card"><div class="val">${c.val ?? '-'}</div><div class="lbl">${c.lbl}</div></div>`
  ).join('');

  const evBody = document.getElementById('ev-body');
  evBody.innerHTML = '';
  for (const ev of (data.eventos||[])) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${ev.t_s}</td><td>${ev.regla_id}</td>
      <td><span class="tag tag-block">${ev.bloque||'-'}</span></td>
      <td><span class="tag tag-action">${ev.acciones}</span></td>
      <td>${ev.belief}</td>`;
    evBody.appendChild(tr);
  }
}

// ============================================================
// Router del sidebar (hash -> seccion)
// ============================================================
const SECCIONES = ['reglas','filtros','defuzzy','fuzzy','variables','permisivos'];
const _seccionLoaded = {reglas: false, filtros: false, defuzzy: false, fuzzy: false, variables: false, permisivos: false};

function _activarSeccion(name) {
  if (!SECCIONES.includes(name)) name = 'reglas';
  for (const s of SECCIONES) {
    const sec = document.getElementById('seccion-' + s);
    if (sec) sec.classList.toggle('active', s === name);
  }
  for (const a of document.querySelectorAll('.sidebar a[data-sec]')) {
    a.classList.toggle('active', a.dataset.sec === name);
  }
  if (name === 'reglas' && !_seccionLoaded.reglas) {
    loadRules();
    _seccionLoaded.reglas = true;
  }
  if (name === 'filtros' && !_seccionLoaded.filtros) {
    loadFiltros();
    _seccionLoaded.filtros = true;
  }
  if (name === 'defuzzy' && !_seccionLoaded.defuzzy) {
    loadDefuzzy();
    _seccionLoaded.defuzzy = true;
  }
  if (name === 'fuzzy' && !_seccionLoaded.fuzzy) {
    loadFuzzy();
    _seccionLoaded.fuzzy = true;
  }
  if (name === 'variables' && !_seccionLoaded.variables) {
    loadVariables();
    _seccionLoaded.variables = true;
  }
  if (name === 'permisivos' && !_seccionLoaded.permisivos) {
    loadPermisivos();
    _seccionLoaded.permisivos = true;
  }
}

function _onHash() {
  const h = (location.hash || '#reglas').replace(/^#/, '');
  _activarSeccion(h);
}
window.addEventListener('hashchange', _onHash);

// ============================================================
// Filtros Exp-Q
// ============================================================
async function loadFiltros() {
  const r = await fetch('/api/filtros');
  const d = await r.json();
  const body = document.getElementById('filtros-body');
  body.innerHTML = '';
  for (const v of d.variables) {
    const cur = d.actual[v] || d.defaults[v];
    const def = d.defaults[v];
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td><span class="tag tag-var">' + v + '</span></td>'
      + '<td><input data-var="' + v + '" data-k="q" type="number" step="0.01" min="0" max="1" value="' + cur.q + '"></td>'
      + '<td><input data-var="' + v + '" data-k="window_size" type="number" step="1" min="1" max="1000" value="' + cur.window_size + '"></td>'
      + '<td style="color:#64748b;font-size:.78rem">q=' + def.q + ', ws=' + def.window_size + '</td>';
    body.appendChild(tr);
  }
  _setFiltrosMsg('');
}

function _setFiltrosMsg(text, color) {
  const m = document.getElementById('filtros-msg');
  m.textContent = text || '';
  m.style.color = color || '#94a3b8';
}

async function saveFiltros() {
  const payload = {};
  for (const inp of document.querySelectorAll('#filtros-body input')) {
    const v = inp.dataset.var, k = inp.dataset.k;
    if (!payload[v]) payload[v] = {};
    payload[v][k] = (k === 'window_size') ? parseInt(inp.value, 10) : parseFloat(inp.value);
  }
  const r = await fetch('/api/filtros', {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const d = await r.json();
  if (!r.ok) return _setFiltrosMsg(d.error || 'Error', '#ef4444');
  _setFiltrosMsg('Configuracion guardada. Se aplicara en la proxima simulacion.', '#22c55e');
  setTimeout(() => _setFiltrosMsg(''), 4000);
}

async function resetFiltros() {
  if (!confirm('Restaurar los defaults del nucleo en filtros.json?')) return;
  const r = await fetch('/api/filtros/reset', {method: 'POST'});
  if (!r.ok) return _setFiltrosMsg('Error al restaurar.', '#ef4444');
  await loadFiltros();
  _setFiltrosMsg('Restaurado a defaults.', '#22c55e');
  setTimeout(() => _setFiltrosMsg(''), 4000);
}

// ============================================================
// Defuzzy
// ============================================================
let _defuzzyState = null;  // {familias, acciones, defaults, actual}

async function loadDefuzzy() {
  const r = await fetch('/api/defuzzy');
  _defuzzyState = await r.json();
  const sel = document.getElementById('dfz-familia');
  sel.innerHTML = '';
  for (const f of _defuzzyState.familias) {
    const o = document.createElement('option'); o.value = f; o.textContent = f;
    sel.appendChild(o);
  }
  _renderDefuzzy();
}

function _setDefuzzyMsg(text, color) {
  const m = document.getElementById('defuzzy-msg');
  m.textContent = text || '';
  m.style.color = color || '#94a3b8';
}

function _renderDefuzzy() {
  if (!_defuzzyState) return;
  const fam = document.getElementById('dfz-familia').value;
  const tabla = _defuzzyState.actual[fam] || _defuzzyState.defaults[fam];
  const def = _defuzzyState.defaults[fam];
  const axis = tabla.belief_axis;
  const steps = tabla.steps_por_accion;
  const t = document.getElementById('defuzzy-table');
  let html = '<thead><tr><th style="width:170px">Accion / belief</th>';
  for (let i = 0; i < axis.length; i++) {
    html += '<th><input data-row="axis" data-col="' + i + '" type="number" step="0.01" min="0" max="1" value="' + axis[i] + '" style="width:80px"></th>';
  }
  html += '<th style="width:160px">Default core</th></tr></thead><tbody>';
  for (const k of _defuzzyState.acciones) {
    const row = steps[k] || [];
    const drow = (def.steps_por_accion[k]) || [];
    html += '<tr><td><span class="tag tag-action">' + k + '</span></td>';
    for (let i = 0; i < axis.length; i++) {
      const v = (row[i] === undefined) ? 0.0 : row[i];
      html += '<td><input data-row="' + k + '" data-col="' + i + '" type="number" step="0.01" value="' + v + '" style="width:80px"></td>';
    }
    html += '<td style="color:#64748b;font-size:.72rem">' + drow.join(', ') + '</td></tr>';
  }
  html += '</tbody>';
  t.innerHTML = html;
}

function _readDefuzzyTable() {
  const fam = document.getElementById('dfz-familia').value;
  const inputs = document.querySelectorAll('#defuzzy-table input');
  const axis = [];
  const steps = {};
  for (const k of _defuzzyState.acciones) steps[k] = [];
  for (const inp of inputs) {
    const row = inp.dataset.row, col = parseInt(inp.dataset.col, 10);
    const val = parseFloat(inp.value);
    if (row === 'axis') axis[col] = val;
    else steps[row][col] = val;
  }
  return {fam, payload: {belief_axis: axis, steps_por_accion: steps}};
}

function addDefuzzyCol() {
  if (!_defuzzyState) return;
  const fam = document.getElementById('dfz-familia').value;
  const t = _defuzzyState.actual[fam];
  // Calcula nuevo belief: si <1, agrega 1.0; si no, promedia los dos ultimos.
  const ax = t.belief_axis;
  const last = ax[ax.length - 1];
  const next = (last < 1.0) ? Math.min(1.0, +(last + 0.25).toFixed(3)) : last;
  if (next <= last) return _setDefuzzyMsg('No se puede agregar mas: el eje ya llega a 1.0.', '#fbbf24');
  t.belief_axis = ax.concat([next]);
  for (const k of _defuzzyState.acciones) {
    const arr = t.steps_por_accion[k];
    t.steps_por_accion[k] = arr.concat([arr[arr.length - 1] || 0.0]);
  }
  _renderDefuzzy();
}

function delDefuzzyCol() {
  if (!_defuzzyState) return;
  const fam = document.getElementById('dfz-familia').value;
  const t = _defuzzyState.actual[fam];
  if (t.belief_axis.length <= 2) return _setDefuzzyMsg('No se puede borrar: minimo 2 puntos.', '#fbbf24');
  t.belief_axis = t.belief_axis.slice(0, -1);
  for (const k of _defuzzyState.acciones) {
    t.steps_por_accion[k] = t.steps_por_accion[k].slice(0, -1);
  }
  _renderDefuzzy();
}

async function saveDefuzzy() {
  const {fam, payload} = _readDefuzzyTable();
  const body = {}; body[fam] = payload;
  const r = await fetch('/api/defuzzy', {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const d = await r.json();
  if (!r.ok) return _setDefuzzyMsg(d.error || 'Error', '#ef4444');
  _defuzzyState.actual = d.actual;
  _setDefuzzyMsg('Familia \'' + fam + '\' guardada. Se aplicara en la proxima simulacion.', '#22c55e');
  setTimeout(() => _setDefuzzyMsg(''), 4000);
}

async function resetDefuzzy() {
  if (!confirm('Restaurar TODAS las familias defuzzy al default del nucleo?')) return;
  const r = await fetch('/api/defuzzy/reset', {method: 'POST'});
  const d = await r.json();
  if (!r.ok) return _setDefuzzyMsg('Error al restaurar.', '#ef4444');
  _defuzzyState.actual = d.actual;
  _renderDefuzzy();
  _setDefuzzyMsg('Restaurado a defaults.', '#22c55e');
  setTimeout(() => _setDefuzzyMsg(''), 4000);
}

// ============================================================
// Fuzzy (membresias por variable)
// ============================================================
const _fuzzyState = {variables: [], tipoPorVar: {}, labelKeys: ['HIGH','OK','LOW'], defaults: {}, actual: {}};

async function loadFuzzy() {
  const r = await fetch('/api/fuzzy');
  const d = await r.json();
  _fuzzyState.variables   = d.variables;
  _fuzzyState.tipoPorVar  = d.tipo_por_var;
  _fuzzyState.labelKeys   = d.label_keys;
  _fuzzyState.defaults    = d.defaults;
  _fuzzyState.actual      = d.actual;
  const sel = document.getElementById('fz-variable');
  sel.innerHTML = _fuzzyState.variables.map(v => `<option value="${v}">${v}</option>`).join('');
  _renderFuzzy();
}

function _setFuzzyMsg(msg, color) {
  const el = document.getElementById('fuzzy-msg');
  el.textContent = msg || '';
  el.style.color = color || '#94a3b8';
}

function _renderFuzzy() {
  const sel = document.getElementById('fz-variable');
  const v = sel.value || _fuzzyState.variables[0];
  if (!v) return;
  document.getElementById('fz-tipo').textContent = _fuzzyState.tipoPorVar[v] || '?';
  const cfg = _fuzzyState.actual[v];
  if (!cfg) return;
  const offset = cfg.offset;
  const n = offset.length;
  const tbl = document.getElementById('fuzzy-table');
  let html = '<thead><tr><th>Punto</th>';
  for (let i = 0; i < n; i++) html += `<th>P${i+1}</th>`;
  html += '</tr></thead><tbody>';
  // Fila offset
  html += '<tr><td><strong>offset</strong></td>';
  for (let i = 0; i < n; i++) {
    html += `<td><input class="fz-off" data-i="${i}" type="number" step="any" value="${offset[i]}" style="width:90px"></td>`;
  }
  html += '</tr>';
  // Filas HIGH/OK/LOW
  for (const k of _fuzzyState.labelKeys) {
    const arr = cfg.labels[k] || [];
    html += `<tr><td><strong>${k}</strong></td>`;
    for (let i = 0; i < n; i++) {
      const val = (arr[i] !== undefined) ? arr[i] : 0;
      html += `<td><input class="fz-lbl" data-k="${k}" data-i="${i}" type="number" step="0.1" min="0" max="1" value="${val}" style="width:80px"></td>`;
    }
    html += '</tr>';
  }
  html += '</tbody>';
  tbl.innerHTML = html;
}

function _readFuzzyTable() {
  const sel = document.getElementById('fz-variable');
  const v = sel.value;
  const offInputs = document.querySelectorAll('.fz-off');
  const offset = Array.from(offInputs).map(el => parseFloat(el.value));
  const labels = {};
  for (const k of _fuzzyState.labelKeys) {
    const inputs = document.querySelectorAll(`.fz-lbl[data-k="${k}"]`);
    labels[k] = Array.from(inputs).map(el => parseFloat(el.value));
  }
  return {v, cfg: {offset, labels}};
}

function addFuzzyCol() {
  const {v, cfg} = _readFuzzyTable();
  const n = cfg.offset.length;
  const last = cfg.offset[n-1] || 0;
  const prev = cfg.offset[n-2] || (last - 1);
  const step = (last - prev) || 1;
  cfg.offset.push(last + step);
  for (const k of _fuzzyState.labelKeys) cfg.labels[k].push(0.0);
  _fuzzyState.actual[v] = {..._fuzzyState.actual[v], offset: cfg.offset, labels: cfg.labels};
  _renderFuzzy();
}

function delFuzzyCol() {
  const {v, cfg} = _readFuzzyTable();
  if (cfg.offset.length <= 3) {
    _setFuzzyMsg('Minimo 3 puntos.', '#ef4444');
    return;
  }
  cfg.offset.pop();
  for (const k of _fuzzyState.labelKeys) cfg.labels[k].pop();
  _fuzzyState.actual[v] = {..._fuzzyState.actual[v], offset: cfg.offset, labels: cfg.labels};
  _renderFuzzy();
}

async function saveFuzzy() {
  const {v, cfg} = _readFuzzyTable();
  const payload = {[v]: {offset: cfg.offset, labels: cfg.labels}};
  const r = await fetch('/api/fuzzy', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const d = await r.json();
  if (!r.ok) return _setFuzzyMsg(d.error || 'Error.', '#ef4444');
  _fuzzyState.actual = d.actual;
  _setFuzzyMsg('Variable \'' + v + '\' guardada. Se aplicara en la proxima simulacion.', '#22c55e');
  setTimeout(() => _setFuzzyMsg(''), 4000);
}

async function resetFuzzy() {
  if (!confirm('Restaurar TODAS las variables fuzzy al default del nucleo?')) return;
  const r = await fetch('/api/fuzzy/reset', {method: 'POST'});
  const d = await r.json();
  if (!r.ok) return _setFuzzyMsg('Error al restaurar.', '#ef4444');
  _fuzzyState.actual = d.actual;
  _renderFuzzy();
  _setFuzzyMsg('Restaurado a defaults.', '#22c55e');
  setTimeout(() => _setFuzzyMsg(''), 4000);
}

// ============================================================
// Variables (crudas + definiciones calculadas)
// ============================================================
const _varsState = {tipos: [], operaciones: [], variablesProceso: [], defaults: null, actual: null};

async function loadVariables() {
  const r = await fetch('/api/variables');
  const d = await r.json();
  _varsState.tipos            = d.tipos;
  _varsState.operaciones      = d.operaciones;
  _varsState.variablesProceso = d.variables_proceso;
  _varsState.defaults         = d.defaults;
  _varsState.actual           = JSON.parse(JSON.stringify(d.actual));
  _renderVariables();
}

function _setVariablesMsg(msg, color) {
  const el = document.getElementById('variables-msg');
  el.textContent = msg || '';
  el.style.color = color || '#94a3b8';
}

function _refsDisponibles(hastaIdx) {
  const refs = new Set();
  for (const k of Object.keys(_varsState.actual.crudas || {})) refs.add(k);
  for (const v of _varsState.variablesProceso) refs.add(v);
  refs.add('t_s');
  const defs = _varsState.actual.definiciones || [];
  for (let i = 0; i < Math.min(hastaIdx, defs.length); i++) {
    if (defs[i] && defs[i].nombre) refs.add(defs[i].nombre);
  }
  return Array.from(refs);
}

function _renderVariables() {
  // Crudas
  const ct = document.getElementById('crudas-table');
  let html = '<thead><tr><th style="width:30%">Nombre</th><th>Descripcion</th><th style="width:80px">Acciones</th></tr></thead><tbody>';
  const crudas = _varsState.actual.crudas || {};
  const entries = Object.entries(crudas);
  if (entries.length === 0) {
    html += '<tr><td colspan="3" style="color:#64748b">Sin variables crudas. Use "+ Cruda" para agregar.</td></tr>';
  }
  for (const [k, v] of entries) {
    html += `<tr>
      <td><input class="cr-nombre" data-old="${k}" type="text" value="${k}" style="width:100%"></td>
      <td><input class="cr-descr"  data-old="${k}" type="text" value="${(v||'').replace(/"/g,'&quot;')}" style="width:100%"></td>
      <td><button class="btn-sm btn-danger" onclick="delCruda('${k}')">Borrar</button></td>
    </tr>`;
  }
  html += '</tbody>';
  ct.innerHTML = html;

  // Definiciones
  const dt = document.getElementById('definiciones-table');
  let dh = '<thead><tr>'
    + '<th style="width:60px">Orden</th>'
    + '<th style="width:18%">Nombre</th>'
    + '<th style="width:12%">Tipo</th>'
    + '<th>Parametros</th>'
    + '<th>Descripcion</th>'
    + '<th style="width:140px">Acciones</th>'
    + '</tr></thead><tbody>';
  const defs = _varsState.actual.definiciones || [];
  if (defs.length === 0) {
    dh += '<tr><td colspan="6" style="color:#64748b">Sin definiciones. Use "+ Aritmetica" / "+ Rolling".</td></tr>';
  }
  defs.forEach((d, i) => {
    const refs = _refsDisponibles(i);
    const optsRef = refs.map(r => `<option value="${r}">${r}</option>`).join('');
    let params = '';
    if (d.tipo === 'aritmetica') {
      const opOpts = _varsState.operaciones.map(o => `<option value="${o}" ${o===d.operacion?'selected':''}>${o}</option>`).join('');
      const a = (d.args && d.args[0]) || '';
      const b = (d.args && d.args[1]) || '';
      params = `
        <select class="df-op" data-i="${i}">${opOpts}</select>
        <select class="df-arg0" data-i="${i}">${optsRef.replace(`value="${a}"`,`value="${a}" selected`)}</select>
        <select class="df-arg1" data-i="${i}">${optsRef.replace(`value="${b}"`,`value="${b}" selected`)}</select>`;
    } else {
      const a = d.arg || '';
      params = `
        <select class="df-arg" data-i="${i}">${optsRef.replace(`value="${a}"`,`value="${a}" selected`)}</select>
        ventana_min: <input class="df-win" data-i="${i}" type="number" min="0.1" step="0.1" value="${d.ventana_min}" style="width:80px">`;
    }
    dh += `<tr>
      <td>
        <button class="btn-sm" onclick="moveDef(${i},-1)" ${i===0?'disabled':''}>&uarr;</button>
        <button class="btn-sm" onclick="moveDef(${i},+1)" ${i===defs.length-1?'disabled':''}>&darr;</button>
      </td>
      <td><input class="df-nombre" data-i="${i}" type="text" value="${d.nombre}" style="width:100%"></td>
      <td><strong>${d.tipo}</strong></td>
      <td>${params}</td>
      <td><input class="df-descr" data-i="${i}" type="text" value="${(d.descripcion||'').replace(/"/g,'&quot;')}" style="width:100%"></td>
      <td><button class="btn-sm btn-danger" onclick="delDef(${i})">Borrar</button></td>
    </tr>`;
  });
  dh += '</tbody>';
  dt.innerHTML = dh;
}

function _commitCrudasFromInputs() {
  // Lee inputs cr-nombre/cr-descr y rebuild el dict (preserva orden de UI).
  const nombres = document.querySelectorAll('.cr-nombre');
  const descrs  = document.querySelectorAll('.cr-descr');
  const out = {};
  for (let i = 0; i < nombres.length; i++) {
    const n = nombres[i].value.trim();
    if (!n) continue;
    out[n] = descrs[i].value;
  }
  _varsState.actual.crudas = out;
}

function _commitDefinicionesFromInputs() {
  const defs = _varsState.actual.definiciones || [];
  defs.forEach((d, i) => {
    const nom = document.querySelector(`.df-nombre[data-i="${i}"]`);
    const des = document.querySelector(`.df-descr[data-i="${i}"]`);
    if (nom) d.nombre = nom.value.trim();
    if (des) d.descripcion = des.value;
    if (d.tipo === 'aritmetica') {
      const op = document.querySelector(`.df-op[data-i="${i}"]`);
      const a0 = document.querySelector(`.df-arg0[data-i="${i}"]`);
      const a1 = document.querySelector(`.df-arg1[data-i="${i}"]`);
      if (op) d.operacion = op.value;
      d.args = [a0 ? a0.value : '', a1 ? a1.value : ''];
    } else {
      const ar = document.querySelector(`.df-arg[data-i="${i}"]`);
      const wn = document.querySelector(`.df-win[data-i="${i}"]`);
      if (ar) d.arg = ar.value;
      if (wn) d.ventana_min = parseFloat(wn.value);
    }
  });
}

function addCruda() {
  _commitCrudasFromInputs();
  let n = 1;
  while (_varsState.actual.crudas['cruda_' + n] !== undefined) n++;
  _varsState.actual.crudas['cruda_' + n] = '';
  _renderVariables();
}

function delCruda(nombre) {
  _commitCrudasFromInputs();
  delete _varsState.actual.crudas[nombre];
  _renderVariables();
}

function addDefinicion(tipo) {
  _commitCrudasFromInputs();
  _commitDefinicionesFromInputs();
  const defs = _varsState.actual.definiciones;
  let n = 1;
  const taken = new Set(defs.map(d => d.nombre));
  while (taken.has('var_calc_' + n)) n++;
  const ref0 = _refsDisponibles(defs.length)[0] || 't_s';
  let item = {nombre: 'var_calc_' + n, descripcion: '', tipo};
  if (tipo === 'aritmetica') {
    item.operacion = 'suma';
    item.args = [ref0, ref0];
  } else {
    item.arg = ref0;
    item.ventana_min = 30.0;
  }
  defs.push(item);
  _renderVariables();
}

function delDef(i) {
  _commitCrudasFromInputs();
  _commitDefinicionesFromInputs();
  _varsState.actual.definiciones.splice(i, 1);
  _renderVariables();
}

function moveDef(i, delta) {
  _commitCrudasFromInputs();
  _commitDefinicionesFromInputs();
  const defs = _varsState.actual.definiciones;
  const j = i + delta;
  if (j < 0 || j >= defs.length) return;
  const tmp = defs[i]; defs[i] = defs[j]; defs[j] = tmp;
  _renderVariables();
}

async function saveVariables() {
  _commitCrudasFromInputs();
  _commitDefinicionesFromInputs();
  const r = await fetch('/api/variables', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(_varsState.actual),
  });
  const d = await r.json();
  if (!r.ok) return _setVariablesMsg(d.error || 'Error.', '#ef4444');
  _varsState.actual = JSON.parse(JSON.stringify(d.actual));
  _renderVariables();
  _setVariablesMsg('Catalogo guardado. Se aplicara en la proxima simulacion.', '#22c55e');
  setTimeout(() => _setVariablesMsg(''), 4000);
}

async function resetVariables() {
  if (!confirm('Restaurar variables (crudas + definiciones) al default del nucleo?')) return;
  const r = await fetch('/api/variables/reset', {method: 'POST'});
  const d = await r.json();
  if (!r.ok) return _setVariablesMsg('Error al restaurar.', '#ef4444');
  _varsState.actual = JSON.parse(JSON.stringify(d.actual));
  _renderVariables();
  _setVariablesMsg('Restaurado a defaults.', '#22c55e');
  setTimeout(() => _setVariablesMsg(''), 4000);
}

// ============================================================
// Permisivos
// ============================================================
const _permState = {operadores: [], fuzzyVars: [], defaults: null, actual: null};

async function loadPermisivos() {
  const r = await fetch('/api/permisivos');
  const d = await r.json();
  _permState.operadores = d.operadores;
  _permState.fuzzyVars  = d.fuzzy_vars;
  _permState.defaults   = d.defaults;
  _permState.actual     = JSON.parse(JSON.stringify(d.actual));
  _renderPermisivos();
}

function _setPermisivosMsg(msg, color) {
  const el = document.getElementById('permisivos-msg');
  el.textContent = msg || '';
  el.style.color = color || '#94a3b8';
}

function _escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _renderPermisivos() {
  const cont = document.getElementById('permisivos-list');
  const nombres = Object.keys(_permState.actual);
  if (nombres.length === 0) {
    cont.innerHTML = '<div style="color:#64748b">Sin permisivos. Use "+ Permisivo".</div>';
    return;
  }
  let html = '';
  for (const nom of nombres) {
    const conds = _permState.actual[nom] || [];
    html += `<div class="placeholder-card" style="margin-bottom:14px">`;
    html += `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px">`;
    html += `<div style="display:flex;gap:6px;align-items:center;flex:1">`;
    html += `<strong style="color:#22d3ee">Nombre:</strong>`;
    html += `<input class="perm-nombre" data-old="${nom}" type="text" value="${_escapeHtml(nom)}" style="min-width:280px">`;
    html += `<span style="color:#94a3b8;font-size:.8rem">expuesto como <code>__PERM_${_escapeHtml(nom)}</code></span>`;
    html += `</div>`;
    html += `<div style="display:flex;gap:6px">`;
    html += `<button class="btn-sm btn-success" onclick="addCondicion('${nom}')">+ Condicion</button>`;
    html += `<button class="btn-sm btn-danger"  onclick="delPermisivo('${nom}')">Borrar permisivo</button>`;
    html += `</div></div>`;
    html += `<table><thead><tr><th style="width:50px">#</th><th>Condicion (JSON)</th><th style="width:80px">Acciones</th></tr></thead><tbody>`;
    if (conds.length === 0) {
      html += `<tr><td colspan="3" style="color:#64748b">Sin condiciones.</td></tr>`;
    }
    conds.forEach((c, i) => {
      const txt = JSON.stringify(c, null, 0);
      html += `<tr>`;
      html += `<td style="color:#94a3b8">${i}</td>`;
      html += `<td><textarea class="perm-cond" data-perm="${nom}" data-i="${i}" rows="2" style="width:100%;font-family:ui-monospace,monospace;font-size:.8rem;background:#0f172a;color:#cbd5e1">${_escapeHtml(txt)}</textarea></td>`;
      html += `<td><button class="btn-sm btn-danger" onclick="delCondicion('${nom}',${i})">Borrar</button></td>`;
      html += `</tr>`;
    });
    html += `</tbody></table></div>`;
  }
  cont.innerHTML = html;
}

function _commitPermisivosFromInputs() {
  // 1) condiciones: parsear textareas en el orden actual del state.
  const errores = [];
  for (const nom of Object.keys(_permState.actual)) {
    const conds = _permState.actual[nom];
    conds.forEach((_, i) => {
      const ta = document.querySelector(`.perm-cond[data-perm="${nom}"][data-i="${i}"]`);
      if (!ta) return;
      try {
        conds[i] = JSON.parse(ta.value);
      } catch (e) {
        errores.push(`${nom}[${i}]: JSON invalido (${e.message})`);
      }
    });
  }
  // 2) renombrar permisivos en base a inputs perm-nombre (preserva orden).
  const inputs = document.querySelectorAll('.perm-nombre');
  const out = {};
  const vistos = new Set();
  for (const inp of inputs) {
    const oldName = inp.dataset.old;
    const newName = (inp.value || '').trim().toUpperCase();
    if (!newName) { errores.push(`Nombre vacio (anterior: ${oldName}).`); continue; }
    if (vistos.has(newName)) { errores.push(`Nombre duplicado: ${newName}.`); continue; }
    vistos.add(newName);
    out[newName] = _permState.actual[oldName] || [];
  }
  _permState.actual = out;
  return errores;
}

function addPermisivo() {
  const errs = _commitPermisivosFromInputs();
  const inp = document.getElementById('perm-new-nombre');
  const nom = (inp.value || '').trim().toUpperCase();
  if (!nom) return _setPermisivosMsg('Debe indicar un nombre.', '#ef4444');
  if (_permState.actual[nom]) return _setPermisivosMsg(`Ya existe: ${nom}`, '#ef4444');
  _permState.actual[nom] = [{"var": "tonelaje_sag_delta_30min", "op": ">", "value": 0}];
  inp.value = '';
  _renderPermisivos();
  if (errs.length) _setPermisivosMsg('Avisos: ' + errs.join(' | '), '#f59e0b');
  else _setPermisivosMsg('');
}

function delPermisivo(nombre) {
  if (!confirm(`Borrar permisivo ${nombre}?`)) return;
  _commitPermisivosFromInputs();
  delete _permState.actual[nombre];
  _renderPermisivos();
}

function addCondicion(nombre) {
  const errs = _commitPermisivosFromInputs();
  // El renombrado pudo cambiar la key; ubicar la actual coincidente.
  let key = nombre;
  if (!(key in _permState.actual)) {
    for (const k of Object.keys(_permState.actual)) { key = k; break; }
  }
  if (!_permState.actual[key]) return;
  _permState.actual[key].push({"fuzzy_var": "torque", "label": "OK", "min_mu": 0.5});
  _renderPermisivos();
  if (errs.length) _setPermisivosMsg('Avisos: ' + errs.join(' | '), '#f59e0b');
}

function delCondicion(nombre, idx) {
  _commitPermisivosFromInputs();
  const arr = _permState.actual[nombre];
  if (!arr) return;
  arr.splice(idx, 1);
  _renderPermisivos();
}

async function savePermisivos() {
  const errs = _commitPermisivosFromInputs();
  if (errs.length) return _setPermisivosMsg('Errores: ' + errs.join(' | '), '#ef4444');
  const r = await fetch('/api/permisivos', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(_permState.actual),
  });
  const d = await r.json();
  if (!r.ok) return _setPermisivosMsg(d.error || 'Error.', '#ef4444');
  _permState.actual = JSON.parse(JSON.stringify(d.actual));
  _renderPermisivos();
  _setPermisivosMsg('Permisivos guardados. Se aplicaran en la proxima simulacion.', '#22c55e');
  setTimeout(() => _setPermisivosMsg(''), 4000);
}

async function resetPermisivos() {
  if (!confirm('Restaurar permisivos al default del nucleo?')) return;
  const r = await fetch('/api/permisivos/reset', {method: 'POST'});
  const d = await r.json();
  if (!r.ok) return _setPermisivosMsg('Error al restaurar.', '#ef4444');
  _permState.actual = JSON.parse(JSON.stringify(d.actual));
  _renderPermisivos();
  _setPermisivosMsg('Restaurado a defaults.', '#22c55e');
  setTimeout(() => _setPermisivosMsg(''), 4000);
}

_onHash();
</script>
</main>
</body>
</html>"""


@app.route("/")
def index():
    page = HTML_PAGE
    page = page.replace("VARIABLES_JSON", json.dumps(VARIABLES_DISPONIBLES))
    page = page.replace("LABELS_JSON", json.dumps(ETIQUETAS_DISPONIBLES))
    page = page.replace("ACTIONS_JSON", json.dumps(ACCIONES_DISPONIBLES))
    page = page.replace("BLOCKS_JSON", json.dumps(BLOQUES_DISPONIBLES))
    return Response(page, mimetype="text/html")


# ============================================================
# UI -- Graficos en tiempo real
# ============================================================

# PVs principales mostradas como graficos (las mas relevantes del Espesador).
CHART_VARS = [
    {"key": "torque",              "label": "Torque (%)",              "color": "#38bdf8"},
    {"key": "bed_level",           "label": "Bed Level (m)",           "color": "#a78bfa"},
    {"key": "densidad",            "label": "Densidad descarga (%)",   "color": "#22c55e"},
    {"key": "presion_descarga",    "label": "Presion descarga",        "color": "#fb923c"},
    {"key": "presion_diferencial", "label": "Presion diferencial",     "color": "#f472b6"},
    {"key": "nivel_rastra",        "label": "Nivel rastra (%)",        "color": "#facc15"},
]

CHARTS_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espesador -- Graficos en Tiempo Real</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}
h1{color:#38bdf8;margin-bottom:4px}
h2{color:#94a3b8;font-size:.9rem;margin-bottom:16px;font-weight:400}
nav{display:flex;gap:16px;margin-bottom:16px}
nav a{color:#94a3b8;text-decoration:none;padding-bottom:2px}
nav a.active{color:#38bdf8;font-weight:600;border-bottom:2px solid #38bdf8}
button{cursor:pointer;border:none;border-radius:6px;padding:8px 16px;font-size:.875rem;font-weight:600;transition:.15s}
.btn-success{background:#22c55e;color:#fff}.btn-success:hover{background:#16a34a}
.btn-danger{background:#ef4444;color:#fff}.btn-danger:hover{background:#dc2626}
.btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}
.controls{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.controls label{font-size:.8rem;color:#94a3b8}
.controls select,.controls input{background:#1e293b;border:1px solid #475569;color:#e2e8f0;border-radius:6px;padding:4px 8px;font-size:.8rem}
.status-bar{background:#1e293b;border-radius:8px;padding:10px 16px;margin-bottom:16px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
.status-bar .item{text-align:center}
.status-bar .val{font-size:1.1rem;font-weight:700;color:#38bdf8}
.status-bar .lbl{font-size:.65rem;color:#94a3b8;text-transform:uppercase}
.progress-bar{width:100%;height:6px;background:#334155;border-radius:3px;overflow:hidden;margin-bottom:4px}
.progress-bar .fill{height:100%;background:#22c55e;transition:width .3s}
.charts-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
.chart-card{background:#1e293b;border-radius:10px;padding:14px;position:relative}
.chart-card h3{font-size:.8rem;color:#94a3b8;margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em}
.chart-card canvas{width:100%!important;height:220px!important}
.events-panel{background:#1e293b;border-radius:10px;padding:14px;max-height:260px;overflow-y:auto}
.events-panel h3{font-size:.8rem;color:#94a3b8;margin-bottom:8px;text-transform:uppercase}
.ev-item{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #334155;font-size:.8rem}
.ev-item .t{color:#38bdf8;min-width:55px}
.ev-item .r{color:#a78bfa;min-width:60px}
.ev-item .b{color:#22c55e;min-width:80px;font-size:.7rem;text-transform:uppercase}
.ev-item .a{color:#fb923c;flex:1}
@media(max-width:900px){.charts-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<nav>
  <a href="/">Reglas</a>
  <a href="/graficos" class="active">Graficos en Vivo</a>
</nav>
<h1>Espesador -- Graficos en Tiempo Real</h1>
<h2>Cada tick muestra los nuevos puntos. Las reglas activas se listan en la columna de eventos.</h2>

<div class="controls">
  <button class="btn-success" id="btn-start" onclick="startStream()">&#9654; Iniciar Simulacion</button>
  <button class="btn-danger"  id="btn-stop"  onclick="stopStream()" disabled>&#9632; Detener</button>
  <button class="btn-primary" id="btn-reset" onclick="resetStream()">&#8634; Reiniciar</button>
  <label>Velocidad:
    <select id="sel-speed" onchange="changeSpeed()">
      <option value="5000">5s (real)</option>
      <option value="2000" selected>2s (rapido)</option>
      <option value="1000">1s (muy rapido)</option>
      <option value="500">0.5s (turbo)</option>
    </select>
  </label>
  <label>Puntos por tick:
    <select id="sel-batch">
      <option value="3">3</option>
      <option value="5" selected>5</option>
      <option value="10">10</option>
      <option value="20">20</option>
    </select>
  </label>
</div>

<div class="progress-bar"><div class="fill" id="progress-fill" style="width:0%"></div></div>
<div class="status-bar" id="status-bar"></div>

<div class="charts-grid" id="charts-grid"></div>

<div class="events-panel">
  <h3>Eventos en Vivo</h3>
  <div id="ev-list"><span style="color:#64748b;font-size:.8rem">Sin eventos aun...</span></div>
</div>

<script>
const CHART_VARS = CHART_VARS_JSON;
const SP_KEYS    = SP_KEYS_JSON;
const MAX_PTS    = 300;
let timer = null;
let totalEventos = 0;
const charts = {};
let spChart = null;

// Construye stat cards dinamicas para los SP.
(function buildStatBar() {
  const bar = document.getElementById('status-bar');
  const base = [
    {id:'st-cursor',  lbl:'Muestra'},
    {id:'st-total',   lbl:'Total'},
    {id:'st-time',    lbl:'t (s)'},
    {id:'st-eventos', lbl:'Eventos'},
  ];
  const sp = SP_KEYS.map(k => ({id:'st-' + k, lbl:k}));
  const status = [{id:'st-status', lbl:'Estado', color:'#94a3b8'}];
  const items = [...base, ...sp, ...status];
  bar.innerHTML = items.map(it =>
    `<div class="item"><div class="val" id="${it.id}" ${it.color?`style="color:${it.color}"`:''}>-</div>
      <div class="lbl">${it.lbl}</div></div>`
  ).join('');
})();

// Construye un chart por variable de proceso.
(function buildCharts() {
  const grid = document.getElementById('charts-grid');
  for (const cv of CHART_VARS) {
    const card = document.createElement('div');
    card.className = 'chart-card';
    card.innerHTML = `<h3>${cv.label}</h3><canvas id="ch-${cv.key}"></canvas>`;
    grid.appendChild(card);
    charts[cv.key] = new Chart(document.getElementById('ch-' + cv.key), {
      type:'line',
      data:{labels:[], datasets:[{label:cv.label, data:[], borderColor:cv.color,
            backgroundColor:cv.color+'22', borderWidth:2, pointRadius:0, fill:true, tension:.3}]},
      options:{animation:false, responsive:true, maintainAspectRatio:false,
        scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8},grid:{color:'#1e293b'}},
                y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
        plugins:{legend:{display:false}}}
    });
  }
  // Setpoints (una grafica con N datasets).
  const card = document.createElement('div');
  card.className = 'chart-card';
  card.innerHTML = `<h3>Setpoints</h3><canvas id="ch-sp"></canvas>`;
  document.getElementById('charts-grid').appendChild(card);
  const palette = ['#38bdf8','#a78bfa','#fb923c','#f472b6','#22c55e','#facc15'];
  spChart = new Chart(document.getElementById('ch-sp'), {
    type:'line',
    data:{labels:[], datasets: SP_KEYS.map((k,i) => ({
      label:k, data:[], borderColor:palette[i%palette.length],
      borderWidth:2, pointRadius:0, tension:.3
    }))},
    options:{animation:false, responsive:true, maintainAspectRatio:false,
      scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8},grid:{color:'#1e293b'}},
              y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
      plugins:{legend:{display:true,labels:{color:'#94a3b8',font:{size:10}}}}}
  });
})();

function pushPt(chart, label, value) {
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (chart.data.labels.length > MAX_PTS) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
}

function pushSp(label, values) {
  spChart.data.labels.push(label);
  SP_KEYS.forEach((k,i) => spChart.data.datasets[i].data.push(values[k]));
  if (spChart.data.labels.length > MAX_PTS) {
    spChart.data.labels.shift();
    spChart.data.datasets.forEach(ds => ds.data.shift());
  }
}

function clearCharts() {
  Object.values(charts).forEach(ch => {
    ch.data.labels = []; ch.data.datasets[0].data = []; ch.update();
  });
  spChart.data.labels = [];
  spChart.data.datasets.forEach(ds => ds.data = []);
  spChart.update();
  document.getElementById('ev-list').innerHTML =
    '<span style="color:#64748b;font-size:.8rem">Sin eventos aun...</span>';
  totalEventos = 0;
  document.getElementById('st-eventos').textContent = '0';
}

async function startStream() {
  if (timer) return;
  document.getElementById('btn-start').disabled = true;
  document.getElementById('btn-stop').disabled = false;
  document.getElementById('st-status').textContent = 'Iniciando...';
  document.getElementById('st-status').style.color = '#fbbf24';

  clearCharts();
  const batch = parseInt(document.getElementById('sel-batch').value) || 5;
  const res = await fetch('/api/simulacion/start', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({batch_size: batch})
  });
  const data = await res.json();
  if (!data.ok) { alert(data.error || 'Error iniciando'); stopStream(); return; }
  document.getElementById('st-total').textContent = data.total;
  document.getElementById('st-status').textContent = 'En vivo';
  document.getElementById('st-status').style.color = '#22c55e';

  const speed = parseInt(document.getElementById('sel-speed').value) || 2000;
  timer = setInterval(fetchNext, speed);
  fetchNext();
}

function stopStream() {
  if (timer) { clearInterval(timer); timer = null; }
  document.getElementById('btn-start').disabled = false;
  document.getElementById('btn-stop').disabled = true;
  document.getElementById('st-status').textContent = 'Detenido';
  document.getElementById('st-status').style.color = '#94a3b8';
}

async function resetStream() {
  stopStream();
  clearCharts();
  document.getElementById('st-cursor').textContent = '0';
  document.getElementById('st-time').textContent = '0.0';
  document.getElementById('progress-fill').style.width = '0%';
  ['st-total', ...SP_KEYS.map(k=>'st-'+k)].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '-';
  });
  await fetch('/api/simulacion/reset', {method:'POST'});
}

function changeSpeed() {
  if (!timer) return;
  clearInterval(timer);
  const speed = parseInt(document.getElementById('sel-speed').value) || 2000;
  timer = setInterval(fetchNext, speed);
}

async function fetchNext() {
  try {
    const res = await fetch('/api/simulacion/next');
    const data = await res.json();
    if (!data.ok) return;
    if (data.done) {
      stopStream();
      document.getElementById('st-status').textContent = 'Completado';
      document.getElementById('st-status').style.color = '#38bdf8';
      return;
    }

    for (const p of data.points) {
      const lbl = p.t_min.toFixed(1);
      for (const cv of CHART_VARS) {
        if (p[cv.key] !== undefined) pushPt(charts[cv.key], lbl, p[cv.key]);
      }
      const spVals = {};
      SP_KEYS.forEach(k => spVals[k] = p[k]);
      pushSp(lbl, spVals);
    }
    Object.values(charts).forEach(ch => ch.update());
    spChart.update();

    const last = data.points[data.points.length - 1];
    document.getElementById('st-cursor').textContent = data.cursor;
    document.getElementById('st-time').textContent = last.t_s;
    SP_KEYS.forEach(k => {
      const el = document.getElementById('st-' + k);
      if (el && last[k] !== undefined) el.textContent = last[k];
    });
    document.getElementById('progress-fill').style.width =
      (data.cursor / data.total * 100).toFixed(1) + '%';

    if (data.eventos && data.eventos.length > 0) {
      const el = document.getElementById('ev-list');
      if (totalEventos === 0) el.innerHTML = '';
      for (const ev of data.eventos) {
        totalEventos++;
        const div = document.createElement('div');
        div.className = 'ev-item';
        div.innerHTML = `<span class="t">${ev.t_s}s</span>
          <span class="r">${ev.regla_id}</span>
          <span class="b">${ev.bloque||''}</span>
          <span class="a">${ev.acciones}</span>`;
        el.prepend(div);
      }
      document.getElementById('st-eventos').textContent = totalEventos;
    }
  } catch (e) {
    console.error(e);
  }
}
</script>
</body>
</html>"""


@app.route("/graficos")
def graficos():
    page = CHARTS_PAGE
    page = page.replace("CHART_VARS_JSON", json.dumps(CHART_VARS))
    page = page.replace("SP_KEYS_JSON", json.dumps(SETPOINT_KEYS))
    return Response(page, mimetype="text/html")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Sistema Experto Espesador (v2) -- Editor de Reglas")
    print("  http://127.0.0.1:5000")
    print("  http://127.0.0.1:5000/graficos  (graficos en tiempo real)")
    print("=" * 60)
    app.run(debug=True, host="127.0.0.1", port=5000)
