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
  GET    /api/tags                 -- Listar todos los tags con su estado KEPserver.
  POST   /api/tags                 -- Crear un tag nuevo.
  PUT    /api/tags/<id>            -- Actualizar un tag (nombre, tipo, enabled).
  DELETE /api/tags/<id>            -- Eliminar un tag.
  POST   /api/tags/<id>/write      -- Escribir un valor al KEPserver.
  POST   /api/tags/refresh         -- Re-leer todos los valores del KEPserver.
  GET    /api/licenciamiento       -- Estado actual del licenciamiento (prototipo).
  POST   /api/licenciamiento/activar -- Activar licencia por 3/6/9/12 meses.
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
import threading
import time
import traceback
from calendar import monthrange
from collections import deque
from datetime import datetime

from flask import Flask, Response, jsonify, request

from config import (
    SETPOINT_KEYS,
    VARIABLES_CRUDAS_REQUERIDAS,
    VARIABLES_EXTERNAS,
    VARIABLES_PROCESO,
)
from defuzzy_actions import DEFUZZY_POR_FAMILIA
from estados_espesador import ESTADOS_ESPESADOR
from exp_q_filter import CONFIG_FILTRO_ESPESADOR_DEFAULT
from fuzzys_models_espesador import FUZZY_MODELOS
from calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS
from permisivos import PERMISIVOS, nombre_variable_permisivo
from waits_catalogo import (
    WAIT_FLOCULANTE_CRITICO,
    WAIT_FLOCULANTE_ESTABILIDAD,
    WAIT_TONELAJE_CRITICO,
    WAIT_TONELAJE_ESTABILIDAD,
    WAIT_VEL_BOMBA_CRITICO,
    WAIT_VEL_BOMBA_ESTABILIDAD,
    WAIT_VEL_PRESION_CAMA,
    WAIT_VEL_SOLIDOS,
)

app = Flask(__name__)


# ============================================================
# Sistema de alertas centralizadas
# ============================================================

class AlertCollector:
    """Collects and deduplicates system alerts from multiple sources."""

    CATEGORIES = {
        "import":    {"label": "Error de Import",       "color": "#ef4444", "icon": "!"},
        "kep":       {"label": "Conexion KEPserver",    "color": "#f97316", "icon": "K"},
        "reglas":    {"label": "Reglas / Motor",        "color": "#f59e0b", "icon": "R"},
        "se_engine": {"label": "Motor SE",              "color": "#a855f7", "icon": "S"},
        "generator": {"label": "Generador de Datos",    "color": "#6366f1", "icon": "G"},
        "config":    {"label": "Configuracion / JSON",  "color": "#ec4899", "icon": "C"},
        "general":   {"label": "Error General",         "color": "#64748b", "icon": "?"},
    }

    def __init__(self):
        self._alerts: list[dict] = []
        self._lock = threading.Lock()
        self._next_id = 1

    def add(self, category: str, message: str, detail: str = ""):
        with self._lock:
            for a in self._alerts:
                if a["category"] == category and a["message"] == message and not a["resolved"]:
                    a["count"] += 1
                    a["last_seen"] = time.time()
                    return
            self._alerts.append({
                "id": self._next_id,
                "category": category,
                "message": message,
                "detail": detail,
                "count": 1,
                "first_seen": time.time(),
                "last_seen": time.time(),
                "resolved": False,
            })
            self._next_id += 1

    def resolve(self, alert_id: int):
        with self._lock:
            for a in self._alerts:
                if a["id"] == alert_id:
                    a["resolved"] = True
                    return True
            return False

    def resolve_category(self, category: str):
        with self._lock:
            for a in self._alerts:
                if a["category"] == category and not a["resolved"]:
                    a["resolved"] = True

    def get_active(self) -> list[dict]:
        with self._lock:
            return [dict(a) for a in self._alerts if not a["resolved"]]

    def get_all(self) -> list[dict]:
        with self._lock:
            return [dict(a) for a in self._alerts]

    def clear_resolved(self):
        with self._lock:
            self._alerts = [a for a in self._alerts if not a["resolved"]]


_alerts = AlertCollector()

REGLAS_JSON     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reglas.json")
FILTROS_JSON    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filtros.json")
DEFUZZY_JSON    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defuzzy.json")
FUZZY_JSON      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuzzy.json")
VARIABLES_JSON  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "variables.json")
PERMISIVOS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "permisivos.json")
TAGS_JSON       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tags.json")
LICENCIA_JSON   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "licenciamiento.json")


# ============================================================
# Licenciamiento (prototipo)
# ============================================================

LICENCIA_PERIODOS_VALIDOS = (3, 6, 9, 12)


def _add_months(base: datetime, months: int) -> datetime:
  month_index = (base.month - 1) + months
  year = base.year + (month_index // 12)
  month = (month_index % 12) + 1
  day = min(base.day, monthrange(year, month)[1])
  return base.replace(year=year, month=month, day=day)


def _mask_license_code(code: str) -> str:
  raw = (code or "").strip()
  if not raw:
    return "No ingresado"
  if len(raw) <= 4:
    return "*" * len(raw)
  return ("*" * (len(raw) - 4)) + raw[-4:]


def _default_license_store() -> dict:
  return {
    "prototype": True,
    "codigo": "",
    "periodo_meses": None,
    "activada_en": None,
    "expira_en": None,
    "notas": "Prototipo: aun no se definen funciones a habilitar o deshabilitar al expirar.",
  }


def _save_license_store(data: dict) -> None:
  with open(LICENCIA_JSON, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)


def _load_license_store() -> dict:
  if not os.path.exists(LICENCIA_JSON):
    data = _default_license_store()
    _save_license_store(data)
    return data
  try:
    with open(LICENCIA_JSON, "r", encoding="utf-8") as f:
      data = json.load(f)
  except (OSError, ValueError):
    return _default_license_store()
  if not isinstance(data, dict):
    return _default_license_store()
  merged = _default_license_store()
  merged.update(data)
  return merged


def _build_license_public_state(data: dict) -> dict:
  state = _default_license_store()
  state.update(data or {})

  now = datetime.now()
  activada_en = None
  expira_en = None
  try:
    if state.get("activada_en"):
      activada_en = datetime.fromisoformat(str(state.get("activada_en")))
  except ValueError:
    activada_en = None
  try:
    if state.get("expira_en"):
      expira_en = datetime.fromisoformat(str(state.get("expira_en")))
  except ValueError:
    expira_en = None

  if not state.get("codigo") or activada_en is None or expira_en is None:
    status = "sin_activar"
    estado_label = "Sin activar"
    dias_restantes = None
  elif expira_en < now:
    status = "expirada"
    estado_label = "Expirada"
    dias_restantes = 0
  else:
    status = "activa"
    estado_label = "Activa"
    dias_restantes = max(0, int((expira_en - now).total_seconds() // 86400))

  return {
    "prototype": True,
    "status": status,
    "estado_label": estado_label,
    "codigo_masked": _mask_license_code(str(state.get("codigo") or "")),
    "periodo_meses": state.get("periodo_meses"),
    "activada_en": state.get("activada_en"),
    "expira_en": state.get("expira_en"),
    "dias_restantes": dias_restantes,
    "notas": state.get("notas") or _default_license_store()["notas"],
  }


@app.route("/api/licenciamiento", methods=["GET"])
def api_get_licenciamiento():
  return jsonify(_build_license_public_state(_load_license_store()))


@app.route("/api/licenciamiento/activar", methods=["POST"])
def api_activate_licenciamiento():
  body = request.get_json(force=True)
  codigo = str(body.get("codigo") or "").strip()
  if not codigo:
    return jsonify({"error": "Debes ingresar un codigo de licencia."}), 400

  try:
    periodo_meses = int(body.get("periodo_meses"))
  except (TypeError, ValueError):
    return jsonify({"error": "El periodo debe ser 3, 6, 9 o 12 meses."}), 400

  if periodo_meses not in LICENCIA_PERIODOS_VALIDOS:
    return jsonify({"error": "Periodo invalido. Usa 3, 6, 9 o 12 meses."}), 400

  activada_en = datetime.now().replace(microsecond=0)
  expira_en = _add_months(activada_en, periodo_meses)
  store = _default_license_store()
  store.update({
    "codigo": codigo,
    "periodo_meses": periodo_meses,
    "activada_en": activada_en.isoformat(),
    "expira_en": expira_en.isoformat(),
  })
  _save_license_store(store)
  return jsonify({"ok": True, "licencia": _build_license_public_state(store)})


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


def _build_estados_serializable() -> dict:
    """Serializa ESTADOS_ESPESADOR para enviar al frontend."""
    from estados_builder import _resolver_condicion
    result = {}
    for nombre, estado in ESTADOS_ESPESADOR.items():
        condicion = estado.get("condicion")
        result[nombre] = {
            "nombre": nombre,
            "tipo": estado.get("tipo", "estado"),
            "condicion": _resolver_condicion(condicion) if condicion else [],
        }
    return result


ESTADOS_SERIALIZADOS = _build_estados_serializable()

WAITS_CATALOGO_DISPONIBLES = [
    WAIT_VEL_BOMBA_CRITICO,
    WAIT_VEL_BOMBA_ESTABILIDAD,
    WAIT_TONELAJE_CRITICO,
    WAIT_TONELAJE_ESTABILIDAD,
    WAIT_FLOCULANTE_CRITICO,
    WAIT_FLOCULANTE_ESTABILIDAD,
    WAIT_VEL_SOLIDOS,
    WAIT_VEL_PRESION_CAMA,
]


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

    # Fuerza (opcional): leaf [v,l] o {OR: [[v,l],...]}
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

    # Formato nuevo: lista top-level de items donde cada item es:
    #   - hoja: [variable, etiqueta]
    #   - grupo OR: {"OR": [[v,l], ...]}
    #   - grupo AND (estado): {"AND": [...]}
    # Formato legacy (single AND wrapper): [{"AND": [...]}] con solo hojas/OR dentro
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
        # Grupo AND (estado/subestado)
        if isinstance(item, dict) and "AND" in item:
            and_norm, err = _norm_and_group(item, f"Condicion #{idx}")
            if err is not None:
                return None, err
            condiciones_norm.append(and_norm)
            continue

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
# API REST -- Estados y Subestados
# ============================================================

ESTADOS_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estados.json")


def _defaults_estados() -> dict:
    return dict(ESTADOS_SERIALIZADOS)


def _load_estados() -> dict:
    if not os.path.exists(ESTADOS_JSON_PATH):
        cfg = _defaults_estados()
        _save_estados(cfg)
        return cfg
    try:
        with open(ESTADOS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return _defaults_estados()


def _save_estados(data: dict) -> None:
    with open(ESTADOS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@app.route("/api/estados", methods=["GET"])
def api_get_estados():
    return jsonify(_load_estados())


@app.route("/api/estados/<nombre>", methods=["GET"])
def api_get_estado(nombre: str):
    estados = _load_estados()
    est = estados.get(nombre)
    if est is None:
        return jsonify({"error": f"Estado '{nombre}' no encontrado"}), 404
    return jsonify(est)


@app.route("/api/estados", methods=["POST"])
def api_create_estado():
    data = request.get_json(force=True)
    nombre = str(data.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "Campo 'nombre' requerido"}), 400
    estados = _load_estados()
    if nombre in estados:
        return jsonify({"error": f"Estado '{nombre}' ya existe"}), 409
    estados[nombre] = {
        "nombre": nombre,
        "tipo": str(data.get("tipo", "estado")),
        "condicion": data.get("condicion", {"AND": []}),
    }
    _save_estados(estados)
    return jsonify({"ok": True, "estado": estados[nombre]}), 201


@app.route("/api/estados/<nombre>", methods=["PUT"])
def api_update_estado(nombre: str):
    estados = _load_estados()
    if nombre not in estados:
        return jsonify({"error": f"Estado '{nombre}' no encontrado"}), 404
    data = request.get_json(force=True)
    estados[nombre] = {
        "nombre": nombre,
        "tipo": str(data.get("tipo", estados[nombre].get("tipo", "estado"))),
        "condicion": data.get("condicion", estados[nombre].get("condicion")),
    }
    _save_estados(estados)
    return jsonify({"ok": True, "estado": estados[nombre]})


@app.route("/api/estados/<nombre>", methods=["DELETE"])
def api_delete_estado(nombre: str):
    estados = _load_estados()
    if nombre not in estados:
        return jsonify({"error": f"Estado '{nombre}' no encontrado"}), 404
    del estados[nombre]
    _save_estados(estados)
    return jsonify({"ok": True})


@app.route("/api/estados/reset", methods=["POST"])
def api_reset_estados():
    cfg = _defaults_estados()
    _save_estados(cfg)
    return jsonify({"ok": True})


# ============================================================
# API REST -- Waits Catalogo
# ============================================================

WAITS_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "waits.json")


def _defaults_waits() -> list[dict]:
    return list(WAITS_CATALOGO_DISPONIBLES)


def _load_waits() -> list[dict]:
    if not os.path.exists(WAITS_JSON_PATH):
        cfg = _defaults_waits()
        _save_waits(cfg)
        return cfg
    try:
        with open(WAITS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return _defaults_waits()


def _save_waits(data: list[dict]) -> None:
    with open(WAITS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@app.route("/api/waits", methods=["GET"])
def api_get_waits():
    return jsonify(_load_waits())


@app.route("/api/waits/<path:wait_id>", methods=["GET"])
def api_get_wait(wait_id: str):
    waits = _load_waits()
    for w in waits:
        if w.get("wait_id") == wait_id:
            return jsonify(w)
    return jsonify({"error": f"Wait '{wait_id}' no encontrado"}), 404


@app.route("/api/waits", methods=["POST"])
def api_create_wait():
    data = request.get_json(force=True)
    nombre = str(data.get("nombre", "")).strip()
    if not nombre:
        return jsonify({"error": "Campo 'nombre' requerido"}), 400
    from waits_catalogo import crear_wait
    new_wait = crear_wait(
        nombre,
        variable_controlada=data.get("variable_controlada"),
        descripcion=data.get("descripcion", ""),
    )
    waits = _load_waits()
    for w in waits:
        if w["wait_id"] == new_wait["wait_id"]:
            return jsonify({"error": f"Wait '{new_wait['wait_id']}' ya existe"}), 409
    waits.append(new_wait)
    _save_waits(waits)
    return jsonify({"ok": True, "wait": new_wait}), 201


@app.route("/api/waits/<path:wait_id>", methods=["PUT"])
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


@app.route("/api/waits/<path:wait_id>", methods=["DELETE"])
def api_delete_wait(wait_id: str):
    waits = _load_waits()
    idx = next((i for i, w in enumerate(waits) if w.get("wait_id") == wait_id), None)
    if idx is None:
        return jsonify({"error": f"Wait '{wait_id}' no encontrado"}), 404
    removed = waits.pop(idx)
    _save_waits(waits)
    return jsonify({"ok": True, "eliminado": removed})


@app.route("/api/waits/reset", methods=["POST"])
def api_reset_waits():
    cfg = _defaults_waits()
    _save_waits(cfg)
    return jsonify({"ok": True})


# ============================================================
# Tags KEPserver
# ============================================================

def _load_tags() -> dict:
    if not os.path.exists(TAGS_JSON):
        return {"tags": [], "next_id": 1}
    with open(TAGS_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_tags(data: dict) -> None:
    with open(TAGS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


KEPSERVER_URL = "opc.tcp://127.0.0.1:49320"


def _read_kepserver_tags_batch(tag_names: list[str]) -> dict[str, dict]:
    """Read multiple tags from KEPserver in a single OPC-UA session.

    Returns {tag_name: {"connected", "exists", "value", "quality"}} for each tag.
    """
    default = lambda: {"connected": False, "exists": False, "value": None, "quality": "Unknown"}
    results = {n: default() for n in tag_names}
    if not tag_names:
        return results
    try:
        from opcua import Client  # type: ignore
        client = Client(KEPSERVER_URL)
        client.connect()
        try:
            for name in tag_names:
                try:
                    node = client.get_node(f'ns=2;s={name}')
                    val = node.get_value()
                    results[name] = {"connected": True, "exists": True,
                                     "value": val, "quality": "Good"}
                except Exception:
                    results[name] = {"connected": True, "exists": False,
                                     "value": None, "quality": "Bad"}
        finally:
            client.disconnect()
    except Exception:
        pass
    return results


def _try_write_kepserver_tag(tag_name: str, value, data_type: str) -> dict:
    """Write a value to a KEPserver tag via OPC-UA."""
    try:
        from opcua import Client, ua  # type: ignore
        client = Client(KEPSERVER_URL)
        client.connect()
        try:
            node = client.get_node(f'ns=2;s={tag_name}')
            type_map = {
                "Float": ua.VariantType.Float,
                "Int": ua.VariantType.Int32,
                "Boolean": ua.VariantType.Boolean,
                "String": ua.VariantType.String,
            }
            vtype = type_map.get(data_type, ua.VariantType.Float)
            node.set_value(ua.DataValue(ua.Variant(value, vtype)))
            return {"ok": True, "error": None}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            client.disconnect()
    except Exception as e:
        return {"ok": False, "error": f"Sin conexion OPC-UA: {e}"}


def _enrich_tags_with_kepserver(tags: list[dict]) -> list[dict]:
    """Add KEPserver live data to each tag dict."""
    enabled_tags = {t["name"]: t for t in tags if t.get("enabled", True)}
    live = _read_kepserver_tags_batch(list(enabled_tags.keys()))
    results = []
    for tag in tags:
        if tag.get("enabled", True) and tag["name"] in live:
            results.append({**tag, **live[tag["name"]]})
        else:
            results.append({**tag, "connected": False, "exists": False,
                            "value": None, "quality": "Suspended"})
    return results


@app.route("/api/tags", methods=["GET"])
def api_get_tags():
    data = _load_tags()
    return jsonify({
        "tags": _enrich_tags_with_kepserver(data["tags"]),
        "simulation_mode": data.get("simulation_mode", True),
    })


@app.route("/api/tags", methods=["POST"])
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
    new_tag = {
        "id": store["next_id"],
        "name": name,
        "data_type": data_type,
        "enabled": True,
    }
    store["tags"].append(new_tag)
    store["next_id"] += 1
    _save_tags(store)
    live = _read_kepserver_tags_batch([name])
    return jsonify({"ok": True, "tag": {**new_tag, **live.get(name, {})}}), 201


@app.route("/api/tags/<int:tag_id>", methods=["PUT"])
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
    _save_tags(store)
    live = _read_kepserver_tags_batch([tag["name"]]) if tag["enabled"] else {}
    info = live.get(tag["name"], {"connected": False, "exists": False, "value": None, "quality": "Suspended"})
    return jsonify({"ok": True, "tag": {**tag, **info}})


@app.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id: int):
    store = _load_tags()
    before = len(store["tags"])
    store["tags"] = [t for t in store["tags"] if t["id"] != tag_id]
    if len(store["tags"]) == before:
        return jsonify({"error": "Tag no encontrado."}), 404
    _save_tags(store)
    return jsonify({"ok": True})


@app.route("/api/tags/<int:tag_id>/write", methods=["POST"])
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


@app.route("/api/tags/refresh", methods=["POST"])
def api_refresh_tags():
    """Re-read all tag values from KEPserver."""
    store = _load_tags()
    return jsonify({"tags": _enrich_tags_with_kepserver(store["tags"])})


@app.route("/api/tags/simulation", methods=["GET"])
def api_get_simulation_mode():
    store = _load_tags()
    return jsonify({"simulation_mode": store.get("simulation_mode", True)})


@app.route("/api/tags/simulation", methods=["PUT"])
def api_set_simulation_mode():
    body = request.get_json(force=True)
    mode = bool(body.get("simulation_mode", True))
    store = _load_tags()
    store["simulation_mode"] = mode
    _save_tags(store)
    return jsonify({"ok": True, "simulation_mode": mode})


# ============================================================
# Generador de datos para Tags KEPserver
# ============================================================

GENERATOR_DEFAULTS_PV = {
    "RETO.PV.torque":               {"min": 40.0,  "max": 90.0,  "noise": 3.0},
    "RETO.PV.bed_mass":             {"min": 200.0, "max": 900.0, "noise": 20.0},
    "RETO.PV.bed_level":            {"min": 1.5,   "max": 6.0,   "noise": 0.2},
    "RETO.PV.densidad":             {"min": 45.0,  "max": 75.0,  "noise": 2.0},
    "RETO.PV.torque_bomba":         {"min": 30.0,  "max": 80.0,  "noise": 3.0},
    "RETO.PV.potencia_bomba":       {"min": 50.0,  "max": 200.0, "noise": 8.0},
    "RETO.PV.presion_descarga":     {"min": 2.0,   "max": 12.0,  "noise": 0.5},
    "RETO.PV.presion_diferencial":  {"min": 0.5,   "max": 4.0,   "noise": 0.2},
    "RETO.PV.nivel_rastra":         {"min": 20.0,  "max": 80.0,  "noise": 3.0},
}

GENERATOR_DEFAULTS_CRUDA = {
    "RETO.CRUDA.tonelaje_sag_1":   {"min": 800.0,  "max": 1800.0, "noise": 40.0},
    "RETO.CRUDA.tonelaje_sag_2":   {"min": 800.0,  "max": 1800.0, "noise": 40.0},
    "RETO.CRUDA.tonelaje_relave":  {"min": 1500.0, "max": 3500.0, "noise": 60.0},
    "RETO.CRUDA.presion_bomba_1":  {"min": 2.0,    "max": 10.0,   "noise": 0.4},
    "RETO.CRUDA.presion_bomba_2":  {"min": 2.0,    "max": 10.0,   "noise": 0.4},
    "RETO.CRUDA.turbiedad_agua":   {"min": 5.0,    "max": 50.0,   "noise": 3.0},
}

GENERATOR_DEFAULTS_LIM = {
    "RETO.LIM.torque_lmin":               {"min": 40.0,  "max": 40.0,  "noise": 0.0},
    "RETO.LIM.torque_lmax":               {"min": 90.0,  "max": 90.0,  "noise": 0.0},
    "RETO.LIM.bed_mass_lmin":             {"min": 200.0, "max": 200.0, "noise": 0.0},
    "RETO.LIM.bed_mass_lmax":             {"min": 900.0, "max": 900.0, "noise": 0.0},
    "RETO.LIM.bed_level_lmin":            {"min": 0.8,   "max": 0.8,   "noise": 0.0},
    "RETO.LIM.bed_level_lmax":            {"min": 4.0,   "max": 4.0,   "noise": 0.0},
    "RETO.LIM.densidad_lmin":             {"min": 55.0,  "max": 55.0,  "noise": 0.0},
    "RETO.LIM.densidad_lmax":             {"min": 78.0,  "max": 78.0,  "noise": 0.0},
    "RETO.LIM.torque_bomba_lmin":         {"min": 30.0,  "max": 30.0,  "noise": 0.0},
    "RETO.LIM.torque_bomba_lmax":         {"min": 90.0,  "max": 90.0,  "noise": 0.0},
    "RETO.LIM.potencia_bomba_lmin":       {"min": 150.0, "max": 150.0, "noise": 0.0},
    "RETO.LIM.potencia_bomba_lmax":       {"min": 750.0, "max": 750.0, "noise": 0.0},
    "RETO.LIM.presion_descarga_lmin":     {"min": 5.0,   "max": 5.0,   "noise": 0.0},
    "RETO.LIM.presion_descarga_lmax":     {"min": 22.0,  "max": 22.0,  "noise": 0.0},
    "RETO.LIM.presion_diferencial_lmin":  {"min": 1.0,   "max": 1.0,   "noise": 0.0},
    "RETO.LIM.presion_diferencial_lmax":  {"min": 12.0,  "max": 12.0,  "noise": 0.0},
    "RETO.LIM.nivel_rastra_lmin":         {"min": 0.0,   "max": 0.0,   "noise": 0.0},
    "RETO.LIM.nivel_rastra_lmax":         {"min": 20.0,  "max": 20.0,  "noise": 0.0},
}

GENERATOR_DEFAULTS = {**GENERATOR_DEFAULTS_PV, **GENERATOR_DEFAULTS_CRUDA, **GENERATOR_DEFAULTS_LIM}


# ============================================================
# Tag history buffer (circular, last N values per tag)
# ============================================================

_TAG_HISTORY_SIZE = 50
_tag_history: dict[str, deque[dict]] = {}
_tag_history_lock = threading.Lock()


def _record_tag_values(tag_values: dict[str, float]):
    """Append current values to the per-tag history ring buffer."""
    ts = time.time()
    with _tag_history_lock:
        for tag_name, val in tag_values.items():
            if tag_name not in _tag_history:
                _tag_history[tag_name] = deque(maxlen=_TAG_HISTORY_SIZE)
            _tag_history[tag_name].append({"t": ts, "v": val})


def _get_tag_history() -> dict[str, list]:
    with _tag_history_lock:
        return {k: list(v) for k, v in _tag_history.items()}


def _tres_fases_valor(tick: int, n_ciclo: int, vmin: float, vmax: float, noise: float) -> float:
    """Generate a value following 3-phase pattern (stable, alert, recovery) for given tick."""
    import random
    phase_pos = (tick % n_ciclo) / n_ciclo

    if phase_pos < 0.35:
        base = vmin + (vmax - vmin) * 0.2
    elif phase_pos < 0.70:
        progress = (phase_pos - 0.35) / 0.35
        base = vmin + (vmax - vmin) * (0.2 + 0.7 * progress)
    else:
        progress = (phase_pos - 0.70) / 0.30
        base = vmin + (vmax - vmin) * (0.9 - 0.5 * progress)

    val = base + random.gauss(0, noise)
    return max(vmin, min(vmax, val))


class TagGenerator:
    """Background thread that writes generated values to KEPserver."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._tick = 0
        self._running = False
        self._intervalo_s = 5.0
        self._n_ciclo = 60
        self._ranges: dict[str, dict] = dict(GENERATOR_DEFAULTS)
        self._last_values: dict[str, float] = {}
        self._last_error: str | None = None
        self._load_config()

    def _load_config(self):
        store = _load_tags()
        gen_cfg = store.get("generator", {})
        self._intervalo_s = gen_cfg.get("intervalo_s", 5.0)
        self._n_ciclo = gen_cfg.get("n_ciclo", 60)
        if gen_cfg.get("ranges"):
            self._ranges.update(gen_cfg["ranges"])

    def _save_config(self):
        store = _load_tags()
        store["generator"] = {
            "intervalo_s": self._intervalo_s,
            "n_ciclo": self._n_ciclo,
            "ranges": self._ranges,
        }
        _save_tags(store)

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                self._write_tick()
                self._tick += 1
            except Exception as e:
                self._last_error = str(e)
                _alerts.add("generator", str(e), traceback.format_exc())
            self._stop_event.wait(self._intervalo_s)

    def _write_tick(self):
        tags_to_write = {}
        for tag_name, cfg in self._ranges.items():
            val = _tres_fases_valor(
                self._tick, self._n_ciclo,
                cfg["min"], cfg["max"], cfg.get("noise", 0)
            )
            tags_to_write[tag_name] = val
            self._last_values[tag_name] = val

        _record_tag_values(tags_to_write)

        try:
            from opcua import Client, ua  # type: ignore
            client = Client(KEPSERVER_URL)
            client.connect()
            try:
                for tag_name, val in tags_to_write.items():
                    try:
                        node = client.get_node(f'ns=2;s={tag_name}')
                        node.set_value(
                            ua.DataValue(ua.Variant(float(val), ua.VariantType.Float))
                        )
                    except Exception:
                        pass
            finally:
                client.disconnect()
            self._last_error = None
            _alerts.resolve_category("generator")
        except Exception as e:
            self._last_error = f"OPC-UA: {e}"
            _alerts.add("kep", f"Generador: {e}", traceback.format_exc())

    def start(self):
        if self._running:
            return
        self._load_config()
        self._stop_event.clear()
        self._tick = 0
        self._last_error = None
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._running = True

    def stop(self):
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._running = False
        self._thread = None

    def update_config(self, intervalo_s=None, n_ciclo=None, ranges=None):
        if intervalo_s is not None:
            self._intervalo_s = max(0.5, float(intervalo_s))
        if n_ciclo is not None:
            self._n_ciclo = max(10, int(n_ciclo))
        if ranges is not None:
            for tag_name, cfg in ranges.items():
                if tag_name in self._ranges:
                    self._ranges[tag_name].update(cfg)
                else:
                    self._ranges[tag_name] = cfg
        self._save_config()

    def status(self) -> dict:
        return {
            "running": self._running,
            "tick": self._tick,
            "intervalo_s": self._intervalo_s,
            "n_ciclo": self._n_ciclo,
            "ranges": self._ranges,
            "last_values": self._last_values,
            "last_error": self._last_error,
        }


_tag_generator = TagGenerator()


# ============================================================
# SE Engine -- Sistema Experto en tiempo real via KEPserver
# ============================================================

TAG_TO_PV = {f"RETO.PV.{v}": v for v in [
    "torque", "bed_mass", "bed_level", "densidad", "torque_bomba",
    "potencia_bomba", "presion_descarga", "presion_diferencial", "nivel_rastra"
]}
TAG_TO_CRUDA = {f"RETO.CRUDA.{v}": v for v in [
    "tonelaje_sag_1", "tonelaje_sag_2", "tonelaje_relave",
    "presion_bomba_1", "presion_bomba_2", "turbiedad_agua"
]}
TAG_TO_LIM = {}
for _var in ["torque", "bed_mass", "bed_level", "densidad", "torque_bomba",
             "potencia_bomba", "presion_descarga", "presion_diferencial", "nivel_rastra"]:
    TAG_TO_LIM[f"RETO.LIM.{_var}_lmin"] = (_var, "lmin")
    TAG_TO_LIM[f"RETO.LIM.{_var}_lmax"] = (_var, "lmax")

SP_TO_TAG = {
    "sp_tonelaje":   "RETO.SP.sp_tonelaje",
    "sp_floculante": "RETO.SP.sp_floculante",
    "sp_vel_bomba":  "RETO.SP.sp_vel_bomba",
}


class SEEngine:
    """Background thread that reads tags, runs the expert system pipeline, writes SPs."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._tick = 0
        self._t_s = 0.0
        self._intervalo_s = 5.0

        self._setpoints: dict = {}
        self._limites_sp: dict = {}
        self._last_action_time: dict = {}
        self._hist: dict = {}
        self._filtro = None
        self._reglas: list = []
        self._permisivos_config: dict = {}

        self._last_events: list = []
        self._last_error: str | None = None

    def _init_state(self):
        from simulacion import SETPOINTS_BASE, LIMITES_SP
        from exp_q_filter import ExpQFilter, CONFIG_FILTRO_ESPESADOR_DEFAULT
        from runner import cargar_reglas_json, cargar_permisivos_json
        import copy

        self._setpoints = copy.deepcopy(SETPOINTS_BASE)
        self._limites_sp = copy.deepcopy(LIMITES_SP)
        self._last_action_time = {}
        self._hist = {}
        self._t_s = 0.0
        self._tick = 0
        self._last_events = []
        self._last_error = None

        self._filtro = ExpQFilter(CONFIG_FILTRO_ESPESADOR_DEFAULT)
        self._filtro.reset()

        self._reglas = cargar_reglas_json()
        self._permisivos_config = cargar_permisivos_json()

    def _read_tags(self) -> tuple[dict, dict, dict] | None:
        """Read PV, CRUDA and LIM tags from KEPserver. Returns (inputs, crudas, limites) or None."""
        all_tags = list(TAG_TO_PV.keys()) + list(TAG_TO_CRUDA.keys()) + list(TAG_TO_LIM.keys())
        live = _read_kepserver_tags_batch(all_tags)

        inputs = {}
        for tag, var in TAG_TO_PV.items():
            info = live.get(tag, {})
            if info.get("exists") and info.get("value") is not None:
                inputs[var] = float(info["value"])
            else:
                return None

        crudas = {}
        for tag, var in TAG_TO_CRUDA.items():
            info = live.get(tag, {})
            if info.get("exists") and info.get("value") is not None:
                crudas[var] = float(info["value"])
            else:
                crudas[var] = 0.0

        limites = {}
        for tag, (var, bound) in TAG_TO_LIM.items():
            info = live.get(tag, {})
            if var not in limites:
                limites[var] = {}
            if info.get("exists") and info.get("value") is not None:
                limites[var][bound] = float(info["value"])
            else:
                limites[var][bound] = 0.0

        return inputs, crudas, limites

    def _write_setpoints(self):
        """Write current setpoints to KEPserver."""
        sp_vals = {tag: float(self._setpoints.get(sp_key, 0.0)) for sp_key, tag in SP_TO_TAG.items()}
        _record_tag_values(sp_vals)
        try:
            from opcua import Client, ua  # type: ignore
            client = Client(KEPSERVER_URL)
            client.connect()
            try:
                for sp_key, tag_name in SP_TO_TAG.items():
                    val = self._setpoints.get(sp_key, 0.0)
                    try:
                        node = client.get_node(f'ns=2;s={tag_name}')
                        node.set_value(ua.DataValue(ua.Variant(float(val), ua.VariantType.Float)))
                    except Exception:
                        pass
            finally:
                client.disconnect()
        except Exception as e:
            self._last_error = f"Write SP: {e}"
            _alerts.add("kep", f"SE Write SP: {e}", traceback.format_exc())

    def _run_tick(self):
        """Execute one tick of the SE pipeline."""
        from runner import _evaluar_estado_fuzzy, extraer_inputs_desde_row
        from permisivos import evaluar_permisivos, inyectar_permisivos_en_fuzzy_out
        import motor as motor_mod
        from defuzzy_actions import apply_actions
        from config import VARIABLES_PROCESO, COLUMNAS_ENTRADA
        import pandas as pd

        data = self._read_tags()
        if data is None:
            self._last_error = "No se pudieron leer todos los PV tags del KEPserver."
            _alerts.add("kep", "No se pudieron leer los PV tags del KEPserver.")
            return

        inputs_raw, crudas, limites = data

        inputs = self._filtro.actualizar(inputs_raw)

        self._t_s += self._intervalo_s

        row_data = {**inputs, **crudas}
        for var, bounds in limites.items():
            row_data[f"{var}_lmin"] = bounds.get("lmin", 0)
            row_data[f"{var}_lmax"] = bounds.get("lmax", 0)
        for sp_key, val in self._setpoints.items():
            row_data[sp_key] = val
        row_data["t_s"] = self._t_s
        row = pd.Series(row_data)

        fuzzy_out = _evaluar_estado_fuzzy(
            row, self._hist,
            columnas_entrada=COLUMNAS_ENTRADA,
            meta_flags=None,
            inputs_override=inputs
        )

        estados_perm = evaluar_permisivos(
            self._permisivos_config,
            fuzzy_out=fuzzy_out,
            row=row,
            inputs=inputs,
            setpoints=self._setpoints,
            columnas_entrada=COLUMNAS_ENTRADA,
        )
        fuzzy_out = inyectar_permisivos_en_fuzzy_out(fuzzy_out, estados_perm)

        motor_out = motor_mod.evaluar_reglas(
            self._reglas, fuzzy_out, self._t_s,
            self._last_action_time, min_belief=0.05
        )

        self._last_action_time = motor_out.get("last_action_time", self._last_action_time)

        tick_events = []
        for evento in motor_out.get("fired", []):
            acciones_con_belief = [(a, evento.get("belief", 0.5)) for a in evento.get("acciones", [])]
            if acciones_con_belief:
                apply_actions(acciones_con_belief, self._setpoints, self._limites_sp)
            tick_events.append({
                "regla_id": evento.get("id", "?"),
                "bloque": evento.get("bloque", ""),
                "acciones": evento.get("acciones", []),
                "belief": round(evento.get("belief", 0), 3),
            })

        if tick_events:
            self._last_events = tick_events[-5:]

        self._write_setpoints()
        self._last_error = None
        _alerts.resolve_category("se_engine")
        _alerts.resolve_category("kep")

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                self._run_tick()
                self._tick += 1
            except Exception as e:
                self._last_error = str(e)
                _alerts.add("se_engine", str(e), traceback.format_exc())
            self._stop_event.wait(self._intervalo_s)

    def start(self, intervalo_s: float = 5.0):
        if self._running:
            return
        self._intervalo_s = intervalo_s
        self._init_state()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._running = True

    def stop(self):
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._running = False
        self._thread = None

    def status(self) -> dict:
        return {
            "running": self._running,
            "tick": self._tick,
            "t_s": round(self._t_s, 1),
            "setpoints": {k: round(v, 3) for k, v in self._setpoints.items()},
            "last_events": self._last_events,
            "last_error": self._last_error,
        }


_se_engine = SEEngine()


# ============================================================
# Startup health checks → populate alerts
# ============================================================

def _startup_checks():
    # 1. Check KEPserver connectivity
    try:
        from opcua import Client  # type: ignore
        c = Client(KEPSERVER_URL)
        c.connect()
        c.disconnect()
    except ImportError:
        _alerts.add("import", "Modulo 'opcua' no instalado.", "pip install opcua")
    except Exception as e:
        _alerts.add("kep", f"KEPserver no accesible en {KEPSERVER_URL}", str(e))

    # 2. Check JSON configs exist and parse
    for name, path in [
        ("reglas.json", REGLAS_JSON), ("filtros.json", FILTROS_JSON),
        ("defuzzy.json", DEFUZZY_JSON), ("fuzzy.json", FUZZY_JSON),
        ("variables.json", VARIABLES_JSON), ("permisivos.json", PERMISIVOS_JSON),
        ("tags.json", TAGS_JSON),
    ]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)
        except Exception as e:
            _alerts.add("config", f"Error parseando {name}", str(e))

    # 3. Check reglas load
    try:
        from runner import cargar_reglas_json
        reglas = cargar_reglas_json()
        if not reglas:
            _alerts.add("reglas", "No hay reglas cargadas.", "reglas.json esta vacio o falta.")
    except Exception as e:
        _alerts.add("reglas", f"Error cargando reglas: {e}", traceback.format_exc())

    # 4. Check critical imports
    for mod_name in ["runner", "motor", "defuzzy_actions", "fuzzys_models_espesador", "exp_q_filter", "simulacion"]:
        try:
            __import__(mod_name)
        except Exception as e:
            _alerts.add("import", f"Error importando {mod_name}", str(e))


_startup_checks()


# ============================================================
# API — Alerts
# ============================================================

@app.route("/api/alerts", methods=["GET"])
def api_alerts():
    show = request.args.get("show", "active")
    if show == "all":
        return jsonify({"alerts": _alerts.get_all(), "categories": AlertCollector.CATEGORIES})
    return jsonify({"alerts": _alerts.get_active(), "categories": AlertCollector.CATEGORIES})


@app.route("/api/alerts/<int:alert_id>/resolve", methods=["POST"])
def api_resolve_alert(alert_id: int):
    ok = _alerts.resolve(alert_id)
    return jsonify({"ok": ok})


@app.route("/api/alerts/clear", methods=["POST"])
def api_clear_alerts():
    _alerts.clear_resolved()
    return jsonify({"ok": True})


@app.route("/api/se/status", methods=["GET"])
def api_se_status():
    return jsonify(_se_engine.status())


@app.route("/api/se/start", methods=["POST"])
def api_se_start():
    body = request.get_json(force=True) if request.content_length else {}
    intervalo = float(body.get("intervalo_s", 5.0)) if body else 5.0
    _se_engine.start(intervalo_s=intervalo)
    return jsonify({"ok": True, "running": True})


@app.route("/api/se/stop", methods=["POST"])
def api_se_stop():
    _se_engine.stop()
    return jsonify({"ok": True, "running": False})


@app.route("/api/tags/generator", methods=["GET"])
def api_get_generator():
    return jsonify(_tag_generator.status())


@app.route("/api/tags/generator", methods=["PUT"])
def api_put_generator():
    body = request.get_json(force=True)
    _tag_generator.update_config(
        intervalo_s=body.get("intervalo_s"),
        n_ciclo=body.get("n_ciclo"),
        ranges=body.get("ranges"),
    )
    return jsonify({"ok": True, **_tag_generator.status()})


@app.route("/api/tags/generator/start", methods=["POST"])
def api_start_generator():
    _tag_generator.start()
    return jsonify({"ok": True, "running": True})


@app.route("/api/tags/generator/stop", methods=["POST"])
def api_stop_generator():
    _tag_generator.stop()
    return jsonify({"ok": True, "running": False})


# ============================================================
# API — Entrada de Datos (all tags + history)
# ============================================================

@app.route("/api/entrada", methods=["GET"])
def api_entrada():
    """Return all configured tags with current values and history."""
    store = _load_tags()
    tags = store.get("tags", [])
    enabled_tags = [t for t in tags if t.get("enabled", True)]
    tag_names = [t["name"] for t in enabled_tags]

    live = _read_kepserver_tags_batch(tag_names) if tag_names else {}
    hist = _get_tag_history()

    result = []
    for t in enabled_tags:
        name = t["name"]
        info = live.get(name, {})
        direction = "output" if name.startswith("RETO.SP.") else "input"
        group = "pv"
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
            "name": name,
            "value": info.get("value"),
            "exists": info.get("exists", False),
            "direction": direction,
            "group": group,
            "history": hist.get(name, []),
        })
    return jsonify(result)


@app.route("/api/entrada/history", methods=["GET"])
def api_entrada_history():
    """Return only history buffers (lighter payload for polling)."""
    return jsonify(_get_tag_history())


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
.sidebar .step-num{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:.65rem;font-weight:700;margin-right:6px;flex-shrink:0}
.sidebar .step-num.s1{background:rgba(37,99,235,.2);color:#3b82f6}
.sidebar .step-num.s2{background:rgba(8,145,178,.2);color:#06b6d4}
.sidebar .step-num.s4{background:rgba(99,102,241,.2);color:#6366f1}
.sidebar .step-num.s5{background:rgba(168,85,247,.2);color:#a855f7}
.sidebar .step-num.s6{background:rgba(20,184,166,.2);color:#14b8a6}
.sidebar .step-num.s7{background:rgba(245,158,11,.2);color:#f59e0b}
.sidebar .step-num.s8{background:rgba(34,197,94,.2);color:#22c55e}
.sidebar .section-label{display:block;color:#475569;font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.5px;padding:4px 10px;margin-top:6px}
.sidebar hr.sep{border:none;border-top:1px solid #1e293b;margin:12px 4px}

/* Alert rail (right edge) */
.alert-rail{width:10px;flex-shrink:0;background:#0b1322;border-left:1px solid #1e293b;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:3px;padding:6px 1px;cursor:pointer;transition:width .25s;overflow:hidden;z-index:100}
.alert-rail:hover{width:14px;background:#111827}
.alert-rail.expanded{width:340px;padding:12px;cursor:default;overflow-y:auto}
.alert-rail .rail-dots{display:flex;flex-direction:column;gap:3px;align-items:center;padding-top:4px}
.alert-rail.expanded .rail-dots{display:none}
.rail-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;animation:pulse-dot 2s infinite}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.4}}
.rail-ok{width:8px;height:8px;border-radius:50%;background:#22c55e40;border:1px solid #22c55e30;margin-top:4px}

/* Expanded alert panel */
.alert-panel{display:none}
.alert-rail.expanded .alert-panel{display:block}
.alert-panel-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.alert-panel-header h4{color:#e2e8f0;font-size:.85rem;display:flex;align-items:center;gap:6px}
.alert-panel-header .close-btn{background:none;border:none;color:#64748b;font-size:1.1rem;cursor:pointer;padding:2px 6px;border-radius:4px}
.alert-panel-header .close-btn:hover{color:#e2e8f0;background:#1e293b}
.alert-count-badge{background:#ef4444;color:#fff;font-size:.65rem;padding:1px 6px;border-radius:8px;font-weight:700}
.alert-count-badge.ok{background:#22c55e}
.alert-card{background:#1e293b;border-radius:8px;padding:10px 12px;margin-bottom:8px;border-left:3px solid #64748b;cursor:pointer;transition:.15s}
.alert-card:hover{background:#243044}
.alert-card .alert-cat{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.3px;margin-bottom:3px}
.alert-card .alert-msg{font-size:.78rem;color:#cbd5e1;line-height:1.4}
.alert-card .alert-meta{font-size:.68rem;color:#475569;margin-top:4px;display:flex;justify-content:space-between}
.alert-card .alert-detail{display:none;margin-top:8px;padding:8px;background:#0f172a;border-radius:6px;font-size:.72rem;color:#94a3b8;font-family:monospace;white-space:pre-wrap;max-height:150px;overflow-y:auto}
.alert-card.open .alert-detail{display:block}
.alert-card .resolve-btn{background:none;border:1px solid #334155;color:#64748b;font-size:.68rem;padding:2px 8px;border-radius:4px;cursor:pointer;margin-top:6px}
.alert-card .resolve-btn:hover{border-color:#22c55e;color:#22c55e}
.alert-ok-msg{color:#22c55e;font-size:.82rem;text-align:center;padding:30px 10px;line-height:1.6}

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
.action-row select{max-width:260px}
.action-row input[type=number]{background:#0f172a;border:1px solid #475569;border-radius:4px;padding:4px 6px;color:#e2e8f0;font-size:.8rem}
#sim-results{background:#1e293b;border-radius:8px;padding:16px;margin-top:20px;display:none}
#sim-results h3{color:#22c55e;margin-bottom:12px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}
.stat-card{background:#334155;border-radius:8px;padding:12px;text-align:center}
.stat-card .val{font-size:1.5rem;font-weight:700;color:#38bdf8}
.stat-card .lbl{font-size:.7rem;color:#94a3b8;text-transform:uppercase;margin-top:4px}
.ev-table{max-height:300px;overflow-y:auto}
.loading{color:#94a3b8;font-style:italic}
.hint{font-size:.7rem;color:#64748b;margin-top:2px}

.tags-legend{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px;padding:12px 16px;background:#1e293b;border-radius:8px;border:1px solid #334155}
.tags-legend-item{display:flex;align-items:center;gap:6px;font-size:.8rem;color:#cbd5e1}
.tags-legend-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0}
.dot-connected{background:#22c55e;box-shadow:0 0 6px rgba(34,197,94,.5)}
.dot-disconnected{background:#ef4444;box-shadow:0 0 6px rgba(239,68,68,.5)}
.dot-not-found{background:#f59e0b;box-shadow:0 0 6px rgba(245,158,11,.5)}
.dot-suspended{background:#64748b}
.dot-unknown{background:#6366f1;box-shadow:0 0 6px rgba(99,102,241,.4)}

.tag-status-cell{display:flex;align-items:center;gap:8px}
.tag-status-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:.75rem;font-weight:600;white-space:nowrap}
.badge-connected{background:rgba(34,197,94,.15);color:#22c55e;border:1px solid rgba(34,197,94,.3)}
.badge-disconnected{background:rgba(239,68,68,.15);color:#ef4444;border:1px solid rgba(239,68,68,.3)}
.badge-not-found{background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.3)}
.badge-suspended{background:rgba(100,116,139,.15);color:#94a3b8;border:1px solid rgba(100,116,139,.3)}
.badge-unknown{background:rgba(99,102,241,.15);color:#818cf8;border:1px solid rgba(99,102,241,.3)}
.badge-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}

.tag-value-display{font-family:'Cascadia Code','Fira Code',monospace;font-size:.9rem;color:#38bdf8;font-weight:600}
.tag-value-na{color:#64748b;font-style:italic;font-weight:400}

.tag-row-suspended{opacity:.5}
.tag-row-suspended td{color:#64748b}

.tags-add-form{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px;padding:12px 16px;background:#1e293b;border-radius:8px;border:1px solid #334155}
.tags-add-form input,.tags-add-form select{min-width:200px}
.tags-actions{display:flex;gap:4px}

.toggle-switch{position:relative;display:inline-block;width:52px;height:28px;flex-shrink:0}
.toggle-switch input{opacity:0;width:0;height:0}
.toggle-slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#ef4444;transition:.3s;border-radius:28px}
.toggle-slider:before{position:absolute;content:"";height:22px;width:22px;left:3px;bottom:3px;background:#fff;transition:.3s;border-radius:50%}
.toggle-switch input:checked + .toggle-slider{background:#22c55e}
.toggle-switch input:checked + .toggle-slider:before{transform:translateX(24px)}

.sim-mode-on #sim-toggle-box{border-color:#22c55e;background:linear-gradient(135deg,#1a2e1a,#1e293b)}
.sim-mode-off #sim-toggle-box{border-color:#ef4444;background:linear-gradient(135deg,#2e1a1a,#1e293b)}

.tag-group-header{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:8px 14px;margin-top:16px;margin-bottom:8px;font-size:.82rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;display:flex;align-items:center;gap:8px}
.tag-group-header .group-dot{width:10px;height:10px;border-radius:50%}
</style>
</head>
<body>
<aside class="sidebar">
  <div class="brand">Espesador v2</div>
  <span class="section-label">Pipeline SE</span>
  <a href="/entrada" class="external" style="color:#38bdf8;font-weight:600"><span class="step-num s1">1</span>Entrada de Datos →</a>
  <a href="#variables"  data-sec="variables"><span class="step-num s2">2</span>Variables calc.</a>
  <a href="#filtros"    data-sec="filtros"><span class="step-num s4">4</span>Filtros Exp-Q</a>
  <a href="#fuzzy"      data-sec="fuzzy"><span class="step-num s5">5</span>Fuzzificacion</a>
  <a href="#estados"    data-sec="estados"><span class="step-num s5">5b</span>Estados</a>
  <a href="#waits"      data-sec="waits"><span class="step-num s5">5c</span>Waits</a>
  <a href="#permisivos" data-sec="permisivos"><span class="step-num s6">6</span>Permisivos</a>
  <a href="#reglas"     data-sec="reglas"><span class="step-num s7">7</span>Motor de Reglas</a>
  <a href="#defuzzy"    data-sec="defuzzy"><span class="step-num s8">8</span>Defuzzificacion</a>
  <hr class="sep">
  <span class="section-label">Conexion</span>
  <a href="#tags" data-sec="tags">Tags KEPserver</a>
  <a href="#licenciamiento" data-sec="licenciamiento">Licenciamiento</a>
  <hr class="sep">
  <span class="section-label">Visualizacion</span>
  <a href="/graficos" class="external">Graficos en Vivo →</a>
  <a href="/diagrama" class="external">Diagrama de Flujo →</a>
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
    <span style="font-size:.7rem;color:#64748b;margin-left:12px">Tiempos en <b>segundos</b> (separados por punto decimal, ej: 900.0 = 15 min)</span>
  </div>
</div>

<table id="rules-table">
<thead><tr>
  <th>ID</th><th>Bloque</th><th>Prioridad</th><th>Fuerza</th>
  <th>Activacion (IF)</th><th>Acciones (THEN)</th><th>Waits</th><th>Reiniciar Waits</th>
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

<section class="seccion" id="seccion-estados">
  <div class="top-bar">
    <div>
      <h1>Estados y Subestados</h1>
      <h2>Conjuntos reutilizables de condiciones para construir reglas. Cada estado agrupa multiples condiciones (AND).
          Los subestados son condiciones simples con nombre. Tiempos en <b>segundos</b> (decimal con punto).</h2>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="openEstadoModal()">+ Nuevo Estado</button>
      <button class="btn-danger" onclick="resetEstados()">Restaurar default</button>
    </div>
  </div>
  <div id="estados-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>
  <table id="estados-table">
    <thead><tr>
      <th>Nombre</th><th>Tipo</th><th>Condiciones (AND)</th><th style="width:100px">Opciones</th>
    </tr></thead>
    <tbody id="estados-body"></tbody>
  </table>
</section>

<section class="seccion" id="seccion-waits">
  <div class="top-bar">
    <div>
      <h1>Waits (Catalogo)</h1>
      <h2>Waits reutilizables que bloquean acciones del motor. Se definen sin duracion fija; la regla asigna la duracion.
          Cada wait esta asociado a una variable controlada (setpoint). Tiempos en <b>segundos</b> (decimal con punto).</h2>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn-primary" onclick="openWaitModal()">+ Nuevo Wait</button>
      <button class="btn-danger" onclick="resetWaits()">Restaurar default</button>
    </div>
  </div>
  <div id="waits-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>
  <table id="waits-table">
    <thead><tr>
      <th>Nombre</th><th>Wait ID</th><th>Variable Controlada</th><th>Descripcion</th><th style="width:100px">Opciones</th>
    </tr></thead>
    <tbody id="waits-body"></tbody>
  </table>
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

<section class="seccion" id="seccion-tags">
  <div class="top-bar">
    <div>
      <h1>Tags KEPserver</h1>
      <h2>Monitor y editor de tags OPC-UA. Agrega, elimina, habilita o suspende tags
          del KEPserver. Los indicadores de color muestran el estado de cada tag en tiempo real.</h2>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn-primary" onclick="refreshTags()">Actualizar valores</button>
    </div>
  </div>

  <!-- Toggle simulacion -->
  <div id="sim-toggle-box" style="display:flex;align-items:center;gap:14px;margin-bottom:18px;padding:14px 20px;border-radius:10px;border:2px solid #334155;background:#1e293b">
    <div style="flex:1">
      <div style="font-weight:700;font-size:.95rem;margin-bottom:4px" id="sim-toggle-title">Modo de Entrada</div>
      <div style="font-size:.8rem;color:#94a3b8" id="sim-toggle-desc">Cargando...</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <span id="sim-label-off" style="font-size:.78rem;font-weight:600;color:#64748b">TAGS REALES</span>
      <label class="toggle-switch">
        <input type="checkbox" id="sim-toggle-input" onchange="toggleSimulationMode(this.checked)">
        <span class="toggle-slider"></span>
      </label>
      <span id="sim-label-on" style="font-size:.78rem;font-weight:600;color:#64748b">SIMULACION</span>
    </div>
  </div>

  <!-- Boton Iniciar/Detener Sistema -->
  <div id="system-control-box" style="display:flex;align-items:center;gap:14px;margin-bottom:18px;padding:14px 20px;border-radius:10px;border:2px solid #334155;background:#1e293b">
    <div style="flex:1">
      <div style="font-weight:700;font-size:.95rem;margin-bottom:4px" id="system-status-title">Sistema detenido</div>
      <div style="font-size:.8rem;color:#94a3b8" id="system-status-desc">Presiona "Iniciar Sistema" para arrancar el generador de datos y activar la lectura de tags.</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <span id="system-indicator" style="width:14px;height:14px;border-radius:50%;background:#64748b;flex-shrink:0"></span>
      <button class="btn-success" id="btn-system-start" onclick="startSystem()">Iniciar Sistema</button>
      <button class="btn-danger" id="btn-system-stop" onclick="stopSystem()" style="display:none">Detener Sistema</button>
    </div>
  </div>

  <!-- SE Live Status Panel -->
  <div id="se-live-panel" style="display:none;margin-bottom:18px;padding:14px 20px;border-radius:10px;border:2px solid #3b82f6;background:linear-gradient(135deg,#1a1e3a,#1e293b)">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
      <span style="font-weight:700;font-size:.9rem;color:#3b82f6">Sistema Experto en Vivo</span>
      <span id="se-tick-display" style="font-size:.75rem;color:#64748b;margin-left:auto">Tick: 0</span>
    </div>
    <div id="se-sp-values" style="display:flex;gap:24px;margin-bottom:10px"></div>
    <div style="font-size:.75rem;color:#94a3b8;margin-bottom:4px;font-weight:600">Ultimos eventos:</div>
    <div id="se-events-display" style="max-height:100px;overflow-y:auto"><span style="color:#64748b;font-size:.78rem">Esperando primer tick...</span></div>
    <div id="se-error-display" style="display:none;margin-top:8px;font-size:.8rem;color:#ef4444"></div>
  </div>

  <div class="tags-legend">
    <span style="font-weight:600;color:#94a3b8;margin-right:4px">Leyenda:</span>
    <span class="tags-legend-item"><span class="tags-legend-dot dot-connected"></span> Conectado y existente</span>
    <span class="tags-legend-item"><span class="tags-legend-dot dot-not-found"></span> Conectado pero tag no existe</span>
    <span class="tags-legend-item"><span class="tags-legend-dot dot-disconnected"></span> Sin conexion al servidor</span>
    <span class="tags-legend-item"><span class="tags-legend-dot dot-suspended"></span> Tag suspendido</span>
    <span class="tags-legend-item"><span class="tags-legend-dot dot-unknown"></span> Estado desconocido</span>
  </div>

  <!-- Generador de datos -->
  <details id="gen-panel" style="margin-bottom:18px">
    <summary style="cursor:pointer;padding:12px 16px;background:#1e293b;border:1px solid #334155;border-radius:8px;font-weight:700;color:#e2e8f0;font-size:.9rem;display:flex;align-items:center;gap:10px;user-select:none">
      <span style="font-size:1.1rem">&#9881;</span> Generador de Datos para KEPserver
      <span id="gen-status-badge" style="margin-left:auto;padding:3px 10px;border-radius:12px;font-size:.72rem;font-weight:700;background:rgba(100,116,139,.2);color:#94a3b8">DETENIDO</span>
    </summary>
    <div style="padding:16px;background:#1e293b;border:1px solid #334155;border-top:none;border-radius:0 0 8px 8px">
      <p style="font-size:.82rem;color:#94a3b8;margin-bottom:14px">
        Genera valores dinamicos (3 fases: estable, alerta, recuperacion) y los escribe al KEPserver periodicamente.
        Solo escribe en tags RETO.PV.* y RETO.CRUDA.* (entradas de proceso).
      </p>

      <!-- Controles globales -->
      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:16px">
        <div style="display:flex;flex-direction:column;gap:4px">
          <label style="font-size:.7rem;color:#94a3b8;text-transform:uppercase">Intervalo (seg)</label>
          <input id="gen-intervalo" type="number" step="0.5" min="0.5" max="60" value="5" style="width:90px">
        </div>
        <div style="display:flex;flex-direction:column;gap:4px">
          <label style="font-size:.7rem;color:#94a3b8;text-transform:uppercase">Ticks por ciclo</label>
          <input id="gen-n-ciclo" type="number" step="1" min="10" max="500" value="60" style="width:90px">
        </div>
        <div style="display:flex;flex-direction:column;gap:4px">
          <label style="font-size:.7rem;color:#94a3b8;text-transform:uppercase">Tick actual</label>
          <span id="gen-tick" style="font-size:.9rem;font-weight:700;color:#38bdf8;padding:8px 0">0</span>
        </div>
        <div style="display:flex;gap:8px;margin-left:auto">
          <button class="btn-primary" onclick="saveGeneratorConfig()">Guardar config</button>
          <button class="btn-success" id="gen-start-btn" onclick="startGenerator()">Iniciar</button>
          <button class="btn-danger" id="gen-stop-btn" onclick="stopGenerator()" style="display:none">Detener</button>
        </div>
      </div>

      <div id="gen-error" style="font-size:.82rem;color:#ef4444;margin-bottom:8px;min-height:1em"></div>

      <!-- Tabla de rangos -->
      <div style="max-height:400px;overflow-y:auto">
        <table style="font-size:.82rem">
          <thead>
            <tr>
              <th>Tag</th>
              <th style="width:90px">Min</th>
              <th style="width:90px">Max</th>
              <th style="width:80px">Ruido</th>
              <th style="width:100px">Valor actual</th>
            </tr>
          </thead>
          <tbody id="gen-ranges-body"></tbody>
        </table>
      </div>
    </div>
  </details>

  <div id="tags-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>

  <div class="tags-add-form">
    <input id="tag-new-name" type="text" placeholder="Nombre del tag (ej: RETO.IN.Potencia_SAG)" style="flex:1;min-width:280px">
    <select id="tag-new-type">
      <option value="Float">Float</option>
      <option value="Int">Int</option>
      <option value="Boolean">Boolean</option>
      <option value="String">String</option>
    </select>
    <button class="btn-success" onclick="addTag()">+ Agregar Tag</button>
  </div>

  <table>
    <thead>
      <tr>
        <th style="width:40px">#</th>
        <th>Nombre del Tag</th>
        <th style="width:90px">Tipo</th>
        <th style="width:120px">Valor</th>
        <th style="width:160px">Estado</th>
        <th style="width:100px">Quality</th>
        <th style="width:180px">Acciones</th>
      </tr>
    </thead>
    <tbody id="tags-body">
      <tr><td colspan="7" class="loading">Cargando tags...</td></tr>
    </tbody>
  </table>

  <details style="margin-top:18px">
    <summary style="cursor:pointer;color:#94a3b8">Informacion sobre Tags KEPserver</summary>
    <div style="background:#0f172a;color:#cbd5e1;padding:14px;border-radius:6px;font-size:.82rem;margin-top:6px;line-height:1.6">
      <p><strong>Formato de tag:</strong> <code>Canal.Dispositivo.Variable</code> (ej: <code>RETO.IN.Potencia_SAG</code>)</p>
      <p style="margin-top:8px"><strong>Tipos soportados:</strong> Float, Int, Boolean, String.</p>
      <p style="margin-top:8px"><strong>Conectividad:</strong> El sistema intenta conectarse via OPC-UA a <code>opc.tcp://127.0.0.1:49320</code>.
         Si el KEPserver no esta disponible, los tags se mostraran como desconectados pero su configuracion se mantiene.</p>
      <p style="margin-top:8px"><strong>Tags suspendidos:</strong> No se leeran ni escribiran al KEPserver mientras esten suspendidos.
         Esto es util para desactivar temporalmente un tag sin eliminarlo.</p>
    </div>
  </details>
</section>

<section class="seccion" id="seccion-licenciamiento">
  <div class="top-bar">
    <div>
      <h1>Licenciamiento</h1>
      <h2>Prototipo de activacion manual por codigo. Aunque la licencia expire, esta lamina seguira accesible desde el navegador.</h2>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <span class="tag" style="background:rgba(250,204,21,.15);color:#facc15;border-color:rgba(250,204,21,.35)">PROTOTIPO</span>
    </div>
  </div>

  <div id="lic-msg" style="margin-bottom:10px;font-size:.85rem;min-height:1.2em"></div>

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:18px">
    <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px">
      <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Estado</div>
      <div id="lic-status-badge" style="display:inline-flex;align-items:center;padding:6px 12px;border-radius:999px;background:rgba(100,116,139,.18);color:#cbd5e1;font-weight:700">Sin activar</div>
      <div style="margin-top:10px;font-size:.82rem;color:#94a3b8">Aun no se aplican restricciones funcionales cuando la licencia vence.</div>
    </div>
    <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px">
      <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Codigo registrado</div>
      <div id="lic-code-display" style="font-size:1rem;font-weight:700;color:#e2e8f0">No ingresado</div>
      <div style="margin-top:10px;font-size:.82rem;color:#94a3b8">Se muestra enmascarado dentro de la interfaz.</div>
    </div>
    <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:16px">
      <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Vigencia</div>
      <div id="lic-period-display" style="font-size:1rem;font-weight:700;color:#e2e8f0">Sin periodo</div>
      <div id="lic-remaining-display" style="margin-top:10px;font-size:.82rem;color:#94a3b8">Sin dias restantes</div>
    </div>
  </div>

  <div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:18px;margin-bottom:18px">
    <div style="display:grid;grid-template-columns:minmax(260px,2fr) minmax(140px,1fr) auto;gap:12px;align-items:end">
      <div>
        <label style="display:block;font-size:.76rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Codigo de activacion</label>
        <input id="lic-code-input" type="text" placeholder="Ingresa codigo de licencia" style="width:100%">
      </div>
      <div>
        <label style="display:block;font-size:.76rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Periodo</label>
        <select id="lic-period-select" style="width:100%">
          <option value="3">3 meses</option>
          <option value="6">6 meses</option>
          <option value="9">9 meses</option>
          <option value="12">12 meses</option>
        </select>
      </div>
      <button class="btn-success" onclick="activateLicense()">Activar</button>
    </div>
  </div>

  <div style="background:#0f172a;border:1px solid #334155;border-radius:10px;padding:16px">
    <div style="font-size:.76rem;color:#64748b;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Detalle</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;font-size:.85rem;color:#cbd5e1">
      <div><strong>Activada en:</strong> <span id="lic-activated-display">-</span></div>
      <div><strong>Expira en:</strong> <span id="lic-expires-display">-</span></div>
      <div><strong>Modo actual:</strong> <span id="lic-mode-display">Prototipo</span></div>
    </div>
    <div id="lic-notes" style="margin-top:12px;font-size:.82rem;color:#94a3b8"></div>
  </div>
</section>

<!-- Modal edicion -->
<div class="modal-overlay" id="modal-overlay">
<div class="modal" style="width:900px">
  <h3 id="modal-title">Nueva Regla</h3>
  <div class="form-grid">
    <div class="form-group"><label>ID</label><input id="f-id" placeholder="ej: E1.S1 o E6.S2"></div>
    <div class="form-group"><label>Bloque</label>
      <select id="f-bloque"></select>
      <span class="hint">critico bloquea estabilidad; optimizacion corre independiente.</span>
    </div>
    <div class="form-group"><label>Prioridad</label><input id="f-priority" type="number" step="1" value="50"></div>
    <div class="form-group"><label>Weight</label><input id="f-weight" type="number" step="0.1" value="1.0"></div>
    <div class="form-group full">
      <label>Fuerza (condiciones OR que aportan el belief)</label>
      <div id="fuerza-container"></div>
      <button class="btn-sm" style="background:#06b6d4;color:#fff;margin-top:4px" onclick="addFuerzaRow()">+ Condicion de fuerza</button>
      <span class="hint">Multiples filas se evaluan como OR. Si no se define, se usa el min de la activacion. Tiempos en <b>segundos</b> (ej: 900.0 = 15 min).</span>
    </div>
    <div class="form-group full">
      <label>Activacion (IF) -- Estados/Subestados + condiciones hoja</label>
      <div id="estados-container" style="margin-bottom:8px"></div>
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <button class="btn-sm" style="background:#8b5cf6;color:#fff" onclick="addEstado()">+ Estado/Subestado</button>
      </div>
      <div id="conds-container"></div>
      <div style="display:flex;gap:6px;margin-top:4px">
        <button class="btn-primary btn-sm" onclick="addCond()">+ Condicion hoja</button>
        <button class="btn-sm" style="background:#facc15;color:#1e293b" onclick="addOrGroup()">+ Grupo OR</button>
      </div>
    </div>
    <div class="form-group full">
      <label>Acciones (THEN) con Waits</label>
      <div id="actions-container"></div>
      <button class="btn-primary btn-sm" onclick="addActionRow()" style="margin-top:4px">+ Accion</button>
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
const SETPOINTS = SETPOINTS_JSON;
let ESTADOS = ESTADOS_JSON;
let WAITS   = WAITS_JSON;

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
function _isAndGroup(x) {
  return x && typeof x === 'object' && !Array.isArray(x) && Array.isArray(x.AND);
}
function _condsToItems(ifList) {
  if (!Array.isArray(ifList)) return [];
  if (ifList.length === 1 && ifList[0] && Array.isArray(ifList[0].AND)) {
    return ifList[0].AND;
  }
  return ifList;
}
function _isComplexIf(ifList) {
  const items = _condsToItems(ifList);
  if (!items.length) return false;
  return !items.every(it => _isLeaf(it) || _isOrGroup(it) || _isAndGroup(it));
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
  if (_isAndGroup(it)) {
    const inner = it.AND.map(leaf => {
      if (_isLeaf(leaf)) return `<span class="tag tag-var">${leaf[0]}</span> <span class="tag tag-label">${leaf[1]}</span>`;
      return _renderItemHTML(leaf);
    }).join(' <b style="color:#22c55e">&</b> ');
    return `<span style="color:#64748b;font-size:.7rem">[Estado]</span> ${inner}`;
  }
  return `<span class="tag" style="border-left:3px solid #f87171">?</span>`;
}

function _populateBlockSelect() {
  const s = document.getElementById('f-bloque');
  s.innerHTML = BLOCKS.map(b => `<option value="${b}">${b}</option>`).join('');
}
_populateBlockSelect();

function addFuerzaRow(v, l) {
  if (!v) v = VARS[0];
  if (!l) l = 'LOW';
  const c = document.getElementById('fuerza-container');
  const row = document.createElement('div');
  row.className = 'cond-row fuerza-row';
  row.style.cssText = 'border-left:3px solid #06b6d4;padding-left:6px';
  row.innerHTML = `
    <span style="color:#06b6d4;font-size:.7rem;width:22px">OR</span>
    <select class="cfv">${VARS.map(x=>`<option ${x===v?'selected':''}>${x}</option>`).join('')}</select>
    <select class="cfl">${LABELS.map(x=>`<option ${x===l?'selected':''}>${x}</option>`).join('')}</select>
    <button class="btn-danger btn-sm" onclick="this.parentElement.remove()">x</button>`;
  c.appendChild(row);
}

function _getEstadoNames() { return Object.keys(ESTADOS); }
function _selEstado(selected) {
  const names = _getEstadoNames();
  return `<select class="ce">${names.map(e => `<option value="${e}" ${e===selected?'selected':''}>${e}</option>`).join('')}</select>`;
}

function addEstado(nombre) {
  const names = _getEstadoNames();
  if (!nombre) nombre = names[0] || '';
  const c = document.getElementById('estados-container');
  const row = document.createElement('div');
  row.className = 'cond-row';
  row.dataset.kind = 'estado';
  row.style.cssText = 'border-left:3px solid #8b5cf6;padding-left:6px';
  row.innerHTML = `
    <span style="color:#8b5cf6;font-size:.7rem;width:60px">ESTADO</span>
    ${_selEstado(nombre)}
    <button class="btn-danger btn-sm" onclick="this.parentElement.remove()">x</button>`;
  c.appendChild(row);
}

function _selWait(selected) {
  const opts = WAITS.map(w => `<option value="${w.wait_id}" ${w.wait_id===selected?'selected':''}>${w.nombre} (${w.variable_controlada})</option>`);
  return `<select class="cw"><option value="">(ninguno)</option>${opts.join('')}</select>`;
}

function addActionRow(accion, waitId, duracionS, reiniciarList) {
  if (!accion) accion = ACTIONS[0];
  if (!waitId) waitId = '';
  if (!duracionS) duracionS = 900;
  if (!reiniciarList) reiniciarList = [];
  const c = document.getElementById('actions-container');
  const row = document.createElement('div');
  row.className = 'action-row';
  row.style.cssText = 'border:1px solid #334155;border-radius:6px;padding:8px;margin-bottom:6px;background:#1a2436';

  const reiniciarCheckboxes = WAITS.map(w => {
    const match = reiniciarList.find(r => r.id === w.wait_id);
    const checked = match ? 'checked' : '';
    const dur = match ? match.duracion : duracionS;
    return `<label style="font-size:.75rem;display:flex;align-items:center;gap:4px;color:#94a3b8">
      <input type="checkbox" class="cr" value="${w.wait_id}" ${checked}>
      ${w.nombre}
      <input type="number" class="crd" data-wait="${w.wait_id}" value="${dur}" min="0" step="60" style="width:65px;font-size:.75rem" title="duracion (s)">
      <span style="font-size:.65rem;color:#64748b">s</span></label>`;
  }).join('');

  row.innerHTML = `
    <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px">
      <span style="color:#22c55e;font-size:.7rem;width:50px">ACCION</span>
      <select class="ca">${ACTIONS.map(x=>`<option ${x===accion?'selected':''}>${x}</option>`).join('')}</select>
      <button class="btn-danger btn-sm" onclick="this.closest('.action-row').remove()">x</button>
    </div>
    <div style="display:flex;gap:6px;align-items:center;margin-bottom:4px">
      <span style="color:#38bdf8;font-size:.7rem;width:50px">WAIT</span>
      ${_selWait(waitId)}
      <input type="number" class="cd" value="${duracionS}" min="0" step="60" style="width:80px" placeholder="seg">
      <span style="font-size:.7rem;color:#64748b">s</span>
    </div>
    <div style="margin-top:4px">
      <span style="color:#fb923c;font-size:.7rem">REINICIAR WAITS (marcar + duracion en segundos):</span>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:4px">${reiniciarCheckboxes}</div>
    </div>`;
  c.appendChild(row);
}

function addAction(a) { addActionRow(a); }

function _renderFuerza(fuerza) {
  if (!fuerza) return '<span style="color:#64748b">-</span>';
  if (Array.isArray(fuerza) && fuerza.length === 2 && typeof fuerza[0] === 'string' && typeof fuerza[1] === 'string') {
    return `<span class="tag">${fuerza[0]} = ${fuerza[1]}</span>`;
  }
  if (fuerza.OR && Array.isArray(fuerza.OR)) {
    return fuerza.OR.map(f => `<span class="tag">${f[0]} = ${f[1]}</span>`).join(' <b style="color:#facc15;font-size:.7rem">OR</b> ');
  }
  return `<span class="tag">${JSON.stringify(fuerza)}</span>`;
}

function _renderThenActions(then_list) {
  if (!then_list || then_list.length === 0) return '-';
  return then_list.map(item => {
    if (typeof item === 'string') return `<span class="tag tag-action">${item}</span>`;
    if (typeof item === 'object' && item.accion) return `<span class="tag tag-action">${item.accion}</span>`;
    return `<span class="tag tag-action">${JSON.stringify(item)}</span>`;
  }).join('<br>');
}

function _renderWaits(then_list) {
  if (!then_list || then_list.length === 0) return '-';
  const parts = [];
  for (const item of then_list) {
    if (typeof item !== 'object' || !item.waits) continue;
    for (const w of item.waits) {
      parts.push(`<span class="tag" style="border-left:3px solid #38bdf8">${w.nombre || w.wait_id} (${w.duracion_s}s)</span>`);
    }
  }
  return parts.length > 0 ? parts.join('<br>') : '-';
}

function _renderReiniciarWaits(then_list) {
  if (!then_list || then_list.length === 0) return '-';
  const parts = [];
  for (const item of then_list) {
    if (typeof item !== 'object' || !item.reiniciar_waits) continue;
    for (const w of item.reiniciar_waits) {
      const dur = w.duracion_s != null ? w.duracion_s + 's' : '';
      parts.push(`<span class="tag" style="border-left:3px solid #fb923c">${w.nombre || w.wait_id} (${dur})</span>`);
    }
  }
  return parts.length > 0 ? parts.join('<br>') : '-';
}

async function loadRules() {
  const res = await fetch('/api/reglas');
  const rules = await res.json();
  const tbody = document.getElementById('rules-body');
  tbody.innerHTML = '';
  if (!rules || rules.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;color:#64748b;padding:24px">
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
    const fuerza = _renderFuerza(r.fuerza);
    const acts = _renderThenActions(r['then']);
    const waits = _renderWaits(r['then']);
    const reiniciar = _renderReiniciarWaits(r['then']);
    tr.innerHTML = `<td><b>${r.id}</b></td>
      <td><span class="tag tag-block">${r.bloque||'-'}</span></td>
      <td>${r.priority ?? '-'}</td>
      <td>${fuerza}</td>
      <td>${conds}</td><td>${acts}</td>
      <td>${waits}</td><td>${reiniciar}</td>
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


async function openModal(rule=null) {
  // Refresh estados and waits to pick up any newly created ones
  try {
    const [re, rw] = await Promise.all([fetch('/api/estados'), fetch('/api/waits')]);
    if (re.ok) ESTADOS = await re.json();
    if (rw.ok) WAITS = await rw.json();
  } catch(e) {}
  editingId = null;
  document.getElementById('modal-title').textContent = 'Nueva Regla';
  document.getElementById('f-id').value = '';
  document.getElementById('f-id').disabled = false;
  document.getElementById('f-priority').value = '50';
  document.getElementById('f-weight').value = '1.0';
  document.getElementById('f-bloque').value = BLOCKS[1] || BLOCKS[0];
  document.getElementById('fuerza-container').innerHTML = '';
  document.getElementById('estados-container').innerHTML = '';
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

    // Fuerza (single leaf [v,l], OR {OR:[[v,l]...]}, or list [[v,l]...])
    if (rule.fuerza) {
      const f = rule.fuerza;
      if (Array.isArray(f) && f.length === 2 && typeof f[0] === 'string' && typeof f[1] === 'string') {
        addFuerzaRow(f[0], f[1]);
      } else if (Array.isArray(f)) {
        for (const item of f) { if (Array.isArray(item) && item.length===2) addFuerzaRow(item[0], item[1]); }
      } else if (f.OR && Array.isArray(f.OR)) {
        for (const item of f.OR) { if (Array.isArray(item) && item.length===2) addFuerzaRow(item[0], item[1]); }
      }
    }

    // Condiciones: separar estados/subestados (AND groups) de hojas
    for (const it of _condsToItems(rule['if'])) {
      if (_isAndGroup(it)) {
        // Try to match to a known estado
        const matched = _matchEstado(it);
        if (matched) { addEstado(matched); }
        else {
          // Add each leaf from AND group as individual conditions
          for (const leaf of it.AND) { if (_isLeaf(leaf)) addCond(leaf[0], leaf[1]); }
        }
      } else if (_isOrGroup(it)) { addOrGroup(it.OR); }
      else if (_isLeaf(it)) { addCond(it[0], it[1]); }
    }

    // Acciones con waits
    for (const a of (rule['then']||[])) {
      if (typeof a === 'string') {
        addActionRow(a, '', 900, []);
      } else if (typeof a === 'object' && a.accion) {
        const waitId = (a.waits && a.waits[0]) ? a.waits[0].wait_id : '';
        const duracion = (a.waits && a.waits[0]) ? a.waits[0].duracion_s : 900;
        const reiniciar = (a.reiniciar_waits || []).map(w => ({id: w.wait_id, duracion: w.duracion_s || 900}));
        addActionRow(a.accion, waitId, duracion, reiniciar);
      }
    }
  } else {
    addCond(); addActionRow();
  }
  document.getElementById('modal-overlay').classList.add('active');
}

function _matchEstado(andGroup) {
  const json_and = JSON.stringify(andGroup.AND);
  for (const [name, est] of Object.entries(ESTADOS)) {
    if (est.condicion && est.condicion.AND && JSON.stringify(est.condicion.AND) === json_and) return name;
  }
  return null;
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

  // Fuerza (OR de condiciones)
  const fuerzaRows = [...document.querySelectorAll('#fuerza-container .fuerza-row')];
  const fuerzaItems = fuerzaRows.map(r => [r.querySelector('.cfv').value, r.querySelector('.cfl').value]);
  let fuerza = null;
  if (fuerzaItems.length === 1) fuerza = fuerzaItems[0];
  else if (fuerzaItems.length > 1) fuerza = {OR: fuerzaItems};

  // Recolecta estados del contenedor de estados
  const estadosContainer = document.getElementById('estados-container');
  const ifItems = [];
  for (const child of estadosContainer.children) {
    if (child.dataset.kind === 'estado') {
      const nombre = child.querySelector('.ce').value;
      const est = ESTADOS[nombre];
      if (est && est.condicion) {
        ifItems.push(est.condicion);
      }
    }
  }

  // Recolecta condiciones hoja y grupos OR
  const container = document.getElementById('conds-container');
  for (const child of container.children) {
    if (child.dataset.kind === 'leaf') {
      ifItems.push([child.querySelector('.cv').value, child.querySelector('.cl').value]);
    } else if (child.dataset.kind === 'or') {
      const leaves = [...child.querySelectorAll('.or-leaves .or-leaf')].map(r => [
        r.querySelector('.cv').value, r.querySelector('.cl').value
      ]);
      if (leaves.length < 2) return alert('Cada grupo OR debe tener al menos 2 hojas.');
      ifItems.push({OR: leaves});
    }
  }
  if (ifItems.length === 0) return alert('Agrega al menos una condicion o estado.');

  // Acciones con waits
  const acts = [];
  for (const row of document.querySelectorAll('#actions-container .action-row')) {
    const accion = row.querySelector('.ca').value;
    const waitId = row.querySelector('.cw').value;
    const duracion = parseFloat(row.querySelector('.cd').value) || 900;
    const reiniciarChecked = [...row.querySelectorAll('.cr:checked')];

    const waits = [];
    if (waitId) {
      const waitDef = WAITS.find(w => w.wait_id === waitId);
      if (waitDef) {
        waits.push({...waitDef, duracion_s: duracion, accion: accion});
      }
    }
    const reiniciar_waits = [];
    for (const cb of reiniciarChecked) {
      const rId = cb.value;
      const rDef = WAITS.find(w => w.wait_id === rId);
      const rDurInput = row.querySelector(`.crd[data-wait="${rId}"]`);
      const rDur = rDurInput ? parseFloat(rDurInput.value) || 900 : 900;
      if (rDef) {
        reiniciar_waits.push({...rDef, duracion_s: rDur, accion: accion});
      }
    }
    acts.push({accion, waits, reiniciar_waits});
  }
  if (acts.length === 0) return alert('Agrega al menos una accion.');

  const rule = {
    id:       id,
    bloque:   document.getElementById('f-bloque').value,
    fuerza:   fuerza,
    'if':     ifItems,
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
const SECCIONES = ['reglas','filtros','defuzzy','fuzzy','estados','waits','variables','permisivos','tags','licenciamiento'];
const _seccionLoaded = {reglas: false, filtros: false, defuzzy: false, fuzzy: false, estados: false, waits: false, variables: false, permisivos: false, tags: false, licenciamiento: false};

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
  if (name === 'estados' && !_seccionLoaded.estados) {
    loadEstadosTab();
    _seccionLoaded.estados = true;
  }
  if (name === 'waits' && !_seccionLoaded.waits) {
    loadWaitsTab();
    _seccionLoaded.waits = true;
  }
  if (name === 'permisivos' && !_seccionLoaded.permisivos) {
    loadPermisivos();
    _seccionLoaded.permisivos = true;
  }
  if (name === 'tags' && !_seccionLoaded.tags) {
    loadTags();
    _seccionLoaded.tags = true;
  }
  if (name === 'licenciamiento' && !_seccionLoaded.licenciamiento) {
    loadLicenciamiento();
    _seccionLoaded.licenciamiento = true;
  }
}

// ============================================================
// Estados CRUD
// ============================================================
let _estadosData = {};

async function loadEstadosTab() {
  const res = await fetch('/api/estados');
  _estadosData = await res.json();
  ESTADOS = _estadosData;
  _renderEstadosTable();
}

function _renderEstadosTable() {
  const tbody = document.getElementById('estados-body');
  tbody.innerHTML = '';
  for (const [nombre, est] of Object.entries(_estadosData)) {
    const tr = document.createElement('tr');
    const conds = est.condicion && est.condicion.AND
      ? est.condicion.AND.map(c => `<span class="tag tag-var">${c[0]}</span> <span class="tag tag-label">${c[1]}</span>`).join('<br>')
      : JSON.stringify(est.condicion || []);
    tr.innerHTML = `<td><b>${nombre}</b></td>
      <td><span class="tag tag-block">${est.tipo}</span></td>
      <td>${conds}</td>
      <td>
        <button class="btn-primary btn-sm" onclick="editEstadoTab('${nombre}')">Editar</button>
        <button class="btn-danger btn-sm" onclick="deleteEstadoTab('${nombre}')">Borrar</button>
      </td>`;
    tbody.appendChild(tr);
  }
}

function openEstadoModal(est=null) {
  const nombre = est ? est.nombre : '';
  const tipo = est ? est.tipo : 'estado';
  const condiciones = est && est.condicion && est.condicion.AND ? est.condicion.AND : [];
  const condsHtml = condiciones.map(c =>
    `<div class="cond-row" style="margin-bottom:2px"><select class="esv">${VARS.map(v=>`<option ${v===c[0]?'selected':''}>${v}</option>`).join('')}</select>` +
    `<select class="esl">${LABELS.map(l=>`<option ${l===c[1]?'selected':''}>${l}</option>`).join('')}</select>` +
    `<button class="btn-danger btn-sm" onclick="this.parentElement.remove()">x</button></div>`
  ).join('');

  const html = `<div class="modal-overlay active" id="estado-modal-overlay">
    <div class="modal" style="width:600px" onclick="event.stopPropagation()">
      <h3>${est ? 'Editar' : 'Nuevo'} Estado</h3>
      <div class="form-grid">
        <div class="form-group"><label>Nombre</label><input id="es-nombre" value="${nombre}" ${est?'disabled':''}></div>
        <div class="form-group"><label>Tipo</label>
          <select id="es-tipo"><option ${tipo==='estado'?'selected':''}>estado</option><option ${tipo==='subestado'?'selected':''}>subestado</option></select>
        </div>
        <div class="form-group full">
          <label>Condiciones (AND)</label>
          <div id="es-conds">${condsHtml}</div>
          <button class="btn-primary btn-sm" onclick="document.getElementById('es-conds').insertAdjacentHTML('beforeend',
            '<div class=\\'cond-row\\' style=\\'margin-bottom:2px\\'><select class=\\'esv\\'>${VARS.map(v=>'<option>'+v+'</option>').join('')}</select><select class=\\'esl\\'>${LABELS.map(l=>'<option>'+l+'</option>').join('')}</select><button class=\\'btn-danger btn-sm\\' onclick=\\'this.parentElement.remove()\\'>x</button></div>'
          )" style="margin-top:4px">+ Condicion</button>
        </div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button class="btn-danger" onclick="document.getElementById('estado-modal-overlay').remove()">Cancelar</button>
        <button class="btn-success" onclick="saveEstadoTab(${est?'true':'false'})">Guardar</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

async function editEstadoTab(nombre) {
  const res = await fetch('/api/estados/' + encodeURIComponent(nombre));
  if (!res.ok) return alert('Error');
  const est = await res.json();
  openEstadoModal(est);
}

async function deleteEstadoTab(nombre) {
  if (!confirm('Eliminar estado ' + nombre + '?')) return;
  const res = await fetch('/api/estados/' + encodeURIComponent(nombre), {method:'DELETE'});
  if (res.ok) loadEstadosTab();
  else alert('Error eliminando');
}

async function saveEstadoTab(isEdit) {
  const nombre = document.getElementById('es-nombre').value.trim();
  if (!nombre) return alert('Nombre requerido');
  const tipo = document.getElementById('es-tipo').value;
  const rows = [...document.querySelectorAll('#es-conds .cond-row')];
  const conds = rows.map(r => [r.querySelector('.esv').value, r.querySelector('.esl').value]);
  if (conds.length === 0) return alert('Al menos una condicion');
  const payload = {nombre, tipo, condicion: {AND: conds}};
  const method = isEdit ? 'PUT' : 'POST';
  const url = isEdit ? '/api/estados/' + encodeURIComponent(nombre) : '/api/estados';
  const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Error');
  document.getElementById('estado-modal-overlay').remove();
  loadEstadosTab();
}

async function resetEstados() {
  if (!confirm('Restaurar estados al default del core?')) return;
  await fetch('/api/estados/reset', {method:'POST'});
  loadEstadosTab();
}

// ============================================================
// Waits CRUD
// ============================================================
let _waitsData = [];

async function loadWaitsTab() {
  const res = await fetch('/api/waits');
  _waitsData = await res.json();
  WAITS = _waitsData;
  _renderWaitsTable();
}

function _renderWaitsTable() {
  const tbody = document.getElementById('waits-body');
  tbody.innerHTML = '';
  for (const w of _waitsData) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><b>${w.nombre}</b></td>
      <td><code style="font-size:.75rem">${w.wait_id}</code></td>
      <td>${w.variable_controlada || '-'}</td>
      <td style="font-size:.8rem">${w.descripcion || ''}</td>
      <td>
        <button class="btn-primary btn-sm" onclick="editWaitTab('${w.wait_id}')">Editar</button>
        <button class="btn-danger btn-sm" onclick="deleteWaitTab('${w.wait_id}')">Borrar</button>
      </td>`;
    tbody.appendChild(tr);
  }
}

function openWaitModal(w=null) {
  const nombre = w ? w.nombre : '';
  const varCtrl = w ? (w.variable_controlada || '') : '';
  const desc = w ? (w.descripcion || '') : '';
  const spVars = SETPOINTS;

  const html = `<div class="modal-overlay active" id="wait-modal-overlay">
    <div class="modal" style="width:550px" onclick="event.stopPropagation()">
      <h3>${w ? 'Editar' : 'Nuevo'} Wait</h3>
      <div class="form-grid">
        <div class="form-group"><label>Nombre</label><input id="wt-nombre" value="${nombre}" ${w?'disabled':''}></div>
        <div class="form-group"><label>Variable controlada (setpoint)</label>
          <select id="wt-var"><option value="">(ninguna)</option>${spVars.map(v=>`<option ${v===varCtrl?'selected':''}>${v}</option>`).join('')}</select>
        </div>
        <div class="form-group full"><label>Descripcion</label><input id="wt-desc" value="${desc}"></div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button class="btn-danger" onclick="document.getElementById('wait-modal-overlay').remove()">Cancelar</button>
        <button class="btn-success" onclick="saveWaitTab(${w?'true':'false'})">Guardar</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

async function editWaitTab(waitId) {
  const res = await fetch('/api/waits/' + encodeURIComponent(waitId));
  if (!res.ok) return alert('Error');
  const w = await res.json();
  openWaitModal(w);
}

async function deleteWaitTab(waitId) {
  if (!confirm('Eliminar wait ' + waitId + '?')) return;
  const res = await fetch('/api/waits/' + encodeURIComponent(waitId), {method:'DELETE'});
  if (res.ok) loadWaitsTab();
  else alert('Error eliminando');
}

async function saveWaitTab(isEdit) {
  const nombre = document.getElementById('wt-nombre').value.trim();
  if (!nombre) return alert('Nombre requerido');
  const variable_controlada = document.getElementById('wt-var').value || null;
  const descripcion = document.getElementById('wt-desc').value.trim();
  const payload = {nombre, variable_controlada, descripcion};
  const method = isEdit ? 'PUT' : 'POST';
  const url = isEdit ? '/api/waits/' + encodeURIComponent('wait::' + nombre.toLowerCase().replace(/[^a-z0-9]/g,'_')) : '/api/waits';
  const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Error');
  document.getElementById('wait-modal-overlay').remove();
  loadWaitsTab();
}

async function resetWaits() {
  if (!confirm('Restaurar waits al default del core?')) return;
  await fetch('/api/waits/reset', {method:'POST'});
  loadWaitsTab();
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

// ============================================================
// Tags KEPserver
// ============================================================

let _tagsData = [];
let _simulationMode = true;

function _setLicMsg(text, color) {
  const m = document.getElementById('lic-msg');
  if (m) { m.textContent = text || ''; m.style.color = color || '#94a3b8'; }
}

function _formatLicDate(value) {
  if (!value) return '-';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString('es-CL');
}

function _renderLicenciamiento(data) {
  const statusEl = document.getElementById('lic-status-badge');
  const codeEl = document.getElementById('lic-code-display');
  const periodEl = document.getElementById('lic-period-display');
  const remainEl = document.getElementById('lic-remaining-display');
  const activatedEl = document.getElementById('lic-activated-display');
  const expiresEl = document.getElementById('lic-expires-display');
  const notesEl = document.getElementById('lic-notes');
  const modeEl = document.getElementById('lic-mode-display');

  const palette = {
    sin_activar: {bg: 'rgba(100,116,139,.18)', fg: '#cbd5e1'},
    activa: {bg: 'rgba(34,197,94,.18)', fg: '#22c55e'},
    expirada: {bg: 'rgba(239,68,68,.18)', fg: '#f87171'},
  };
  const style = palette[data.status] || palette.sin_activar;

  statusEl.textContent = data.estado_label || 'Sin activar';
  statusEl.style.background = style.bg;
  statusEl.style.color = style.fg;
  codeEl.textContent = data.codigo_masked || 'No ingresado';
  periodEl.textContent = data.periodo_meses ? (data.periodo_meses + ' meses') : 'Sin periodo';
  remainEl.textContent = data.dias_restantes === null || data.dias_restantes === undefined
    ? 'Sin dias restantes'
    : ('Dias restantes: ' + data.dias_restantes);
  activatedEl.textContent = _formatLicDate(data.activada_en);
  expiresEl.textContent = _formatLicDate(data.expira_en);
  notesEl.textContent = data.notas || '';
  modeEl.textContent = data.prototype ? 'Prototipo' : 'Licenciamiento activo';
}

async function loadLicenciamiento() {
  try {
    const r = await fetch('/api/licenciamiento');
    const d = await r.json();
    if (!r.ok) {
      _setLicMsg(d.error || 'Error cargando licenciamiento.', '#ef4444');
      return;
    }
    _renderLicenciamiento(d);
  } catch (e) {
    _setLicMsg('Error cargando licenciamiento: ' + e.message, '#ef4444');
  }
}

async function activateLicense() {
  const codeEl = document.getElementById('lic-code-input');
  const periodEl = document.getElementById('lic-period-select');
  const codigo = (codeEl.value || '').trim();
  const periodoMeses = parseInt(periodEl.value, 10);

  if (!codigo) {
    _setLicMsg('Ingresa un codigo de licencia.', '#f59e0b');
    codeEl.focus();
    return;
  }

  try {
    const r = await fetch('/api/licenciamiento/activar', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({codigo: codigo, periodo_meses: periodoMeses})
    });
    const d = await r.json();
    if (!r.ok) {
      _setLicMsg(d.error || 'No se pudo activar la licencia.', '#ef4444');
      return;
    }
    _renderLicenciamiento(d.licencia || {});
    codeEl.value = '';
    _setLicMsg('Licencia prototipo activada por ' + periodoMeses + ' meses.', '#22c55e');
    setTimeout(() => _setLicMsg(''), 4000);
  } catch (e) {
    _setLicMsg('Error activando licencia: ' + e.message, '#ef4444');
  }
}

function _setTagsMsg(text, color) {
  const m = document.getElementById('tags-msg');
  if (m) { m.textContent = text || ''; m.style.color = color || '#94a3b8'; }
}

function _updateSimToggleUI(mode) {
  _simulationMode = mode;
  const input = document.getElementById('sim-toggle-input');
  const title = document.getElementById('sim-toggle-title');
  const desc = document.getElementById('sim-toggle-desc');
  const labelOn = document.getElementById('sim-label-on');
  const labelOff = document.getElementById('sim-label-off');
  const section = document.getElementById('seccion-tags');

  if (input) input.checked = mode;
  section.classList.toggle('sim-mode-on', mode);
  section.classList.toggle('sim-mode-off', !mode);

  if (mode) {
    title.textContent = 'Modo: SIMULACION activa';
    title.style.color = '#22c55e';
    desc.textContent = 'El sistema usa variables simuladas desde un DataFrame (simulacion.py). Los Tags del KEPserver NO se utilizan como entrada.';
    labelOn.style.color = '#22c55e';
    labelOff.style.color = '#64748b';
  } else {
    title.textContent = 'Modo: TAGS REALES (KEPserver)';
    title.style.color = '#ef4444';
    desc.textContent = 'El sistema lee datos de entrada directamente desde los Tags del KEPserver via OPC-UA. Las variables simuladas estan desactivadas.';
    labelOn.style.color = '#64748b';
    labelOff.style.color = '#ef4444';
  }
}

async function toggleSimulationMode(checked) {
  try {
    const r = await fetch('/api/tags/simulation', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({simulation_mode: checked})
    });
    const d = await r.json();
    if (d.ok) {
      _updateSimToggleUI(d.simulation_mode);
      _updateSystemControlUI();
      _setTagsMsg(d.simulation_mode ? 'Modo simulacion activado.' : 'Modo tags reales activado.', '#22c55e');
      setTimeout(() => _setTagsMsg(''), 3000);
    }
  } catch (e) {
    _setTagsMsg('Error al cambiar modo: ' + e.message, '#ef4444');
  }
}

let _systemRunning = false;

function _updateSystemControlUI() {
  const title = document.getElementById('system-status-title');
  const desc = document.getElementById('system-status-desc');
  const indicator = document.getElementById('system-indicator');
  const startBtn = document.getElementById('btn-system-start');
  const stopBtn = document.getElementById('btn-system-stop');
  const box = document.getElementById('system-control-box');

  if (_simulationMode) {
    title.textContent = 'Sistema en modo Simulacion';
    title.style.color = '#94a3b8';
    desc.textContent = 'El SE usa variables simuladas del DataFrame. No requiere iniciar el sistema manualmente.';
    indicator.style.background = '#64748b';
    startBtn.style.display = 'none';
    stopBtn.style.display = 'none';
    box.style.borderColor = '#334155';
  } else if (_systemRunning) {
    title.textContent = 'Sistema ACTIVO';
    title.style.color = '#22c55e';
    desc.textContent = 'El generador escribe al KEPserver y el SE lee tags en tiempo real.';
    indicator.style.background = '#22c55e';
    indicator.style.boxShadow = '0 0 8px rgba(34,197,94,.6)';
    startBtn.style.display = 'none';
    stopBtn.style.display = '';
    box.style.borderColor = '#22c55e';
  } else {
    title.textContent = 'Sistema detenido';
    title.style.color = '#f59e0b';
    desc.textContent = 'Modo Tags Reales seleccionado. Presiona "Iniciar Sistema" para arrancar el generador y la lectura de tags.';
    indicator.style.background = '#f59e0b';
    indicator.style.boxShadow = 'none';
    startBtn.style.display = '';
    stopBtn.style.display = 'none';
    box.style.borderColor = '#f59e0b';
  }
}

async function startSystem() {
  _setTagsMsg('Iniciando sistema...', '#94a3b8');
  try {
    await saveGeneratorConfig();
    const r1 = await fetch('/api/tags/generator/start', {method: 'POST'});
    const d1 = await r1.json();
    const r2 = await fetch('/api/se/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({intervalo_s: parseFloat(document.getElementById('gen-intervalo').value) || 5})
    });
    const d2 = await r2.json();
    if (d1.ok && d2.ok) {
      _systemRunning = true;
      _updateSystemControlUI();
      _startGenPolling();
      _startSEPolling();
      _setTagsMsg('Sistema iniciado. Generador + SE activos.', '#22c55e');
      setTimeout(() => _setTagsMsg(''), 4000);
    }
  } catch (e) {
    _setTagsMsg('Error iniciando sistema: ' + e.message, '#ef4444');
  }
}

async function stopSystem() {
  try {
    await fetch('/api/se/stop', {method: 'POST'});
    await fetch('/api/tags/generator/stop', {method: 'POST'});
    _systemRunning = false;
    _updateSystemControlUI();
    _stopGenPolling();
    _stopSEPolling();
    _setTagsMsg('Sistema detenido.', '#f59e0b');
    setTimeout(() => _setTagsMsg(''), 3000);
  } catch (e) {
    _setTagsMsg('Error: ' + e.message, '#ef4444');
  }
}

let _sePolling = null;

function _startSEPolling() {
  _stopSEPolling();
  _sePolling = setInterval(_fetchSEStatus, 3000);
  _fetchSEStatus();
}

function _stopSEPolling() {
  if (_sePolling) { clearInterval(_sePolling); _sePolling = null; }
}

async function _fetchSEStatus() {
  try {
    const r = await fetch('/api/se/status');
    const d = await r.json();
    _renderSEStatus(d);
  } catch(e) { /* ignore */ }
}

function _renderSEStatus(status) {
  const el = document.getElementById('se-live-panel');
  if (!el) return;
  if (!status.running) {
    el.style.display = 'none';
    return;
  }
  el.style.display = '';

  const spHtml = Object.entries(status.setpoints || {}).map(([k, v]) =>
    '<div style="text-align:center"><div style="font-size:1.1rem;font-weight:700;color:#38bdf8">' + v + '</div><div style="font-size:.65rem;color:#94a3b8;text-transform:uppercase">' + k + '</div></div>'
  ).join('');
  document.getElementById('se-sp-values').innerHTML = spHtml;
  document.getElementById('se-tick-display').textContent = 'Tick: ' + status.tick + ' | t=' + status.t_s + 's';

  const evEl = document.getElementById('se-events-display');
  if (status.last_events && status.last_events.length > 0) {
    evEl.innerHTML = status.last_events.map(ev =>
      '<div style="font-size:.78rem;padding:3px 0;border-bottom:1px solid #334155">'
      + '<span style="color:#a78bfa;margin-right:8px">' + ev.regla_id + '</span>'
      + '<span style="color:#22c55e;margin-right:8px;font-size:.7rem">' + ev.bloque + '</span>'
      + '<span style="color:#fb923c">' + (ev.acciones||[]).join(', ') + '</span>'
      + ' <span style="color:#64748b">(b=' + ev.belief + ')</span></div>'
    ).join('');
  } else {
    evEl.innerHTML = '<span style="color:#64748b;font-size:.78rem">Sin eventos en este tick</span>';
  }

  if (status.last_error) {
    document.getElementById('se-error-display').textContent = status.last_error;
    document.getElementById('se-error-display').style.display = '';
  } else {
    document.getElementById('se-error-display').style.display = 'none';
  }
}

function _getStatusInfo(tag) {
  if (!tag.enabled) return {cls: 'badge-suspended', label: 'Suspendido', dotCls: 'dot-suspended'};
  if (tag.connected && tag.exists) return {cls: 'badge-connected', label: 'Conectado', dotCls: 'dot-connected'};
  if (tag.connected && !tag.exists) return {cls: 'badge-not-found', label: 'No existe', dotCls: 'dot-not-found'};
  if (!tag.connected && tag.quality === 'Unknown') return {cls: 'badge-unknown', label: 'Sin info', dotCls: 'dot-unknown'};
  return {cls: 'badge-disconnected', label: 'Desconectado', dotCls: 'dot-disconnected'};
}

function _getTagGroup(name) {
  if (name.startsWith('RETO.PV.')) return {key: 'pv', label: 'Variables de Proceso (PV)', color: '#a855f7'};
  if (name.startsWith('RETO.CRUDA.')) return {key: 'cruda', label: 'Sensores Crudos', color: '#0891b2'};
  if (name.startsWith('RETO.LIM.')) return {key: 'lim', label: 'Limites Fuzzy (lmin / lmax)', color: '#6366f1'};
  if (name.startsWith('RETO.SP.')) return {key: 'sp', label: 'Setpoints', color: '#22c55e'};
  if (name.startsWith('RETO.IN.')) return {key: 'in', label: 'Entradas Originales (legacy)', color: '#f59e0b'};
  return {key: 'other', label: 'Otros Tags', color: '#64748b'};
}

function _renderTags() {
  const body = document.getElementById('tags-body');
  if (!body) return;
  if (_tagsData.length === 0) {
    body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#64748b;padding:24px">No hay tags configurados. Agrega uno usando el formulario superior.</td></tr>';
    return;
  }

  // Group tags
  const groupOrder = ['pv', 'cruda', 'lim', 'sp', 'in', 'other'];
  const groups = {};
  for (const tag of _tagsData) {
    const g = _getTagGroup(tag.name);
    if (!groups[g.key]) groups[g.key] = {info: g, tags: []};
    groups[g.key].tags.push(tag);
  }

  body.innerHTML = '';
  for (const gKey of groupOrder) {
    const group = groups[gKey];
    if (!group || group.tags.length === 0) continue;
    // Group header row
    const headerTr = document.createElement('tr');
    headerTr.innerHTML = '<td colspan="7"><div class="tag-group-header"><span class="group-dot" style="background:' + group.info.color + '"></span>' + group.info.label + ' (' + group.tags.length + ')</div></td>';
    body.appendChild(headerTr);

    for (const tag of group.tags) {
      const st = _getStatusInfo(tag);
      const tr = document.createElement('tr');
      if (!tag.enabled) tr.className = 'tag-row-suspended';

      const valDisplay = tag.enabled
        ? (tag.value !== null && tag.value !== undefined
            ? '<span class="tag-value-display">' + tag.value + '</span>'
            : '<span class="tag-value-na">N/A</span>')
        : '<span class="tag-value-na">--</span>';

      const qualityDisplay = tag.enabled ? (tag.quality || '--') : '--';

      const toggleBtn = tag.enabled
        ? '<button class="btn-sm" style="background:#f59e0b;color:#1e293b" onclick="toggleTag(' + tag.id + ',false)" title="Suspender">Suspender</button>'
        : '<button class="btn-sm btn-success" onclick="toggleTag(' + tag.id + ',true)" title="Habilitar">Habilitar</button>';

      tr.innerHTML =
        '<td style="color:#64748b">' + tag.id + '</td>'
        + '<td><span class="tag tag-var" style="font-size:.82rem">' + tag.name + '</span></td>'
        + '<td><span class="tag" style="font-size:.78rem">' + tag.data_type + '</span></td>'
        + '<td>' + valDisplay + '</td>'
        + '<td><span class="tag-status-badge ' + st.cls + '"><span class="badge-dot ' + st.dotCls + '"></span>' + st.label + '</span></td>'
        + '<td style="font-size:.78rem;color:#94a3b8">' + qualityDisplay + '</td>'
        + '<td class="tags-actions">'
        + toggleBtn
        + ' <button class="btn-sm btn-danger" onclick="deleteTag(' + tag.id + ',\'' + tag.name.replace(/'/g, "\\'") + '\')">Eliminar</button>'
        + '</td>';
      body.appendChild(tr);
    }
  }
}

async function loadTags() {
  try {
    const r = await fetch('/api/tags');
    const d = await r.json();
    _tagsData = d.tags || [];
    _updateSimToggleUI(d.simulation_mode !== undefined ? d.simulation_mode : true);
    _renderTags();
    loadGenerator().then(() => {
      _updateSystemControlUI();
    });
  } catch (e) {
    _setTagsMsg('Error cargando tags: ' + e.message, '#ef4444');
  }
}

async function refreshTags() {
  _setTagsMsg('Actualizando...', '#94a3b8');
  try {
    const r = await fetch('/api/tags/refresh', {method: 'POST'});
    const d = await r.json();
    _tagsData = d.tags || [];
    _renderTags();
    _setTagsMsg('Valores actualizados.', '#22c55e');
    setTimeout(() => _setTagsMsg(''), 3000);
  } catch (e) {
    _setTagsMsg('Error: ' + e.message, '#ef4444');
  }
}

async function addTag() {
  const nameEl = document.getElementById('tag-new-name');
  const typeEl = document.getElementById('tag-new-type');
  const name = (nameEl.value || '').trim();
  if (!name) {
    _setTagsMsg('Ingresa un nombre para el tag.', '#f59e0b');
    nameEl.focus();
    return;
  }
  try {
    const r = await fetch('/api/tags', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, data_type: typeEl.value})
    });
    const d = await r.json();
    if (!r.ok) {
      _setTagsMsg(d.error || 'Error al crear tag.', '#ef4444');
      return;
    }
    nameEl.value = '';
    await loadTags();
    _setTagsMsg('Tag "' + name + '" agregado.', '#22c55e');
    setTimeout(() => _setTagsMsg(''), 3000);
  } catch (e) {
    _setTagsMsg('Error: ' + e.message, '#ef4444');
  }
}

async function deleteTag(id, name) {
  if (!confirm('Eliminar el tag "' + name + '"?')) return;
  try {
    const r = await fetch('/api/tags/' + id, {method: 'DELETE'});
    const d = await r.json();
    if (!r.ok) {
      _setTagsMsg(d.error || 'Error al eliminar.', '#ef4444');
      return;
    }
    await loadTags();
    _setTagsMsg('Tag "' + name + '" eliminado.', '#22c55e');
    setTimeout(() => _setTagsMsg(''), 3000);
  } catch (e) {
    _setTagsMsg('Error: ' + e.message, '#ef4444');
  }
}

async function toggleTag(id, enable) {
  try {
    const r = await fetch('/api/tags/' + id, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled: enable})
    });
    const d = await r.json();
    if (!r.ok) {
      _setTagsMsg(d.error || 'Error al actualizar.', '#ef4444');
      return;
    }
    await loadTags();
    _setTagsMsg(enable ? 'Tag habilitado.' : 'Tag suspendido.', '#22c55e');
    setTimeout(() => _setTagsMsg(''), 3000);
  } catch (e) {
    _setTagsMsg('Error: ' + e.message, '#ef4444');
  }
}

// ============================================================
// Generador de datos KEPserver
// ============================================================

let _genPolling = null;

function _renderGenRanges(status) {
  const body = document.getElementById('gen-ranges-body');
  if (!body) return;
  const ranges = status.ranges || {};
  const lastVals = status.last_values || {};
  body.innerHTML = '';

  const sorted = Object.keys(ranges).sort();
  for (const tag of sorted) {
    const cfg = ranges[tag];
    const val = lastVals[tag];
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td><span class="tag tag-var" style="font-size:.78rem">' + tag + '</span></td>'
      + '<td><input data-gen-tag="' + tag + '" data-gen-k="min" type="number" step="0.1" value="' + cfg.min + '" style="width:80px"></td>'
      + '<td><input data-gen-tag="' + tag + '" data-gen-k="max" type="number" step="0.1" value="' + cfg.max + '" style="width:80px"></td>'
      + '<td><input data-gen-tag="' + tag + '" data-gen-k="noise" type="number" step="0.1" value="' + (cfg.noise || 0) + '" style="width:70px"></td>'
      + '<td class="tag-value-display">' + (val !== undefined && val !== null ? val.toFixed(2) : '--') + '</td>';
    body.appendChild(tr);
  }
}

function _updateGenUI(status) {
  const badge = document.getElementById('gen-status-badge');
  const startBtn = document.getElementById('gen-start-btn');
  const stopBtn = document.getElementById('gen-stop-btn');
  const tickEl = document.getElementById('gen-tick');
  const errEl = document.getElementById('gen-error');
  const intervaloEl = document.getElementById('gen-intervalo');
  const cicloEl = document.getElementById('gen-n-ciclo');

  if (status.running) {
    badge.textContent = 'ACTIVO (' + status.tick + '/' + status.n_ciclo + ')';
    badge.style.background = 'rgba(34,197,94,.2)';
    badge.style.color = '#22c55e';
    startBtn.style.display = 'none';
    stopBtn.style.display = '';
  } else {
    badge.textContent = 'DETENIDO';
    badge.style.background = 'rgba(100,116,139,.2)';
    badge.style.color = '#94a3b8';
    startBtn.style.display = '';
    stopBtn.style.display = 'none';
  }
  if (tickEl) tickEl.textContent = status.tick || 0;
  if (errEl) errEl.textContent = status.last_error || '';
  if (intervaloEl && !status.running) intervaloEl.value = status.intervalo_s;
  if (cicloEl && !status.running) cicloEl.value = status.n_ciclo;

  _renderGenRanges(status);
}

async function loadGenerator() {
  try {
    const r = await fetch('/api/tags/generator');
    const d = await r.json();
    _updateGenUI(d);
    _systemRunning = d.running;
  } catch (e) { /* ignore */ }
}

async function saveGeneratorConfig() {
  const intervalo = parseFloat(document.getElementById('gen-intervalo').value) || 5;
  const nCiclo = parseInt(document.getElementById('gen-n-ciclo').value) || 60;

  const ranges = {};
  for (const inp of document.querySelectorAll('[data-gen-tag]')) {
    const tag = inp.dataset.genTag;
    const k = inp.dataset.genK;
    if (!ranges[tag]) ranges[tag] = {};
    ranges[tag][k] = parseFloat(inp.value) || 0;
  }

  try {
    const r = await fetch('/api/tags/generator', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({intervalo_s: intervalo, n_ciclo: nCiclo, ranges: ranges})
    });
    const d = await r.json();
    if (d.ok) {
      _updateGenUI(d);
      _setTagsMsg('Configuracion del generador guardada.', '#22c55e');
      setTimeout(() => _setTagsMsg(''), 3000);
    }
  } catch (e) {
    _setTagsMsg('Error guardando config: ' + e.message, '#ef4444');
  }
}

async function startGenerator() {
  await saveGeneratorConfig();
  try {
    const r = await fetch('/api/tags/generator/start', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      _setTagsMsg('Generador iniciado.', '#22c55e');
      setTimeout(() => _setTagsMsg(''), 3000);
      _startGenPolling();
      loadGenerator();
    }
  } catch (e) {
    _setTagsMsg('Error: ' + e.message, '#ef4444');
  }
}

async function stopGenerator() {
  try {
    const r = await fetch('/api/tags/generator/stop', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      _setTagsMsg('Generador detenido.', '#22c55e');
      setTimeout(() => _setTagsMsg(''), 3000);
      _stopGenPolling();
      loadGenerator();
    }
  } catch (e) {
    _setTagsMsg('Error: ' + e.message, '#ef4444');
  }
}

function _startGenPolling() {
  _stopGenPolling();
  _genPolling = setInterval(async () => {
    try {
      const r = await fetch('/api/tags/generator');
      const d = await r.json();
      _updateGenUI(d);
    } catch (e) { /* ignore */ }
  }, 3000);
}

function _stopGenPolling() {
  if (_genPolling) { clearInterval(_genPolling); _genPolling = null; }
}

_onHash();

// ============================================================
// Alert rail
// ============================================================
const ALERT_CATS = ALERT_CATEGORIES_JSON;
let _alertTimer = null;

function _toggleAlertRail() {
  const rail = document.getElementById('alertRail');
  rail.classList.toggle('expanded');
  if (rail.classList.contains('expanded')) _fetchAlerts();
}

function _closeAlertRail() {
  document.getElementById('alertRail').classList.remove('expanded');
}

function _toggleAlertDetail(cardEl) {
  cardEl.classList.toggle('open');
}

async function _fetchAlerts() {
  try {
    const r = await fetch('/api/alerts');
    const data = await r.json();
    _renderAlertDots(data.alerts);
    _renderAlertPanel(data.alerts);
  } catch(e) {}
}

function _renderAlertDots(alerts) {
  const wrap = document.getElementById('alertDots');
  wrap.innerHTML = '';
  if (alerts.length === 0) {
    wrap.innerHTML = '<div class="rail-ok" title="Sin alertas"></div>';
    return;
  }
  alerts.forEach(a => {
    const cat = ALERT_CATS[a.category] || ALERT_CATS.general;
    const dot = document.createElement('div');
    dot.className = 'rail-dot';
    dot.style.background = cat.color;
    dot.title = a.message;
    wrap.appendChild(dot);
  });
}

function _renderAlertPanel(alerts) {
  const panel = document.getElementById('alertPanelContent');
  const badge = document.getElementById('alertBadge');
  badge.textContent = alerts.length;
  badge.className = 'alert-count-badge' + (alerts.length === 0 ? ' ok' : '');

  if (alerts.length === 0) {
    panel.innerHTML = '<div class="alert-ok-msg">Sin alertas activas.<br>El sistema esta funcionando correctamente.</div>';
    return;
  }

  panel.innerHTML = '';
  alerts.forEach(a => {
    const cat = ALERT_CATS[a.category] || ALERT_CATS.general;
    const ago = Math.round((Date.now()/1000 - a.last_seen));
    const agoStr = ago < 60 ? ago+'s' : Math.round(ago/60)+'m';
    const card = document.createElement('div');
    card.className = 'alert-card';
    card.style.borderLeftColor = cat.color;
    card.onclick = (e) => { if(!e.target.classList.contains('resolve-btn')) _toggleAlertDetail(card); };
    card.innerHTML = `
      <div class="alert-cat" style="color:${cat.color}">${cat.icon} ${cat.label}</div>
      <div class="alert-msg">${a.message}</div>
      <div class="alert-meta"><span>hace ${agoStr}${a.count>1 ? ' (x'+a.count+')' : ''}</span></div>
      ${a.detail ? '<div class="alert-detail">'+a.detail.replace(/</g,'&lt;')+'</div>' : ''}
      <button class="resolve-btn" onclick="event.stopPropagation();_resolveAlert(${a.id})">Marcar resuelto</button>
    `;
    panel.appendChild(card);
  });
}

async function _resolveAlert(id) {
  await fetch('/api/alerts/'+id+'/resolve', {method:'POST'});
  _fetchAlerts();
}

_fetchAlerts();
_alertTimer = setInterval(_fetchAlerts, 10000);

</script>
</main>

<!-- Alert rail (right sidebar) -->
<div class="alert-rail" id="alertRail" onclick="if(!this.classList.contains('expanded'))_toggleAlertRail()">
  <div class="rail-dots" id="alertDots"></div>
  <div class="alert-panel" onclick="event.stopPropagation()">
    <div class="alert-panel-header">
      <h4>Alertas <span class="alert-count-badge" id="alertBadge">0</span></h4>
      <button class="close-btn" onclick="event.stopPropagation();_closeAlertRail()">&times;</button>
    </div>
    <div id="alertPanelContent"></div>
  </div>
</div>

</body>
</html>"""


@app.route("/")
def index():
    page = HTML_PAGE
    page = page.replace("VARIABLES_JSON", json.dumps(VARIABLES_DISPONIBLES))
    page = page.replace("LABELS_JSON", json.dumps(ETIQUETAS_DISPONIBLES))
    page = page.replace("ACTIONS_JSON", json.dumps(ACCIONES_DISPONIBLES))
    page = page.replace("BLOCKS_JSON", json.dumps(BLOQUES_DISPONIBLES))
    page = page.replace("SETPOINTS_JSON", json.dumps(SETPOINT_KEYS))
    page = page.replace("ESTADOS_JSON", json.dumps(_load_estados()))
    page = page.replace("WAITS_JSON", json.dumps(_load_waits()))
    page = page.replace("ALERT_CATEGORIES_JSON", json.dumps(AlertCollector.CATEGORIES))
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
.btn-sm{padding:4px 10px;font-size:.75rem}
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

/* Tabs */
.tab-bar{display:flex;gap:0;margin-bottom:20px;border-bottom:2px solid #1e293b}
.tab-btn{padding:10px 20px;font-size:.85rem;font-weight:600;color:#64748b;background:none;border:none;border-bottom:3px solid transparent;cursor:pointer;transition:.15s}
.tab-btn:hover{color:#e2e8f0}
.tab-btn.active{color:#38bdf8;border-bottom-color:#38bdf8}
.tab-panel{display:none}
.tab-panel.active{display:block}

/* Custom chart builder */
.var-picker{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;max-height:200px;overflow-y:auto;padding:12px;background:#1e293b;border-radius:8px;border:1px solid #334155}
.var-chip{padding:4px 10px;border-radius:14px;font-size:.75rem;cursor:pointer;border:1px solid #475569;color:#94a3b8;transition:.15s;user-select:none}
.var-chip:hover{border-color:#38bdf8;color:#e2e8f0}
.var-chip.selected{background:#3b82f6;border-color:#3b82f6;color:#fff}
.custom-chart-wrap{background:#1e293b;border-radius:10px;padding:16px;min-height:300px}
.custom-chart-wrap canvas{width:100%!important;height:350px!important}
</style>
</head>
<body>
<nav>
  <a href="/">Configuracion SE</a>
  <a href="/entrada">Entrada de Datos</a>
  <a href="/graficos" class="active">Graficos en Vivo</a>
  <a href="/diagrama">Diagrama de Flujo</a>
</nav>
<h1>Espesador -- Graficos en Tiempo Real</h1>
<h2>Monitoreo en vivo de variables de proceso, tags KEPserver y simulacion del sistema experto.</h2>

  <a href="/#licenciamiento">Licenciamiento</a>
<!-- Tab bar -->
<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('sim')">Simulacion SE</button>
  <button class="tab-btn" onclick="switchTab('tags')">Tags en Vivo</button>
  <button class="tab-btn" onclick="switchTab('custom')">Grafico Personalizado</button>
</div>

<!-- TAB 1: Simulacion (original) -->
<div class="tab-panel active" id="tab-sim">
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
</div>

<!-- TAB 2: Tags en Vivo (KEPserver real-time) -->
<div class="tab-panel" id="tab-tags">
<div class="controls">
  <button class="btn-success" id="btn-tags-start" onclick="startTagsPolling()">&#9654; Iniciar Monitoreo</button>
  <button class="btn-danger" id="btn-tags-stop" onclick="stopTagsPolling()" disabled>&#9632; Detener</button>
  <label>Intervalo:
    <select id="sel-tags-interval" onchange="changeTagsInterval()">
      <option value="1000">1s</option>
      <option value="2000" selected>2s</option>
      <option value="5000">5s</option>
      <option value="10000">10s</option>
    </select>
  </label>
  <span id="tags-live-status" style="font-size:.8rem;color:#94a3b8">Detenido</span>
</div>
<div class="charts-grid" id="tags-charts-grid"></div>
<div style="margin-top:8px;font-size:.78rem;color:#64748b">
  Muestra los tags RETO.PV.* del KEPserver en tiempo real. Si el generador esta activo, veras los valores moverse.
</div>
</div>

<!-- TAB 3: Grafico Personalizado -->
<div class="tab-panel" id="tab-custom">
<p style="font-size:.85rem;color:#94a3b8;margin-bottom:12px">Selecciona los tags o variables que quieres graficar juntos. Haz clic para agregar/quitar.</p>
<div class="controls">
  <button class="btn-success" id="btn-custom-start" onclick="startCustomPolling()">&#9654; Iniciar</button>
  <button class="btn-danger" id="btn-custom-stop" onclick="stopCustomPolling()" disabled>&#9632; Detener</button>
  <label>Intervalo:
    <select id="sel-custom-interval">
      <option value="1000">1s</option>
      <option value="2000" selected>2s</option>
      <option value="5000">5s</option>
    </select>
  </label>
  <button class="btn-primary btn-sm" onclick="clearCustomChart()">Limpiar grafico</button>
</div>
<div class="var-picker" id="var-picker"></div>
<div class="custom-chart-wrap">
  <canvas id="custom-chart"></canvas>
</div>
</div>

<script>
const CHART_VARS = CHART_VARS_JSON;
const SP_KEYS    = SP_KEYS_JSON;
const MAX_PTS    = 300;
let timer = null;
let totalEventos = 0;
const charts = {};
let spChart = null;

// ============================================================
// Tab switching
// ============================================================
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
  if (name === 'tags' && !_tagsChartsBuilt) buildTagsCharts();
  if (name === 'custom' && !_customChartBuilt) buildCustomChart();
}

// ============================================================
// TAB 1: Simulacion (original logic preserved)
// ============================================================
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

// ============================================================
// TAB 2: Tags en Vivo (KEPserver polling)
// ============================================================
let _tagsChartsBuilt = false;
let _tagsTimer = null;
const _tagsCharts = {};
const TAGS_PV = [
  {key:'RETO.PV.torque',             label:'Torque (%)',           color:'#38bdf8'},
  {key:'RETO.PV.bed_mass',           label:'Bed Mass',            color:'#a78bfa'},
  {key:'RETO.PV.bed_level',          label:'Bed Level (m)',       color:'#22c55e'},
  {key:'RETO.PV.densidad',           label:'Densidad (%)',        color:'#fb923c'},
  {key:'RETO.PV.torque_bomba',       label:'Torque Bomba (%)',    color:'#f472b6'},
  {key:'RETO.PV.potencia_bomba',     label:'Potencia Bomba (kW)', color:'#facc15'},
  {key:'RETO.PV.presion_descarga',   label:'Presion Descarga',    color:'#34d399'},
  {key:'RETO.PV.presion_diferencial',label:'Presion Diferencial', color:'#f87171'},
  {key:'RETO.PV.nivel_rastra',       label:'Nivel Rastra (%)',    color:'#c084fc'},
];

function buildTagsCharts() {
  const grid = document.getElementById('tags-charts-grid');
  grid.innerHTML = '';
  for (const tv of TAGS_PV) {
    const card = document.createElement('div');
    card.className = 'chart-card';
    card.innerHTML = `<h3>${tv.label}</h3><canvas id="tch-${tv.key.replace(/\./g,'_')}"></canvas>`;
    grid.appendChild(card);
    _tagsCharts[tv.key] = new Chart(document.getElementById('tch-' + tv.key.replace(/\./g,'_')), {
      type:'line',
      data:{labels:[], datasets:[{label:tv.label, data:[], borderColor:tv.color,
            backgroundColor:tv.color+'22', borderWidth:2, pointRadius:1, fill:true, tension:.3}]},
      options:{animation:false, responsive:true, maintainAspectRatio:false,
        scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8},grid:{color:'#1e293b'}},
                y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
        plugins:{legend:{display:false}}}
    });
  }
  _tagsChartsBuilt = true;
}

async function _fetchTagsLive() {
  try {
    const r = await fetch('/api/tags');
    const d = await r.json();
    const now = new Date().toLocaleTimeString('es',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    for (const tv of TAGS_PV) {
      const tag = d.tags.find(t => t.name === tv.key);
      const ch = _tagsCharts[tv.key];
      if (!ch) continue;
      const val = tag && tag.value !== null ? tag.value : null;
      ch.data.labels.push(now);
      ch.data.datasets[0].data.push(val);
      if (ch.data.labels.length > MAX_PTS) {
        ch.data.labels.shift();
        ch.data.datasets[0].data.shift();
      }
      ch.update();
    }
  } catch(e) { console.error(e); }
}

function startTagsPolling() {
  if (_tagsTimer) return;
  const interval = parseInt(document.getElementById('sel-tags-interval').value) || 2000;
  _tagsTimer = setInterval(_fetchTagsLive, interval);
  _fetchTagsLive();
  document.getElementById('btn-tags-start').disabled = true;
  document.getElementById('btn-tags-stop').disabled = false;
  document.getElementById('tags-live-status').textContent = 'Monitoreando...';
  document.getElementById('tags-live-status').style.color = '#22c55e';
}

function stopTagsPolling() {
  if (_tagsTimer) { clearInterval(_tagsTimer); _tagsTimer = null; }
  document.getElementById('btn-tags-start').disabled = false;
  document.getElementById('btn-tags-stop').disabled = true;
  document.getElementById('tags-live-status').textContent = 'Detenido';
  document.getElementById('tags-live-status').style.color = '#94a3b8';
}

function changeTagsInterval() {
  if (!_tagsTimer) return;
  stopTagsPolling();
  startTagsPolling();
}

// ============================================================
// TAB 3: Grafico Personalizado
// ============================================================
let _customChartBuilt = false;
let _customChart = null;
let _customTimer = null;
let _selectedVars = [];
const PALETTE = ['#38bdf8','#a78bfa','#22c55e','#fb923c','#f472b6','#facc15','#34d399','#f87171','#c084fc','#67e8f9','#fca5a5','#a3e635','#e879f9','#fcd34d','#6ee7b7'];

const ALL_AVAILABLE_TAGS = [
  'RETO.PV.torque','RETO.PV.bed_mass','RETO.PV.bed_level','RETO.PV.densidad',
  'RETO.PV.torque_bomba','RETO.PV.potencia_bomba','RETO.PV.presion_descarga',
  'RETO.PV.presion_diferencial','RETO.PV.nivel_rastra',
  'RETO.CRUDA.tonelaje_sag_1','RETO.CRUDA.tonelaje_sag_2','RETO.CRUDA.tonelaje_relave',
  'RETO.CRUDA.presion_bomba_1','RETO.CRUDA.presion_bomba_2','RETO.CRUDA.turbiedad_agua',
  'RETO.SP.sp_tonelaje','RETO.SP.sp_floculante','RETO.SP.sp_vel_bomba',
  'RETO.IN.Potencia_SAG','RETO.IN.Potencia_Bolas','RETO.IN.Nivel_Molino'
];

function buildCustomChart() {
  const picker = document.getElementById('var-picker');
  picker.innerHTML = '';
  for (const tag of ALL_AVAILABLE_TAGS) {
    const chip = document.createElement('span');
    chip.className = 'var-chip';
    chip.textContent = tag.replace('RETO.','');
    chip.dataset.tag = tag;
    chip.onclick = function() { toggleVarSelection(this); };
    picker.appendChild(chip);
  }

  _customChart = new Chart(document.getElementById('custom-chart'), {
    type:'line',
    data:{labels:[], datasets:[]},
    options:{animation:false, responsive:true, maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:10},grid:{color:'#1e293b'}},
              y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
      plugins:{legend:{display:true,labels:{color:'#94a3b8',font:{size:10}}}}}
  });
  _customChartBuilt = true;
}

function toggleVarSelection(chip) {
  const tag = chip.dataset.tag;
  const idx = _selectedVars.indexOf(tag);
  if (idx >= 0) {
    _selectedVars.splice(idx, 1);
    chip.classList.remove('selected');
    _rebuildCustomDatasets();
  } else {
    if (_selectedVars.length >= 8) return;
    _selectedVars.push(tag);
    chip.classList.add('selected');
    _rebuildCustomDatasets();
  }
}

function _rebuildCustomDatasets() {
  if (!_customChart) return;
  const existingLabels = _customChart.data.labels;
  _customChart.data.datasets = _selectedVars.map((tag, i) => ({
    label: tag.replace('RETO.',''),
    data: new Array(existingLabels.length).fill(null),
    borderColor: PALETTE[i % PALETTE.length],
    borderWidth: 2, pointRadius: 1, tension: .3
  }));
  _customChart.update();
}

async function _fetchCustomLive() {
  if (_selectedVars.length === 0) return;
  try {
    const r = await fetch('/api/tags');
    const d = await r.json();
    const now = new Date().toLocaleTimeString('es',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    _customChart.data.labels.push(now);
    if (_customChart.data.labels.length > MAX_PTS) _customChart.data.labels.shift();

    for (let i = 0; i < _selectedVars.length; i++) {
      const tag = d.tags.find(t => t.name === _selectedVars[i]);
      const val = tag && tag.value !== null ? tag.value : null;
      _customChart.data.datasets[i].data.push(val);
      if (_customChart.data.datasets[i].data.length > MAX_PTS)
        _customChart.data.datasets[i].data.shift();
    }
    _customChart.update();
  } catch(e) { console.error(e); }
}

function startCustomPolling() {
  if (_customTimer) return;
  if (_selectedVars.length === 0) { alert('Selecciona al menos un tag para graficar.'); return; }
  const interval = parseInt(document.getElementById('sel-custom-interval').value) || 2000;
  _customTimer = setInterval(_fetchCustomLive, interval);
  _fetchCustomLive();
  document.getElementById('btn-custom-start').disabled = true;
  document.getElementById('btn-custom-stop').disabled = false;
}

function stopCustomPolling() {
  if (_customTimer) { clearInterval(_customTimer); _customTimer = null; }
  document.getElementById('btn-custom-start').disabled = false;
  document.getElementById('btn-custom-stop').disabled = true;
}

function clearCustomChart() {
  if (!_customChart) return;
  _customChart.data.labels = [];
  _customChart.data.datasets.forEach(ds => ds.data = []);
  _customChart.update();
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
# UI -- Diagrama de flujo del sistema experto
# ============================================================

DIAGRAM_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espesador -- Diagrama de Flujo del Sistema Experto</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
nav{background:#0b1322;border-bottom:1px solid #1e293b;padding:12px 24px;display:flex;gap:16px;align-items:center}
nav a{color:#94a3b8;text-decoration:none;font-size:.85rem;padding:6px 12px;border-radius:6px;transition:.15s}
nav a:hover{background:#1e293b;color:#e2e8f0}
nav a.active{background:#1e293b;color:#38bdf8;font-weight:600}
.container{max-width:1400px;margin:0 auto;padding:24px}
h1{color:#38bdf8;margin-bottom:6px;font-size:1.5rem}
.subtitle{color:#94a3b8;font-size:.95rem;margin-bottom:28px}

.flow-wrapper{position:relative;overflow-x:auto;padding:20px 0}

/* Pipeline vertical */
.pipeline{display:flex;flex-direction:column;align-items:center;gap:0;min-width:700px}

/* Nodo principal */
.node{position:relative;border-radius:12px;padding:18px 24px;min-width:520px;max-width:700px;text-align:left;cursor:pointer;transition:all .2s;border:2px solid transparent}
.node:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.4)}
.node.expanded .node-details{display:block}
.node-header{display:flex;align-items:center;gap:12px}
.node-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.2rem;flex-shrink:0;font-weight:700}
.node-title{font-weight:700;font-size:1rem}
.node-desc{font-size:.82rem;color:#94a3b8;margin-top:2px}
.node-details{display:none;margin-top:14px;padding-top:14px;border-top:1px solid rgba(255,255,255,.1);font-size:.82rem;line-height:1.7;color:#cbd5e1}
.node-details code{background:#0f172a;padding:2px 6px;border-radius:4px;font-size:.78rem;color:#38bdf8}
.node-details .detail-grid{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin-top:8px}
.node-details .detail-label{color:#64748b;font-weight:600;text-transform:uppercase;font-size:.7rem;letter-spacing:.03em}
.node-badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:.7rem;font-weight:600;margin:2px}

/* Flecha conector */
.arrow{display:flex;flex-direction:column;align-items:center;padding:0;height:40px;position:relative}
.arrow-line{width:3px;flex:1;background:linear-gradient(180deg,var(--arrow-from,#334155),var(--arrow-to,#334155))}
.arrow-head{width:0;height:0;border-left:8px solid transparent;border-right:8px solid transparent;border-top:10px solid var(--arrow-to,#334155)}
.arrow-label{position:absolute;right:calc(50% + 20px);top:50%;transform:translateY(-50%);font-size:.7rem;color:#64748b;white-space:nowrap;background:#0f172a;padding:2px 6px;border-radius:4px}
.arrow-label-left{left:calc(50% + 20px);right:auto}

/* Colores por etapa */
.stage-input{background:linear-gradient(135deg,#1e3a5f,#1a2744);border-color:#2563eb}
.stage-input .node-icon{background:#1d4ed8;color:#fff}

.stage-calc{background:linear-gradient(135deg,#1e3348,#1a2744);border-color:#0891b2}
.stage-calc .node-icon{background:#0e7490;color:#fff}

.stage-filter{background:linear-gradient(135deg,#1e2e44,#1a2744);border-color:#6366f1}
.stage-filter .node-icon{background:#4f46e5;color:#fff}

.stage-fuzzy{background:linear-gradient(135deg,#2d1f48,#1a2744);border-color:#a855f7}
.stage-fuzzy .node-icon{background:#7c3aed;color:#fff}

.stage-perm{background:linear-gradient(135deg,#1e3340,#1a2744);border-color:#14b8a6}
.stage-perm .node-icon{background:#0d9488;color:#fff}

.stage-motor{background:linear-gradient(135deg,#3a2a1a,#2a2030);border-color:#f59e0b}
.stage-motor .node-icon{background:#d97706;color:#fff}

.stage-defuzzy{background:linear-gradient(135deg,#1a3329,#1a2744);border-color:#22c55e}
.stage-defuzzy .node-icon{background:#16a34a;color:#fff}

.stage-output{background:linear-gradient(135deg,#3a1a1a,#2a1a30);border-color:#ef4444}
.stage-output .node-icon{background:#dc2626;color:#fff}

.stage-loop{background:linear-gradient(135deg,#2a2a1a,#2a2030);border-color:#facc15}
.stage-loop .node-icon{background:#ca8a04;color:#fff}

/* Branch fork */
.branch{display:flex;gap:20px;justify-content:center;align-items:flex-start;min-width:700px;flex-wrap:wrap}
.branch .node{min-width:220px;max-width:320px;flex:1}

/* Iteración visual */
.loop-indicator{display:flex;align-items:center;gap:10px;padding:10px 20px;background:#1e293b;border:2px dashed #facc15;border-radius:10px;color:#facc15;font-size:.85rem;font-weight:600;margin-top:8px}
.loop-indicator .icon{font-size:1.3rem}

/* Legend */
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:30px;padding:16px 20px;background:#1e293b;border-radius:10px;border:1px solid #334155}
.legend-title{width:100%;font-size:.8rem;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:.05em;margin-bottom:4px}
.legend-item{display:flex;align-items:center;gap:6px;font-size:.78rem;color:#cbd5e1}
.legend-dot{width:14px;height:14px;border-radius:4px;flex-shrink:0}

/* Mode tag */
.mode-tag{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:12px;font-size:.7rem;font-weight:600}
.mode-tag.sim{background:rgba(168,85,247,.12);color:#a855f7;border:1px solid rgba(168,85,247,.3)}
.mode-tag.kep{background:rgba(34,197,94,.12);color:#22c55e;border:1px solid rgba(34,197,94,.3)}
.mode-sep{color:#475569;font-size:.7rem;margin:0 2px}

/* Click hint */
.click-hint{text-align:center;color:#475569;font-size:.78rem;margin-bottom:16px;font-style:italic}
</style>
</head>
<body>
<nav>
  <a href="/">Configuracion SE</a>
  <a href="/entrada">Entrada de Datos</a>
  <a href="/graficos">Graficos en Vivo</a>
  <a href="/diagrama" class="active">Diagrama de Flujo</a>
</nav>
<div class="container">
<h1>Diagrama de Flujo del Sistema Experto</h1>
<p class="subtitle">Pipeline completo de procesamiento por cada tick temporal. Haz clic en cada etapa para ver detalles tecnicos.</p>
  <a href="/#licenciamiento">Licenciamiento</a>
<p class="click-hint">Haz clic en cualquier nodo para expandir/contraer los detalles</p>

<div class="flow-wrapper">
<div class="pipeline">

<!-- STAGE 1: Data input -->
<div class="node stage-input" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">1</div>
    <div>
      <div class="node-title">Entrada de Datos</div>
      <div class="node-desc">Variables de proceso, sensores crudos y limites fuzzy</div>
    </div>
    <span class="mode-tag sim">Simulacion</span>
    <span class="mode-sep">/</span>
    <span class="mode-tag kep">Tags KEPserver</span>
  </div>
  <div class="node-details">
    <p>El sistema soporta <b>dos modos de entrada</b>:</p>
    <p style="margin:6px 0"><span class="mode-tag sim">Simulacion ON</span> Se generan datos sinteticos con <code>simulacion.py</code> en 3 fases (estable, alerta, recuperacion) y el generador de datos (<code>TagGenerator</code>) escribe valores dinamicos en KEPserver.</p>
    <p style="margin:6px 0"><span class="mode-tag kep">Simulacion OFF</span> Se leen directamente los tags reales del KEPserver via OPC-UA. Los tags deben tener valores reales proporcionados por el proceso o un sistema externo.</p>
    <div class="detail-grid">
      <span class="detail-label">Variables PV (9)</span>
      <span><code>RETO.PV.torque</code>, <code>RETO.PV.bed_mass</code>, <code>RETO.PV.bed_level</code>, <code>RETO.PV.densidad</code>, <code>RETO.PV.torque_bomba</code>, <code>RETO.PV.potencia_bomba</code>, <code>RETO.PV.presion_descarga</code>, <code>RETO.PV.presion_diferencial</code>, <code>RETO.PV.nivel_rastra</code></span>
      <span class="detail-label">Sensores crudos (6)</span>
      <span><code>RETO.CRUDA.tonelaje_sag_1/2</code>, <code>RETO.CRUDA.tonelaje_relave</code>, <code>RETO.CRUDA.presion_bomba_1/2</code>, <code>RETO.CRUDA.turbiedad_agua</code></span>
      <span class="detail-label">Limites fuzzy (18)</span>
      <span><code>RETO.LIM.{var}_lmin</code>, <code>RETO.LIM.{var}_lmax</code> por cada PV</span>
      <span class="detail-label">Protocolo</span>
      <span>OPC-UA (<code>opc.tcp://127.0.0.1:49320</code>) — lectura batch en una sola conexion</span>
    </div>
  </div>
</div>

<div class="arrow" style="--arrow-from:#2563eb;--arrow-to:#0891b2">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">DataFrame completo</div>
</div>

<!-- STAGE 2: Calculated variables -->
<div class="node stage-calc" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">2</div>
    <div>
      <div class="node-title">Variables Calculadas</div>
      <div class="node-desc">Derivacion de variables a partir de sensores crudos</div>
    </div>
  </div>
  <div class="node-details">
    <p>Se calculan 5 variables derivadas a partir de los sensores crudos usando <code>calcular_variables_df()</code>.
       Estas no pasan por fuzzificacion directa, pero los permisivos las consultan.</p>
    <div class="detail-grid">
      <span class="detail-label">tonelaje_sag_total</span>
      <span><code>tonelaje_sag_1 + tonelaje_sag_2</code></span>
      <span class="detail-label">tonelaje_sag_delta_30min</span>
      <span>Delta rolling en ventana de 30 min</span>
      <span class="detail-label">tonelaje_sag_desv_est_30min</span>
      <span>Desviacion estandar rolling 30 min</span>
      <span class="detail-label">diferencial_ton_sag_relave</span>
      <span><code>tonelaje_sag_total - tonelaje_relave</code></span>
      <span class="detail-label">diferencial_presion_bbas</span>
      <span><code>presion_bomba_1 - presion_bomba_2</code></span>
    </div>
    <p style="margin-top:8px;color:#64748b">Funcion: <code>fun_calc_variables.calcular_variables_df(df, dt_s, definiciones)</code></p>
  </div>
</div>

<div class="arrow" style="--arrow-from:#0891b2;--arrow-to:#0891b2">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">DF + columnas derivadas</div>
</div>

<!-- STAGE 3: Extract row -->
<div class="node stage-calc" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">3</div>
    <div>
      <div class="node-title">Extraccion de Inputs</div>
      <div class="node-desc">Se construye un dict {variable: valor} del instante actual</div>
    </div>
  </div>
  <div class="node-details">
    <p>En cada tick, se obtienen las 9 variables de proceso como diccionario <code>{nombre: float}</code>. Este es el <b>dato crudo</b> antes de filtrar.</p>
    <div class="detail-grid">
      <span class="detail-label">Modo Simulacion</span>
      <span><code>extraer_inputs_desde_row()</code> extrae de una fila del DataFrame</span>
      <span class="detail-label">Modo KEPserver</span>
      <span><code>SEEngine._read_tags()</code> lee directamente los tags PV, CRUDA y LIM via OPC-UA</span>
      <span class="detail-label">Salida</span>
      <span><code>inputs_raw = {torque: 45.2, bed_level: 3.1, ...}</code> (9 valores)</span>
    </div>
  </div>
</div>

<div class="arrow" style="--arrow-from:#0891b2;--arrow-to:#6366f1">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">inputs_raw (9 floats)</div>
</div>

<!-- STAGE 4: Filter -->
<div class="node stage-filter" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">4</div>
    <div>
      <div class="node-title">Filtro Exp-Q</div>
      <div class="node-desc">Suavizado exponencial cuadratico para reducir ruido</div>
    </div>
  </div>
  <div class="node-details">
    <p>Cada variable de proceso pasa por un filtro de media ponderada exponencial:
       <code>y = &sum; w<sub>i</sub> &middot; x<sub>k-i</sub> / &sum; w<sub>i</sub></code> donde
       <code>w<sub>i</sub> = exp(-q &middot; i&sup2;)</code>.</p>
    <div class="detail-grid">
      <span class="detail-label">Parametros</span>
      <span><code>q</code> (peso decaimiento) y <code>window_size</code> (muestras) por variable</span>
      <span class="detail-label">Ejemplo</span>
      <span>torque: q=0.15, ws=10 | bed_level: q=0.20, ws=8</span>
      <span class="detail-label">Efecto</span>
      <span>Reduce ruido manteniendo tendencia; q alto = mas filtrado</span>
      <span class="detail-label">Estado</span>
      <span>Mantiene buffer <code>deque</code> entre ticks (memoria temporal)</span>
    </div>
    <p style="margin-top:8px;color:#64748b">Clase: <code>ExpQFilter.actualizar(inputs_raw) -> inputs</code></p>
  </div>
</div>

<div class="arrow" style="--arrow-from:#6366f1;--arrow-to:#a855f7">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">inputs (9 floats filtrados)</div>
</div>

<!-- STAGE 5: Fuzzification -->
<div class="node stage-fuzzy" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">5</div>
    <div>
      <div class="node-title">Fuzzificacion</div>
      <div class="node-desc">Conversion de valores numericos a etiquetas linguisticas con grado de pertenencia</div>
    </div>
  </div>
  <div class="node-details">
    <p>Cada variable se evalua contra sus funciones de membresia (modelos tipo <code>high</code>, <code>low</code>, <code>norm</code>)
       para obtener grados de pertenencia &mu; a etiquetas linguisticas.</p>
    <div class="detail-grid">
      <span class="detail-label">Etiquetas base</span>
      <span><span class="node-badge" style="background:#22c55e30;color:#22c55e">LOW</span>
            <span class="node-badge" style="background:#3b82f630;color:#3b82f6">OK</span>
            <span class="node-badge" style="background:#ef444430;color:#ef4444">HIGH</span></span>
      <span class="detail-label">Pendientes</span>
      <span><span class="node-badge" style="background:#ef444430;color:#ef4444">DEC</span>
            <span class="node-badge" style="background:#64748b30;color:#94a3b8">STABLE</span>
            <span class="node-badge" style="background:#22c55e30;color:#22c55e">INC</span>
            (regresion lineal en ventana de 60s)</span>
      <span class="detail-label">Compuestas</span>
      <span><span class="node-badge" style="background:#f59e0b30;color:#f59e0b">NO-HIGH</span>
            <span class="node-badge" style="background:#f59e0b30;color:#f59e0b">NO-LOW</span>
            <span class="node-badge" style="background:#a855f730;color:#a855f7">CERCA_ALTO</span>
            <span class="node-badge" style="background:#a855f730;color:#a855f7">CERCA_BAJO</span></span>
      <span class="detail-label">Formula NO-*</span>
      <span><code>&mu;(NO-X) = 1 - &mu;(X)</code></span>
      <span class="detail-label">Formula CERCA</span>
      <span><code>CERCA_ALTO = min(&mu;(OK), &mu;(HIGH))</code></span>
    </div>
    <p style="margin-top:8px"><b>Salida:</b> <code>fuzzy_out = {torque: {dom: "HIGH", val: 0.85, pert: {HIGH: 0.85, OK: 0.15, LOW: 0.0, ...}}, pend_torque: {...}, ...}</code></p>
  </div>
</div>

<div class="arrow" style="--arrow-from:#a855f7;--arrow-to:#14b8a6">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">fuzzy_out (etiquetas + &mu;)</div>
</div>

<!-- STAGE 6: Permisivos -->
<div class="node stage-perm" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">6</div>
    <div>
      <div class="node-title">Evaluacion de Permisivos</div>
      <div class="node-desc">Condiciones logicas que habilitan/bloquean acciones del motor</div>
    </div>
  </div>
  <div class="node-details">
    <p>Los permisivos son condiciones booleanas complejas (AND/OR/NOT) que verifican el estado del proceso.
       Se inyectan en <code>fuzzy_out</code> como pseudo-variables <code>__PERM_*</code> con etiquetas ON/OFF (&mu;=1 o 0).</p>
    <div class="detail-grid">
      <span class="detail-label">PERMITIR_FRENAR_DESCARGA</span>
      <span>ON cuando NO hay alertas criticas en SAG/tonelaje/tendencias</span>
      <span class="detail-label">PERMITIR_SOLTAR_DESCARGA</span>
      <span>ON cuando NO hay alertas en presiones/torque de bombas</span>
      <span class="detail-label">OPTIMIZAR_SUBIR_DENSIDAD</span>
      <span>ON cuando condiciones favorecen subir objetivo de densidad</span>
      <span class="detail-label">OPTIMIZAR_BAJAR_DENSIDAD</span>
      <span>ON cuando condiciones favorecen bajar objetivo</span>
    </div>
    <p style="margin-top:8px"><b>Mecanismo:</b> Las reglas refieren a <code>__PERM_*</code> en su <code>if</code>. Si el permisivo esta OFF, la regla que requiere ON no dispara.</p>
  </div>
</div>

<div class="arrow" style="--arrow-from:#14b8a6;--arrow-to:#f59e0b">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">fuzzy_out + __PERM_* inyectados</div>
</div>

<!-- STAGE 7: Motor de reglas -->
<div class="node stage-motor" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">7</div>
    <div>
      <div class="node-title">Motor de Reglas</div>
      <div class="node-desc">Evaluacion jerarquica de reglas con bloques, prioridades y cooldowns</div>
    </div>
  </div>
  <div class="node-details">
    <p>El motor evalua todas las reglas contra <code>fuzzy_out</code> usando logica AND/OR/NOT. Las reglas se agrupan en bloques jerarquicos.</p>
    <div class="detail-grid">
      <span class="detail-label">Belief</span>
      <span><code>belief = weight &times; min(&mu;(condiciones))</code> — umbral minimo 0.05</span>
      <span class="detail-label">Bloque critico</span>
      <span>Nivel 1 — si dispara, <b>bloquea</b> estabilidad</span>
      <span class="detail-label">Bloque estabilidad</span>
      <span>Nivel 2 — bloqueado si critico disparo</span>
      <span class="detail-label">Bloque optimizacion</span>
      <span>Independiente — siempre se evalua</span>
      <span class="detail-label">Cooldown</span>
      <span>vel_bomba: 900s | floculante: 1800s | tonelaje: 2700s — por familia de SP</span>
    </div>
    <p style="margin-top:8px"><b>Salida:</b> Lista de eventos con <code>{regla_id, bloque, acciones, belief}</code></p>
  </div>
</div>

<div class="arrow" style="--arrow-from:#f59e0b;--arrow-to:#22c55e">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">eventos fired [{accion, belief}, ...]</div>
</div>

<!-- STAGE 8: Defuzzification -->
<div class="node stage-defuzzy" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">8</div>
    <div>
      <div class="node-title">Defuzzificacion (Sugeno)</div>
      <div class="node-desc">Conversion de acciones fuzzy a cambios numericos en setpoints</div>
    </div>
  </div>
  <div class="node-details">
    <p>Cada accion disparada se traduce a un delta numerico sobre el setpoint correspondiente,
       interpolando en tablas de Sugeno segun el <code>belief</code>.</p>
    <div class="detail-grid">
      <span class="detail-label">Acciones</span>
      <span><code>AUMENTAR|DISMINUIR</code> + <code>VEL_BOMBA|TONELAJE|FLOCULANTE</code> + <code>FUERTE|SUAVE|</code>(normal)</span>
      <span class="detail-label">Interpolacion</span>
      <span><code>step = np.interp(belief, belief_axis, steps)</code></span>
      <span class="detail-label">Aplicacion</span>
      <span><code>SP[familia] += step</code>, luego clamp a limites [LL, HL]</span>
      <span class="detail-label">Acumulativo</span>
      <span>Multiples reglas en un tick aplican secuencialmente sobre el mismo SP</span>
    </div>
    <p style="margin-top:8px"><b>Ejemplo:</b> <code>DISMINUIR_VEL_BOMBA_FUERTE</code> @ belief=0.8 → step=-0.4 → <code>sp_vel_bomba -= 0.4</code></p>
  </div>
</div>

<div class="arrow" style="--arrow-from:#22c55e;--arrow-to:#ef4444">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
  <div class="arrow-label">setpoints actualizados</div>
</div>

<!-- STAGE 9: Output -->
<div class="node stage-output" onclick="this.classList.toggle('expanded')">
  <div class="node-header">
    <div class="node-icon">9</div>
    <div>
      <div class="node-title">Salida: Setpoints Actualizados</div>
      <div class="node-desc">Nuevos valores de consigna para el proceso del Espesador</div>
    </div>
  </div>
  <div class="node-details">
    <p>Los 3 setpoints se actualizan en cada tick y se acumulan entre iteraciones.
       En modo KEPserver, los valores se <b>escriben de vuelta</b> a los tags <code>RETO.SP.*</code> via OPC-UA.</p>
    <div class="detail-grid">
      <span class="detail-label">sp_vel_bomba</span>
      <span>Velocidad de la bomba de descarga &rarr; <code>RETO.SP.sp_vel_bomba</code></span>
      <span class="detail-label">sp_tonelaje</span>
      <span>Tonelaje objetivo de alimentacion &rarr; <code>RETO.SP.sp_tonelaje</code></span>
      <span class="detail-label">sp_floculante</span>
      <span>Dosificacion de floculante &rarr; <code>RETO.SP.sp_floculante</code></span>
      <span class="detail-label">Persistencia</span>
      <span>Los SPs se arrastran al siguiente tick como estado mutable</span>
      <span class="detail-label">Escritura</span>
      <span><span class="mode-tag kep">KEPserver</span> OPC-UA write a <code>RETO.SP.*</code> &nbsp;|&nbsp; <span class="mode-tag sim">Simulacion</span> solo en memoria</span>
    </div>
  </div>
</div>

<!-- Loop indicator -->
<div class="arrow" style="--arrow-from:#ef4444;--arrow-to:#facc15">
  <div class="arrow-line"></div>
  <div class="arrow-head"></div>
</div>

<div class="loop-indicator">
  <span class="icon">&#x21bb;</span>
  <span>Se repite cada 5 segundos (configurable). Lee tags &rarr; ejecuta pipeline &rarr; escribe SPs. El estado (filtros, hist, cooldowns, SPs) se arrastra entre iteraciones.</span>
</div>

</div><!-- /pipeline -->
</div><!-- /flow-wrapper -->

<!-- Legend -->
<div class="legend">
  <div class="legend-title">Leyenda de etapas</div>
  <span class="legend-item"><span class="legend-dot" style="background:#2563eb"></span> Entrada de datos</span>
  <span class="legend-item"><span class="legend-dot" style="background:#0891b2"></span> Calculo / Extraccion</span>
  <span class="legend-item"><span class="legend-dot" style="background:#6366f1"></span> Filtrado Exp-Q</span>
  <span class="legend-item"><span class="legend-dot" style="background:#a855f7"></span> Fuzzificacion</span>
  <span class="legend-item"><span class="legend-dot" style="background:#14b8a6"></span> Permisivos</span>
  <span class="legend-item"><span class="legend-dot" style="background:#f59e0b"></span> Motor de reglas</span>
  <span class="legend-item"><span class="legend-dot" style="background:#22c55e"></span> Defuzzificacion</span>
  <span class="legend-item"><span class="legend-dot" style="background:#ef4444"></span> Salida (Setpoints)</span>
  <span class="legend-item"><span class="legend-dot" style="background:#facc15"></span> Iteracion / Loop</span>
</div>

<div style="margin-top:20px;padding:16px 20px;background:#1e293b;border-radius:10px;border:1px solid #334155;font-size:.82rem;color:#94a3b8;line-height:1.7">
  <p><b style="color:#e2e8f0">Estado persistente entre ticks:</b></p>
  <p>&bull; <code style="color:#6366f1">ExpQFilter</code> — buffers de muestras anteriores para el filtrado</p>
  <p>&bull; <code style="color:#a855f7">hist</code> — puntos recientes para calculo de pendientes (regresion lineal)</p>
  <p>&bull; <code style="color:#f59e0b">last_action_time</code> — timestamp de ultima accion por familia SP (cooldown)</p>
  <p>&bull; <code style="color:#22c55e">setpoints_actuales</code> — valores acumulados de los 3 setpoints</p>
</div>

</div><!-- /container -->
</body>
</html>"""


@app.route("/diagrama")
def diagrama():
    return Response(DIAGRAM_PAGE, mimetype="text/html")


# ============================================================
# UI -- Entrada de Datos (live tags + history)
# ============================================================

ENTRADA_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Espesador -- Entrada de Datos</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
nav{display:flex;gap:8px;padding:12px 20px;background:#0b1322;border-bottom:1px solid #1e293b}
nav a{color:#94a3b8;text-decoration:none;padding:6px 14px;border-radius:6px;font-size:.85rem;transition:.15s}
nav a:hover{background:#1e293b;color:#e2e8f0}
nav a.active{background:#1e293b;color:#38bdf8;font-weight:600}
.container{max-width:1400px;margin:0 auto;padding:20px}
h1{font-size:1.4rem;margin-bottom:4px}
.subtitle{color:#64748b;font-size:.85rem;margin-bottom:20px}

/* Filters */
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
.filter-btn{padding:5px 14px;border-radius:16px;border:1px solid #334155;background:transparent;color:#94a3b8;cursor:pointer;font-size:.78rem;transition:.15s}
.filter-btn:hover{border-color:#38bdf8;color:#e2e8f0}
.filter-btn.active{background:#1e3a5f;border-color:#38bdf8;color:#38bdf8;font-weight:600}
.filter-btn.dir-input.active{background:#1e3a5f;border-color:#3b82f6;color:#3b82f6}
.filter-btn.dir-output.active{background:#0f2e1e;border-color:#22c55e;color:#22c55e}
.search-box{padding:6px 12px;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;font-size:.82rem;width:220px}
.search-box::placeholder{color:#475569}

/* Tag count */
.tag-count{color:#64748b;font-size:.78rem;margin-bottom:8px}

/* Table */
.tbl-wrap{overflow-x:auto;border-radius:10px;border:1px solid #1e293b}
table{width:100%;border-collapse:collapse;font-size:.82rem}
thead{background:#0b1322;position:sticky;top:0}
th{text-align:left;padding:10px 12px;color:#64748b;font-weight:600;border-bottom:1px solid #1e293b;white-space:nowrap}
th.sel-col{width:36px;text-align:center}
td{padding:8px 12px;border-bottom:1px solid #1e293b20}
tr:hover{background:#1e293b40}
tr.selected-row{background:#1e3a5f30}

/* Checkbox */
.tag-cb{width:16px;height:16px;accent-color:#3b82f6;cursor:pointer}

/* Direction badge */
.dir-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.7rem;font-weight:600}
.dir-badge.input{background:rgba(59,130,246,.15);color:#3b82f6;border:1px solid rgba(59,130,246,.3)}
.dir-badge.output{background:rgba(34,197,94,.15);color:#22c55e;border:1px solid rgba(34,197,94,.3)}

/* Group badge */
.grp-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.68rem;font-weight:600}
.grp-pv{background:rgba(59,130,246,.12);color:#3b82f6}
.grp-cruda{background:rgba(245,158,11,.12);color:#f59e0b}
.grp-lim{background:rgba(99,102,241,.12);color:#6366f1}
.grp-sp{background:rgba(34,197,94,.12);color:#22c55e}
.grp-other{background:rgba(100,116,139,.12);color:#94a3b8}

/* Value cell */
.val-cell{font-family:'Cascadia Code','Fira Code',monospace;font-weight:600;font-size:.85rem}
.val-cell.no-data{color:#475569;font-style:italic;font-weight:400}

/* Status dot */
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status-dot.ok{background:#22c55e}
.status-dot.missing{background:#ef4444}

/* Sparkline */
.sparkline-cell{width:140px;min-width:140px}
.sparkline-cell canvas{display:block}

/* Selected tags panel */
.sel-panel{margin-top:24px;background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);border-radius:12px;padding:20px 24px;border:1px solid #334155;box-shadow:0 4px 20px rgba(0,0,0,.3)}
.sel-panel-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.sel-panel-header h3{font-size:1rem;color:#e2e8f0;display:flex;align-items:center;gap:8px}
.sel-panel-header h3 .count-badge{background:#3b82f6;color:#fff;font-size:.7rem;padding:2px 8px;border-radius:10px}
.sel-tags-wrap{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}
.sel-chip{display:inline-flex;align-items:center;gap:4px;padding:5px 12px;border-radius:16px;font-size:.75rem;font-weight:600;cursor:pointer;transition:.15s}
.sel-chip.input{background:rgba(59,130,246,.12);color:#60a5fa;border:1px solid rgba(59,130,246,.25)}
.sel-chip.output{background:rgba(34,197,94,.12);color:#4ade80;border:1px solid rgba(34,197,94,.25)}
.sel-chip:hover{transform:scale(1.03)}
.sel-chip .x{margin-left:4px;opacity:.4;font-size:.9rem}
.sel-chip:hover .x{opacity:1;color:#ef4444}
.sel-chart-wrap{background:#0b1322;border-radius:10px;padding:16px;border:1px solid #1e293b}
.sel-chart-wrap canvas{width:100%!important;height:380px!important}
.sel-controls{display:flex;gap:8px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.sel-controls button{padding:6px 14px;border-radius:8px;border:1px solid #334155;background:transparent;color:#94a3b8;cursor:pointer;font-size:.78rem;transition:.15s}
.sel-controls button:hover{border-color:#38bdf8;color:#e2e8f0;background:#1e293b}
.sel-controls button.primary{background:#3b82f6;border-color:#3b82f6;color:#fff}
.sel-controls button.primary:hover{background:#2563eb}
.sel-controls .sep{color:#334155;font-size:.75rem}
.refresh-btn{padding:5px 12px;border-radius:8px;border:1px solid #334155;background:transparent;color:#94a3b8;cursor:pointer;font-size:.75rem;transition:.15s}
.refresh-btn:hover{border-color:#38bdf8;color:#e2e8f0}
.refresh-btn.active{background:rgba(56,189,248,.1);border-color:#38bdf8;color:#38bdf8;font-weight:600}
.legend-row{display:flex;gap:16px;margin-top:12px;padding:10px 12px;background:#1e293b50;border-radius:8px;font-size:.75rem;color:#94a3b8}
.legend-row .leg-item{display:flex;align-items:center;gap:6px}
.legend-row .leg-line{width:24px;height:2px;border-radius:1px}
.legend-row .leg-line.solid{background:#3b82f6}
.legend-row .leg-line.dashed{background:#22c55e;background:repeating-linear-gradient(90deg,#22c55e 0,#22c55e 5px,transparent 5px,transparent 8px)}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<nav>
  <a href="/">Configuracion SE</a>
  <a href="/entrada" class="active">Entrada de Datos</a>
  <a href="/graficos">Graficos en Vivo</a>
  <a href="/diagrama">Diagrama de Flujo</a>
</nav>
<div class="container">
<h1>Entrada de Datos</h1>
<p class="subtitle">Vista en tiempo real de todos los tags del sistema &mdash; entradas (PV, Crudas, Limites) y salidas (Setpoints). Historial de los ultimos 50 valores.</p>
  <a href="/#licenciamiento">Licenciamiento</a>

<!-- Filters -->
<div class="filters">
  <button class="filter-btn active" data-filter="all" onclick="setFilter('all',this)">Todos</button>
  <button class="filter-btn dir-input" data-filter="input" onclick="setFilter('input',this)">Entradas</button>
  <button class="filter-btn dir-output" data-filter="output" onclick="setFilter('output',this)">Salidas</button>
  <span style="color:#334155">|</span>
  <button class="filter-btn" data-filter="pv" onclick="setFilter('pv',this)">PV</button>
  <button class="filter-btn" data-filter="cruda" onclick="setFilter('cruda',this)">Crudas</button>
  <button class="filter-btn" data-filter="lim" onclick="setFilter('lim',this)">Limites</button>
  <button class="filter-btn" data-filter="sp" onclick="setFilter('sp',this)">Setpoints</button>
  <input type="text" class="search-box" id="searchBox" placeholder="Buscar tag..." oninput="applyFilters()">
</div>

<div class="tag-count" id="tagCount"></div>

<!-- Table -->
<div class="tbl-wrap">
<table>
<thead>
<tr>
  <th class="sel-col"><input type="checkbox" class="tag-cb" id="cbAll" onchange="toggleAll(this)"></th>
  <th>Tag</th>
  <th>Grupo</th>
  <th>Direccion</th>
  <th>Estado</th>
  <th>Valor</th>
  <th class="sparkline-cell">Historial (50)</th>
</tr>
</thead>
<tbody id="tagsBody"></tbody>
</table>
</div>

<!-- Selected tags panel -->
<div class="sel-panel" id="selPanel" style="display:none">
<div class="sel-panel-header">
  <h3>Comparacion en Tiempo Real <span class="count-badge" id="selCountBadge">0</span></h3>
  <span style="color:#475569;font-size:.75rem" id="selInfo"></span>
</div>
<div class="sel-tags-wrap" id="selTagsWrap"></div>
<div class="sel-controls">
  <button class="primary" onclick="clearSelection()">Limpiar seleccion</button>
  <span class="sep">|</span>
  <span style="color:#64748b;font-size:.78rem">Refresco:</span>
  <button class="refresh-btn active" data-rate="2000" onclick="setRefresh(2000,this)">2s</button>
  <button class="refresh-btn" data-rate="5000" onclick="setRefresh(5000,this)">5s</button>
  <button class="refresh-btn" data-rate="10000" onclick="setRefresh(10000,this)">10s</button>
</div>
<div class="sel-chart-wrap">
  <canvas id="selChart"></canvas>
</div>
<div class="legend-row">
  <div class="leg-item"><span class="leg-line solid"></span> Entrada (linea solida)</div>
  <div class="leg-item"><span class="leg-line dashed"></span> Salida (linea punteada)</div>
</div>
</div>

</div><!-- /container -->

<script>
let allTags = [];
let selectedTags = new Set();
let currentFilter = 'all';
let sparkCanvases = {};
let selChart = null;
let pollTimer = null;
let pollRate = 2000;

const GRP_LABELS = {pv:'PV',cruda:'Cruda',lim:'Limite',sp:'Setpoint',other:'Otro'};
const GRP_COLORS = {pv:'#3b82f6',cruda:'#f59e0b',lim:'#6366f1',sp:'#22c55e',other:'#94a3b8'};
const DIR_COLORS = {input:'#3b82f6', output:'#22c55e'};

function setRefresh(rate, btn) {
  pollRate = rate;
  document.querySelectorAll('.refresh-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  startPolling();
}

async function fetchData() {
  const res = await fetch('/api/entrada');
  allTags = await res.json();
  renderTable();
  renderSparklines();
  updateSelPanel();
}

function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyFilters();
}

function applyFilters() {
  const search = document.getElementById('searchBox').value.toLowerCase();
  const rows = document.querySelectorAll('#tagsBody tr');
  let visible = 0;
  rows.forEach(row => {
    const name = row.dataset.name.toLowerCase();
    const dir = row.dataset.dir;
    const grp = row.dataset.grp;
    let show = true;
    if (currentFilter === 'input' && dir !== 'input') show = false;
    if (currentFilter === 'output' && dir !== 'output') show = false;
    if (['pv','cruda','lim','sp'].includes(currentFilter) && grp !== currentFilter) show = false;
    if (search && !name.includes(search)) show = false;
    row.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  document.getElementById('tagCount').textContent = visible + ' de ' + allTags.length + ' tags';
}

function renderTable() {
  const tbody = document.getElementById('tagsBody');
  tbody.innerHTML = '';
  allTags.forEach((tag, i) => {
    const tr = document.createElement('tr');
    tr.dataset.name = tag.name;
    tr.dataset.dir = tag.direction;
    tr.dataset.grp = tag.group;
    if (selectedTags.has(tag.name)) tr.classList.add('selected-row');

    const valStr = tag.value !== null && tag.value !== undefined ? parseFloat(tag.value).toFixed(3) : 'Sin datos';
    const valClass = tag.value !== null && tag.value !== undefined ? 'val-cell' : 'val-cell no-data';
    const statusCls = tag.exists ? 'ok' : 'missing';
    const statusTxt = tag.exists ? 'Conectado' : 'No encontrado';

    tr.innerHTML = `
      <td style="text-align:center"><input type="checkbox" class="tag-cb" data-tag="${tag.name}" ${selectedTags.has(tag.name)?'checked':''} onchange="toggleTagSel(this)"></td>
      <td style="font-family:monospace;font-weight:600;font-size:.82rem">${tag.name}</td>
      <td><span class="grp-badge grp-${tag.group}">${GRP_LABELS[tag.group]||tag.group}</span></td>
      <td><span class="dir-badge ${tag.direction}">${tag.direction==='input'?'ENTRADA':'SALIDA'}</span></td>
      <td><span class="status-dot ${statusCls}"></span>${statusTxt}</td>
      <td class="${valClass}">${valStr}</td>
      <td class="sparkline-cell"><canvas id="spark-${i}" width="130" height="32"></canvas></td>
    `;
    tbody.appendChild(tr);
  });
  applyFilters();
}

function renderSparklines() {
  allTags.forEach((tag, i) => {
    const canvas = document.getElementById('spark-' + i);
    if (!canvas || !tag.history || tag.history.length < 2) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const vals = tag.history.map(p => p.v);
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const range = mx - mn || 1;
    const color = tag.direction === 'output' ? '#22c55e' : GRP_COLORS[tag.group] || '#3b82f6';

    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    vals.forEach((v, j) => {
      const x = (j / (vals.length - 1)) * w;
      const y = h - ((v - mn) / range) * (h - 4) - 2;
      j === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.beginPath();
    ctx.fillStyle = color;
    const lastX = w;
    const lastY = h - ((vals[vals.length-1] - mn) / range) * (h - 4) - 2;
    ctx.arc(lastX, lastY, 2.5, 0, Math.PI * 2);
    ctx.fill();
  });
}

function toggleTagSel(cb) {
  const name = cb.dataset.tag;
  if (cb.checked) selectedTags.add(name); else selectedTags.delete(name);
  cb.closest('tr').classList.toggle('selected-row', cb.checked);
  updateSelPanel();
}

function toggleAll(cb) {
  const rows = document.querySelectorAll('#tagsBody tr');
  rows.forEach(row => {
    if (row.style.display === 'none') return;
    const checkbox = row.querySelector('.tag-cb');
    if (!checkbox || !checkbox.dataset.tag) return;
    checkbox.checked = cb.checked;
    if (cb.checked) selectedTags.add(checkbox.dataset.tag);
    else selectedTags.delete(checkbox.dataset.tag);
    row.classList.toggle('selected-row', cb.checked);
  });
  updateSelPanel();
}

function clearSelection() {
  selectedTags.clear();
  document.querySelectorAll('#tagsBody .tag-cb').forEach(cb => { cb.checked = false; });
  document.querySelectorAll('#tagsBody tr').forEach(tr => tr.classList.remove('selected-row'));
  document.getElementById('cbAll').checked = false;
  updateSelPanel();
}

function updateSelPanel() {
  const panel = document.getElementById('selPanel');
  if (selectedTags.size === 0) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const wrap = document.getElementById('selTagsWrap');
  wrap.innerHTML = '';
  selectedTags.forEach(name => {
    const tag = allTags.find(t => t.name === name);
    if (!tag) return;
    const chip = document.createElement('span');
    chip.className = 'sel-chip ' + tag.direction;
    chip.innerHTML = name + '<span class="x">&times;</span>';
    chip.onclick = () => { selectedTags.delete(name); const cb = document.querySelector(`.tag-cb[data-tag="${name}"]`); if(cb){cb.checked=false;cb.closest('tr').classList.remove('selected-row');} updateSelPanel(); };
    wrap.appendChild(chip);
  });

  const nIn = [...selectedTags].filter(n => { const t = allTags.find(x=>x.name===n); return t && t.direction==='input'; }).length;
  const nOut = [...selectedTags].filter(n => { const t = allTags.find(x=>x.name===n); return t && t.direction==='output'; }).length;
  document.getElementById('selInfo').textContent = nIn + ' entradas, ' + nOut + ' salidas';
  document.getElementById('selCountBadge').textContent = selectedTags.size;

  buildSelChart();
}

function _makeGradient(ctx, color) {
  const g = ctx.createLinearGradient(0, 0, 0, 380);
  g.addColorStop(0, color + '30');
  g.addColorStop(1, color + '02');
  return g;
}

function buildSelChart() {
  const canvas = document.getElementById('selChart');
  const ctx = canvas.getContext('2d');
  if (selChart) selChart.destroy();

  const datasets = [];
  const palette = ['#3b82f6','#22c55e','#f59e0b','#ef4444','#a855f7','#ec4899','#14b8a6','#6366f1','#84cc16','#f97316','#06b6d4','#e11d48'];
  let ci = 0;
  let maxLen = 0;

  selectedTags.forEach(name => {
    const tag = allTags.find(t => t.name === name);
    if (!tag || !tag.history || tag.history.length === 0) return;
    if (tag.history.length > maxLen) maxLen = tag.history.length;
    const color = palette[ci % palette.length];
    const isOutput = tag.direction === 'output';
    datasets.push({
      label: name.replace('RETO.',''),
      data: tag.history.map(p => p.v),
      borderColor: color,
      backgroundColor: _makeGradient(ctx, color),
      fill: datasets.length === 0,
      borderWidth: isOutput ? 2.5 : 1.8,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointHoverBackgroundColor: color,
      pointHoverBorderColor: '#fff',
      pointHoverBorderWidth: 2,
      tension: 0.35,
      borderDash: isOutput ? [6, 4] : [],
    });
    ci++;
  });

  const labels = [];
  if (maxLen > 0) {
    const refTag = allTags.find(t => selectedTags.has(t.name) && t.history && t.history.length > 0);
    if (refTag) {
      refTag.history.forEach(p => {
        const d = new Date(p.t * 1000);
        labels.push(d.toLocaleTimeString('es-CL',{hour:'2-digit',minute:'2-digit',second:'2-digit'}));
      });
    }
  }

  selChart = new Chart(ctx, {
    type: 'line',
    data: {labels, datasets},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: {duration: 300, easing: 'easeOutQuart'},
      interaction: {mode: 'index', intersect: false},
      scales: {
        x: {
          ticks: {color:'#475569', maxTicksLimit: 12, maxRotation: 0, font:{size:10}},
          grid: {color:'#1e293b80', lineWidth: 0.5}
        },
        y: {
          ticks: {color:'#475569', font:{size:10}, callback: v => v >= 1000 ? (v/1000).toFixed(1)+'k' : Number(v.toFixed(2))},
          grid: {color:'#1e293b80', lineWidth: 0.5}
        }
      },
      plugins: {
        legend: {
          labels: {color:'#cbd5e1', boxWidth:16, boxHeight:2, font:{size:11}, padding:12, usePointStyle:false},
          position: 'top'
        },
        tooltip: {
          backgroundColor: '#0f172aee',
          borderColor: '#334155',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: '#94a3b8',
          padding: 10,
          cornerRadius: 8,
          displayColors: true,
          boxWidth: 10,
          boxHeight: 2,
          callbacks: {
            label: item => ' ' + item.dataset.label + ': ' + Number(item.raw).toFixed(3)
          }
        }
      }
    }
  });
}

async function pollUpdate() {
  try {
    const res = await fetch('/api/entrada');
    const data = await res.json();
    allTags = data;

    // Update values & sparklines in-place
    const rows = document.querySelectorAll('#tagsBody tr');
    data.forEach((tag, i) => {
      if (!rows[i]) return;
      const valTd = rows[i].querySelector('.val-cell');
      if (valTd) {
        if (tag.value !== null && tag.value !== undefined) {
          valTd.textContent = parseFloat(tag.value).toFixed(3);
          valTd.classList.remove('no-data');
        } else {
          valTd.textContent = 'Sin datos';
          valTd.classList.add('no-data');
        }
      }
    });
    renderSparklines();
    if (selectedTags.size > 0) buildSelChart();
  } catch(e) {}
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollUpdate, pollRate);
}

// Init
fetchData().then(() => startPolling());

// Alert indicator (floating)
const _ACATS = ALERT_CATEGORIES_JSON;
async function _pollAlerts() {
  try {
    const r = await fetch('/api/alerts');
    const d = await r.json();
    const el = document.getElementById('alertFloat');
    const cnt = d.alerts.length;
    el.querySelector('.af-count').textContent = cnt;
    el.style.borderColor = cnt > 0 ? '#ef4444' : '#22c55e';
    el.querySelector('.af-count').style.background = cnt > 0 ? '#ef4444' : '#22c55e';
    const body = el.querySelector('.af-body');
    if (cnt === 0) {
      body.innerHTML = '<div style="color:#22c55e;font-size:.8rem;padding:8px">Sin alertas</div>';
    } else {
      body.innerHTML = d.alerts.map(a => {
        const cat = _ACATS[a.category] || _ACATS.general;
        return '<div style="padding:6px 0;border-bottom:1px solid #1e293b;font-size:.78rem"><span style="color:'+cat.color+';font-weight:600">'+cat.icon+'</span> '+a.message+'</div>';
      }).join('');
    }
  } catch(e){}
}
_pollAlerts(); setInterval(_pollAlerts, 10000);
</script>

<!-- Floating alert badge -->
<div id="alertFloat" style="position:fixed;bottom:16px;right:16px;background:#0b1322;border:2px solid #22c55e;border-radius:12px;z-index:200;cursor:pointer;min-width:36px;transition:.2s" onclick="this.classList.toggle('open')">
  <div style="display:flex;align-items:center;gap:6px;padding:6px 10px">
    <span class="af-count" style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;background:#22c55e;color:#fff;font-size:.72rem;font-weight:700">0</span>
    <span style="color:#94a3b8;font-size:.78rem">Alertas</span>
  </div>
  <div class="af-body" style="display:none;padding:4px 12px 8px 12px;max-height:250px;overflow-y:auto"></div>
</div>
<style>
#alertFloat.open{width:320px}
#alertFloat.open .af-body{display:block}
</style>
</body>
</html>"""


@app.route("/entrada")
def entrada():
    page = ENTRADA_PAGE.replace("ALERT_CATEGORIES_JSON", json.dumps(AlertCollector.CATEGORIES))
    return Response(page, mimetype="text/html")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Sistema Experto Espesador (v2)")
    print("  http://127.0.0.1:5000")
    print("  http://127.0.0.1:5000/entrada   (entrada de datos / tags)")
    print("  http://127.0.0.1:5000/#tags     (editor Tags KEPserver)")
    print("  http://127.0.0.1:5000/graficos  (graficos en tiempo real)")
    print("  http://127.0.0.1:5000/diagrama  (diagrama de flujo SE)")
    print("=" * 60)
    app.run(debug=True, host="127.0.0.1", port=5000)
