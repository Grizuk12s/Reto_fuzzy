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

import hashlib
import hmac
import json
import os
import threading
import time
import traceback
from collections import deque
from datetime import date, datetime, timedelta

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
TRACKING_JSON     = os.path.join(_CFG_DIR, "tracking.json")


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
        "heartbeat": {"label": "Heartbeat KEPserver",   "color": "#14b8a6", "icon": "H"},
        "licencia":  {"label": "Licencia demo",          "color": "#dc2626", "icon": "L"},
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

# Alias legacy: `KEPSERVER_URL` era una constante. Ahora se resuelve dinamicamente
# desde el JSON de configuracion via `_kep.get_url()`.
def KEPSERVER_URL() -> str:  # noqa: N802
    return _kep.get_url()

# ------------------------------------------------------------
# Categoria del tag — clasificacion MANUAL elegida en la UI.
# ------------------------------------------------------------
# Antes el agrupado de la tabla se deducia del prefijo del nombre
# (RETO.PV.*, RETO.CRUDA.*, ...). Eso no sirve con nomenclaturas de
# planta como `PCS7.OS01.PU009_Velocidad_PV`, asi que ahora la
# categoria es un campo propio del tag que el usuario selecciona.
#
# IMPORTANTE: la categoria es metadato de presentacion/organizacion.
# El motor del SE sigue resolviendo que leer y escribir por nombre
# exacto de tag (TAG_TO_PV / TAG_TO_CRUDA / TAG_TO_LIM / SP_TO_TAG).
CATEGORIAS_TAG = ("pv", "cruda", "lim", "sp", "otro")

_PREFIJO_A_CATEGORIA = {
    "RETO.PV.":    "pv",
    "RETO.CRUDA.": "cruda",
    "RETO.LIM.":   "lim",
    "RETO.SP.":    "sp",
}


def inferir_categoria(name: str) -> str:
    """Categoria por prefijo. Solo se usa como fallback de migracion."""
    for prefijo, cat in _PREFIJO_A_CATEGORIA.items():
        if (name or "").startswith(prefijo):
            return cat
    return "otro"


def normalizar_categoria(value, name: str = "") -> str:
    """Valida la categoria recibida; si es invalida cae al fallback por prefijo."""
    cat = str(value or "").strip().lower()
    return cat if cat in CATEGORIAS_TAG else inferir_categoria(name)


# ------------------------------------------------------------
# Rol del SE — que variable concreta representa el tag.
# ------------------------------------------------------------
# La categoria dice QUE TIPO de senal es; el rol dice CUAL.
# `PCS7.OS01.PU009_Velocidad_SP` es categoria "sp", pero hay que
# saber si es sp_tonelaje, sp_floculante o sp_vel_bomba.
#
# El catalogo de roles NO se escribe aqui: se deriva de config.py,
# que es el contrato del proceso. Asi no hay dos fuentes de verdad.

def catalogo_roles() -> dict[str, list[str]]:
    """Roles validos por categoria, derivados del contrato en config.py."""
    from config import VARIABLES_PROCESO, VARIABLES_CRUDAS_REQUERIDAS, SETPOINT_KEYS

    lims = []
    for var in VARIABLES_PROCESO:
        lims.append(f"{var}_lmin")
        lims.append(f"{var}_lmax")

    return {
        "pv":    list(VARIABLES_PROCESO),
        "cruda": list(VARIABLES_CRUDAS_REQUERIDAS),
        "lim":   lims,
        "sp":    list(SETPOINT_KEYS),
        "otro":  [],
    }


def inferir_rol(name: str, categoria: str) -> str:
    """Deduce el rol desde el sufijo de un nombre `RETO.*` (solo migracion)."""
    validos = catalogo_roles().get(categoria, [])
    if not validos:
        return ""
    sufijo = (name or "").split(".")[-1]
    return sufijo if sufijo in validos else ""


def normalizar_rol(value, categoria: str) -> str:
    """Valida el rol contra el catalogo de su categoria. Vacio = sin asignar."""
    rol = str(value or "").strip()
    if categoria == "otro":
        return ""
    return rol if rol in catalogo_roles().get(categoria, []) else ""


def construir_mapeo(tags: list[dict] | None = None) -> dict:
    """Construye los mapeos tag<->rol que consume el motor, desde tags.json.

    Reemplaza a los diccionarios hardcodeados TAG_TO_PV / TAG_TO_CRUDA /
    TAG_TO_LIM / SP_TO_TAG, que solo servian con nomenclatura `RETO.*`.

    Devuelve tambien la cobertura (roles sin asignar y roles duplicados)
    para que la UI y el arranque del motor puedan validar antes de correr.
    """
    if tags is None:
        tags = _load_tags().get("tags", [])

    catalogo = catalogo_roles()
    tag_to_pv: dict[str, str] = {}
    tag_to_cruda: dict[str, str] = {}
    tag_to_lim: dict[str, tuple[str, str]] = {}
    sp_to_tag: dict[str, str] = {}

    asignados: dict[str, list[str]] = {}   # "categoria::rol" -> [tags]

    for t in tags:
        if not t.get("enabled", True):
            continue
        cat = t.get("categoria")
        rol = t.get("rol") or ""
        if cat not in catalogo or not rol or rol not in catalogo[cat]:
            continue

        nombre = t["name"]
        asignados.setdefault(f"{cat}::{rol}", []).append(nombre)

        if cat == "pv":
            tag_to_pv[nombre] = rol
        elif cat == "cruda":
            tag_to_cruda[nombre] = rol
        elif cat == "lim":
            var, bound = rol.rsplit("_", 1)      # "torque_lmin" -> ("torque", "lmin")
            tag_to_lim[nombre] = (var, bound)
        elif cat == "sp":
            sp_to_tag[rol] = nombre

    faltantes = {
        cat: [r for r in roles if f"{cat}::{r}" not in asignados]
        for cat, roles in catalogo.items() if roles
    }
    duplicados = {k: v for k, v in asignados.items() if len(v) > 1}

    return {
        "tag_to_pv":    tag_to_pv,
        "tag_to_cruda": tag_to_cruda,
        "tag_to_lim":   tag_to_lim,
        "sp_to_tag":    sp_to_tag,
        "faltantes":    faltantes,
        "duplicados":   duplicados,
        # El motor no puede correr sin todos los PV, sus LIM y los SP.
        # Las CRUDA degradan (afectan permisivos) pero no impiden arrancar.
        "listo": not faltantes.get("pv") and not faltantes.get("lim")
                 and not faltantes.get("sp") and not duplicados,
    }


def _load_tags() -> dict:
    if not os.path.exists(TAGS_JSON):
        return {"tags": [], "next_id": 1}
    with open(TAGS_JSON, encoding="utf-8") as f:
        data = json.load(f)

    # Migracion suave: los tags creados antes del selector no traen
    # `categoria`. Se les asigna una vez segun su prefijo actual y
    # queda persistida en el proximo _save_tags().
    for t in data.get("tags", []):
        if t.get("categoria") not in CATEGORIAS_TAG:
            t["categoria"] = inferir_categoria(t.get("name", ""))
        # Idem para el rol: se deduce del sufijo `RETO.*` una sola vez.
        if "rol" not in t:
            t["rol"] = inferir_rol(t.get("name", ""), t["categoria"])

    return data


def _save_tags(data: dict) -> None:
    with open(TAGS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================
# Licencia — helper compartido (guardas de escritura / edicion)
# ============================================================
# Fecha maxima duplicada aqui para evitar import circular con web/api/config.py
# Debe coincidir con LICENCIA_FECHA_MAXIMA en web/api/config.py.
_LICENCIA_FECHA_MAXIMA_ISO = "2026-10-12"

# Clave HMAC embebida en el binario. No es "cripto real" (esta en el codigo),
# pero previene edicion manual del JSON: cualquier cambio rompe la firma.
_LICENSE_SECRET = b"SE-HUTBAY-ESPESADOR-PROTOTIPO-2026-K7v3PqL2xMz9NwR-h"
_LICENSE_SIGNED_FIELDS = (
    "active", "months", "activated_at", "expires_at",
    "last_seen_at", "consumed_seconds", "duration_seconds",
)
# Tolerancia (segundos) para el retroceso del reloj. Cubre reajustes NTP normales.
_LICENSE_CLOCK_GRACE_SEC = 60
# Rate-limit para persistir el checkpoint (evita I/O en cada tick).
_LICENSE_PERSIST_MIN_INTERVAL_SEC = 30
_license_lock = threading.Lock()
_license_last_persist_ts = 0.0


def _license_sign(cfg: dict) -> str:
    payload = "|".join(str(cfg.get(k, "")) for k in _LICENSE_SIGNED_FIELDS)
    return hmac.new(_LICENSE_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _license_verify(cfg: dict) -> bool:
    stored = cfg.get("signature")
    if not stored:
        return False
    try:
        return hmac.compare_digest(str(stored), _license_sign(cfg))
    except (TypeError, ValueError):
        return False


def _license_now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _license_parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _license_check() -> dict:
    """Estado de licencia con checkpoint monotono, contador de uso y firma HMAC.

    Devuelve:
      {"valid": bool, "expired": bool, "tampered": bool, "reason": str,
       "expires_at": str|None, "remaining_seconds": float|None,
       "consumed_seconds": float|None, "duration_seconds": float|None,
       "last_seen_at": str|None}
    """
    global _license_last_persist_ts
    default = {
        "valid": False, "expired": False, "tampered": False,
        "reason": "Sin licencia activa", "expires_at": None,
        "remaining_seconds": None, "consumed_seconds": None,
        "duration_seconds": None, "last_seen_at": None,
    }

    with _license_lock:
        try:
            if not os.path.exists(LICENCIA_JSON):
                return default
            with open(LICENCIA_JSON, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return {**default, "reason": "Error leyendo licencia"}

        if not isinstance(cfg, dict) or not cfg.get("active"):
            return default

        expires_at_str = cfg.get("expires_at")
        activated_at_str = cfg.get("activated_at")
        if not expires_at_str or not activated_at_str:
            return {**default, "reason": "Licencia incompleta"}

        # Migracion de formato antiguo (sin firma): computar campos faltantes y firmar.
        if not cfg.get("signature"):
            months = cfg.get("months")
            if not cfg.get("duration_seconds") and isinstance(months, int) and months > 0:
                cfg["duration_seconds"] = float(months) * 30.0 * 86400.0
            if cfg.get("consumed_seconds") is None:
                cfg["consumed_seconds"] = 0.0
            if not cfg.get("last_seen_at"):
                cfg["last_seen_at"] = _license_now_iso()
            cfg["signature"] = _license_sign(cfg)
            try:
                with open(LICENCIA_JSON, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                _license_last_persist_ts = time.time()
            except OSError:
                pass

        # 1) Verificar firma HMAC (deteccion de edicion manual del JSON).
        if not _license_verify(cfg):
            return {
                **default, "tampered": True,
                "reason": "Firma de licencia invalida (archivo manipulado)",
                "expires_at": expires_at_str,
            }

        try:
            expires_dt = datetime.strptime(str(expires_at_str), "%Y-%m-%d").date()
        except ValueError:
            return {**default, "tampered": True, "reason": "Fecha invalida", "expires_at": expires_at_str}

        now = datetime.now()
        today = now.date()
        activated_dt = datetime.strptime(str(activated_at_str), "%Y-%m-%d")
        last_seen = _license_parse_dt(cfg.get("last_seen_at")) or activated_dt

        # 2) Detectar retroceso del reloj del sistema.
        if now < last_seen - timedelta(seconds=_LICENSE_CLOCK_GRACE_SEC):
            return {
                **default, "tampered": True,
                "reason": (
                    f"Reloj del sistema retrocedido: ultimo checkpoint "
                    f"{last_seen.isoformat()}, ahora {now.replace(microsecond=0).isoformat()}"
                ),
                "expires_at": expires_at_str,
                "last_seen_at": cfg.get("last_seen_at"),
            }

        # 3) Contador monotono de segundos consumidos.
        elapsed = max(0.0, (now - last_seen).total_seconds())
        consumed = float(cfg.get("consumed_seconds") or 0.0) + elapsed
        duration = float(cfg.get("duration_seconds") or 0.0)
        remaining = max(0.0, duration - consumed) if duration else None

        expired_by_date = expires_dt < today
        expired_by_usage = duration > 0 and consumed >= duration

        # 4) Actualizar checkpoint + refirmar. Persistir con rate-limit.
        cfg["last_seen_at"] = _license_now_iso()
        cfg["consumed_seconds"] = round(consumed, 1)
        cfg["signature"] = _license_sign(cfg)

        now_ts = time.time()
        if (now_ts - _license_last_persist_ts) >= _LICENSE_PERSIST_MIN_INTERVAL_SEC:
            try:
                with open(LICENCIA_JSON, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                _license_last_persist_ts = now_ts
            except OSError:
                pass

        if expired_by_date or expired_by_usage:
            reason = "Licencia demo expirada por fecha" if expired_by_date else "Licencia demo expirada por uso"
            return {
                "valid": False, "expired": True, "tampered": False,
                "reason": f"{reason} (expira {expires_at_str})",
                "expires_at": expires_at_str, "remaining_seconds": 0.0,
                "consumed_seconds": consumed, "duration_seconds": duration,
                "last_seen_at": cfg["last_seen_at"],
            }

        return {
            "valid": True, "expired": False, "tampered": False,
            "reason": "", "expires_at": expires_at_str,
            "remaining_seconds": remaining, "consumed_seconds": consumed,
            "duration_seconds": duration, "last_seen_at": cfg["last_seen_at"],
        }


def _license_sign_and_save(cfg: dict) -> dict:
    """Firma cfg y lo persiste. Usado por el flujo de activacion."""
    global _license_last_persist_ts
    with _license_lock:
        cfg["signature"] = _license_sign(cfg)
        with open(LICENCIA_JSON, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        _license_last_persist_ts = time.time()
    return cfg


def _license_is_valid() -> bool:
    return _license_check()["valid"]


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
# 3600 muestras @ 5s = 5 horas de datos. Cubre con margen la ventana
# máxima de 3h del Explorador de Series (/espesador/graficos).
_TAG_HISTORY_SIZE = 3600
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

    # Categorias que participan del pipeline del SE (las unicas simulables).
    CATEGORIAS_SIMULABLES = ("pv", "cruda", "lim", "sp")

    def sincronizar_con_tags(self) -> None:
        """Espeja exactamente la tabla de tags. Sin ruido.

        - Aparece: tag habilitado con categoria PV/CRUDA/LIM/SP.
          Entra con `enabled=False` (opt-in) para no pisar por accidente
          una señal que ya viene de planta.
        - Desaparece: tag eliminado, suspendido, o movido a categoria OTRO.
        - Se conserva lo que ya configuraste (min/max/ruido/enabled).
        """
        try:
            tags = _load_tags().get("tags", [])
        except Exception:
            return

        vigentes = {
            t["name"]: t for t in tags
            if t.get("name")
            and t.get("enabled", True)
            and t.get("categoria") in self.CATEGORIAS_SIMULABLES
        }

        # Fuera lo que ya no corresponde (borrado, suspendido o pasado a OTRO).
        for nombre in [n for n in self._ranges if n not in vigentes]:
            del self._ranges[nombre]

        # Alta de los nuevos, desactivados.
        for nombre, t in vigentes.items():
            if nombre in self._ranges:
                self._ranges[nombre].setdefault("enabled", True)
                self._ranges[nombre]["categoria"] = t.get("categoria")
                continue
            es_lim = t.get("categoria") == "lim"
            self._ranges[nombre] = {
                "min": 0.0,
                "max": 0.0 if es_lim else 100.0,
                "noise": 0.0 if es_lim else 1.0,
                "enabled": False,
                "categoria": t.get("categoria"),
            }

    def _write_tick(self):
        tags_to_write = {}
        for tag_name, cfg in self._ranges.items():
            if not cfg.get("enabled", True):
                continue          # desactivado: lo alimenta la planta, no el generador
            val = _tres_fases_valor(
                self._tick, self._n_ciclo,
                cfg["min"], cfg["max"], cfg.get("noise", 0)
            )
            tags_to_write[tag_name] = val
            self._last_values[tag_name] = val

        if not tags_to_write:
            self._last_error = "Ningun tag habilitado para simular."
            return

        _record_tag_values(tags_to_write)

        lic = _license_check()
        if not lic["valid"]:
            self._last_error = f"Generador bloqueado: {lic['reason']}"
            _alerts.add("licencia", f"Generador bloqueado: {lic['reason']}")
            return

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
                self._ranges[tag_name].setdefault("enabled", True)
        self._save_config()

    def status(self) -> dict:
        # Refresca la lista cada vez que la UI consulta: si diste de alta un
        # tag nuevo, aparece aqui solo (desactivado) sin tocar el contenedor.
        self.sincronizar_con_tags()
        habilitados = sum(1 for c in self._ranges.values() if c.get("enabled", True))
        return {
            "running":     self._running,
            "tick":        self._tick,
            "intervalo_s": self._intervalo_s,
            "intervalo_ms": int(round(self._intervalo_s * 1000)),
            "n_ciclo":     self._n_ciclo,
            "ranges":      self._ranges,
            "habilitados": habilitados,
            "total":       len(self._ranges),
            "last_values": self._last_values,
            "last_error":  self._last_error,
        }


_tag_generator = TagGenerator()


# ============================================================
# HeartbeatManager — pulso periodico via dos tags KEPserver
# ============================================================
#
# Escribe un valor alternante (value_a <-> value_b) al tag OUT cada
# `intervalo_s` segundos y lee el tag IN para verificar el "eco". Si el
# valor leido coincide con el ultimo escrito, se considera que el sistema
# remoto (PLC / KEPserver) esta vivo. La configuracion se persiste en
# tags.json bajo la clave "heartbeat".

HEARTBEAT_DEFAULTS = {
    "enabled":     False,
    "tag_out":     "RETO.HB.OUT",
    "tag_in":      "RETO.HB.IN",
    "intervalo_s": 2.0,
    "value_a":     0.0,
    "value_b":     1.0,
    "data_type":   "Float",
}


class HeartbeatManager:
    """Envia un pulso alternante a un tag OUT y verifica el eco en un tag IN."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._tick = 0
        self._cfg: dict = dict(HEARTBEAT_DEFAULTS)
        self._last_out = None
        self._last_in = None
        self._last_write_ok = False
        self._last_echo_ok = False
        self._last_error: str | None = None
        self._last_ts: float | None = None
        self._load_config()

    def _load_config(self):
        store = _load_tags()
        cfg = store.get("heartbeat") or {}
        merged = dict(HEARTBEAT_DEFAULTS)
        merged.update(cfg)
        self._cfg = merged

    def _save_config(self):
        store = _load_tags()
        store["heartbeat"] = self._cfg
        _save_tags(store)

    def _next_value(self):
        return self._cfg["value_a"] if (self._tick % 2 == 0) else self._cfg["value_b"]

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                lic = _license_check()
                if not lic["valid"]:
                    self._last_error = f"Heartbeat bloqueado: {lic['reason']}"
                    self._last_write_ok = False
                    self._last_echo_ok = False
                    _alerts.add("licencia", f"Heartbeat bloqueado: {lic['reason']}")
                    self._last_ts = time.time()
                    self._tick += 1
                    self._stop_event.wait(max(0.2, float(self._cfg.get("intervalo_s", 2.0))))
                    continue

                val_out = self._next_value()
                dtype = self._cfg.get("data_type", "Float")
                res = _kep.write_tag(self._cfg["tag_out"], val_out, dtype)
                self._last_out = val_out
                self._last_write_ok = bool(res.get("ok"))
                if not self._last_write_ok:
                    self._last_error = res.get("error") or "Escritura fallida"
                    _alerts.add("heartbeat", f"HB write: {self._last_error}")
                else:
                    _alerts.resolve_category("heartbeat")

                read = _kep.read_tags_batch([self._cfg["tag_in"]])
                info = read.get(self._cfg["tag_in"], {}) or {}
                self._last_in = info.get("value")
                try:
                    self._last_echo_ok = bool(
                        info.get("connected") and info.get("exists")
                        and self._last_in is not None
                        and float(self._last_in) == float(val_out)
                    )
                except Exception:
                    self._last_echo_ok = False

                if self._last_write_ok:
                    self._last_error = None
                self._last_ts = time.time()
            except Exception as e:
                self._last_error = str(e)
                _alerts.add("heartbeat", str(e), traceback.format_exc())
            self._tick += 1
            self._stop_event.wait(max(0.2, float(self._cfg.get("intervalo_s", 2.0))))

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
        # Persistir el flag para reflejar el estado deseado
        self._cfg["enabled"] = True
        self._save_config()

    def stop(self):
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._running = False
        self._thread = None
        self._cfg["enabled"] = False
        self._save_config()

    def update_config(self, **kwargs):
        allowed = ("tag_out", "tag_in", "intervalo_s", "value_a", "value_b", "data_type")
        for k, v in kwargs.items():
            if k not in allowed or v is None:
                continue
            if k == "intervalo_s":
                self._cfg[k] = max(0.2, float(v))
            elif k in ("value_a", "value_b"):
                try:
                    self._cfg[k] = float(v)
                except (TypeError, ValueError):
                    pass
            elif k == "data_type":
                if v in ("Float", "Int", "Boolean", "String"):
                    self._cfg[k] = v
            else:
                s = str(v).strip()
                if s:
                    self._cfg[k] = s
        self._save_config()

    def status(self) -> dict:
        return {
            "running":       self._running,
            "tick":          self._tick,
            "config":        self._cfg,
            "last_out":      self._last_out,
            "last_in":       self._last_in,
            "last_write_ok": self._last_write_ok,
            "last_echo_ok":  self._last_echo_ok,
            "last_error":    self._last_error,
            "last_ts":       self._last_ts,
        }


_heartbeat = HeartbeatManager()


# ============================================================
# SEEngine — tag mapping constants
# ============================================================

# ------------------------------------------------------------
# DEPRECADO: mapeo fijo con nomenclatura `RETO.*`.
# ------------------------------------------------------------
# El motor ya no usa estos diccionarios: resuelve tag<->rol desde
# tags.json via construir_mapeo(), para soportar cualquier
# nomenclatura de planta (PCS7.OS01.*, etc.).
# Se conservan como referencia del contrato y para tests.
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
# Traza del pipeline — observabilidad por tick
# ------------------------------------------------------------
# Registra que paso en cada etapa (lectura -> mapeo -> filtro ->
# derivadas -> fuzzy -> permisivos -> reglas -> defuzzy -> escritura)
# para la pagina /espesador/traza.
#
# Es puramente de lectura: no cambia ninguna decision del SE.
# ============================================================
_TRAZA_SIZE = 60
_trazas: deque = deque(maxlen=_TRAZA_SIZE)
_traza_lock = threading.Lock()


def _nueva_traza(tick: int, t_s: float, mapeo: dict) -> dict:
    return {
        "tick": tick,
        "t_s": round(float(t_s), 2),
        "ts_wall": time.time(),
        "abortado_en": None,
        "motivo_aborto": None,
        "cobertura": {
            "faltantes": mapeo.get("faltantes", {}),
            "duplicados": list(mapeo.get("duplicados", {}).keys()),
            "listo": mapeo.get("listo", False),
        },
        "lectura": [], "filtro": [], "limites": {}, "derivadas": {},
        "fuzzy": [], "permisivos": {}, "reglas": [], "waits_activos": [],
        "disparadas": [], "defuzzy": [], "escritura": {},
    }


def _traza_lectura(mapeo: dict, live: dict, fallas: dict) -> list[dict]:
    """Una fila por tag mapeado: valor, calidad y si fallo."""
    fallidos = {f["tag"] for grupo in fallas.values() for f in grupo}
    filas = []

    def _add(tag, cat, rol):
        info = live.get(tag, {})
        filas.append({
            "tag": tag, "categoria": cat, "rol": rol,
            "valor": info.get("value"),
            "quality": info.get("quality", "Unknown"),
            "ok": tag not in fallidos,
        })

    for tag, rol in mapeo.get("tag_to_pv", {}).items():
        _add(tag, "pv", rol)
    for tag, rol in mapeo.get("tag_to_cruda", {}).items():
        _add(tag, "cruda", rol)
    for tag, (var, bound) in mapeo.get("tag_to_lim", {}).items():
        _add(tag, "lim", f"{var}_{bound}")
    return filas


def _traza_fuzzy(fuzzy_out: dict, inputs: dict, limites: dict) -> list[dict]:
    """Estado fuzzy por variable: valor, limites usados, dominio y pertenencias."""
    filas = []
    for var, meta in (fuzzy_out or {}).items():
        if not isinstance(meta, dict):
            continue
        lim = limites.get(var, {})
        filas.append({
            "var": var,
            "es_pendiente": str(var).startswith("pend_"),
            "valor": (round(float(inputs[var]), 4) if var in inputs else None),
            "lmin": lim.get("lmin"),
            "lmax": lim.get("lmax"),
            "dom": meta.get("dom"),
            "pert": {str(k): round(float(v), 3)
                     for k, v in (meta.get("pert") or {}).items() if float(v) > 0},
        })
    return sorted(filas, key=lambda f: (f["es_pendiente"], f["var"]))


def _traza_waits(estado_waits: dict, t_s: float) -> list[dict]:
    """Waits actualmente activos y cuanto les falta para liberar."""
    activos = []
    for wid, est in (estado_waits or {}).items():
        dur = float(est.get("duracion_s", 0.0))
        restante = dur - (float(t_s) - float(est.get("t_ultima_activacion_s", -1e18)))
        if restante > 0:
            activos.append({
                "wait_id": wid,
                "variable_controlada": est.get("variable_controlada"),
                "regla_id": est.get("regla_id"),
                "duracion_s": dur,
                "restante_s": round(restante, 1),
            })
    return sorted(activos, key=lambda w: -w["restante_s"])


def _traza_push(tz: dict) -> None:
    with _traza_lock:
        _trazas.append(tz)


def _get_trazas(n: int = 1) -> list[dict]:
    with _traza_lock:
        datos = list(_trazas)
    return datos[-int(n):] if n > 0 else datos


def _reset_trazas() -> None:
    with _traza_lock:
        _trazas.clear()


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

        # Mapeo tag<->rol resuelto al arrancar (ver construir_mapeo)
        self._mapeo: dict = {"tag_to_pv": {}, "tag_to_cruda": {},
                             "tag_to_lim": {}, "sp_to_tag": {}}
        self._last_read: dict = {}
        self._last_fallas: dict = {"pv": [], "cruda": [], "lim": []}

    def _init_state(self):
        from simulacion import SETPOINTS_BASE, LIMITES_SP
        from core.filters.exp_q import ExpQFilter, CONFIG_FILTRO_ESPESADOR_DEFAULT
        from runner import cargar_reglas_json, cargar_permisivos_json
        import copy

        self._mapeo = construir_mapeo()
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

        try:
            from web.api.postgres import ensure_tables
            ensure_tables("espesadores")
        except Exception:
            pass

    def _read_tags(self) -> tuple[dict, dict, dict] | None:
        """Lee PV, CRUDA y LIM tags del KEPserver. Devuelve (inputs, crudas, limites) o None."""
        TAG_TO_PV    = self._mapeo["tag_to_pv"]
        TAG_TO_CRUDA = self._mapeo["tag_to_cruda"]
        TAG_TO_LIM   = self._mapeo["tag_to_lim"]

        all_tags = list(TAG_TO_PV.keys()) + list(TAG_TO_CRUDA.keys()) + list(TAG_TO_LIM.keys())
        live = _read_kepserver_tags_batch(all_tags)
        self._last_read = live          # lo consume la traza

        # Registro de fallas por rol — lo consume la traza para explicar
        # exactamente que falto, en vez de un "no se pudo leer" generico.
        fallas = {"pv": [], "cruda": [], "lim": []}

        def _ok(info):
            return info.get("exists") and info.get("value") is not None

        inputs = {}
        for tag, var in TAG_TO_PV.items():
            info = live.get(tag, {})
            if _ok(info):
                inputs[var] = float(info["value"])
            else:
                fallas["pv"].append({"rol": var, "tag": tag,
                                     "quality": info.get("quality", "Unknown")})

        crudas = {}
        for tag, var in TAG_TO_CRUDA.items():
            info = live.get(tag, {})
            if _ok(info):
                crudas[var] = float(info["value"])
            else:
                crudas[var] = 0.0
                fallas["cruda"].append({"rol": var, "tag": tag,
                                        "quality": info.get("quality", "Unknown")})

        limites = {}
        for tag, (var, bound) in TAG_TO_LIM.items():
            info = live.get(tag, {})
            if var not in limites:
                limites[var] = {}
            if _ok(info):
                limites[var][bound] = float(info["value"])
            else:
                limites[var][bound] = 0.0
                fallas["lim"].append({"rol": f"{var}_{bound}", "tag": tag,
                                      "quality": info.get("quality", "Unknown")})

        self._last_fallas = fallas

        # Sin todos los PV el pipeline no es evaluable: se aborta el tick.
        # (comportamiento existente; ahora queda registrado el detalle)
        if fallas["pv"]:
            return None

        return inputs, crudas, limites

    def _write_setpoints(self):
        """Escribe setpoints actuales al KEPserver (IT-8: OPC-UA → connectors/kepserver.py)."""
        sp_vals = {tag: float(self._setpoints.get(sp_key, 0.0))
                   for sp_key, tag in self._mapeo["sp_to_tag"].items()}
        _record_tag_values(sp_vals)
        lic = _license_check()
        if not lic["valid"]:
            self._last_error = f"SE bloqueado: {lic['reason']}"
            _alerts.add("licencia", f"Escritura SP bloqueada: {lic['reason']}")
            return
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

        # --- Traza del tick (observabilidad; no altera ninguna decision) ---
        tz = _nueva_traza(self._tick, self._t_s, self._mapeo)

        data = self._read_tags()
        tz["lectura"] = _traza_lectura(self._mapeo, self._last_read, self._last_fallas)

        if data is None:
            faltan = ", ".join(f["rol"] for f in self._last_fallas.get("pv", []))
            self._last_error = f"PV no legibles: {faltan}"
            _alerts.add("kep", f"No se pudieron leer los PV tags del KEPserver: {faltan}")
            tz["abortado_en"] = "lectura"
            tz["motivo_aborto"] = f"PV no legibles: {faltan}"
            _traza_push(tz)
            return

        inputs_raw, crudas, limites = data
        inputs = self._filtro.actualizar(inputs_raw)
        self._t_s += self._intervalo_s

        tz["filtro"] = [
            {"rol": v, "crudo": round(float(inputs_raw[v]), 4),
             "filtrado": round(float(inputs.get(v, inputs_raw[v])), 4)}
            for v in sorted(inputs_raw)
        ]
        tz["limites"] = {v: {b: round(float(x), 4) for b, x in bounds.items()}
                         for v, bounds in limites.items()}

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

        tz["derivadas"] = {k: (round(float(v), 4) if isinstance(v, (int, float)) else None)
                           for k, v in row_data.items()
                           if k not in inputs and k not in crudas and k != "t_s"}
        tz["fuzzy"] = _traza_fuzzy(fuzzy_out, inputs, limites)

        estados_perm = evaluar_permisivos(
            self._permisivos_config,
            fuzzy_out=fuzzy_out,
            row=row,
            inputs=inputs,
            setpoints=self._setpoints,
            columnas_entrada=COLUMNAS_ENTRADA,
        )
        fuzzy_out = inyectar_permisivos_en_fuzzy_out(fuzzy_out, estados_perm)
        tz["permisivos"] = {str(k): str(v) for k, v in (estados_perm or {}).items()}

        sp_antes = dict(self._setpoints)

        motor_out = motor_mod.evaluar_reglas(
            self._reglas, fuzzy_out, self._t_s,
            self._last_action_time, min_belief=0.05
        )

        self._last_action_time = motor_out.get("last_action_time", self._last_action_time)
        # Por que NO dispararon las demas reglas (reporte aditivo del motor)
        tz["reglas"] = motor_out.get("evaluadas", [])
        tz["waits_activos"] = _traza_waits(self._last_action_time, self._t_s)

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

        tz["disparadas"] = tick_events
        tz["defuzzy"] = [
            {"sp": k,
             "antes":   round(float(sp_antes.get(k, 0.0)), 4),
             "despues": round(float(v), 4),
             "delta":   round(float(v) - float(sp_antes.get(k, 0.0)), 4),
             "limites": list(self._limites_sp.get(k, (None, None))),
             "tag":     self._mapeo["sp_to_tag"].get(k)}
            for k, v in self._setpoints.items()
        ]

        self._write_setpoints()
        tz["escritura"] = {"error": self._last_error}
        _traza_push(tz)

        try:
            from web.api.postgres import persist_tick
            entrada_vals = {}
            for tag, var in self._mapeo["tag_to_pv"].items():
                if var in inputs_raw:
                    entrada_vals[tag] = inputs_raw[var]
            for tag, var in self._mapeo["tag_to_cruda"].items():
                if var in crudas:
                    entrada_vals[tag] = crudas[var]
            for tag, (var, bound) in self._mapeo["tag_to_lim"].items():
                if var in limites and bound in limites[var]:
                    entrada_vals[tag] = limites[var][bound]
            salida_vals = {tag: float(self._setpoints.get(sp_key, 0.0))
                           for sp_key, tag in self._mapeo["sp_to_tag"].items()}
            store = _load_tags()
            tag_meta = {}
            for t in store.get("tags", []):
                tag_meta[t["name"]] = {
                    "pseudonimo": t.get("pseudonimo"),
                    "instrumento": t.get("instrumento"),
                    "unidad_ing": t.get("unidad_ing"),
                    "equipo": t.get("equipo"),
                }
            persist_tick(entrada_vals, salida_vals, tag_meta, "espesadores")
        except Exception as e:
            _alerts.add("general", f"PostgreSQL: {e}")

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

    def start(self, intervalo_s: float = 5.0) -> dict:
        """Arranca el motor. Devuelve {"ok": bool, "error": str|None}.

        No arranca si el mapeo tag<->rol esta incompleto: correr con roles
        sin asignar produce fuzzificacion sobre limites en 0.0 y reglas que
        disparan sobre datos inventados. Mejor negarse y decir que falta.
        """
        if self._running:
            return {"ok": True, "error": None}

        mapeo = construir_mapeo()
        if not mapeo["listo"]:
            detalle = []
            for cat in ("pv", "lim", "sp"):
                if mapeo["faltantes"].get(cat):
                    detalle.append(f"{cat.upper()} sin mapear: "
                                   + ", ".join(mapeo["faltantes"][cat]))
            if mapeo["duplicados"]:
                detalle.append("roles duplicados: "
                               + ", ".join(mapeo["duplicados"].keys()))
            msg = "Mapeo de tags incompleto. " + " | ".join(detalle)
            self._last_error = msg
            _alerts.add("se_engine", msg)
            return {"ok": False, "error": msg}

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
POSTGRES_PAGE   = _load_template("postgres.html")
GRAFICOS_PAGE   = _load_template("graficos.html")
TRAZA_PAGE      = _load_template("traza.html")

# CHART_VARS se mantiene por compatibilidad (usado por otros modulos),
# pero el nuevo Explorador de Series construye sus datasets desde el
# catalogo de tags (/api/entrada) — no depende de esta lista.
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
            _alerts.add("kep", f"KEPserver no accesible en {_kep.get_url()}", err)

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
