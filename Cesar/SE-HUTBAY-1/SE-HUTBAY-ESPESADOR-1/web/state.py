# -*- coding: utf-8 -*-
"""Estado compartido entre blueprints del Sistema Experto Espesador.

IT-7: Este módulo centraliza todo lo que más de un blueprint necesita:
  - AlertCollector + instancia _alerts
  - Constantes de rutas JSON y catálogos
  - Helpers de Tags / KEPserver / historial
  - TagGenerator + instancia _tag_generator
  - SEEngine + instancia _se_engine
  - Estado de simulación streaming (_sim_state)
  - Carga de templates HTML
  - Helpers JSON compartidos entre múltiples blueprints
  - _startup_checks()

Importado por web/api/config.py, tags.py, se.py y views.py.
NO importa nada de web/api/*.py (sin dependencias circulares).
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections import deque

from config import (
    SETPOINT_KEYS,
    VARIABLES_CRUDAS_REQUERIDAS,
    VARIABLES_EXTERNAS,
    VARIABLES_PROCESO,
)
from defuzzy_actions import DEFUZZY_POR_FAMILIA
from estados_espesador import ESTADOS_ESPESADOR
from core.filters.exp_q import CONFIG_FILTRO_ESPESADOR_DEFAULT
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

# ============================================================
# Rutas JSON y constantes de infraestructura
# ============================================================

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # raíz del proyecto

_CFG_DIR = os.path.join(_HERE, "config", "espesador")  # IT-9

REGLAS_JSON     = os.path.join(_CFG_DIR, "reglas.json")
FILTROS_JSON    = os.path.join(_CFG_DIR, "filtros.json")
DEFUZZY_JSON    = os.path.join(_CFG_DIR, "defuzzy.json")
FUZZY_JSON      = os.path.join(_CFG_DIR, "fuzzy.json")
VARIABLES_JSON  = os.path.join(_CFG_DIR, "variables.json")
PERMISIVOS_JSON = os.path.join(_CFG_DIR, "permisivos.json")
TAGS_JSON       = os.path.join(_CFG_DIR, "tags.json")
LICENCIA_JSON   = os.path.join(_CFG_DIR, "licencia.json")
ESTADOS_JSON_PATH = os.path.join(_CFG_DIR, "estados.json")
WAITS_JSON_PATH   = os.path.join(_CFG_DIR, "waits.json")


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


# ============================================================
# Catálogos
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
        for key in keys:
            partes = key.split("_")
            direccion = partes[0]
            intensidad = "_".join(partes[1:]) if len(partes) > 1 else ""
            nombre = f"{direccion}_{sufijo}"
            if intensidad:
                nombre += f"_{intensidad}"
            nombres.append(nombre)
    return nombres


VARIABLES_DISPONIBLES  = _build_variables_disponibles()
ACCIONES_DISPONIBLES   = _build_acciones_disponibles()
PERMISIVOS_DISPONIBLES = list(PERMISIVOS.keys())

VARIABLES_VALIDAS = set(VARIABLES_DISPONIBLES)
ETIQUETAS_VALIDAS = set(ETIQUETAS_DISPONIBLES)
ACCIONES_VALIDAS  = set(ACCIONES_DISPONIBLES)
BLOQUES_VALIDOS   = set(BLOQUES_DISPONIBLES)


def _build_estados_serializable() -> dict:
    from core.states.builder import _resolver_condicion
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
# Helpers JSON compartidos entre blueprints
# (estados y waits son usados tanto por config.py como views.py)
# ============================================================

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


def _definiciones_lista_a_dict(definiciones_lista: list) -> dict:
    """Convierte lista de definiciones calculadas (del JSON) a dict ordenado.
    Compartido entre config.py (variables route) y se.py (_ejecutar_simulacion).
    """
    out: dict = {}
    for item in definiciones_lista:
        nombre = item["nombre"]
        cfg: dict = {"descripcion": item.get("descripcion", ""), "tipo": item["tipo"]}
        if item["tipo"] == "aritmetica":
            cfg["operacion"] = item["operacion"]
            cfg["args"] = list(item["args"])
        else:
            cfg["arg"] = item["arg"]
            cfg["ventana_min"] = float(item["ventana_min"])
        out[nombre] = cfg
    return out


# ============================================================
# Tags KEPserver — helpers (IT-8: lógica OPC-UA → connectors/kepserver.py)
# ============================================================

import connectors.kepserver as _kep

# Mantener KEPSERVER_URL como alias para compatibilidad con código existente
KEPSERVER_URL = _kep.URL

def _load_tags() -> dict:
    if not os.path.exists(TAGS_JSON):
        return {"tags": [], "next_id": 1}
    with open(TAGS_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_tags(data: dict) -> None:
    with open(TAGS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_kepserver_tags_batch(tag_names: list[str]) -> dict[str, dict]:
    """Delegado a connectors.kepserver.read_tags_batch (IT-8)."""
    return _kep.read_tags_batch(tag_names)


def _try_write_kepserver_tag(tag_name: str, value, data_type: str) -> dict:
    """Delegado a connectors.kepserver.write_tag (IT-8)."""
    return _kep.write_tag(tag_name, value, data_type)


def _enrich_tags_with_kepserver(tags: list[dict]) -> list[dict]:
    """Delegado a connectors.kepserver.enrich_tags (IT-8)."""
    return _kep.enrich_tags(tags)


# ============================================================
# Tag history buffer (circular, últimos N valores por tag)
# ============================================================

_TAG_HISTORY_SIZE = 50
_tag_history: dict[str, deque[dict]] = {}
_tag_history_lock = threading.Lock()


def _record_tag_values(tag_values: dict[str, float]):
    """Agrega valores actuales al ring buffer de historial por tag."""
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
    """Genera un valor en patrón 3-fases (estable → alerta → recuperación)."""
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


# ============================================================
# Defaults del generador de tags
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
# TagGenerator — genera datos en background y los escribe al KEPserver
# ============================================================

class TagGenerator:
    """Background thread que escribe valores generados al KEPserver."""

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
            _kep.write_float_batch(tags_to_write)  # IT-8: OPC-UA → connectors/kepserver.py
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
            "running":     self._running,
            "tick":        self._tick,
            "intervalo_s": self._intervalo_s,
            "n_ciclo":     self._n_ciclo,
            "ranges":      self._ranges,
            "last_values": self._last_values,
            "last_error":  self._last_error,
        }


_tag_generator = TagGenerator()


# ============================================================
# SEEngine — tag mapping constants
# ============================================================

TAG_TO_PV = {f"RETO.PV.{v}": v for v in [
    "torque", "bed_mass", "bed_level", "densidad", "torque_bomba",
    "potencia_bomba", "presion_descarga", "presion_diferencial", "nivel_rastra"
]}
TAG_TO_CRUDA = {f"RETO.CRUDA.{v}": v for v in [
    "tonelaje_sag_1", "tonelaje_sag_2", "tonelaje_relave",
    "presion_bomba_1", "presion_bomba_2", "turbiedad_agua"
]}
TAG_TO_LIM: dict = {}
for _var in ["torque", "bed_mass", "bed_level", "densidad", "torque_bomba",
             "potencia_bomba", "presion_descarga", "presion_diferencial", "nivel_rastra"]:
    TAG_TO_LIM[f"RETO.LIM.{_var}_lmin"] = (_var, "lmin")
    TAG_TO_LIM[f"RETO.LIM.{_var}_lmax"] = (_var, "lmax")

SP_TO_TAG = {
    "sp_tonelaje":   "RETO.SP.sp_tonelaje",
    "sp_floculante": "RETO.SP.sp_floculante",
    "sp_vel_bomba":  "RETO.SP.sp_vel_bomba",
}


# ============================================================
# SEEngine — Sistema Experto en tiempo real via KEPserver
# ============================================================

class SEEngine:
    """Background thread que lee tags, corre el pipeline del SE, escribe SPs."""

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
        from core.filters.exp_q import ExpQFilter, CONFIG_FILTRO_ESPESADOR_DEFAULT
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
        """Lee PV, CRUDA y LIM tags del KEPserver. Devuelve (inputs, crudas, limites) o None."""
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
        """Escribe setpoints actuales al KEPserver (IT-8: OPC-UA → connectors/kepserver.py)."""
        sp_vals = {tag: float(self._setpoints.get(sp_key, 0.0)) for sp_key, tag in SP_TO_TAG.items()}
        _record_tag_values(sp_vals)
        try:
            _kep.write_float_batch(sp_vals)
        except Exception as e:
            self._last_error = f"Write SP: {e}"
            _alerts.add("kep", f"SE Write SP: {e}", traceback.format_exc())

    def _run_tick(self):
        """Ejecuta un tick del pipeline del SE."""
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

        self._last_events = []
        tick_events = []
        for evento in motor_out.get("fired", []):
            acciones_con_belief = [(a, evento.get("belief", 0.5)) for a in evento.get("acciones", [])]
            ev_ok = True
            ev_error = None
            if acciones_con_belief:
                try:
                    apply_actions(acciones_con_belief, self._setpoints, self._limites_sp)
                except Exception as exc:
                    ev_ok = False
                    ev_error = str(exc)
            tick_events.append({
                "regla_id": evento.get("id", "?"),
                "bloque":   evento.get("bloque", ""),
                "acciones": evento.get("acciones", []),
                "belief":   round(evento.get("belief", 0), 3),
                "ok":       ev_ok,
                "error":    ev_error,
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
            "running":      self._running,
            "tick":         self._tick,
            "t_s":          round(self._t_s, 1),
            "setpoints":    {k: round(v, 3) for k, v in self._setpoints.items()},
            "last_events":  self._last_events,
            "last_error":   self._last_error,
        }


_se_engine = SEEngine()


# ============================================================
# Estado de simulación streaming
# ============================================================

_sim_state: dict = {
    "running": False,
    "cursor": 0,
    "df_resultados": None,
    "df_eventos": None,
    "batch_size": 5,
}


# ============================================================
# Templates HTML (cargados desde web/templates/)  [IT-6]
# ============================================================

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _load_template(nombre: str) -> str:
    path = os.path.join(_TEMPLATES_DIR, nombre)
    with open(path, encoding="utf-8") as f:
        return f.read()


BIENVENIDA_PAGE = _load_template("bienvenida.html")
HTML_PAGE       = _load_template("index.html")
DIAGRAM_PAGE    = _load_template("diagrama.html")
ENTRADA_PAGE    = _load_template("entrada.html")

# Gráficos en tiempo real — inline (no es un archivo separado)
CHART_VARS = [
    {"key": "torque",              "label": "Torque (%)",              "color": "#38bdf8"},
    {"key": "bed_level",           "label": "Bed Level (m)",           "color": "#a78bfa"},
    {"key": "densidad",            "label": "Densidad descarga (%)",   "color": "#22c55e"},
    {"key": "presion_descarga",    "label": "Presion descarga",        "color": "#fb923c"},
    {"key": "presion_diferencial", "label": "Presion diferencial",     "color": "#f472b6"},
    {"key": "nivel_rastra",        "label": "Nivel rastra (%)",        "color": "#facc15"},
]


# ============================================================
# Startup health checks
# ============================================================

def _startup_checks():
    """Verifica conectividad y config al arrancar. Popula _alerts."""
    # 1. Conectividad KEPserver (IT-8: via connectors/kepserver.py)
    ok, err = _kep.check_connection()
    if not ok:
        if "no instalado" in err:
            _alerts.add("import", err, "pip install opcua")
        else:
            _alerts.add("kep", f"KEPserver no accesible en {_kep.URL}", err)

    # 2. JSONs de configuración
    for name, path in [
        ("reglas.json", REGLAS_JSON), ("filtros.json", FILTROS_JSON),
        ("defuzzy.json", DEFUZZY_JSON), ("fuzzy.json", FUZZY_JSON),
        ("variables.json", VARIABLES_JSON), ("permisivos.json", PERMISIVOS_JSON),
        ("tags.json", TAGS_JSON), ("licencia.json", LICENCIA_JSON),
    ]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)
        except Exception as e:
            _alerts.add("config", f"Error parseando {name}", str(e))

    # 3. Carga de reglas
    try:
        from runner import cargar_reglas_json
        reglas = cargar_reglas_json()
        if not reglas:
            _alerts.add("reglas", "No hay reglas cargadas.", "reglas.json esta vacio o falta.")
    except Exception as e:
        _alerts.add("reglas", f"Error cargando reglas: {e}", traceback.format_exc())

    # 4. Imports críticos
    for mod_name in ["runner", "motor", "defuzzy_actions", "fuzzys_models_espesador",
                     "core.filters.exp_q", "simulacion"]:
        try:
            __import__(mod_name)
        except Exception as e:
            _alerts.add("import", f"Error importando {mod_name}", str(e))
