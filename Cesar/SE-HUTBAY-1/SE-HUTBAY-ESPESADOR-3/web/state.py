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
from core.filters.exp_q import CONFIG_FILTRO_ESPESADOR_DEFAULT
from core.jsonio import escribir_json_atomico
from fuzzys_models_espesador import FUZZY_MODELOS
from calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS
from permisivos import nombre_variable_permisivo
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
PENDIENTES_JSON   = os.path.join(_CFG_DIR, "pendientes.json")
ACTIVITY_LOG_JSON = os.path.join(_CFG_DIR, "activity_log.json")

# Sintonizacion con la que nace una PV que todavia no tiene filtro. Vive aqui
# (y no en web/api/config.py) porque ahora la usan los dos: el boton
# Sincronizar de la pagina de Filtros y el arranque del motor, que ya no se
# niega a correr por una PV sin filtro y le siembra este default.
def _leer_json(ruta: str, default):
    """Lee un JSON de configuracion sin caer a ninguna plantilla.

    A diferencia de los `cargar_*_json()` de runner.py, aqui un archivo que
    falta significa "no hay nada configurado", no "usa la plantilla del
    espesador". Esto se usa solo para decidir a quien avisar, y sembrar
    variables de otra planta produciria avisos fantasma.
    """
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return default
    if datos is None or not isinstance(datos, type(default)):
        return default
    return datos


FILTRO_NUEVO_DEFAULT = {"q": 0.15, "ventana_s": 50.0}


def _guardar_filtros_sembrados(cfg: dict) -> None:
    """Persiste filtros.json tras sembrar defaults. No debe tumbar el arranque.

    Si el archivo no se puede escribir (permisos, disco lleno) el motor sigue
    con la config en memoria: el filtro queda bien para esta corrida y la
    siguiente vuelve a sembrarlo.
    """
    try:
        escribir_json_atomico(FILTROS_JSON, cfg)
    except OSError as e:
        _alerts.add("config", f"No se pudo guardar filtros.json: {e}")


# ============================================================
# Sistema de alertas centralizadas
# ============================================================

class AlertCollector:
    """Collects and deduplicates system alerts from multiple sources."""

    CATEGORIES = {
        "import":    {"label": "Error de Import",       "color": "#ef4444", "icon": "!"},
        "kep":       {"label": "Conexion KEPserver",    "color": "#f97316", "icon": "K"},
        # Categoria aparte de "kep" A PROPOSITO. Un tick que termina bien prueba
        # que la CONEXION anda, y por eso `_run_tick` cierra "kep" al final de
        # cada tick sano. No prueba nada sobre los INSTRUMENTOS: una PV en Bad no
        # impide que el tick termine bien. Con las dos cosas en la misma
        # categoria, el aviso de calidad se auto-borraba en el mismo tick que lo
        # creaba y no llegaba nunca a la pantalla.
        "calidad":   {"label": "Calidad de dato",       "color": "#eab308", "icon": "Q"},
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
# Log de actividad de configuracion (UI / operador)
# ============================================================

class ActivityLogCollector:
    """Historial de eventos de configuracion: sin guardar, guardados, reinicio, errores.

    Solo observabilidad — no altera el pipeline. El anillo conserva como maximo
    MAX_HISTORY entradas; las de tipo ``saved`` expiran solas a los TRANSIENT_S.

    Con ``storage_path`` el anillo y los avisos fijados (sin guardar / reinicio)
    se persisten en disco y sobreviven al reinicio del proceso Flask.
    """

    KINDS = {
        "unsaved": {"label": "Sin guardar",        "color": "#f59e0b", "icon": "U"},
        "saved":   {"label": "Guardado",           "color": "#22c55e", "icon": "G"},
        "restart": {"label": "Reinicio requerido", "color": "#a855f7", "icon": "R"},
        "error":   {"label": "Error",              "color": "#ef4444", "icon": "E"},
        "info":    {"label": "Sistema",            "color": "#38bdf8", "icon": "i"},
    }
    MAX_HISTORY = 100
    TRANSIENT_S = 5.0
    _STORE_VERSION = 1

    def __init__(self, storage_path: str | None = None):
        self._storage_path = storage_path
        self._entries: list[dict] = []
        self._unsaved: dict[str, int] = {}
        self._restart: dict[str, int] = {}
        # Problemas de configuracion VIGENTES, uno por clave. A diferencia de
        # log_error(), que apila un evento por cada vez que ocurre, un issue es
        # un estado: se actualiza mientras el problema siga y se resuelve solo
        # cuando deja de estar. Sin esto, un desfase que se revisa en cada
        # arranque y en cada guardado llenaria el panel de copias identicas.
        self._issues: dict[str, int] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        if self._storage_path:
            with self._lock:
                self._load_from_disk_unlocked()

    def _now(self) -> float:
        return time.time()

    def _normalize_entry(self, raw: dict) -> dict | None:
        if not isinstance(raw, dict):
            return None
        try:
            eid = int(raw.get("id"))
        except (TypeError, ValueError):
            return None
        kind = str(raw.get("kind") or "")
        if kind not in self.KINDS:
            return None
        ts = raw.get("ts")
        last_seen = raw.get("last_seen", ts)
        try:
            ts_f = float(ts)
            last_seen_f = float(last_seen)
        except (TypeError, ValueError):
            return None
        exp = raw.get("expires_at")
        if exp is not None:
            try:
                exp = float(exp)
            except (TypeError, ValueError):
                exp = None
        return {
            "id": eid,
            "kind": kind,
            "page": str(raw.get("page") or ""),
            "page_label": str(raw.get("page_label") or ""),
            "message": str(raw.get("message") or ""),
            "detail": str(raw.get("detail") or ""),
            "ts": ts_f,
            "last_seen": last_seen_f,
            "expires_at": exp,
            "pinned": bool(raw.get("pinned")),
            "dismissed": bool(raw.get("dismissed")),
        }

    def _load_from_disk_unlocked(self):
        raw = _leer_json(self._storage_path, {})
        if not isinstance(raw, dict):
            return
        entries_in = raw.get("entries")
        if not isinstance(entries_in, list):
            return
        loaded: list[dict] = []
        for item in entries_in:
            norm = self._normalize_entry(item)
            if norm is not None:
                loaded.append(norm)
        self._entries = loaded
        try:
            self._next_id = max(1, int(raw.get("next_id") or 1))
        except (TypeError, ValueError):
            self._next_id = 1
        if self._entries:
            self._next_id = max(self._next_id, max(e["id"] for e in self._entries) + 1)
        unsaved_in = raw.get("unsaved") if isinstance(raw.get("unsaved"), dict) else {}
        restart_in = raw.get("restart") if isinstance(raw.get("restart"), dict) else {}
        ids_vivos = {e["id"] for e in self._entries if not e["dismissed"]}
        self._unsaved = {}
        for k, v in unsaved_in.items():
            try:
                eid = int(v)
            except (TypeError, ValueError):
                continue
            if eid in ids_vivos:
                self._unsaved[str(k)] = eid
        self._restart = {}
        for k, v in restart_in.items():
            try:
                eid = int(v)
            except (TypeError, ValueError):
                continue
            if eid in ids_vivos:
                self._restart[str(k)] = eid
        issues_in = raw.get("issues") if isinstance(raw.get("issues"), dict) else {}
        self._issues = {}
        for k, v in issues_in.items():
            try:
                eid = int(v)
            except (TypeError, ValueError):
                continue
            if eid in ids_vivos:
                self._issues[str(k)] = eid
        self._trim()

    def _persist_unlocked(self):
        if not self._storage_path:
            return
        payload = {
            "version": self._STORE_VERSION,
            "next_id": self._next_id,
            "unsaved": self._unsaved,
            "restart": self._restart,
            "issues": self._issues,
            "entries": self._entries,
        }
        try:
            escribir_json_atomico(self._storage_path, payload)
        except OSError:
            pass

    def _trim(self):
        if len(self._entries) > self.MAX_HISTORY:
            drop = len(self._entries) - self.MAX_HISTORY
            dropped_ids = {e["id"] for e in self._entries[:drop]}
            self._entries = self._entries[drop:]
            for d in (self._unsaved, self._restart, self._issues):
                for k, eid in list(d.items()):
                    if eid in dropped_ids:
                        del d[k]

    def _make(self, kind: str, page: str, page_label: str,
              message: str, detail: str = "",
              transient: bool = False, pinned: bool = False) -> dict:
        now = self._now()
        entry = {
            "id": self._next_id,
            "kind": kind,
            "page": page,
            "page_label": page_label,
            "message": message,
            "detail": detail,
            "ts": now,
            "last_seen": now,
            "expires_at": (now + self.TRANSIENT_S) if transient else None,
            "pinned": pinned,
            "dismissed": False,
        }
        self._next_id += 1
        self._entries.append(entry)
        self._trim()
        self._persist_unlocked()
        return entry

    def _resolve_entry_unlocked(self, entry_id: int):
        for e in self._entries:
            if e["id"] == entry_id:
                e["dismissed"] = True
                return

    def mark_unsaved(self, page: str, page_label: str,
                     message: str = "", detail: str = ""):
        if not message:
            message = f"Cambios sin guardar en {page_label}"
        with self._lock:
            if page in self._unsaved:
                eid = self._unsaved[page]
                for e in self._entries:
                    if e["id"] == eid:
                        e["message"] = message
                        e["detail"] = detail
                        e["last_seen"] = self._now()
                        e["dismissed"] = False
                        self._persist_unlocked()
                        return
            entry = self._make("unsaved", page, page_label, message, detail,
                               pinned=True)
            self._unsaved[page] = entry["id"]
            self._persist_unlocked()

    def clear_unsaved(self, page: str):
        with self._lock:
            eid = self._unsaved.pop(page, None)
            if eid is not None:
                self._resolve_entry_unlocked(eid)
            self._persist_unlocked()

    def log_saved(self, page: str, page_label: str,
                  message: str = "", detail: str = ""):
        if not message:
            message = f"Guardado en {page_label}"
        with self._lock:
            eid = self._unsaved.pop(page, None)
            if eid is not None:
                self._resolve_entry_unlocked(eid)
            self._make("saved", page, page_label, message, detail, transient=True)

    def log_restart_needed(self, page: str, page_label: str,
                           message: str = "", detail: str = ""):
        if not message:
            message = (f"Cambios en {page_label} guardados: reinicia el motor "
                       "para aplicarlos")
        with self._lock:
            if page in self._restart:
                eid = self._restart[page]
                for e in self._entries:
                    if e["id"] == eid:
                        e["message"] = message
                        e["detail"] = detail
                        e["last_seen"] = self._now()
                        e["dismissed"] = False
                        self._persist_unlocked()
                        return
            entry = self._make("restart", page, page_label, message, detail,
                               pinned=True)
            self._restart[page] = entry["id"]
            self._persist_unlocked()

    def clear_restart(self, page: str | None = None):
        with self._lock:
            if page is None:
                for eid in self._restart.values():
                    self._resolve_entry_unlocked(eid)
                self._restart.clear()
            else:
                eid = self._restart.pop(page, None)
                if eid is not None:
                    self._resolve_entry_unlocked(eid)
            self._persist_unlocked()

    def log_error(self, page: str, page_label: str,
                  message: str, detail: str = ""):
        with self._lock:
            self._make("error", page, page_label, message, detail, pinned=True)

    def log_issue(self, key: str, page: str, page_label: str,
                  message: str, detail: str = ""):
        """Alerta roja de un problema VIGENTE, una sola por `key`.

        Si ya hay una abierta con esa clave se actualiza en su lugar (y se
        vuelve a mostrar si el operador la habia descartado, porque el
        problema sigue). `clear_issue` es la contraparte: se llama cuando la
        revision encuentra que ya no pasa.
        """
        with self._lock:
            eid = self._issues.get(key)
            if eid is not None:
                for e in self._entries:
                    if e["id"] == eid:
                        e["message"] = message
                        e["detail"] = detail
                        e["page"] = page
                        e["page_label"] = page_label
                        e["last_seen"] = self._now()
                        e["dismissed"] = False
                        self._persist_unlocked()
                        return
            entry = self._make("error", page, page_label, message, detail,
                               pinned=True)
            self._issues[key] = entry["id"]
            self._persist_unlocked()

    def clear_issue(self, key: str):
        with self._lock:
            eid = self._issues.pop(key, None)
            if eid is not None:
                self._resolve_entry_unlocked(eid)
                self._persist_unlocked()

    def issues_abiertos(self) -> list[str]:
        with self._lock:
            return sorted(self._issues)

    def log_info(self, message: str, detail: str = "",
                 page: str = "", page_label: str = "Sistema"):
        with self._lock:
            self._make("info", page, page_label, message, detail, transient=True)

    def dismiss(self, entry_id: int) -> bool:
        with self._lock:
            for e in self._entries:
                if e["id"] == entry_id and not e["dismissed"]:
                    e["dismissed"] = True
                    for d in (self._unsaved, self._restart, self._issues):
                        for k, eid in list(d.items()):
                            if eid == entry_id:
                                del d[k]
                    self._persist_unlocked()
                    return True
            return False

    def _is_active(self, e: dict) -> bool:
        if e["dismissed"]:
            return False
        if e["kind"] == "saved" or e["kind"] == "info":
            exp = e.get("expires_at")
            if exp is not None and self._now() > exp:
                return False
        return True

    def get_active(self) -> list[dict]:
        with self._lock:
            out = [dict(e) for e in self._entries if self._is_active(e)]
            out.sort(key=lambda x: x["last_seen"], reverse=True)
            return out

    def get_history(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            out = [dict(e) for e in self._entries]
            if kind:
                out = [e for e in out if e["kind"] == kind]
            out.sort(key=lambda x: x["ts"], reverse=True)
            return out[: self.MAX_HISTORY]


_activity_log = ActivityLogCollector(ACTIVITY_LOG_JSON)


# ============================================================
# Catálogos
# ============================================================

BLOQUES_DISPONIBLES = ["critico", "estabilidad", "optimizacion"]

# Etiquetas que el nucleo conoce siempre, sin importar como este configurado
# el fuzzy: las de pendiente, las de permisivo y las derivadas.
ETIQUETAS_BASE = [
    "LOW", "OK", "HIGH",
    "NO-LOW", "NO-OK", "NO-HIGH",
    "CERCA_BAJO", "CERCA_ALTO",
    "INC", "DEC", "STABLE",
    "NO-INC", "NO-DEC",
    "ON", "OFF",
]


def etiquetas_disponibles() -> list[str]:
    """Catalogo de etiquetas validas para las reglas.

    Las filas de cada fuzzy ya no son HIGH/OK/LOW fijas: la pagina Fuzzy deja
    crear, renombrar y borrar filas con nombre libre. Si el catalogo siguiera
    siendo una constante, el editor de reglas no ofreceria las etiquetas
    nuevas y la validacion rechazaria una regla perfectamente valida.

    Se lee fuzzy.json en cada llamada a proposito: es un archivo chico y la
    alternativa (cachear) haria que una etiqueta recien creada no apareciera
    hasta reiniciar. Por cada etiqueta se agrega tambien su NO-<X>, que el
    nucleo genera en expandir_etiquetas_compuestas.
    """
    out = list(ETIQUETAS_BASE)
    vistas = set(out)
    try:
        with open(FUZZY_JSON, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return out
    if not isinstance(cfg, dict):
        return out
    # Las filas de un fuzzy de PENDIENTE son igual de libres que las de una
    # PV: no tienen por que ser INC/DEC/STABLE.
    for origen in (cfg, pendientes_definidas()):
        for spec in (origen or {}).values():
            if not isinstance(spec, dict):
                continue
            for etiqueta in (spec.get("labels") or {}):
                for nombre in (str(etiqueta).upper(), f"NO-{str(etiqueta).upper()}"):
                    if nombre not in vistas:
                        vistas.add(nombre)
                        out.append(nombre)
    return out


# Cuantas escrituras al DCS se conservan en memoria para auditoria. Son
# livianas (un dict chico por tag escrito) y solo crecen cuando el SP se mueve
# de verdad, no en cada tick.
HISTORIAL_ESCRITURAS_MAX = 200

# Compatibilidad: sigue siendo la lista base. Los call sites que validan
# etiquetas deben usar etiquetas_disponibles(), que incluye las del fuzzy.
ETIQUETAS_DISPONIBLES = ETIQUETAS_BASE


def etiquetas_por_variable() -> dict:
    """Que etiquetas tiene sentido pedirle a CADA variable.

    `etiquetas_disponibles()` devuelve la union de todo, y eso alcanza para
    validar "esta etiqueta existe en alguna parte" — pero NO para ofrecerla.
    Ofrecer la union significaba ofrecer `LOW` sobre una pendiente cuyas filas
    son INC/DEC/STABLE, o `INC` sobre un nivel, o `CERCA_ALTO` sobre un fuzzy
    cuyas filas se renombraron al espanol. En los tres casos la regla se
    guardaba, no daba error, y evaluaba 0 PARA SIEMPRE (ver A27).

    Las etiquetas de una variable son:
      - las filas de su fuzzy (`fuzzy.json`) o de su pendiente
        (`pendientes.json`) — son configurables y de nombre libre;
      - ON / OFF si es un pseudo-permisivo `__PERM_X`;
      - mas las derivadas que el motor genera, consultadas a
        `etiquetas_derivadas()`, que es LA MISMA funcion que usa el expansor.

    Una variable que existe pero no esta fuzzificada devuelve lista vacia: es
    la respuesta correcta —no hay nada que preguntarle— y la pagina lo dice en
    vez de ofrecer etiquetas que no van a evaluar.
    """
    from core.fuzzy.evaluator import etiquetas_derivadas

    out: dict = {}

    def _agregar(nombre: str, filas):
        base = [str(x).upper() for x in (filas or [])]
        if not base:
            out.setdefault(str(nombre), [])
            return
        out[str(nombre)] = base + etiquetas_derivadas(base)

    for origen in (_leer_json(FUZZY_JSON, {}), pendientes_definidas()):
        for var, spec in (origen or {}).items():
            if isinstance(spec, dict):
                _agregar(var, (spec.get("labels") or {}).keys())

    # Los permisivos se inyectan como pseudo-variables con ON/OFF fijos
    # (`inyectar_permisivos_en_fuzzy_out`), no salen de ningun fuzzy.
    for nombre in nombres_permisivos():
        _agregar(nombre_variable_permisivo(nombre), ["ON", "OFF"])

    # Toda variable ofrecida tiene entrada, aunque sea vacia: la pagina
    # distingue "no tiene fuzzy" de "no se conoce la variable".
    for var in variables_disponibles():
        out.setdefault(str(var), [])
    return out


def etiquetas_validas_de(variable: str) -> set:
    """Etiquetas aceptables PARA ESA variable. Vacio = no esta fuzzificada."""
    return set(etiquetas_por_variable().get(str(variable), []))


def variables_calculadas_definidas() -> list[dict]:
    """Definiciones de `variables.json`, en orden (el orden encadena).

    Se lee el archivo en cada llamada, igual que `etiquetas_disponibles()` y
    `acciones_disponibles()`: cachearlo haria que una variable recien creada
    no se pudiera usar en una regla hasta reiniciar el servicio.
    """
    datos = _leer_json(VARIABLES_JSON, {})
    defs = datos.get("definiciones")
    if not isinstance(defs, list):
        return []
    return [d for d in defs if isinstance(d, dict) and d.get("nombre")]


def nombres_calculadas() -> list[str]:
    return [str(d["nombre"]) for d in variables_calculadas_definidas()]


def pendientes_definidas() -> dict:
    """Fuzzy de pendiente declarados en `pendientes.json`.

    Se relee en cada llamada, igual que fuzzy.json: una pendiente recien
    creada tiene que poder nombrarse en una regla sin reiniciar.
    """
    cfg = _leer_json(PENDIENTES_JSON, {})
    return {str(k): v for k, v in cfg.items() if isinstance(v, dict)}


def nombres_pendientes() -> list[str]:
    return sorted(pendientes_definidas())


def permisivos_definidos() -> dict:
    """Permisivos declarados en `permisivos.json`.

    Antes el catalogo salia del dict `PERMISIVOS` importado de `permisivos.py`
    — los permisivos HARDCODEADOS del espesador. Era la misma foto al importar
    que ya se corrigio en `etiquetas_disponibles()`, `acciones_disponibles()` y
    `variables_disponibles()`: el editor de estados y de reglas ofrecia cinco
    `__PERM_*` de otra planta que el motor nunca iba a producir (`_run_tick`
    evalua `cargar_permisivos_json()`, no este dict), y no ofrecia el que el
    operador acababa de crear.

    Se lee con `_leer_json` y NO con `cargar_permisivos_json()` de runner.py
    por el mismo motivo que `roles_en_uso()`: ese cae a la plantilla del
    espesador cuando el archivo falta, y aqui eso produce exactamente el ruido
    que se quiere eliminar. Un archivo vacio es un estado valido: significa
    "esta planta todavia no declaro permisivos".
    """
    cfg = _leer_json(PERMISIVOS_JSON, {})
    if not isinstance(cfg, dict):
        return {}
    return {str(k): v for k, v in cfg.items()}


def nombres_permisivos() -> list[str]:
    return list(permisivos_definidos())


def variables_disponibles() -> list[str]:
    """Variables que una regla o un permisivo pueden nombrar.

    Antes esto era `_build_variables_disponibles()`, una foto tomada al
    importar el modulo que ofrecia `VARIABLES_EXTERNAS` — la lista de
    calculadas HARDCODEADA del espesador (`tonelaje_sag_delta_30min` y
    companhia). Consecuencia: una variable calculada creada en la interfaz no
    existia para el validador de reglas, asi que se podia definir y no se
    podia usar; y a la vez se ofrecian cinco variables de otra planta que el
    motor nunca iba a producir.
    """
    nombres: list[str] = []
    nombres.extend(VARIABLES_PROCESO)
    # Las pendientes ya NO se derivan de la lista de PV. Ofrecer `pend_<var>`
    # para toda PV era ofrecer una variable que el motor no podia producir:
    # la regla se escribia, se guardaba, y quedaba `no_evaluable` para
    # siempre. Ahora existen las que estan declaradas en pendientes.json, ni
    # una mas ni una menos.
    nombres.extend(v for v in nombres_pendientes() if v not in nombres)
    nombres.extend(v for v in VARIABLES_CRUDAS_REQUERIDAS if v not in nombres)
    nombres.extend(v for v in nombres_calculadas() if v not in nombres)
    nombres.extend(nombre_variable_permisivo(p) for p in nombres_permisivos())
    return nombres


def variables_validas() -> set:
    """Variables aceptables en una regla, incluidas las calculadas de hoy."""
    return set(variables_disponibles())


def _build_variables_disponibles() -> list[str]:
    """Compatibilidad: foto al importar. Para validar usa variables_validas()."""
    return variables_disponibles()


def acciones_disponibles() -> list[str]:
    """Catalogo de acciones validas para el `then` de las reglas.

    UNICA fuente de verdad: las columnas de defuzzy.json. Una accion existe
    porque alguien la definio en una tabla Sugeno; si no esta ahi, el motor no
    sabria cuanto mover ni que setpoint tocar, asi que ofrecerla en el editor
    solo produce reglas que fallan al aplicarse.

    Antes esta lista se armaba en codigo a partir de las familias del
    espesador antiguo (VEL_BOMBA / TONELAJE / FLOCULANTE), que no tienen nada
    que ver con la operacion configurada hoy: por eso el selector mostraba 18
    acciones inexistentes y no mostraba las recien creadas.

    Se lee el archivo en cada llamada, igual que etiquetas_disponibles(): es
    chico y cachearlo haria que una accion nueva no apareciera hasta reiniciar.
    Sin archivo, archivo corrupto o sin familias, el catalogo es vacio — es un
    estado valido: significa "todavia no hay defuzzy configurado".
    """
    try:
        with open(DEFUZZY_JSON, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(cfg, dict):
        return []
    out: list[str] = []
    for tabla in cfg.values():
        if not isinstance(tabla, dict):
            continue
        for accion in (tabla.get("steps_por_accion") or {}):
            nombre = str(accion).strip().upper()
            if nombre and nombre not in out:
                out.append(nombre)
    return sorted(out)


def acciones_validas() -> set:
    """Acciones aceptables en una regla (las columnas de defuzzy.json)."""
    return set(acciones_disponibles())


VARIABLES_DISPONIBLES  = _build_variables_disponibles()
# Compatibilidad: foto al importar. Para ofrecer o validar acciones usa
# acciones_disponibles() / acciones_validas(), que releen defuzzy.json.
ACCIONES_DISPONIBLES   = acciones_disponibles()
# Compatibilidad: foto al importar. Para ofrecer permisivos usa
# nombres_permisivos(), que relee permisivos.json.
PERMISIVOS_DISPONIBLES = nombres_permisivos()

VARIABLES_VALIDAS = set(VARIABLES_DISPONIBLES)
# Constante historica: solo las base. Para validar usa etiquetas_validas().
ETIQUETAS_VALIDAS = set(ETIQUETAS_DISPONIBLES)


def etiquetas_validas() -> set:
    """Etiquetas aceptables en una regla, incluidas las filas de fuzzy.json."""
    return set(etiquetas_disponibles())
# Constante historica: foto al importar. Para validar usa acciones_validas().
ACCIONES_VALIDAS  = set(ACCIONES_DISPONIBLES)
BLOQUES_VALIDOS   = set(BLOQUES_DISPONIBLES)


# Los estados de fabrica del espesador se eliminaron (2026-09-01): eran una
# plantilla de otra planta que se sembraba sola cuando faltaba estados.json y
# que el boton "Restaurar default" reescribia. Un contrato nuevo arranca sin
# estados, igual que arranca sin reglas y sin defuzzy.
#
# La constante se conserva vacia porque `web/api/config.py` la importa; no
# borrarla evita romper cualquier import de fuera que todavia la nombre.
ESTADOS_SERIALIZADOS: dict = {}

# Los ocho waits de fabrica del espesador (vel_bomba, tonelaje, floculante...)
# se eliminaron: eran una plantilla de otra planta que `_load_waits()` sembraba
# sola cuando faltaba waits.json, y que el boton "Restaurar default" reescribia.
# Mismo criterio que ESTADOS_SERIALIZADOS y que _defaults_defuzzy(). La
# constante se conserva vacia porque `web/api/config.py` la importa.
WAITS_CATALOGO_DISPONIBLES: list = []


# ============================================================
# Helpers JSON compartidos entre blueprints
# (estados y waits son usados tanto por config.py como views.py)
# ============================================================

def _defaults_estados() -> dict:
    """Sin plantilla: un contrato nuevo arranca SIN estados.

    Devolvia `ESTADOS_SERIALIZADOS`, los estados del espesador de fabrica.
    Mientras `estados.json` existiera no se notaba, pero el archivo se siembra
    con este default si falta y el boton "Vaciar estados" lo reescribia: en
    ambos casos aparecian estados de otra planta, que nombran variables que
    este contrato no tiene. Mismo criterio que `_defaults_defuzzy()` y
    `_defaults_tracking()`.
    """
    return {}


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
    escribir_json_atomico(ESTADOS_JSON_PATH, data)


def _defaults_waits() -> list[dict]:
    """Sin plantilla: un contrato nuevo arranca SIN waits. Ver A28."""
    return []


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
    escribir_json_atomico(WAITS_JSON_PATH, data)


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

    # Una variable calculada no se lee de ningun tag, pero SI se fuzzifica, y
    # para eso necesita lmin/lmax como cualquier PV. Se ofrecen sus roles LIM
    # para que el operador pueda cablearlos al DCS si los limites de
    # ingenieria viven alla; si no los asigna, se usa el respaldo fijo de
    # variables.json (ver `limites_fijos_calculadas`).
    # Los SETPOINTS tambien tienen limites: son los que clipean la escritura al
    # DCS. Antes solo existian como numeros fijos en `contrato.json`, que
    # ninguna pagina editaba; ahora se pueden cablear a un tag como cualquier
    # otro limite y `contrato.json` queda como respaldo.
    lims = []
    for var in list(VARIABLES_PROCESO) + nombres_calculadas() + list(SETPOINT_KEYS):
        lims.append(f"{var}_lmin")
        lims.append(f"{var}_lmax")

    return {
        "pv":    list(VARIABLES_PROCESO),
        "cruda": list(VARIABLES_CRUDAS_REQUERIDAS),
        "lim":   lims,
        "sp":    list(SETPOINT_KEYS),
        "otro":  [],
    }


LIMITES_BOUNDS = ("lmin", "lmax")


def _bindings_de_config(cfg: dict) -> dict:
    """Extrae el cableado `limites` de un fuzzy.json / defuzzy.json ya leido."""
    out: dict[tuple[str, str], str] = {}
    for var, spec in (cfg or {}).items():
        if not isinstance(spec, dict):
            continue
        lims = spec.get("limites")
        if not isinstance(lims, dict):
            continue
        for bound in LIMITES_BOUNDS:
            tag = str(lims.get(bound) or "").strip()
            if tag:
                out[(str(var), bound)] = tag
    return out


def bindings_limites() -> dict[tuple[str, str], str]:
    """Cableado (variable, bound) -> tag del DCS. UNICA fuente de verdad.

    Hasta el 2026-09-03 esto vivia en el campo `rol` de los tags de categoria
    LIM: un tag se volvia el limite de una PV cuando alguien le escribia
    `hopper_nvl_pv_a_lmax` en la pagina de Tags. Tenia dos problemas, y el
    segundo era el caro:

      - La asignacion no se veia desde la pagina donde se usa. Mirando el fuzzy
        de una variable era imposible saber contra que escala se estaba
        normalizando.
      - **Un tag tiene un solo rol**, asi que un limite fisico no podia acotar
        dos variables. `PU009_Speed_MIN/MAX` son los limites de la bomba: son a
        la vez la escala de la PV de velocidad Y el tope de escritura del
        setpoint, y con el rol habia que elegir uno de los dos o duplicar el tag
        en KEPserver.

    Ahora el binding lo declara quien lo consume — `fuzzy.json` para las PV,
    `defuzzy.json` para los SP — bajo la clave `limites`, y por NOMBRE de tag.
    Un mismo tag puede aparecer en varias variables sin ambiguedad.

    Se lee con `_leer_json` y no con los `cargar_*_json()` de `runner.py`, por
    el mismo motivo que documenta `roles_en_uso()`: esos caen a la plantilla del
    espesador cuando el archivo falta, y aqui eso cablearia limites de otra
    planta.
    """
    out = _bindings_de_config(_leer_json(FUZZY_JSON, {}))
    # Los SP se mezclan despues, pero no pueden pisar nada: las familias de
    # defuzzy son identificadores de tags SP y los fuzzy, de tags PV.
    out.update(_bindings_de_config(_leer_json(DEFUZZY_JSON, {})))
    return out


def _limites_num_de_spec(spec) -> dict:
    """Respaldo numerico de un fuzzy o de una familia, si esta HABILITADO.

    Formato en disco: `{"habilitado": bool, "lmin": num|null, "lmax": num|null}`.
    Deshabilitado o sin numero devuelve `{}`: el respaldo no existe para el
    motor mientras nadie lo encienda a mano. Es lo contrario de lo que hacia
    `limites_sp` del contrato, que valia siempre y en silencio.
    """
    if not isinstance(spec, dict):
        return {}
    num = spec.get("limites_num")
    if not isinstance(num, dict) or not num.get("habilitado"):
        return {}
    out = {}
    for bound in LIMITES_BOUNDS:
        try:
            out[bound] = float(num[bound])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def limites_num(archivo: str | None = None) -> dict[str, dict]:
    """Respaldos numericos habilitados: `{variable: {lmin, lmax}}`.

    Sin argumento junta los dos archivos (PV de `fuzzy.json`, SP de
    `defuzzy.json`), que no pueden chocar porque una familia de defuzzy es el
    identificador de un tag SP y un fuzzy el de un tag PV.
    """
    rutas = [archivo] if archivo else [FUZZY_JSON, DEFUZZY_JSON]
    out: dict[str, dict] = {}
    for ruta in rutas:
        for var, spec in (_leer_json(ruta, {}) or {}).items():
            nums = _limites_num_de_spec(spec)
            if nums:
                out[str(var)] = nums
    return out


def migrar_limites_sp_del_contrato() -> list[dict]:
    """Adopta `contrato.json -> limites_sp` como respaldo de `defuzzy.json`.

    Hasta el 2026-09-03 el tope de escritura de un SP era un par de numeros en
    el contrato que **ninguna pagina editaba**, y que valia siempre. Ahora el
    respaldo vive junto a la tabla que lo usa, con la misma forma que el de una
    PV y con un interruptor explicito.

    Se migra **habilitado**: en esta planta esos numeros estan gobernando el
    clipeo hoy, y apagarlos al actualizar dejaria el SP sin tope — o sea, sin
    escritura. El "deshabilitado por defecto" es para los respaldos NUEVOS.

    Aditiva e idempotente, igual que `migrar_roles_lim_a_bindings()`: no toca
    `contrato.json` ni pisa un `limites_num` que ya exista.
    """
    from config import LIMITES_SP_CONTRATO

    defuzzy = _leer_json(DEFUZZY_JSON, {})
    migrados = []
    for fam, spec in (defuzzy or {}).items():
        if not isinstance(spec, dict) or isinstance(spec.get("limites_num"), dict):
            continue
        par = LIMITES_SP_CONTRATO.get(fam) or ()
        if len(par) != 2:
            continue
        spec["limites_num"] = {"habilitado": True,
                               "lmin": float(par[0]), "lmax": float(par[1])}
        migrados.append({"familia": fam, "limites": [float(par[0]), float(par[1])]})
    if migrados:
        escribir_json_atomico(DEFUZZY_JSON, defuzzy)
    return migrados


def limites_disponibles(tags: list[dict] | None = None) -> list[dict]:
    """Tags de categoria LIM habilitados, para los desplegables de la interfaz.

    Espejo de `salidas_sp_disponibles()`. A diferencia de las PV y los SP, un
    tag LIM **no tiene identificador propio**: no es una variable, es el valor
    de un limite. Por eso se ofrece por pseudonimo y nombre de tag.
    """
    if tags is None:
        tags = _load_tags().get("tags", [])
    items = []
    for t in tags:
        if t.get("categoria") != "lim" or not t.get("enabled", True):
            continue
        nombre = t["name"]
        items.append({
            "tag": nombre,
            "pseudonimo": (t.get("pseudonimo") or "").strip() or nombre.split(".")[-1],
            "equipo": t.get("equipo", ""),
            "unidad": t.get("unidad_ing", ""),
            "instrumento": t.get("instrumento", ""),
        })
    items.sort(key=lambda x: (x["equipo"] or "", x["pseudonimo"]))
    return items


def limites_huerfanos(tags: list[dict] | None = None) -> list[dict]:
    """Bindings que apuntan a un tag que ya no sirve, con el motivo.

    Un tag borrado, deshabilitado o que cambio de categoria deja el cableado
    colgando. NUNCA se reemplaza solo (regla A23): se reporta para que la
    pagina lo pinte en rojo y obligue a elegir reemplazo.
    """
    if tags is None:
        tags = _load_tags().get("tags", [])
    por_nombre = {t.get("name"): t for t in tags}
    fuera = []
    for (var, bound), tag_name in sorted(bindings_limites().items()):
        t = por_nombre.get(tag_name)
        if t is None:
            motivo = "el tag ya no existe"
        elif t.get("categoria") != "lim":
            motivo = f"el tag dejo de ser LIM (hoy es {str(t.get('categoria')).upper()})"
        elif not t.get("enabled", True):
            motivo = "el tag esta deshabilitado"
        else:
            continue
        fuera.append({"variable": var, "bound": bound, "rol": f"{var}_{bound}",
                      "tag": tag_name, "motivo": motivo})
    return fuera


def tracking_definido() -> dict:
    """Config de seguimiento SP -> readback, de `tracking.json`.

    {<familia>: {"pv_key": str, "rango": float, "habilitado": bool}}

    `pv_key` vacio significa "sin readback": esa familia no se verifica.
    """
    cfg = _leer_json(TRACKING_JSON, {})
    return {str(k): v for k, v in cfg.items() if isinstance(v, dict)}


ARRANQUE_FUENTES = ("tag", "pv", "ninguno")


def arranque_definido() -> dict:
    """Con que valor arranca cada familia de SP cuando el DCS entrega el lazo.

    Vive junto al tracking (`tracking.json`, clave `arranque` de cada familia)
    porque es la misma unidad — una familia de setpoint — y asi se configura en
    la misma pantalla. Pero NO es lo mismo: el tracking decide si se sigue
    empujando, esto decide con que numero se retoma.

    {<familia>: {"fuente": "tag"|"pv"|"ninguno", "tag": str, "pv_key": str}}

    Compatibilidad: una familia sin bloque `arranque` conserva el
    comportamiento anterior — si tiene readback, se siembra con esa PV. Asi una
    config existente no cambia de conducta por actualizar.
    """
    out: dict[str, dict] = {}
    for familia, spec in tracking_definido().items():
        pv_key = str((spec or {}).get("pv_key") or "").strip()
        arr = (spec or {}).get("arranque")
        if not isinstance(arr, dict):
            arr = {"fuente": "pv" if pv_key else "ninguno"}
        fuente = str(arr.get("fuente") or "").strip().lower()
        if fuente not in ARRANQUE_FUENTES:
            fuente = "pv" if pv_key else "ninguno"
        tag = str(arr.get("tag") or "").strip()
        # Una fuente mal configurada no arranca a medias: se apaga. Sembrar con
        # un valor que no se sabe de donde salio es peor que no sembrar.
        if fuente == "tag" and not tag:
            fuente = "ninguno"
        if fuente == "pv" and not pv_key:
            fuente = "ninguno"
        out[familia] = {"fuente": fuente, "tag": tag, "pv_key": pv_key}
    return out


def limites_fijos_calculadas() -> dict[str, dict]:
    """Respaldo de lmin/lmax declarado en `variables.json` por definicion.

    Solo se usa para lo que NO llego por tag. El tag manda: si el limite de
    ingenieria cambia en el DCS, el SE tiene que seguirlo sin que nadie edite
    un JSON.
    """
    out: dict[str, dict] = {}
    for d in variables_calculadas_definidas():
        bounds = {}
        for b in ("lmin", "lmax"):
            if d.get(b) is None:
                continue
            try:
                bounds[b] = float(d[b])
            except (TypeError, ValueError):
                continue
        if bounds:
            out[str(d["nombre"])] = bounds
    return out


CONTRATO_JSON = os.path.join(_CFG_DIR, "contrato.json")


# ------------------------------------------------------------
# Que roles del contrato USA realmente el pipeline
# ------------------------------------------------------------
# Que limite necesita cada tipo de fuzzy para normalizar. Un `high` solo mide
# contra lmax: exigirle lmin era pedir un tag que nadie iba a leer.
_LIMITES_POR_TIPO_FUZZY = {
    "low":  ("lmin",),
    "high": ("lmax",),
    "norm": ("lmin", "lmax"),
}


def limites_requeridos(var: str, fuzzy_cfg: dict | None = None) -> tuple[str, ...]:
    """Bounds ('lmin'/'lmax') que la fuzzificacion de `var` necesita de verdad."""
    cfg = fuzzy_cfg if fuzzy_cfg is not None else _leer_json(FUZZY_JSON, {})
    spec = (cfg or {}).get(str(var))
    if not isinstance(spec, dict):
        return ()
    return _LIMITES_POR_TIPO_FUZZY.get(str(spec.get("type", "")).lower(), ("lmin", "lmax"))


def _vars_de_permisivos(cfg) -> set[str]:
    """Variables que nombran las condiciones de los permisivos (recursivo)."""
    out: set[str] = set()

    def _walk(nodo):
        if isinstance(nodo, list):
            for x in nodo:
                _walk(x)
            return
        if not isinstance(nodo, dict):
            return
        for op in ("OR", "AND", "NOT"):
            if op in nodo:
                _walk(nodo[op])
                return
        if "var" in nodo:
            out.add(str(nodo["var"]))
        if "fuzzy_var" in nodo:
            out.add(str(nodo["fuzzy_var"]))

    for condiciones in (cfg or {}).values():
        _walk(condiciones)
    return out


def roles_en_uso(fuzzy_cfg: dict | None = None) -> dict[str, set[str]]:
    """Roles del contrato que el pipeline CONSUME aguas abajo.

    El SE no debe exigir una senal solo porque este declarada en el contrato:
    debe exigirla cuando alguna etapa posterior la va a usar. Esta funcion es
    ese criterio, y sale de los mismos JSON que configura la interfaz:

      PV     -> tiene modelo en fuzzy.json, la nombra una regla (directa o
                como `pend_<var>`), la nombra un permisivo, es argumento de
                una variable calculada, o es el readback de una familia de
                tracking.
      LIM    -> solo los bounds que la fuzzificacion de esa PV necesita
                (`high` usa lmax, `low` usa lmin, `norm` los dos).
      SP     -> tiene familia en defuzzy.json o alguna regla nombra una
                accion de esa familia.
      CRUDA  -> la usa una variable calculada o un permisivo.

    `fuzzy_cfg` permite pasar los modelos que el motor tiene REALMENTE
    cargados; sin el se lee fuzzy.json. Importa porque el tipo de modelo
    decide que limite hace falta.

    Devuelve conjuntos por categoria, en la misma nomenclatura que
    `catalogo_roles()` (los LIM ya vienen como `<var>_lmin` / `<var>_lmax`).
    """
    # `pares_de_regla` y no `variables_de_regla`: aqui las reglas se leen
    # crudas del JSON, donde una hoja todavia es ["var", "ETIQUETA"] (una
    # lista) y no la tupla que fabrica `cargar_reglas_json`. Solo el primero
    # entiende las dos formas.
    from core.engine.motor import pares_de_regla
    from core.variables.online import fuentes_de_definicion

    if fuzzy_cfg is None:
        fuzzy_cfg = _leer_json(FUZZY_JSON, {})
    reglas      = _leer_json(REGLAS_JSON, [])
    permisivos  = _leer_json(PERMISIVOS_JSON, {})
    pendientes  = _leer_json(PENDIENTES_JSON, {})
    variables   = _leer_json(VARIABLES_JSON, {})
    defuzzy     = _leer_json(DEFUZZY_JSON, {})
    tracking    = _leer_json(TRACKING_JSON, {})

    reglas_on = [r for r in reglas
                 if isinstance(r, dict) and r.get("enabled", True)]

    # --- Variables que nombran las reglas (quitando el prefijo de pendiente
    #     y los pseudo-permisivos __PERM_X, que no son senales del DCS) ---
    vars_reglas: set[str] = set()
    for r in reglas_on:
        try:
            nombres = {var for var, _ in pares_de_regla(r)}
        except Exception:
            nombres = set()
        for n in nombres:
            n = str(n)
            if n.startswith("__PERM_"):
                continue
            # `pend_<var>` ya no es un nombre derivado: las pendientes tienen
            # nombre propio y se resuelven contra pendientes.json mas abajo.
            vars_reglas.add(n)

    vars_perm = _vars_de_permisivos(permisivos)

    # --- Una pendiente en uso arrastra a su variable de origen ---
    # La regla nombra `tendencia_hopper_5min`; sin `hopper_nvl_pv_a` esa
    # tendencia no se puede calcular, aunque nadie escriba su nombre.
    pend_en_uso = {n for n in pendientes if n in (vars_reglas | vars_perm)}
    fuentes_pend = {str((pendientes.get(n) or {}).get("variable") or "")
                    for n in pend_en_uso}
    fuentes_pend.discard("")

    # --- Readbacks declarados en tracking ---
    vars_tracking = {str(spec.get("pv_key")) for spec in (tracking or {}).values()
                     if isinstance(spec, dict) and spec.get("pv_key")}

    # --- Variables calculadas: una en uso arrastra a sus fuentes ---
    # Nadie nombra `hopper_nvl_pv_a` si la regla habla de `nivel_promedio`,
    # pero sin esa PV el promedio no se puede calcular. Se recorre al reves,
    # de la ultima definicion a la primera, porque el orden de variables.json
    # es el de encadenamiento: asi una cadena de tres saltos se propaga en
    # una sola pasada.
    defs_calc = [d for d in (variables.get("definiciones") or [])
                 if isinstance(d, dict) and d.get("nombre")]
    directas = vars_reglas | vars_perm | vars_tracking | set(fuzzy_cfg) | fuentes_pend
    calc_en_uso = {str(d["nombre"]) for d in defs_calc
                   if str(d["nombre"]) in directas}
    fuentes: set[str] = set()
    for d in reversed(defs_calc):
        nombre = str(d["nombre"])
        if nombre not in calc_en_uso:
            continue
        for f in fuentes_de_definicion(d):
            fuentes.add(f)
            if any(str(x.get("nombre")) == f for x in defs_calc):
                calc_en_uso.add(f)

    pv_en_uso = {v for v in VARIABLES_PROCESO
                 if v in fuzzy_cfg or v in vars_reglas or v in vars_perm
                 or v in fuentes or v in vars_tracking or v in fuentes_pend}

    # Las calculadas tambien se fuzzifican, y para eso necesitan sus limites.
    # Se ofrecen como roles LIM aunque la variable no salga de ningun tag.
    lim_en_uso = {f"{v}_{b}" for v in (pv_en_uso | calc_en_uso)
                  for b in limites_requeridos(v, fuzzy_cfg)}

    # Una familia de SP esta en uso cuando su tabla Sugeno declara al menos
    # una accion: es el unico enlace regla -> setpoint que existe. Sin tabla,
    # ninguna accion de ninguna regla puede mover ese SP.
    sp_en_uso = {sp for sp in SETPOINT_KEYS
                 if ((defuzzy.get(sp) or {}).get("steps_por_accion") or {})}

    # Los limites de un SP en uso tambien hacen falta: son los que clipean la
    # escritura al DCS, y sin ellos esa familia no se escribe.
    for sp in sp_en_uso:
        lim_en_uso |= {f"{sp}_lmin", f"{sp}_lmax"}

    # ...pero un bound con respaldo numerico HABILITADO ya esta cubierto: el
    # tag es opcional ahi, y pedirlo seria un aviso que no lleva a ninguna
    # accion. Vale igual para PV y para SP.
    lim_en_uso -= {f"{v}_{b}" for v, nums in limites_num().items() for b in nums}

    cruda_en_uso = {c for c in VARIABLES_CRUDAS_REQUERIDAS
                    if c in fuentes or c in vars_perm}

    return {"pv": pv_en_uso, "lim": lim_en_uso, "sp": sp_en_uso,
            "cruda": cruda_en_uso, "calculada": calc_en_uso,
            "pendiente": pend_en_uso, "otro": set()}


def estado_contrato() -> dict:
    """Compara el contrato EN DISCO con el que tiene cargado el proceso.

    config.py lee contrato.json una sola vez, al importarse: sus constantes
    (VARIABLES_PROCESO / SETPOINT_KEYS / crudas) quedan congeladas mientras el
    servicio vive. Guardar el contrato desde la pagina reescribe el archivo,
    pero el nucleo — y por lo tanto el catalogo de roles y el chequeo de
    cobertura — sigue razonando con la lista vieja hasta reiniciar.

    Eso se veia como un bug: el Contrato mostraba 2 PV y el Mapeo seguia
    exigiendo las 8 de la plantilla. No lo es, pero era invisible. Esto expone
    la diferencia para que la UI la muestre en vez de dejar al operador
    adivinando cual de las dos listas es la real.

    **Desde el 2026-08-21 guardar el contrato recarga el nucleo en caliente**
    (`config.recargar_contrato`), asi que en condiciones normales las dos
    listas coinciden. Esta comparacion se conserva porque sigue siendo la
    unica forma de detectar el caso raro: alguien edito `contrato.json` a mano
    en el disco, sin pasar por la API.
    """
    from config import VARIABLES_PROCESO, VARIABLES_CRUDAS_REQUERIDAS, SETPOINT_KEYS

    nucleo = {
        "variables_proceso": list(VARIABLES_PROCESO),
        "setpoints":         list(SETPOINT_KEYS),
        "crudas":            list(VARIABLES_CRUDAS_REQUERIDAS),
    }

    try:
        with open(CONTRATO_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        # Sin archivo legible no hay con que comparar: se informa "en sincro"
        # antes que inventar una desincronizacion que no se puede probar.
        return {"desincronizado": False, "nucleo": nucleo, "disco": nucleo,
                "agregadas": {}, "quitadas": {}}

    disco = {
        "variables_proceso": [str(v) for v in (data.get("variables_proceso") or [])],
        "setpoints":         [str(v) for v in (data.get("setpoints") or [])],
        # Las crudas viven en variables.json, no en el contrato: se copian del
        # nucleo para no reportar una diferencia que este archivo no describe.
        "crudas":            list(nucleo["crudas"]),
    }

    agregadas, quitadas = {}, {}
    for campo in ("variables_proceso", "setpoints"):
        en_disco, en_nucleo = disco[campo], nucleo[campo]
        nuevas = [v for v in en_disco if v not in en_nucleo]
        viejas = [v for v in en_nucleo if v not in en_disco]
        if nuevas:
            agregadas[campo] = nuevas
        if viejas:
            quitadas[campo] = viejas

    return {
        "desincronizado": bool(agregadas or quitadas),
        "nucleo": nucleo,
        "disco": disco,
        "agregadas": agregadas,
        "quitadas": quitadas,
    }


def roles_huerfanos(tags: list[dict] | None = None) -> list[dict]:
    """Tags cuyo rol guardado ya no existe en el contrato vigente.

    Pasa cuando el contrato renombra o quita una variable: el rol quedo
    escrito en tags.json y era valido cuando se asigno, pero hoy no apunta a
    nada. El motor lo ignora (construir_mapeo lo saltea), asi que el tag
    figura como "sin asignar" sin decir por que. Esto lo hace explicito.
    """
    if tags is None:
        tags = _load_tags().get("tags", [])
    catalogo = catalogo_roles()
    fuera = []
    for t in tags:
        cat = t.get("categoria")
        rol = (t.get("rol") or "").strip()
        # LIM queda fuera: su cableado ya no es el rol. Un rol LIM sobreviviente
        # no es un huerfano sino un resto sin migrar (ver
        # `migrar_roles_lim_a_bindings`), y los bindings colgados los reporta
        # `limites_huerfanos()`.
        if not rol or cat not in catalogo or cat in ("otro", "lim"):
            continue
        if rol not in catalogo[cat]:
            fuera.append({"tag": t.get("name"), "id": t.get("id"),
                          "categoria": cat, "rol": rol,
                          "enabled": bool(t.get("enabled", True))})
    return fuera


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


def migrar_roles_lim_a_bindings() -> dict:
    """Adopta los roles LIM viejos como bindings de `fuzzy.json`/`defuzzy.json`.

    Compatibilidad de una vez con la config anterior al 2026-09-03, cuando el
    limite de una variable era el campo `rol` de un tag LIM. Es idempotente:
    corre en cada arranque y no hace nada si ya esta todo migrado.

    **Es ADITIVA: escribe el binding y NO borra el rol.** El rol queda como
    metadato inerte (`construir_mapeo` ya no lo mira) y la pagina de Tags lo
    muestra como "rol viejo". Blanquearlo parecia mas prolijo y era peligroso:
    esta funcion corre en `_startup_checks()`, o sea en cada `import app` —
    incluido el que hace el suite de tests contra la config VIVA de la planta.
    Si el borrado del rol se persiste y la escritura del binding se pierde o se
    pisa despues, el cableado desaparece y no queda de donde reconstruirlo. Ya
    paso una vez. Aditivo no puede destruir nada.

    Un rol cuya variable todavia no tiene fuzzy ni tabla defuzzy queda
    `pendiente`: esta funcion lo adopta en el proximo arranque, o `crear fuzzy`
    lo hereda al vuelo (`_limites_heredados_del_rol`).
    """
    with _tags_lock:
        store = _load_tags()
        tags = store.get("tags", [])
        candidatos = [t for t in tags
                      if t.get("categoria") == "lim" and str(t.get("rol") or "").strip()]
        if not candidatos:
            return {"migrados": [], "pendientes": []}

        fuzzy   = _leer_json(FUZZY_JSON, {})
        defuzzy = _leer_json(DEFUZZY_JSON, {})
        migrados, pendientes = [], []
        toco_fuzzy = toco_defuzzy = False

        for t in candidatos:
            rol = str(t["rol"]).strip()
            if "_" not in rol:
                continue
            var, bound = rol.rsplit("_", 1)
            if bound not in LIMITES_BOUNDS:
                continue
            destino = fuzzy if var in fuzzy else (defuzzy if var in defuzzy else None)
            if destino is None:
                pendientes.append({"tag": t["name"], "rol": rol})
                continue
            spec = destino[var]
            if not isinstance(spec, dict):
                continue
            lims = spec.setdefault("limites", {})
            if str(lims.get(bound) or "").strip():
                continue                   # ya migrado: nada que hacer
            lims[bound] = t["name"]
            if destino is fuzzy:
                toco_fuzzy = True
            else:
                toco_defuzzy = True
            migrados.append({"tag": t["name"], "rol": rol})

        if toco_fuzzy:
            escribir_json_atomico(FUZZY_JSON, fuzzy)
        if toco_defuzzy:
            escribir_json_atomico(DEFUZZY_JSON, defuzzy)
        return {"migrados": migrados, "pendientes": pendientes}


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
    # Un tag puede acotar VARIAS variables (los limites de la bomba son a la vez
    # la escala de su PV y el tope de su SP), asi que el valor es una lista.
    tag_to_lim: dict[str, list[tuple[str, str]]] = {}
    sp_to_tag: dict[str, str] = {}

    asignados: dict[str, list[str]] = {}   # "categoria::rol" -> [tags]

    for t in tags:
        if not t.get("enabled", True):
            continue
        cat = t.get("categoria")
        rol = t.get("rol") or ""
        # LIM aparte: su cableado ya no sale del rol del tag (ver
        # `bindings_limites`), asi que un rol viejo que sobrevivio a la
        # migracion no debe volver a mapear nada.
        if cat == "lim" or cat not in catalogo or not rol or rol not in catalogo[cat]:
            continue

        nombre = t["name"]
        clave = f"{cat}::{rol}"
        # Rol ya tomado: el segundo tag NO mapea. Antes se asignaba igual y
        # ganaba el ULTIMO recorrido, asi que a que tag le escribia el SE (o de
        # cual leia una PV) dependia del orden del archivo. Un tag agregado
        # despues podia robarle la escritura al de produccion sin que nada
        # fallara. Ahora gana el primero — resultado estable — y el conflicto
        # queda en `duplicados` para que arriba se decida que hacer.
        duplicado = clave in asignados
        asignados.setdefault(clave, []).append(nombre)
        if duplicado:
            continue

        if cat == "pv":
            tag_to_pv[nombre] = rol
        elif cat == "cruda":
            tag_to_cruda[nombre] = rol
        elif cat == "sp":
            sp_to_tag[rol] = nombre

    # --- LIM: el cableado lo declara quien consume el limite ---
    # `fuzzy.json` para las PV y `defuzzy.json` para los SP. Un binding a un tag
    # inexistente, deshabilitado o que ya no es LIM simplemente no mapea: el rol
    # queda como faltante y `limites_huerfanos()` explica por que.
    por_nombre = {t.get("name"): t for t in tags}
    roles_lim = set(catalogo.get("lim", ()))
    for (var, bound), tag_name in bindings_limites().items():
        rol = f"{var}_{bound}"
        if rol not in roles_lim:
            continue                       # variable fuera del contrato vigente
        t = por_nombre.get(tag_name)
        if not t or t.get("categoria") != "lim" or not t.get("enabled", True):
            continue
        asignados.setdefault(f"lim::{rol}", []).append(tag_name)
        tag_to_lim.setdefault(tag_name, []).append((var, bound))

    faltantes = {
        cat: [r for r in roles if f"{cat}::{r}" not in asignados]
        for cat, roles in catalogo.items() if roles
    }
    # Un limite con respaldo numerico HABILITADO ya esta cubierto: ahi el tag
    # es opcional. Sin esta resta, cualquier planta que trabaje con numeros
    # veria el mapeo incompleto para siempre.
    con_numero = {f"{v}_{b}" for v, nums in limites_num().items() for b in nums}
    if con_numero and faltantes.get("lim"):
        faltantes["lim"] = [r for r in faltantes["lim"] if r not in con_numero]
    duplicados = {k: v for k, v in asignados.items() if len(v) > 1}

    # Un rol declarado en el contrato pero que ninguna etapa posterior usa no
    # es un problema: es una senal que todavia no se cableo. Lo que importa es
    # lo que falta Y se usa. Ver `roles_en_uso()`.
    try:
        en_uso = roles_en_uso()
    except Exception:
        en_uso = {}
    faltantes_en_uso = {
        cat: [r for r in roles if r in en_uso.get(cat, set())]
        for cat, roles in faltantes.items()
    }
    faltantes_en_uso = {k: v for k, v in faltantes_en_uso.items() if v}

    return {
        "tag_to_pv":    tag_to_pv,
        "tag_to_cruda": tag_to_cruda,
        "tag_to_lim":   tag_to_lim,
        "sp_to_tag":    sp_to_tag,
        "faltantes":    faltantes,
        "faltantes_en_uso": faltantes_en_uso,
        "en_uso":       {k: sorted(v) for k, v in en_uso.items()},
        "duplicados":   duplicados,
        # Cobertura COMPLETA del contrato. Es informativo para la UI: el motor
        # ya no se niega a arrancar por esto (arranca degradado y avisa), asi
        # que "listo: false" significa "faltan tags por asignar", no "roto".
        "listo": not faltantes.get("pv") and not faltantes.get("lim")
                 and not faltantes.get("sp") and not duplicados,
        # Lo que si deberia mirarse antes de confiar en el SE: lo que falta
        # entre lo que el pipeline realmente consume.
        "listo_en_uso": not faltantes_en_uso and not duplicados,
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
        # Pseudonimo por defecto = ultimo segmento del tag. El nombre completo
        # ya lo trae dentro, y asi ninguna variable nace sin etiqueta legible.
        if not (t.get("pseudonimo") or "").strip():
            t["pseudonimo"] = (t.get("name", "") or "").split(".")[-1]

    return data


def _save_tags(data: dict) -> None:
    escribir_json_atomico(TAGS_JSON, data)


# Lock del READ-MODIFY-WRITE de tags.json, no solo de la escritura.
#
# `escribir_json_atomico` garantiza que el archivo nunca queda a medias, pero
# eso no alcanza aca: el patron real es `store = _load_tags()` → modificar →
# `_save_tags(store)`, y sobre eso escriben CUATRO productores — el generador de
# datos, el heartbeat, el reconciliador de SP y las rutas HTTP de la pagina de
# Tags. Sin cubrir el read, dos de ellos leen la misma version, cada uno aplica
# su cambio sobre esa copia y el segundo `_save_tags` publica un archivo entero
# y consistente al que le falta el cambio del primero. Ya se perdieron datos por
# esto una vez.
#
# Es RLock porque hay caminos anidados: una ruta que ya tiene el lock puede
# llamar a un helper que lo vuelve a pedir.
_tags_lock = threading.RLock()


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
                escribir_json_atomico(LICENCIA_JSON, cfg)
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
                escribir_json_atomico(LICENCIA_JSON, cfg)
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
        escribir_json_atomico(LICENCIA_JSON, cfg)
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

# Espaciado minimo entre muestras guardadas, en segundos. Con el SEEngine en
# ciclo libre el motor puede llamar aca decenas de veces por segundo: sin este
# piso, 3600 muestras se consumirian en unos minutos y el Explorador de Series
# perderia su ventana de horas. El ring buffer es de observabilidad, no de
# control: no necesita cada tick, necesita cubrir tiempo.
_HIST_MIN_INTERVALO_S = 1.0

_tag_history: dict[str, deque[dict]] = {}
_tag_history_lock = threading.Lock()


def _record_tag_values(tag_values: dict[str, float]):
    """Agrega valores actuales al ring buffer de historial por tag.

    Decima por tiempo: guarda como mucho una muestra por tag cada
    `_HIST_MIN_INTERVALO_S`. Descartar no pierde informacion de control (el
    pipeline ya trabajo con el valor); solo raleado del grafico.
    """
    ts = time.time()
    with _tag_history_lock:
        for tag_name, val in tag_values.items():
            buf = _tag_history.get(tag_name)
            if buf is None:
                buf = _tag_history[tag_name] = deque(maxlen=_TAG_HISTORY_SIZE)
            elif buf and (ts - buf[-1]["t"]) < _HIST_MIN_INTERVALO_S:
                continue
            buf.append({"t": ts, "v": val})


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
        # Sin semilla: los rangos salen de tags.json y se sincronizan con la
        # tabla de tags. La plantilla RETO.* pertenecia a otra planta.
        self._ranges: dict[str, dict] = {}
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
        with _tags_lock:
            store = _load_tags()
            store["generator"] = {
                "intervalo_s": self._intervalo_s,
                "n_ciclo": self._n_ciclo,
                "ranges": self._ranges,
            }
            _save_tags(store)

    def _worker(self):
        try:
            while not self._stop_event.is_set():
                try:
                    self._write_tick()
                    self._tick += 1
                except Exception as e:
                    self._last_error = str(e)
                    _alerts.add("generator", str(e), traceback.format_exc())
                self._stop_event.wait(self._intervalo_s)
        except BaseException as e:
            self._last_error = f"Worker generador muerto: {e}"
            _alerts.add("generator",
                        f"El hilo del generador murio: {e}",
                        traceback.format_exc())
            raise
        finally:
            self._running = False
            _kep.close_thread_client()      # ver SEEngine._worker (B1.2)

    # Categorias que participan del pipeline del SE (las unicas simulables).
    CATEGORIAS_SIMULABLES = ("pv", "cruda", "lim", "sp")

    def sincronizar_con_tags(self) -> None:
        """Espeja exactamente la tabla de tags. Sin ruido.

        - Aparece: tag habilitado con categoria PV/CRUDA/LIM/SP.
          Entra con `enabled=False` (opt-in) para no pisar por accidente
          una señal que ya viene de planta.
        - Deja de corresponder (tag suspendido o pasado a OTRO): se marca
          `vigente=False` y NO se genera, pero su min/max/ruido se guarda.
          Antes se borraba la entrada, asi que suspender un tag un rato
          costaba volver a tipear sus rangos al reactivarlo.
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

        # Los tags borrados de verdad si se van; los que solo estan suspendidos
        # o pasaron a OTRO quedan dormidos, con su configuracion intacta.
        nombres_tags = {t.get("name") for t in tags}
        for nombre in list(self._ranges):
            if nombre not in vigentes:
                if nombre in nombres_tags:
                    self._ranges[nombre]["vigente"] = False
                else:
                    del self._ranges[nombre]

        # Alta de los nuevos, desactivados.
        for nombre, t in vigentes.items():
            if nombre in self._ranges:
                self._ranges[nombre].setdefault("enabled", True)
                self._ranges[nombre]["categoria"] = t.get("categoria")
                self._ranges[nombre]["vigente"] = True
                continue
            es_lim = t.get("categoria") == "lim"
            self._ranges[nombre] = {
                "min": 0.0,
                "max": 0.0 if es_lim else 100.0,
                "noise": 0.0 if es_lim else 1.0,
                "enabled": False,
                "categoria": t.get("categoria"),
                "vigente": True,
            }
        # Lo sincronizado se persiste: si no, marcar un tag como dormido (o
        # dar de alta uno nuevo) se perdia al reiniciar el proceso.
        self._save_config()

    def _write_tick(self):
        tags_to_write = {}
        for tag_name, cfg in self._ranges.items():
            if not cfg.get("vigente", True):
                continue          # dormido: el tag esta suspendido o es OTRO
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
        self._load_config()
        if self._running:
            if self._thread is not None and self._thread.is_alive():
                return
            _alerts.add("generator",
                        "Generador marcado corriendo pero con el hilo muerto. "
                        "Se rearranca limpio.")
            self._running = False
            self._thread = None
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
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                _alerts.add("generator",
                            "El hilo del generador no respondio al stop. "
                            "Se conserva running=True para impedir un segundo generador.")
                return
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
        # Los dormidos existen en disco pero no se muestran: la tabla de la UI
        # espeja la tabla de tags, y su configuracion vuelve sola al reactivar.
        vigentes = {n: c for n, c in self._ranges.items() if c.get("vigente", True)}
        habilitados = sum(1 for c in vigentes.values() if c.get("enabled", True))
        return {
            "running":     self._running,
            "tick":        self._tick,
            "intervalo_s": self._intervalo_s,
            "intervalo_ms": int(round(self._intervalo_s * 1000)),
            "n_ciclo":     self._n_ciclo,
            "ranges":      vigentes,
            "habilitados": habilitados,
            "total":       len(vigentes),
            "dormidos":    sorted(n for n in self._ranges if n not in vigentes),
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
        with _tags_lock:
            store = _load_tags()
            store["heartbeat"] = self._cfg
            _save_tags(store)

    def _next_value(self):
        return self._cfg["value_a"] if (self._tick % 2 == 0) else self._cfg["value_b"]

    def _worker(self):
        try:
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

                    tag_in = str(self._cfg.get("tag_in") or "").strip()
                    if not tag_in:
                        # Sin eco declarado: es un heartbeat de solo escritura
                        # (patron real del handshake PCS7). No hay nada que
                        # verificar.
                        self._last_in = None
                        self._last_echo_ok = None
                    else:
                        read = _kep.read_tags_batch([tag_in])
                        info = read.get(tag_in, {}) or {}
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
        except BaseException as e:
            self._last_error = f"Worker heartbeat muerto: {e}"
            _alerts.add("heartbeat",
                        f"El hilo del heartbeat murio: {e}. El DCS puede "
                        "reaccionar como si el SE hubiera crasheado.",
                        traceback.format_exc())
            raise
        finally:
            self._running = False
            _kep.close_thread_client()      # ver SEEngine._worker (B1.2)

    def start(self):
        self._load_config()
        # Reset de hilo zombie (worker crasheado entre pulsos).
        if self._running:
            if self._thread is not None and self._thread.is_alive():
                return
            _alerts.add("heartbeat",
                        "Heartbeat marcado corriendo pero con el hilo muerto. "
                        "Se rearranca limpio.")
            self._running = False
            self._thread = None
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
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                _alerts.add("heartbeat",
                            "El hilo del heartbeat no respondio al stop. "
                            "Se conserva running=True para impedir un segundo pulso.")
                return
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
            elif k == "tag_in":
                # tag_in='' es valido: significa heartbeat sin eco (el DCS solo vigila el pulso).
                self._cfg[k] = str(v).strip()
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
# ============================================================
# Grabadores por regla — historial largo de una regla concreta
#
# La traza es un anillo de 60 ticks (segundos, en ciclo libre) y el historial
# de disparos es global y corto. Para responder "¿por que esta regla no
# actua?" hace falta seguir UNA regla durante minutos u horas.
#
# Se graba por TRANSICION, no por tick: repetir 900 veces "bloqueada por wait"
# llenaria el buffer en un minuto y no diria nada. Mientras el estado y el
# motivo no cambian se incrementa un contador de repeticiones; cada disparo,
# en cambio, es siempre una entrada propia, porque es el evento que interesa
# contar. Vive a nivel de modulo, asi que sobrevive a stop/start del motor.
# ============================================================
_GRABADOR_MAX = 500
_GRABADOR_MAX_ACTIVOS = 5
_grabadores: dict[str, deque] = {}
_grabadores_activos: set[str] = set()
_grabador_lock = threading.Lock()


class GrabadorLimiteActivosError(Exception):
    """Ya hay el maximo de reglas grabando en paralelo."""


def grabador_start(regla_id: str) -> dict:
    """Empieza (o reinicia) la grabacion de una regla. Vacia lo anterior."""
    rid = str(regla_id)
    with _grabador_lock:
        if rid not in _grabadores_activos and len(_grabadores_activos) >= _GRABADOR_MAX_ACTIVOS:
            raise GrabadorLimiteActivosError(
                f"Maximo {_GRABADOR_MAX_ACTIVOS} reglas grabando a la vez. "
                "Detene una antes de empezar otra."
            )
        _grabadores[rid] = deque(maxlen=_GRABADOR_MAX)
        _grabadores_activos.add(rid)
    return grabador_estado(rid)


def grabador_stop(regla_id: str) -> dict:
    """Detiene la grabacion. Lo grabado se conserva para poder mirarlo."""
    rid = str(regla_id)
    with _grabador_lock:
        _grabadores_activos.discard(rid)
    return grabador_estado(rid)


def grabador_estado(regla_id: str | None = None) -> dict:
    with _grabador_lock:
        activos = sorted(_grabadores_activos)
        buffers = {k: len(v) for k, v in _grabadores.items()}
    out = {"activos": activos, "buffers": buffers, "max": _GRABADOR_MAX,
           "max_activos": _GRABADOR_MAX_ACTIVOS}
    if regla_id is not None:
        rid = str(regla_id)
        out["regla_id"] = rid
        out["grabando"] = rid in activos
        out["n"] = buffers.get(rid, 0)
    return out


def grabador_historial(regla_id: str) -> dict:
    """Historial de una regla + el resumen que contesta 'cuantas veces actuo'."""
    rid = str(regla_id)
    with _grabador_lock:
        entradas = list(_grabadores.get(rid, ()))
        grabando = rid in _grabadores_activos
        existe = rid in _grabadores

    disparos = [e for e in entradas if e.get("estado") == "disparo"]
    evaluaciones = sum(int(e.get("repeticiones", 1)) for e in entradas)
    return {
        "regla_id": rid,
        "grabando": grabando,
        "existe": existe,
        "max": _GRABADOR_MAX,
        "entradas": entradas,
        "resumen": {
            "eventos": len(entradas),
            "evaluaciones": evaluaciones,
            "disparos": len(disparos),
            "primer_ts": entradas[0]["ts_wall"] if entradas else None,
            "ultimo_ts": entradas[-1].get("ts_wall_ultimo", entradas[-1]["ts_wall"])
                         if entradas else None,
            "ultimo_disparo_ts": disparos[-1]["ts_wall"] if disparos else None,
            "truncado": len(entradas) >= _GRABADOR_MAX,
        },
    }


def _grabar_evaluaciones(evaluadas: list[dict], efectos: dict, tick: int, t_s: float) -> None:
    """Vuelca al grabador lo que le paso a cada regla vigilada en este tick.

    `efectos` es {regla_id: {"acciones": [...], "setpoints": {...}, "ok": bool}}
    para las reglas que dispararon: interesa el efecto real, no solo que la
    regla se cumplio.
    """
    with _grabador_lock:
        if not _grabadores_activos:
            return
        ahora = time.time()
        for r in evaluadas or []:
            rid = str(r.get("id", ""))
            if rid not in _grabadores_activos:
                continue
            buf = _grabadores.setdefault(rid, deque(maxlen=_GRABADOR_MAX))
            estado = str(r.get("estado", ""))
            motivo = str(r.get("motivo", ""))

            if buf and estado != "disparo":
                ult = buf[-1]
                if ult.get("estado") == estado and ult.get("motivo") == motivo:
                    # Misma situacion que el tick anterior: se cuenta, no se apila.
                    ult["repeticiones"] = int(ult.get("repeticiones", 1)) + 1
                    ult["ts_wall_ultimo"] = ahora
                    ult["t_s_ultimo"] = round(float(t_s), 2)
                    ult["tick_ultimo"] = int(tick)
                    ult["belief"] = r.get("belief")
                    continue

            efecto = efectos.get(rid) or {}
            buf.append({
                "ts_wall": ahora,
                "ts_wall_ultimo": ahora,
                "t_s": round(float(t_s), 2),
                "t_s_ultimo": round(float(t_s), 2),
                "tick": int(tick),
                "tick_ultimo": int(tick),
                "estado": estado,
                "motivo": motivo,
                "belief": r.get("belief"),
                "mu_activacion": r.get("mu_activacion"),
                "bloque": r.get("bloque", ""),
                "variables_faltantes": list(r.get("variables_faltantes") or []),
                "wait_bloqueante": r.get("wait_bloqueante"),
                "acciones": list(efecto.get("acciones") or r.get("acciones") or []),
                "setpoints": efecto.get("setpoints") or {},
                "aplicada": efecto.get("ok"),
                "error": efecto.get("error"),
                # Un disparo que no movio el SP no reinicia su wait: hay que
                # poder distinguirlo de uno que si actuo.
                "movio_sp": efecto.get("movio_sp"),
                # Y hay que poder decir POR QUE no movio: tracking, limite,
                # paso 0 de la tabla o familia inhibida son cosas distintas.
                "motivo_sin_efecto": efecto.get("motivo_sin_efecto"),
                "movio_planta": efecto.get("movio_planta"),
                "waits_revertidos": list(efecto.get("waits_revertidos") or []),
                "repeticiones": 1,
            })


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
            # `faltantes` es la cobertura completa del contrato; `faltantes_en_uso`
            # es lo que ademas consume alguna etapa posterior. La pagina destaca
            # el segundo: lo primero puede ser solo instrumentacion pendiente.
            "faltantes": mapeo.get("faltantes", {}),
            "faltantes_en_uso": mapeo.get("faltantes_en_uso", {}),
            "duplicados": list(mapeo.get("duplicados", {}).keys()),
            "listo": mapeo.get("listo", False),
            "listo_en_uso": mapeo.get("listo_en_uso", False),
        },
        "lectura": [], "estancadas": [], "retenidos": [], "filtro": [],
        "limites": {}, "derivadas": {},
        "derivadas_omitidas": [], "pendientes_omitidas": [], "tracking": [],
        "fuzzy": [], "fuzzy_omitidas": [], "permisivos": {}, "reglas": [],
        "waits_activos": [], "disparadas": [], "defuzzy": [], "escritura": {},
    }


def valor_utilizable(info: dict) -> bool:
    """¿Se puede decidir sobre este dato? (B1.4)

    Un unico criterio para las tres lecturas que importan — PV/CRUDA/LIM, el
    valor inicial de un SP y la reconciliacion con el DCS. Estaban cada una con
    su propio `exists and value is not None`, que era el mismo chequeo tres
    veces; ahora que hay una politica de calidad de verdad, tres copias es el
    camino corto a que una quede atras.

    NO cubre el handshake: ahi la exigencia es mas dura (`Good` a secas, sin
    excepcion configurable) porque un permiso que no se puede verificar es un
    permiso denegado. Ver `_chequear_handshake_dcs`.
    """
    if not info.get("exists") or info.get("value") is None:
        return False
    calidad = info.get("quality", "Unknown")
    if calidad == "Good":
        return True
    if calidad == "Uncertain":
        return _kep.get_aceptar_uncertain()
    return False


def _traza_lectura(mapeo: dict, live: dict, fallas: dict,
                   retenidos: list | None = None) -> list[dict]:
    """Una fila por tag mapeado: valor, calidad y si fallo."""
    fallidos = {f["tag"] for grupo in fallas.values() for f in grupo}
    # Un tag retenido no es ninguno de los dos estados que habia. La calidad
    # que muestra la fila es la de ESTA lectura (mala), pero el valor que uso
    # el pipeline es el retenido — mostrar el valor nuevo seria mentir sobre
    # que numero decidio.
    retenidos_por_tag = {r["tag"]: r for r in (retenidos or [])}
    filas = []

    def _add(tag, cat, rol):
        info = live.get(tag, {})
        ret = retenidos_por_tag.get(tag)
        filas.append({
            "tag": tag, "categoria": cat, "rol": rol,
            "valor": (ret["valor"] if ret else info.get("value")),
            "retenido": bool(ret),
            "retenido_edad_s": (ret["edad_s"] if ret else None),
            "quality": info.get("quality", "Unknown"),
            # B1.4: el nombre exacto del StatusCode. "Bad" no le dice a nadie
            # a donde ir; "BadNotConnected" (cable) y "BadUserAccessDenied"
            # (permisos en el KEPserver) son dos viajes distintos.
            "status_code": info.get("status_code", ""),
            "source_ts": info.get("source_ts"),
            "estancado_s": info.get("estancado_s", 0.0),
            "ok": tag not in fallidos,
        })

    for tag, rol in mapeo.get("tag_to_pv", {}).items():
        _add(tag, "pv", rol)
    for tag, rol in mapeo.get("tag_to_cruda", {}).items():
        _add(tag, "cruda", rol)
    for tag, pares in mapeo.get("tag_to_lim", {}).items():
        # Un tag puede acotar varias variables: una fila por cada uso, para que
        # la traza no oculte que el mismo instrumento alimenta a dos escalas.
        for var, bound in pares:
            _add(tag, "lim", f"{var}_{bound}")
    for sp_key, tag in mapeo.get("sp_to_tag", {}).items():
        _add(tag, "sp", sp_key)
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
            # La marca la pone el evaluador de pendientes: los nombres ya no
            # llevan prefijo `pend_`, los elige el operador.
            "es_pendiente": bool(meta.get("es_pendiente")),
            "fuente": meta.get("variable"),
            "ventana_s": meta.get("ventana_s"),
            "slope_per_min": meta.get("slope_per_min"),
            "valor": (round(float(inputs[var]), 4) if var in inputs else None),
            "lmin": lim.get("lmin"),
            "lmax": lim.get("lmax"),
            "dom": meta.get("dom"),
            "pert": {str(k): round(float(v), 3)
                     for k, v in (meta.get("pert") or {}).items() if float(v) > 0},
        })
    return sorted(filas, key=lambda f: (f["es_pendiente"], f["var"]))


def _sp_en_limite(valor, lims) -> str | None:
    """'max' / 'min' si el setpoint esta pegado a su tope del contrato."""
    if not lims or len(lims) != 2 or lims[0] is None or lims[1] is None:
        return None
    try:
        v, ll, hl = float(valor), float(lims[0]), float(lims[1])
    except (TypeError, ValueError):
        return None
    if v >= hl - 1e-9:
        return "max"
    if v <= ll + 1e-9:
        return "min"
    return None


def _traza_valor_tag_sp(tag: str | None, live: dict) -> dict:
    """Lectura del tag SP tal como llego de OPC en este tick (sin retencion)."""
    if not tag:
        return {"valor": None, "quality": "Unknown", "ok": False}
    info = live.get(tag) or {}
    valor = None
    if info.get("value") is not None:
        try:
            valor = float(info["value"])
        except (TypeError, ValueError):
            pass
    return {
        "valor": round(valor, 4) if valor is not None else None,
        "quality": info.get("quality", "Unknown"),
        "ok": valor_utilizable(info),
    }


def _traza_defuzzy_setpoints(setpoints: dict, sp_antes: dict, mapeo: dict,
                             live: dict, sp_escritos: dict,
                             limites_sp: dict) -> list[dict]:
    """Filas del paso 8: planta (tag), escrito (DCS) e interno (SE)."""
    sp_to_tag = mapeo.get("sp_to_tag", {})
    filas = []
    for k, v in setpoints.items():
        tag = sp_to_tag.get(k)
        planta = _traza_valor_tag_sp(tag, live)
        escrito = None
        if tag and tag in sp_escritos:
            escrito = round(float(sp_escritos[tag]), 4)
        interno_antes = round(float(sp_antes.get(k, 0.0)), 4)
        interno_despues = round(float(v), 4)
        interno_delta = round(interno_despues - interno_antes, 4)
        filas.append({
            "sp": k,
            "tag": tag,
            "planta": planta["valor"],
            "planta_ok": planta["ok"],
            "planta_quality": planta["quality"],
            "escrito": escrito,
            "interno": {
                "antes": interno_antes,
                "despues": interno_despues,
                "delta": interno_delta,
            },
            # Compatibilidad: el historial 7b aun usa estos campos como interno.
            "antes": interno_antes,
            "despues": interno_despues,
            "delta": interno_delta,
            "limites": list(limites_sp.get(k, (None, None))),
            "en_limite": _sp_en_limite(v, limites_sp.get(k)),
            "inhibido": False,
            "inhibido_motivo": None,
        })
    return filas


def _traza_par_sp(antes, despues) -> dict:
    """Par antes/despues/delta con redondeo; tolera None."""
    try:
        a = round(float(antes), 3) if antes is not None else None
    except (TypeError, ValueError):
        a = None
    try:
        d = round(float(despues), 3) if despues is not None else None
    except (TypeError, ValueError):
        d = None
    if a is None or d is None:
        delta = None
    else:
        delta = round(d - a, 3)
    return {"antes": a, "despues": d, "delta": delta}


def _traza_setpoints_disparo(sp_prev: dict, setpoints: dict, mapeo: dict,
                             live: dict, escrito_antes: dict,
                             escrito_despues: dict,
                             tags_escritos: set) -> dict:
    """Efecto de un disparo: planta (tag/DCS) e interno (SE), por familia."""
    sp_to_tag = mapeo.get("sp_to_tag", {})
    out: dict = {}
    for k, val in setpoints.items():
        tag = sp_to_tag.get(k)
        planta_info = _traza_valor_tag_sp(tag, live)
        planta_a = planta_info["valor"]
        planta_d = planta_a
        if tag and tag in tags_escritos and tag in escrito_despues:
            planta_d = round(float(escrito_despues[tag]), 4)
        interno = _traza_par_sp(sp_prev.get(k, 0.0), val)
        planta = _traza_par_sp(planta_a, planta_d)
        out[k] = {
            "planta": planta,
            "interno": interno,
            # Compatibilidad: alias del interno.
            "antes": interno["antes"],
            "despues": interno["despues"],
            "delta": interno["delta"],
        }
    return out


def _movio_planta(setpoints_efecto: dict) -> bool:
    """True si algun SP de planta cambio en este disparo (post-escritura)."""
    for det in (setpoints_efecto or {}).values():
        p = det.get("planta") or {}
        delta = p.get("delta")
        if delta is not None and abs(float(delta)) > SP_DEADBAND:
            return True
    return False


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

# Cuanto tiene que moverse un setpoint para que valga la pena reescribirlo al
# DCS. No es una banda de proceso: es el umbral que separa "cambio real" de
# "ruido de coma flotante" al recalcular el mismo valor. Si en planta se
# quiere una banda de verdad (p. ej. 0.1 % de velocidad), este es el lugar.
SP_DEADBAND = 1e-6

# Tope del presupuesto de rampa de un SP. Acota cuanto puede crecer el paso
# permitido cuando ese setpoint estuvo un rato sin escribirse; con 1.0 s, el
# valor declarado en `rate_sp` es tambien el paso maximo de un solo write.
# El razonamiento completo esta en `SEEngine._aplicar_rate_limit`.
RATE_DT_MAX_S = 1.0


class _SaltarPersistencia(Exception):
    """Corta el armado del payload cuando este tick no toca guardar."""

class SEEngine:
    """Background thread que lee tags, corre el pipeline del SE, escribe SPs."""

    # Piso de periodo por defecto, en segundos. El motor corre en CICLO LIBRE:
    # apenas termina de escribir los setpoints vuelve a leer. El piso solo
    # existe para no saturar el CPU ni el KEPserver cuando el pipeline resulta
    # mas rapido que el proceso que observa; 0.0 lo desactiva del todo.
    PISO_S_DEFAULT = 0.05
    PISO_S_MAX = 60.0

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._tick = 0
        self._t_s = 0.0
        # Origen del reloj monotonico. `_t_s` son segundos REALES desde el
        # arranque del motor: es lo que hace que un wait de 900 s dure 900 s.
        self._t0 = time.monotonic()
        self._piso_s = self.PISO_S_DEFAULT

        # Metricas del lazo (observabilidad; el ciclo libre no tiene periodo
        # nominal que mostrar, hay que medirlo).
        self._dur_tick_s = 0.0
        self._dur_tick_max_s = 0.0
        self._periodo_s = 0.0          # media movil del periodo real
        self._dormido_s = 0.0          # cuanto durmio por el piso, ultimo ciclo

        self._setpoints: dict = {}
        self._limites_sp: dict = {}
        # Cableado limite -> tag de los SP (foto del start) y familias que este
        # tick no tienen limites utilizables. Ver `_resolver_limites_sp`.
        self._bindings_sp: dict = {}
        self._bindings_pv: set = set()
        self._sp_sin_limite: dict = {}
        self._limites_num: dict = {}
        self._limites_num_sp: dict = {}
        # Ultimo valor efectivamente escrito al DCS por SP. Es la referencia
        # del write-on-change: si el SP no cambio, no se escribe.
        self._sp_escritos: dict = {}
        self._sp_rate_t: dict = {}
        self._sp_escrituras = 0
        self._sp_omitidos = 0
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
        self._last_estancadas: list = []
        self._last_retenidos: list = []
        self._hubo_aviso_calidad: bool = False
        # tag -> (ultimo valor utilizable, `_t_s` en que se leyo). Es la memoria
        # que sostiene la retencion del ultimo valor bueno.
        self._ultimo_bueno: dict[str, tuple[float, float]] = {}
        # Roles que el pipeline consume de verdad (ver roles_en_uso). Es el
        # criterio para decidir por que faltante se alerta y por cual no.
        self._en_uso: dict = {}
        # Modelos difusos vigentes y su config cruda (para saber que limite
        # necesita cada variable segun su tipo).
        self._fuzzy_modelos: dict = {}
        self._fuzzy_cfg: dict = {}
        # Familias de SP que se calculan pero NO se escriben al DCS, con el
        # motivo. Reemplaza al viejo "el motor no arranca".
        self._sp_inhibidos: dict = {}
        self._t_ultimo_reintento_sp: float = -1e18
        # Variables calculadas en vivo (definiciones + ventanas) y su respaldo
        # de limites. Se rearman en cada _init_state.
        from core.variables.online import VariablesOnline
        from core.fuzzy.pendientes import PendientesOnline
        self._vars_calc = VariablesOnline([])
        self._limites_calc: dict = {}
        # Fuzzy de pendiente configurables (pendientes.json). Antes eran
        # PEND_MODELOS, hardcodeado con las variables del espesador.
        self._pendientes = PendientesOnline({})
        # Tracking SP -> readback. Familias cuyo proceso todavia no alcanzo el
        # setpoint: se dejan de empujar hasta que el DCS se ponga al dia.
        self._tracking: dict = {}
        self._sp_retenidos: dict = {}
        # Siembra bumpless: cada vez que el DCS ENTREGA el lazo (flanco del
        # handshake FBK: denegado -> concedido), el SP de la familia se
        # reescribe con el valor con el que el equipo venia operando.
        # `_sp_semilla_cfg` es {familia: {fuente, tag, pv_key}} y
        # `_sp_semilla_pendiente` las familias armadas por el ultimo flanco y
        # todavia sin sembrar.
        self._sp_semilla_cfg: dict = {}
        self._sp_semilla_pendiente: set = set()
        # Ultimo estado del permiso del DCS. None = todavia sin evaluar; el
        # primer permiso concedido tras arrancar TAMBIEN es entrega de lazo.
        self._handshake_permiso_prev: bool | None = None
        # Familias sembradas en ESTE tick. El tracking las tiene que dejar
        # pasar una vez (ver `_evaluar_tracking`).
        self._sp_recien_sembrados: set = set()
        # Lo que impide arrancar, detectado en _init_state y leido por start().
        self._problemas_arranque: list[str] = []
        # Lo que degrada pero NO impide arrancar (ej. una PV sin fuzzy).
        self._advertencias_arranque: list[str] = []
        # Ultimos disparos, con su hora de reloj. La traza es un anillo de 60
        # ticks: en ciclo libre eso son pocos segundos, asi que una regla con
        # wait de 10 min no se ve disparar NUNCA ahi. Este historial sobrevive
        # al anillo y es lo que contesta "esta funcionando o no".
        self._historial_disparos: deque = deque(maxlen=50)
        # Historial de ESCRITURAS al DCS. La traza es un anillo de 60 ticks
        # (segundos, en ciclo libre) y el write-on-change hace que la mayoria
        # de los ticks no escriban nada: mirando solo la traza es imposible
        # responder "que le mando el experto a la planta en la ultima hora".
        # Esto sobrevive a los ticks y es el registro de auditoria de lo unico
        # que sale del SE hacia el DCS.
        self._historial_escrituras: deque = deque(maxlen=HISTORIAL_ESCRITURAS_MAX)

    def _leer_sp_actuales(self, solo: list[str] | None = None) -> tuple[dict, dict]:
        """Lee del DCS el valor vigente de cada SP del contrato.

        Arranque bumpless: partir de `SETPOINTS_BASE` (valores fijos del
        espesador) pisaba en el primer tick el setpoint que el operador tenia
        puesto. El punto de partida correcto es lo que el DCS ya tiene.

        `solo` limita la lectura a esas familias (lo usa el reintento por tick
        de los SP que no se pudieron leer al arrancar).

        Devuelve (valores, malos) donde `malos` es {sp_key: motivo}: la clave
        importa porque es lo que se inhibe, no solo lo que se muestra.
        """
        sp_tags = self._mapeo.get("sp_to_tag", {})
        if solo is not None:
            sp_tags = {k: v for k, v in sp_tags.items() if k in set(solo)}
        if not sp_tags:
            return {}, {}
        try:
            live = _read_kepserver_tags_batch(list(sp_tags.values()))
        except Exception as e:                       # KEP caido / sin red
            return {}, {k: f"no se pudo leer del DCS: {e}" for k in sp_tags}

        vals, malos = {}, {}
        for sp_key, tag in sp_tags.items():
            info = live.get(tag, {})
            if valor_utilizable(info):
                vals[sp_key] = float(info["value"])
            else:
                malos[sp_key] = (f"no se pudo leer su valor actual ({tag}, "
                                 f"calidad {info.get('quality', 'Unknown')}"
                                 + (f"/{info['status_code']}"
                                    if info.get("status_code") else "") + ")")
        return vals, malos

    def _init_state(self):
        # El motor solo toma el contrato nuevo en cada start(); releer aqui
        # alinea config.py con contrato.json en disco (import, edicion manual,
        # o guardado desde otra pestana) antes de armar el mapeo.
        import config as cfg_mod
        cfg_mod.recargar_contrato()

        from core.filters.exp_q import ExpQFilter
        from runner import (cargar_reglas_json, cargar_permisivos_json,
                            cargar_filtros_json)
        from config import VARIABLES_PROCESO, SETPOINT_KEYS, LIMITES_SP_CONTRATO

        self._mapeo = construir_mapeo()
        # Los modelos difusos salen de fuzzy.json, no del modulo hardcodeado.
        # `or {}` y no `or None`: None hacia que _evaluar_estado_fuzzy cargara
        # FUZZY_MODELOS del espesador, o sea que un fuzzy.json vacio no dejaba
        # al SE sin modelos, lo dejaba con los de OTRA planta.
        try:
            from core.fuzzy.templates import construir_registry_fuzzy
            from runner import cargar_fuzzy_json
            self._fuzzy_modelos = construir_registry_fuzzy(cargar_fuzzy_json()) or {}
        except Exception:
            self._fuzzy_modelos = {}
        # Los roles "en uso" se calculan con los modelos que el motor tiene
        # cargados de verdad, no con el archivo: si difieren, manda el motor.
        self._fuzzy_cfg = {v: {"type": m.get("type")}
                           for v, m in (self._fuzzy_modelos or {}).items()}
        self._en_uso = roles_en_uso(self._fuzzy_cfg)

        # --- Variables calculadas: de variables.json, evaluadas cada tick ---
        # Hasta el 2026-08-21 estas definiciones solo existian en el runner de
        # CSV: se podian crear en la interfaz y el motor en vivo no las
        # calculaba nunca, asi que una regla que nombrara una quedaba muda.
        from core.variables.online import VariablesOnline
        defs_calc = variables_calculadas_definidas()
        self._vars_calc = VariablesOnline(defs_calc)
        self._vars_calc.reset()
        self._limites_calc = limites_fijos_calculadas()

        # Se declaran ACA y no mas abajo: los bloques de pendientes y de
        # tracking que siguen les agregan avisos. Estaban declaradas despues
        # de su primer uso, asi que cualquier aviso (tipico: una familia de
        # tracking que no esta en el contrato) reventaba el arranque entero
        # con UnboundLocalError. El front recibia la pagina de error 500 de
        # Flask e informaba "Unexpected token '<'", que no decia nada.
        problemas: list[str] = []
        advertencias: list[str] = []

        # --- Fuzzy de pendiente: de pendientes.json, uno o varios por PV ---
        from core.fuzzy.pendientes import PendientesOnline
        cfg_pend = pendientes_definidas()
        self._pendientes = PendientesOnline(cfg_pend)
        self._pendientes.reset()
        huerfanas = [n for n, m in cfg_pend.items()
                     if isinstance(m, dict) and n not in self._pendientes.nombres]
        if huerfanas:
            advertencias.append("fuzzy de pendiente mal formados (se ignoran): "
                                + ", ".join(sorted(huerfanas)))

        # --- Tracking SP -> readback ---
        # Hasta el 2026-08-21 `tracking.json` se configuraba y NADIE lo leia en
        # runtime: la pagina prometia una verificacion que no existia.
        self._tracking = {f: c for f, c in tracking_definido().items()
                          if f in SETPOINT_KEYS and c.get("habilitado", True)
                          and str(c.get("pv_key") or "").strip()}
        self._sp_retenidos = {}
        # Siembra bumpless desde la PV. Usa el mismo `pv_key` que declara
        # tracking.json pero NO mira `habilitado`: son dos cosas distintas.
        # El tracking decide si se sigue empujando; la siembra decide con que
        # valor arranca el SP cuando el DCS entrega el lazo, y eso tiene que
        # valer igual con el tracking apagado.
        self._sp_semilla_cfg = {f: c for f, c in arranque_definido().items()
                                if f in SETPOINT_KEYS and c.get("fuente") != "ninguno"}
        # Arrancar el motor ya NO siembra: el disparador es el flanco del
        # ENABLE del DCS. Un arranque con el permiso ya concedido lo detecta
        # igual el flanco (`_handshake_permiso_prev` empieza en None).
        self._sp_semilla_pendiente = set()
        self._handshake_permiso_prev = None
        self._sp_recien_sembrados = set()
        sin_familia = [f for f in tracking_definido() if f not in SETPOINT_KEYS]
        if sin_familia:
            advertencias.append("tracking de familias que no estan en el contrato "
                                "(se ignoran): " + ", ".join(sorted(sin_familia)))

        # Familias de SP que el motor calcula pero NO escribe al DCS. Es la
        # alternativa a negarse a arrancar: el pipeline corre completo y solo
        # se retiene la escritura de lo que no se puede hacer con seguridad.
        self._sp_inhibidos: dict[str, str] = {}

        # --- Setpoints: del contrato, arrancando en lo que el DCS ya tiene ---
        sp_vals, sp_malos = self._leer_sp_actuales()
        for sp_key, motivo in sp_malos.items():
            # Sin poder leer el valor vigente no hay arranque bumpless: escribir
            # partiria de un valor inventado y daria un salto al operador. Se
            # inhibe la escritura y se reintenta la lectura en cada tick.
            self._sp_inhibidos[sp_key] = f"{motivo}; sin arranque bumpless no se escribe"
        # --- Limites de SP: del tag si esta cableado, del contrato si no ---
        # El cableado se fotografia aca, como todo lo demas: el motor toma la
        # configuracion nueva en su proximo start(). Los VALORES, en cambio, se
        # releen en cada tick (`_resolver_limites_sp`), asi que un limite de
        # ingenieria que cambia en el DCS se sigue solo.
        self._bindings_sp = {k: v for k, v in bindings_limites().items()
                             if k[0] in SETPOINT_KEYS}
        self._sp_sin_limite: dict[str, str] = {}
        # Respaldos numericos: los de las PV (fuzzy.json) y los de los SP
        # (defuzzy.json). Solo traen los HABILITADOS a mano. El respaldo nunca
        # le gana al tag; solo cubre el bound que no tiene tag asignado.
        # Cableado de las PV, para saber que bound NO debe caer al respaldo.
        self._bindings_pv = {k for k in bindings_limites()
                             if k[0] not in SETPOINT_KEYS}
        self._limites_num = limites_num(FUZZY_JSON)
        self._limites_num_sp = limites_num(DEFUZZY_JSON)
        # Arranque: todavia no se leyo ningun tag, asi que el clipeo bumpless
        # usa el respaldo. El primer tick lo reemplaza por lo leido.
        self._limites_sp = {k: (v["lmin"], v["lmax"])
                            for k, v in self._limites_num_sp.items()
                            if k in SETPOINT_KEYS and "lmin" in v and "lmax" in v}

        # El valor que trae el DCS puede caer fuera del rango declarado (SP
        # viejo, limite recien cambiado). Se entra al rango de una vez, para no
        # arrancar en un punto que las reglas no pueden corregir.
        def _en_rango(sp_key, valor):
            lims = self._limites_sp.get(sp_key)
            if not lims:
                return valor
            return min(max(float(valor), float(lims[0])), float(lims[1]))

        self._setpoints = {k: _en_rango(k, sp_vals.get(k, 0.0))
                           for k in SETPOINT_KEYS}

        # La falta de limites ya NO se inhibe aca. Antes era un veredicto de
        # arranque y quedaba pegado toda la corrida; con los limites leidos de
        # un tag eso seria falso apenas el tag conteste. Pasa a evaluarse en
        # cada tick (`_resolver_limites_sp` -> `_sp_sin_limite`), asi la familia
        # se libera sola en cuanto sus limites vuelven a ser legibles.
        if self._sp_inhibidos:
            advertencias.append(
                "setpoints inhibidos (se calculan pero NO se escriben al DCS): "
                + "; ".join(f"{k}: {v}" for k, v in sorted(self._sp_inhibidos.items())))

        self._last_action_time = {}
        self._hist = {}
        # OBLIGATORIO limpiarlo junto con el reloj: `_ultimo_bueno` fecha con
        # `_t_s`, que vuelve a 0 en cada arranque. Una marca del arranque
        # anterior daria una edad NEGATIVA — o sea, dentro de la ventana para
        # siempre — y el motor arrancaria reteniendo valores de la corrida
        # pasada sin que nada lo delate.
        self._ultimo_bueno = {}
        self._last_retenidos = []
        self._t0 = time.monotonic()
        self._t_s = 0.0
        self._tick = 0
        self._dur_tick_s = 0.0
        self._dur_tick_max_s = 0.0
        self._periodo_s = 0.0
        self._dormido_s = 0.0
        # Arranque: el primer tick escribe siempre, aunque el SP no cambie.
        self._sp_escritos = {}
        # tag -> `_t_s` de su ultima escritura efectiva. Se limpia con el reloj
        # por el mismo motivo que `_ultimo_bueno`: `_t_s` vuelve a 0.
        self._sp_rate_t = {}
        self._sp_escrituras = 0
        self._sp_omitidos = 0
        self._last_events = []
        self._historial_disparos.clear()
        self._historial_escrituras.clear()
        self._last_error = None

        # Handshake DCS (fail-closed). Si el DCS no habilita el control externo
        # via enable_fbk_tag, ese tick no se escribe ningun SP. La config vive
        # en tags.json bajo "handshake"; con enabled=false el motor opera sin
        # restricciones (util para simulador local y bring-up).
        hs = (_load_tags().get("handshake") or {})
        self._handshake_enabled = bool(hs.get("enabled"))
        self._handshake_fbk_tag = str(hs.get("enable_fbk_tag") or "").strip()
        self._handshake_ext_tag = str(hs.get("enable_ext_tag") or "").strip()
        self._handshake_ultimo = None  # ultimo estado observado, para la traza

        # --- Filtro Exp-Q: de filtros.json, no del default del espesador ---
        # Con el default hardcodeado, cualquier contrato que no fuera el del
        # espesador reventaba con KeyError en TODOS los ticks, porque
        # ExpQFilter.actualizar() rechaza una variable sin config.
        # Una PV sin entrada en filtros.json ya no impide arrancar: se le
        # siembra la sintonizacion por defecto (la misma del boton Sincronizar)
        # y se avisa. Dejarla sin filtro no era opcion — ExpQFilter lanza
        # KeyError con una variable sin config y se caeria cada tick.
        filtros_cfg = cargar_filtros_json()
        sin_filtro = [v for v in VARIABLES_PROCESO if v not in filtros_cfg]
        if sin_filtro:
            for v in sin_filtro:
                filtros_cfg[v] = dict(FILTRO_NUEVO_DEFAULT)
            _guardar_filtros_sembrados(filtros_cfg)
            advertencias.append(
                "PV sin filtro en filtros.json: se sembro la sintonizacion por "
                f"defecto ({FILTRO_NUEVO_DEFAULT['q']} / "
                f"{FILTRO_NUEVO_DEFAULT['ventana_s']} s) en: " + ", ".join(sin_filtro)
                + ". Revisala en la pagina de Filtros.")
        cfg_pv = {v: filtros_cfg[v] for v in VARIABLES_PROCESO if v in filtros_cfg}
        self._filtro = ExpQFilter(cfg_pv) if cfg_pv else None
        if self._filtro is not None:
            self._filtro.reset()

        # --- Modelo difuso por PV: DEGRADA, no impide arrancar ---
        # No toda PV del contrato tiene por que fuzzificarse: algunas se leen
        # como readback (tracking), para graficar o para permisivos. La que no
        # tiene modelo simplemente no se evalua — evaluar_fuzzys recorre el
        # registry, asi que no aparece en fuzzy_out ni en la traza — y una
        # regla que la nombre queda "no evaluable", cosa que el motor ya
        # explica regla por regla. Bloquear el arranque por esto obligaba a
        # inventar una membresia falsa solo para poder correr.
        modelos = self._fuzzy_modelos or {}
        sin_fuzzy = [v for v in VARIABLES_PROCESO if v not in modelos]
        if sin_fuzzy:
            advertencias.append("PV sin modelo difuso (no se fuzzifican): "
                                + ", ".join(sin_fuzzy))

        # --- Roles del contrato sin tag asignado ---
        # Solo se avisa por los que ALGUNA etapa posterior usa. Un rol
        # declarado que nadie consume es una senal pendiente de cablear, no un
        # defecto: avisar por ella entrenaba al operador a ignorar las alertas.
        faltan_en_uso = self._mapeo.get("faltantes_en_uso") or {}
        if faltan_en_uso:
            advertencias.append(
                "roles sin tag asignado que el pipeline SI usa: "
                + " | ".join(f"{cat.upper()}: " + ", ".join(roles)
                             for cat, roles in sorted(faltan_en_uso.items())))
        ociosos = {cat: [r for r in roles if r not in self._en_uso.get(cat, set())]
                   for cat, roles in (self._mapeo.get("faltantes") or {}).items()}
        ociosos = {k: v for k, v in ociosos.items() if v}
        if ociosos:
            advertencias.append(
                "roles sin tag asignado que hoy no usa nadie (el SE corre igual): "
                + " | ".join(f"{cat.upper()}: " + ", ".join(roles)
                             for cat, roles in sorted(ociosos.items())))
        duplicados = self._mapeo.get("duplicados") or {}
        if duplicados:
            advertencias.append(
                "roles con mas de un tag asignado (se usa el primero y se ignora "
                "el resto; corrigelo en Tags KEPserver): "
                + ", ".join(f"{k} -> {', '.join(v)}" for k, v in sorted(duplicados.items())))
        # Un SP ambiguo no se escribe. Para una PV, elegir mal significa leer el
        # sensor equivocado; para un SP significa MOVER el equipo equivocado, y
        # eso no se resuelve con una advertencia que nadie lee. La familia se
        # calcula y se grafica, pero la escritura queda retenida hasta que
        # quede un solo tag con ese rol.
        for clave, tags_dup in sorted(duplicados.items()):
            cat, _, rol = clave.partition("::")
            if cat != "sp" or rol not in SETPOINT_KEYS:
                continue
            self._sp_inhibidos[rol] = (
                f"rol asignado a {len(tags_dup)} tags ({', '.join(tags_dup)}): "
                "ambiguo, no se escribe hasta que quede uno solo")
            _alerts.add("config",
                        f"Setpoint '{rol}' con mas de un tag asignado: no se escribe.",
                        detail="Dejar un solo tag con ese rol en Tags KEPserver. "
                               "Escribir el setpoint en el tag equivocado mueve un "
                               "equipo que nadie pidio mover.")

        self._problemas_arranque = problemas
        self._advertencias_arranque = advertencias

        # --- Tablas Sugeno: del archivo, y en la INSTANCIA ---
        # Antes el tick usaba defuzzy_actions.DEFUZZY_POR_FAMILIA, un dict de
        # modulo que nacia con las tablas del espesador de fabrica y que solo
        # se reemplazaba... al correr una simulacion desde la web. Es decir:
        # el motor movia los setpoints con las tablas de otra planta, o no los
        # movia en absoluto, y "se arreglaba" si alguien pulsaba Simular.
        # Ademas ese clear()+update() pisaba en caliente el dict que este hilo
        # estaba usando. Ahora cada arranque lee el archivo a su propia copia.
        from runner import cargar_defuzzy_json
        self._defuzzy = cargar_defuzzy_json() or {}
        acciones_tabla = {str(a).upper()
                          for t in self._defuzzy.values()
                          for a in (t or {}).get("steps_por_accion", {})}

        self._reglas = cargar_reglas_json()
        self._permisivos_config = cargar_permisivos_json()

        # Acciones que las reglas nombran y ninguna tabla sabe traducir: la
        # regla dispararia, armaria su wait y no moveria nada.
        sin_tabla = sorted({
            str(a.get("accion") if isinstance(a, dict) else a).upper()
            for r in (self._reglas or [])
            for a in (r.get("then") or [])
        } - acciones_tabla - {"NONE", ""})
        if sin_tabla:
            advertencias.append("acciones sin tabla defuzzy (las reglas que las "
                                "usan no moveran ningun setpoint): " + ", ".join(sin_tabla))

        try:
            from web.api.postgres import ensure_tables
            ensure_tables("espesadores")
        except Exception:
            pass

    def _read_tags(self) -> tuple[dict, dict, dict]:
        """Lee PV, CRUDA y LIM del KEPserver. Devuelve (inputs, crudas, limites).

        NUNCA aborta el tick. Lo que no se pudo leer simplemente no esta en el
        dict que devuelve: una PV ausente no se fuzzifica y las reglas que la
        nombran quedan `no_evaluable`; un limite ausente deja a su PV fuera del
        fuzzy. Eso conserva la regla de oro (no decidir sobre datos inventados)
        sin tirar abajo el resto del pipeline, que puede seguir controlando
        perfectamente con las variables que si llegaron.
        """
        TAG_TO_PV    = self._mapeo["tag_to_pv"]
        TAG_TO_CRUDA = self._mapeo["tag_to_cruda"]
        TAG_TO_LIM   = self._mapeo["tag_to_lim"]
        SP_TO_TAG    = self._mapeo.get("sp_to_tag", {})

        all_tags = list(TAG_TO_PV.keys()) + list(TAG_TO_CRUDA.keys()) + list(TAG_TO_LIM.keys())
        # Piggyback del handshake: entra al mismo lote que el resto. Desde B1.2
        # la sesion OPC-UA es persistente, asi que ya no ahorra una conexion,
        # pero sigue valiendo por otra razon: leerlo en el mismo lote garantiza
        # que la autorizacion y las PV sobre las que se decide vengan de la
        # misma pasada, en vez de dos lecturas separadas en el tiempo.
        if self._handshake_enabled and self._handshake_fbk_tag \
                and self._handshake_fbk_tag not in all_tags:
            all_tags.append(self._handshake_fbk_tag)
        # Piggyback de los SP: se leen para detectar intervencion manual del
        # operador desde el DCS. Van en el mismo lote por el mismo motivo que
        # el handshake — una sola pasada de lectura por tick.
        for sp_tag in SP_TO_TAG.values():
            if sp_tag and sp_tag not in all_tags:
                all_tags.append(sp_tag)
        live = _read_kepserver_tags_batch(all_tags)
        self._last_read = live          # lo consume la traza

        # Registro de fallas por rol — lo consume la traza para explicar
        # exactamente que falto, en vez de un "no se pudo leer" generico.
        fallas = {"pv": [], "cruda": [], "lim": []}
        retenidos: list[dict] = []

        def _detalle(info):
            """Motivo legible de la falla. Sin esto la traza dice 'Bad' y nada más."""
            return {"quality": info.get("quality", "Unknown"),
                    "status_code": info.get("status_code", ""),
                    "conectado": bool(info.get("connected"))}

        ventana_ret = _kep.get_retencion_s()

        def _resolver(tag, rol, info):
            """Valor usable de un tag, con retención del último bueno.

            Devuelve el valor o None. Un tag que acaba de llegar bien refresca
            su marca; uno que se cayo sigue valiendo su ultimo valor bueno
            durante `retencion_s` segundos.

            **El reloj es el del motor, no el del servidor.** El `source_ts` de
            este servidor es la hora de la LECTURA, no la del ultimo cambio (ver
            B1.4), asi que no sirve como edad del dato. Se cuenta desde el ultimo
            tick en que el tag estuvo utilizable, igual que `estancado_s`.
            """
            if valor_utilizable(info):
                try:
                    val = float(info["value"])
                except (TypeError, ValueError):
                    pass
                else:
                    self._ultimo_bueno[tag] = (val, self._t_s)
                    return val
            if ventana_ret <= 0:
                return None
            previo = self._ultimo_bueno.get(tag)
            if previo is None:
                return None                 # nunca estuvo bien: no hay nada que retener
            val, t_bueno = previo
            edad = self._t_s - t_bueno
            if edad > ventana_ret:
                return None                 # se agoto la retencion: ahora si desaparece
            retenidos.append({"rol": rol, "tag": tag, "valor": val,
                              "edad_s": round(edad, 2), **_detalle(info)})
            return val

        inputs = {}
        for tag, var in TAG_TO_PV.items():
            info = live.get(tag, {})
            val = _resolver(tag, var, info)
            if val is not None:
                inputs[var] = val
            else:
                fallas["pv"].append({"rol": var, "tag": tag, **_detalle(info)})

        crudas = {}
        for tag, var in TAG_TO_CRUDA.items():
            info = live.get(tag, {})
            val = _resolver(tag, var, info)
            if val is not None:
                crudas[var] = val
            else:
                crudas[var] = 0.0
                fallas["cruda"].append({"rol": var, "tag": tag, **_detalle(info)})

        limites = {}
        for tag, pares in TAG_TO_LIM.items():
            info = live.get(tag, {})
            # Una sola lectura del tag alimenta a todas las variables que acota.
            for var, bound in pares:
                if var not in limites:
                    limites[var] = {}
                val = _resolver(tag, f"{var}_{bound}", info)
                if val is not None:
                    limites[var][bound] = val
                    continue
                # Antes se rellenaba con 0.0 y el tick seguia. Un limite en 0
                # no es "sin dato": es una escala inventada. Con lmin=lmax=0 un
                # fuzzy `norm` da OK=1.0 perfecto para siempre, y un `high`
                # mide el offset contra cero. El motor se veia verde mientras
                # decidia sobre una variable que ya no significaba nada.
                fallas["lim"].append({"rol": f"{var}_{bound}", "tag": tag,
                                      **_detalle(info)})

        self._last_fallas = fallas
        self._last_retenidos = retenidos
        self._last_estancadas = self._detectar_estancadas(live, TAG_TO_PV)
        hubo_aviso = bool(self._last_estancadas
                          and any(e["rol"] in self._en_uso.get("pv", set())
                                  for e in self._last_estancadas))

        if retenidos:
            hubo_aviso = True
            # Sin los segundos en el mensaje: `AlertCollector.add` deduplica por
            # texto exacto y la edad cambia en cada tick (ver A18).
            _alerts.add("calidad",
                        f"Usando el ultimo valor bueno (hasta {ventana_ret:.0f} s) "
                        f"porque la calidad se cayo: "
                        + ", ".join(sorted(r["rol"] for r in retenidos)),
                        detail="Si la calidad no vuelve antes de ese plazo, la "
                               "variable sale del pipeline y las reglas que la "
                               "nombran quedan no_evaluable. Ver el paso 1 de la traza.")

        # Solo se alerta por lo que alguna etapa posterior usa. Una PV que
        # nadie fuzzifica ni nombra en una regla puede faltar sin consecuencia.
        for cat in ("pv", "lim"):
            usados = [f for f in fallas[cat]
                      if f["rol"] in self._en_uso.get(cat, set())]
            if usados:
                hubo_aviso = True
                # El motivo va en el aviso: "Bad/BadNotConnected" y
                # "Uncertain/UncertainLastUsableValue" mandan al operador a
                # lugares muy distintos, y antes las dos decian lo mismo.
                detalle = ", ".join(
                    f"{f['rol']} ({f['quality']}"
                    + (f"/{f['status_code']}" if f.get("status_code") else "")
                    + ")"
                    for f in sorted(usados, key=lambda x: x["rol"]))
                _alerts.add("calidad", f"{cat.upper()} en uso que no se pudieron usar "
                                       f"(quedan fuera del fuzzy): {detalle}")

        self._hubo_aviso_calidad = hubo_aviso
        return inputs, crudas, limites

    def _detectar_estancadas(self, live: dict, tag_to_pv: dict) -> list[dict]:
        """PV cuyo valor no cambia desde hace demasiado. Avisa, no inhibe.

        Es la parte de B1.4 que el backlog planteaba con el SourceTimestamp. No
        se puede: verificado contra el servidor, el SourceTimestamp avanza en
        CADA lectura aunque el valor no se mueva, asi que como detector de
        congelado da siempre "fresco". El conector mide entonces el
        estancamiento del VALOR, que es la mejor senal disponible (un float
        analogico real jitterea en los ultimos bits).

        AVISA Y NO SACA LA VARIABLE DEL PIPELINE, a diferencia de una calidad
        mala. El motivo es que este detector no puede distinguir un scan
        congelado de un proceso genuinamente quieto: un nivel en un tanque lleno
        o un readback de velocidad clavado en su setpoint no se mueven, y estan
        perfectos. Inhibir por esto dejaria al experto mudo justo en regimen
        estacionario, que es cuando mas se lo necesita. Es un diagnostico para
        el instrumentista, no un interlock.

        Solo mira PV: un tag LIM vale 90.0 para siempre y eso es correcto.
        """
        umbral = _kep.get_estancado_alerta_s()
        if umbral <= 0:
            return []
        estancadas = [
            {"rol": var, "tag": tag,
             "estancado_s": float(live[tag].get("estancado_s") or 0.0),
             "valor": live[tag].get("value")}
            for tag, var in tag_to_pv.items()
            if tag in live and float(live[tag].get("estancado_s") or 0.0) >= umbral
        ]
        en_uso = [e for e in estancadas if e["rol"] in self._en_uso.get("pv", set())]
        if en_uso:
            # El mensaje NO lleva los segundos ni el valor a proposito.
            # `AlertCollector.add` deduplica por mensaje EXACTO: con el numero
            # adentro, cada tick generaria una alerta nueva — a ~6 tick/s son
            # cientos de miles por dia en una lista que se recorre entera en
            # cada add. Con el mensaje estable se incrementa `count` y ya. El
            # numero que cambia vive en la traza, que es su lugar.
            _alerts.add("calidad",
                        f"PV sin cambiar de valor desde hace mas de {umbral:.0f} s "
                        f"(posible scan congelado; se siguen usando): "
                        + ", ".join(sorted(e["rol"] for e in en_uso)),
                        detail="El detector no distingue un scan congelado de un "
                               "proceso quieto: avisa y no inhibe. Ver el paso 1 "
                               "de la traza para los segundos y el valor.")
        return sorted(estancadas, key=lambda e: -e["estancado_s"])

    # Cada cuanto se reintenta leer el valor de un SP inhibido por lectura.
    REINTENTO_SP_S = 5.0

    def _reintentar_sp_inhibidos(self) -> None:
        """Recupera las familias inhibidas por no poder leer su valor en el DCS.

        Un SP inhibido por FALTA DE LIMITES no se recupera aqui: eso se
        corrige en contrato.json y se relee al arrancar. El que si se
        reintenta es el que solo dependia de una lectura, para que un corte
        momentaneo del KEPserver no deje la familia muerta hasta el proximo
        stop/start.
        """
        pendientes = [k for k, motivo in getattr(self, "_sp_inhibidos", {}).items()
                      if "no se pudo leer" in motivo]
        if not pendientes:
            return
        if (self._t_s - getattr(self, "_t_ultimo_reintento_sp", -1e18)) < self.REINTENTO_SP_S:
            return
        self._t_ultimo_reintento_sp = self._t_s

        vals, malos = self._leer_sp_actuales(solo=pendientes)
        for sp_key, valor in vals.items():
            lims = self._limites_sp.get(sp_key)
            if lims:
                valor = min(max(float(valor), float(lims[0])), float(lims[1]))
            self._setpoints[sp_key] = float(valor)
            # Arranque bumpless tardio: se toma el valor del DCS como
            # referencia de escritura para no mandarle un salto al operador.
            tag = self._mapeo["sp_to_tag"].get(sp_key)
            # Si la familia todavia espera su siembra desde la PV, NO se toma
            # este valor como referencia de escritura: hacerlo daria por
            # cumplida la primera escritura sin haberla hecho nunca.
            if tag and sp_key not in getattr(self, "_sp_semilla_pendiente", set()):
                self._sp_escritos[tag] = float(valor)
            self._sp_inhibidos.pop(sp_key, None)
            _alerts.add("se_engine", f"SP {sp_key}: valor leido del DCS, "
                                     "escritura habilitada.")

    def _reconciliar_sp_con_dcs(self) -> list[dict]:
        """Adopta como propio el valor del SP si el operador lo movio en el DCS.

        Sin esto, el operador HMI mueve un SP a mano y el SE lo revierte en el
        proximo write-on-change: `_sp_escritos` tiene el valor viejo, el DCS
        tiene el nuevo, y el SE detecta "cambio" en la direccion contraria.

        Solo se reconcilia lo que el SE efectivamente escribio alguna vez
        (`tag in self._sp_escritos`). Sin esa condicion, el primer tick tras
        arrancar detectaria "todo cambio" y adoptaria valores que el arranque
        bumpless ya trato. Las familias inhibidas o retenidas por tracking
        tampoco entran: como el SE no las esta escribiendo, no tiene sentido
        hablar de "intervencion externa" — su valor en el DCS es libre.

        No se clipea el valor adoptado. El operador puede haber movido a
        proposito fuera del rango de reglas; el clipeo natural del defuzzy en
        la proxima escritura ya lo acota. Pisar en silencio la intervencion
        seria peor.

        Devuelve la lista de reconciliaciones aplicadas (para la traza).
        """
        eventos: list[dict] = []
        SP_TO_TAG = self._mapeo.get("sp_to_tag", {})
        if not SP_TO_TAG:
            return eventos
        inhibidos_o_retenidos = set(getattr(self, "_sp_inhibidos", {}).keys()) \
                              | set(getattr(self, "_sp_retenidos", {}).keys())
        for sp_key, tag in SP_TO_TAG.items():
            if sp_key in inhibidos_o_retenidos:
                continue
            if tag not in self._sp_escritos:
                continue
            info = (self._last_read or {}).get(tag) or {}
            # Adoptar un readback de calidad dudosa seria peor que no
            # reconciliar: el SE se llevaria como "lo que quiso el operador" un
            # valor que el servidor mismo no sostiene, y despues lo defenderia.
            if not valor_utilizable(info):
                continue
            try:
                dcs_val = float(info["value"])
            except (TypeError, ValueError):
                continue
            escrito = float(self._sp_escritos[tag])
            if abs(dcs_val - escrito) <= SP_DEADBAND:
                continue
            eventos.append({
                "sp": sp_key, "tag": tag,
                "antes": round(escrito, 4),
                "dcs":   round(dcs_val, 4),
                "delta": round(dcs_val - escrito, 4),
            })
            self._sp_escritos[tag] = dcs_val
            self._setpoints[sp_key] = dcs_val
        if eventos:
            _alerts.add("se_engine",
                        "Intervencion manual detectada — SP adoptados del DCS: "
                        + ", ".join(f"{e['sp']}={e['dcs']}" for e in eventos))
        return eventos

    def _evaluar_tracking(self, valores: dict) -> dict[str, str]:
        """Familias cuyo proceso todavia no alcanzo el setpoint escrito.

        Compara el SP que el SE mando (`_sp_escritos`, no `_setpoints`: lo que
        el DCS realmente recibio) contra su readback. Si la diferencia supera
        el rango declarado, el proceso viene en camino y no tiene sentido
        seguir empujando: se retiene esa familia hasta que se ponga al dia.

        Fail-closed: si el readback no se puede leer, tampoco se empuja. Sin
        poder verificar que el proceso responde, seguir moviendo el setpoint
        es exactamente lo que el tracking existe para impedir.

        Devuelve {familia: motivo}. El resto del pipeline sigue igual: las
        reglas se evaluan, el fuzzy se calcula, y solo la ACCION sobre esa
        familia queda sin efecto.
        """
        retenidos: dict[str, str] = {}
        recien = getattr(self, "_sp_recien_sembrados", set()) or set()
        for familia, cfg in (self._tracking or {}).items():
            # Familia recien sembrada: `_sp_escritos` todavia tiene el SP viejo,
            # asi que el tracking la compararia contra un objetivo que el SE
            # acaba de descartar y la retendria para siempre — el deadlock que
            # la siembra existe para romper. Se la deja escribir una vez; en el
            # tick siguiente ya compara contra el SP sembrado.
            if familia in recien:
                continue
            pv_key = str(cfg.get("pv_key") or "").strip()
            try:
                rango = float(cfg.get("rango", 0.0))
            except (TypeError, ValueError):
                continue
            if rango <= 0.0:
                continue

            if pv_key not in (valores or {}):
                retenidos[familia] = (f"no se pudo leer el readback '{pv_key}': "
                                      "sin verificar, no se empuja")
                _alerts.add("kep", f"Tracking {familia}: readback '{pv_key}' no legible. "
                                   "La escritura de esa familia queda retenida.")
                continue

            tag = self._mapeo["sp_to_tag"].get(familia)
            # Referencia: el ultimo valor ACEPTADO por el DCS. Usar
            # `_setpoints` compararia el proceso contra un valor que el DCS
            # quiza nunca recibio.
            if tag is None or tag not in self._sp_escritos:
                continue
            objetivo = float(self._sp_escritos[tag])
            real = float(valores[pv_key])
            desvio = abs(objetivo - real)
            if desvio > rango:
                retenidos[familia] = (
                    f"el proceso no alcanzo el setpoint: {pv_key}={real:.2f} vs "
                    f"{objetivo:.2f} (desvio {desvio:.2f} > rango {rango:.2f})")
        return retenidos

    def _armar_semilla_por_enable(self) -> bool:
        """Arma la siembra en el FLANCO del ENABLE del DCS (denegado -> dado).

        El disparador de la siembra no es arrancar el motor: es el momento en
        que el DCS ENTREGA el lazo. Entre medio el operador pudo mover la bomba
        a mano durante horas, asi que el SP que el SE tenia guardado ya no
        describe nada — retomar con ese numero es exactamente el salto que la
        siembra existe para evitar.

        Un arranque con el permiso ya concedido cuenta como flanco: para el SE
        es igual de nuevo, porque todavia no escribio nada en esta corrida.

        Sin handshake exigido no hay entrega de lazo que detectar, y entonces
        no se siembra: el SE ya escribe siempre y el SP vive continuo.
        """
        if not self._handshake_enabled or not self._handshake_fbk_tag:
            self._handshake_permiso_prev = None
            return False
        permiso = self._chequear_handshake_dcs() is None
        previo = self._handshake_permiso_prev
        self._handshake_permiso_prev = permiso
        if not permiso or previo is True:
            return False
        # Flanco: se rearma TODA la familia con readback declarado. Lo que
        # quedara pendiente hasta leer su PV lo resuelve la siembra.
        self._sp_semilla_pendiente = set(self._sp_semilla_cfg)
        if self._sp_semilla_pendiente:
            _alerts.add("handshake",
                        "El DCS concedio el control externo: los SP se siembran "
                        "con el valor actual de su PV antes de volver a escribir.",
                        detail="Se rearma en cada flanco del ENABLE (FBK de "
                               "denegado a concedido).")
        return True

    def _valor_de_arranque(self, sp_key: str, valores: dict):
        """Valor con el que retoma esta familia, o None si todavia no se puede.

        `fuente = "tag"` es el caso de planta: el SP con el que el DCS venia
        operando el equipo. Ese tag no tiene por que estar mapeado a un rol del
        SE — es una referencia de lectura, no una entrada del pipeline — asi
        que se lee a demanda en vez de meterlo al barrido de cada tick. Solo se
        lee mientras la siembra esta pendiente, o sea un puñado de ticks por
        entrega de lazo.

        `fuente = "pv"` es el comportamiento historico y sigue disponible para
        una planta sin ese SP de referencia.
        """
        cfg = self._sp_semilla_cfg.get(sp_key) or {}
        fuente = cfg.get("fuente")
        if fuente == "pv":
            pv_key = cfg.get("pv_key")
            if not pv_key or pv_key not in (valores or {}):
                return None, pv_key or "?"
            try:
                return float(valores[pv_key]), pv_key
            except (TypeError, ValueError):
                return None, pv_key
        if fuente == "tag":
            tag_ref = cfg.get("tag")
            if not tag_ref:
                return None, "?"
            info = (self._last_read or {}).get(tag_ref)
            if info is None:
                try:
                    info = (_read_kepserver_tags_batch([tag_ref]) or {}).get(tag_ref) or {}
                except Exception as exc:      # noqa: BLE001
                    _alerts.add("kep", f"Arranque de {sp_key}: no se pudo leer "
                                       f"{tag_ref} ({exc}).")
                    return None, tag_ref
            if not valor_utilizable(info):
                return None, tag_ref
            try:
                return float(info.get("value")), tag_ref
            except (TypeError, ValueError):
                return None, tag_ref
        return None, "?"

    def _sembrar_setpoints(self, valores: dict) -> list[dict]:
        """Al recibir el lazo, el SP arranca en el valor con el que se venia operando.

        Retomar con el SP que el SE tenia guardado no sirve: entre medio el
        operador pudo mover el equipo a mano durante horas, y entonces la
        primera escritura del experto es un salto — y ademas deja al tracking
        en deadlock, porque el proceso nunca converge a un objetivo que nadie
        estuvo siguiendo.

        De donde sale el numero lo decide la config por familia
        (`arranque_definido`): el SP de referencia del DCS, la PV de readback,
        o nada. El valor se clipea a los limites vigentes. De ahi en adelante
        manda el defuzzy hasta el proximo flanco del ENABLE.

        Se reintenta en cada tick mientras la referencia no sea legible; hasta
        que se siembre, la familia no se escribe (ver `_familias_sin_escritura`).
        """
        eventos: list[dict] = []
        self._sp_recien_sembrados = set()
        for sp_key in sorted(self._sp_semilla_pendiente):
            tag = self._mapeo.get("sp_to_tag", {}).get(sp_key)
            valor, origen = self._valor_de_arranque(sp_key, valores)
            if valor is None:
                continue                      # sin referencia legible: reintenta
            lims = self._limites_sp.get(sp_key)
            recortado = False
            if lims:
                dentro = min(max(valor, float(lims[0])), float(lims[1]))
                recortado = abs(dentro - valor) > SP_DEADBAND
                valor = dentro
            antes = float(self._setpoints.get(sp_key, valor))
            self._setpoints[sp_key] = valor
            self._sp_semilla_pendiente.discard(sp_key)
            self._sp_recien_sembrados.add(sp_key)
            eventos.append({
                "sp": sp_key, "tag": tag,
                "fuente": (self._sp_semilla_cfg.get(sp_key) or {}).get("fuente"),
                "origen": origen,
                "antes": round(antes, 4), "despues": round(valor, 4),
                "recortado_a_limite": recortado,
            })
            _alerts.add("se_engine",
                        f"SP {sp_key}: sembrado desde {origen} al recibir "
                        "el lazo del DCS. Arranque bumpless contra el proceso.",
                        detail="Se siembra en cada flanco del ENABLE (FBK de "
                               "denegado a concedido); despues manda el defuzzy.")
        return eventos

    def _diagnostico_sin_efecto(self, acciones_con_belief: list,
                                sp_prev: dict) -> tuple[str, str]:
        """Por que este disparo no movio ningun setpoint, accion por accion.

        Devuelve (detalle, causa). El detalle lleva numeros y va a la traza;
        la causa es una etiqueta ESTABLE para la alerta, porque
        `AlertCollector.add` deduplica por mensaje exacto y un texto con
        beliefs y valores adentro generaria una alerta nueva por tick.

        Antes se contestaba siempre lo mismo — "limite alcanzado o paso 0 en la
        tabla defuzzy" — aunque el motivo real fuera otro (tracking, handshake,
        familia inhibida, sin limites legibles). Ese texto mandaba a mirar el
        lugar equivocado y era el error silencioso mas caro del pipeline: la
        traza decia "limite" mientras la escritura estaba retenida.
        """
        from core.engine.defuzzy import step_por_accion_tabla

        bloqueadas = dict(getattr(self, "_sp_sin_escritura", {}) or {})
        motivos: list[str] = []
        causas: list[str] = []
        for accion, belief in (acciones_con_belief or []):
            try:
                fam, step = step_por_accion_tabla(accion, belief, self._defuzzy)
            except Exception as exc:
                motivos.append(f"{accion}: no se pudo resolver la accion ({exc})")
                causas.append("accion no resoluble")
                continue
            if fam in bloqueadas:
                motivos.append(f"{accion} -> {fam}: {bloqueadas[fam]}")
                causas.append("escritura retenida (la familia no se escribe)")
                continue
            if abs(step) <= SP_DEADBAND:
                motivos.append(f"{accion} -> {fam}: la tabla defuzzy da paso "
                               f"{step:.6g} para belief {float(belief):.3f}")
                causas.append("paso 0 en la tabla defuzzy")
                continue
            lims = self._limites_sp.get(fam)
            actual = float(sp_prev.get(fam, 0.0))
            if lims:
                lmin, lmax = float(lims[0]), float(lims[1])
                if step > 0 and actual >= lmax - SP_DEADBAND:
                    motivos.append(f"{accion} -> {fam}: SP en el limite superior "
                                   f"({lmax:.6g}), el paso {step:+.6g} se recorta")
                    causas.append("SP en el limite superior")
                    continue
                if step < 0 and actual <= lmin + SP_DEADBAND:
                    motivos.append(f"{accion} -> {fam}: SP en el limite inferior "
                                   f"({lmin:.6g}), el paso {step:+.6g} se recorta")
                    causas.append("SP en el limite inferior")
                    continue
            else:
                motivos.append(f"{accion} -> {fam}: la familia no tiene limites "
                               "utilizables, el paso no se aplica")
                causas.append("sin limites utilizables")
                continue
            motivos.append(f"{accion} -> {fam}: paso {step:+.6g} calculado pero el "
                           "SP quedo igual (por debajo de la banda muerta)")
            causas.append("paso por debajo de la banda muerta")
        detalle = "; ".join(motivos) or "el disparo no traia acciones sobre setpoints"
        # Causas unicas y ordenadas: el texto de la alerta tiene que ser el
        # mismo tick tras tick mientras la situacion no cambie.
        causa = ", ".join(sorted(set(causas))) or "sin acciones sobre setpoints"
        return detalle, causa

    def _resolver_limites_sp(self, limites: dict) -> None:
        """Fija los limites vigentes de cada SP con lo leido en ESTE tick.

        Son los que clipean la escritura al DCS. Por bound, en este orden:

          1. hay tag cableado y se pudo leer  -> ese valor;
          2. no hay tag cableado y hay respaldo numerico HABILITADO -> el numero;
          3. hay tag cableado y NO se pudo leer -> la familia se bloquea.
          4. ni tag ni respaldo -> la familia se bloquea.

        El caso 3 es fail-closed a proposito y NO cae al numero del contrato:
        el servidor acaba de decir que ese limite no es confiable, y clipear
        contra un numero viejo seria decidir el tope de escritura con un dato
        que el DCS no sostiene. Es el mismo criterio con el que una PV sin su
        limite sale del fuzzy en vez de fuzzificarse contra una escala
        inventada. La retencion del ultimo valor bueno ya cubre el parpadeo:
        aca solo llega lo que estuvo caido mas de `retencion_s`.

        Una familia bloqueada sale de `self._limites_sp`, asi que
        `apply_actions_tabla` ni siquiera le aplica el paso — el objetivo
        interno tampoco acumula.
        """
        from config import SETPOINT_KEYS

        vigentes: dict[str, tuple] = {}
        bloqueadas: dict[str, str] = {}
        for sp_key in SETPOINT_KEYS:
            leidos = limites.get(sp_key) or {}
            num = self._limites_num_sp.get(sp_key) or {}
            par: dict[str, float] = {}
            faltan: list[str] = []
            for bound in LIMITES_BOUNDS:
                tag = self._bindings_sp.get((sp_key, bound))
                if tag:
                    if bound in leidos:
                        par[bound] = float(leidos[bound])
                    else:
                        faltan.append(f"{bound}: el tag {tag} no se pudo leer")
                elif bound in num:
                    par[bound] = float(num[bound])
                else:
                    faltan.append(f"{bound}: sin tag cableado ni respaldo numerico habilitado")
            if faltan:
                bloqueadas[sp_key] = ("sin limites, no se escribe al DCS sin tope ("
                                      + "; ".join(faltan) + ")")
                continue
            if par["lmin"] >= par["lmax"]:
                bloqueadas[sp_key] = (f"limites invertidos: lmin={par['lmin']:.6g} >= "
                                      f"lmax={par['lmax']:.6g}. Es una escala inventada.")
                continue
            vigentes[sp_key] = (par["lmin"], par["lmax"])

        self._limites_sp = vigentes
        self._sp_sin_limite = bloqueadas
        if bloqueadas:
            # Sin numeros en el texto: `AlertCollector.add` deduplica por mensaje
            # exacto y un limite leido de un tag cambia solo (ver A18).
            _alerts.add("calidad",
                        "Setpoints sin limites utilizables (se calculan pero NO se "
                        "escriben al DCS): " + ", ".join(sorted(bloqueadas)),
                        detail="El motivo por familia esta en el paso 8 de la traza.")

    def _familias_sin_escritura(self) -> dict[str, str]:
        """Familias cuyo SP no se escribira en este tick y su motivo.

        Misma logica que `_write_setpoints`: inhibidos de config, tracking y
        handshake. El objetivo interno no debe acumularse cuando la salida esta
        bloqueada — si no, el experto se desacopla del DCS en silencio.
        """
        bloqueadas = dict(getattr(self, "_sp_inhibidos", {}))
        # Falta de limites: se recalcula en cada tick, asi que la familia se
        # libera sola en cuanto el limite vuelve a ser legible.
        for fam, motivo in (getattr(self, "_sp_sin_limite", {}) or {}).items():
            bloqueadas.setdefault(fam, motivo)
        # Fail-closed hasta la siembra: el DCS entrego el lazo pero todavia no
        # se pudo leer la PV con la que arranca la familia. Escribir ahora
        # mandaria el SP viejo, que es justo lo que la siembra evita. Se libera
        # sola en cuanto la PV se lee.
        for fam in sorted(getattr(self, "_sp_semilla_pendiente", set()) or ()):
            cfg_arr = (getattr(self, "_sp_semilla_cfg", {}) or {}).get(fam) or {}
            ref = cfg_arr.get("tag") if cfg_arr.get("fuente") == "tag" else cfg_arr.get("pv_key")
            bloqueadas.setdefault(
                fam, f"siembra pendiente tras el ENABLE del DCS: esperando "
                     f"'{ref or '?'}' para arrancar el SP en su valor")
        for fam, motivo in (getattr(self, "_sp_retenidos", {}) or {}).items():
            bloqueadas.setdefault(fam, f"tracking: {motivo}")
        if self._handshake_enabled and self._handshake_fbk_tag:
            motivo_hs = self._chequear_handshake_dcs()
            if motivo_hs is not None:
                for sp_key in self._mapeo.get("sp_to_tag", {}):
                    bloqueadas.setdefault(sp_key, f"handshake DCS: {motivo_hs}")
        return bloqueadas

    def _alinear_setpoints_con_escrito(self, familias) -> list[dict]:
        """Adopta `_sp_escritos` como objetivo interno en las familias dadas.

        Solo actua donde ya hay referencia de lo que el DCS acepto. Las familias
        con rampa en curso (objetivo por delante del escrito pero sin bloqueo)
        no entran aqui.
        """
        eventos: list[dict] = []
        for sp_key in familias or ():
            tag = self._mapeo.get("sp_to_tag", {}).get(sp_key)
            if not tag or tag not in self._sp_escritos:
                continue
            escrito = float(self._sp_escritos[tag])
            interno = float(self._setpoints.get(sp_key, escrito))
            if abs(interno - escrito) <= SP_DEADBAND:
                continue
            eventos.append({
                "sp": sp_key,
                "tag": tag,
                "antes": round(interno, 4),
                "despues": round(escrito, 4),
                "delta": round(escrito - interno, 4),
            })
            self._setpoints[sp_key] = escrito
        return eventos

    def _chequear_handshake_dcs(self) -> str | None:
        """Fail-closed: devuelve el motivo por el cual NO se debe escribir SP,
        o None si el DCS habilita el control externo.

        Es fail-closed: sin poder verificar el permiso (tag no existe, sin
        conexion, calidad mala, valor no True), retorna un motivo — asi el
        motor NO escribe. Un handshake que no se puede leer es indistinguible
        de un handshake denegado.

        Lee de `self._last_read` (poblado por _read_tags al inicio del tick):
        no abre una sesion OPC-UA extra.
        """
        tag = self._handshake_fbk_tag
        info = (self._last_read or {}).get(tag) or {}
        if not info:
            self._handshake_ultimo = {"ok": False, "motivo": "sin lectura previa"}
            return f"no se leyo {tag} en este tick"
        if not info.get("connected"):
            self._handshake_ultimo = {"ok": False, "motivo": "sin conexion"}
            return f"sin conexion OPC-UA al leer {tag}"
        if not info.get("exists"):
            self._handshake_ultimo = {"ok": False, "motivo": "tag inexistente"}
            return f"{tag} no existe en KEPserver"
        val = info.get("value")
        quality = str(info.get("quality") or "Good")
        try:
            permiso = bool(val) and quality.lower() == "good"
        except Exception:
            permiso = False
        if not permiso:
            self._handshake_ultimo = {"ok": False,
                                      "motivo": f"denegado (val={val}, q={quality})"}
            return f"{tag}={val} (calidad {quality})"
        self._handshake_ultimo = {"ok": True, "motivo": None}
        return None

    def _pedir_control_dcs(self, pedir: bool) -> None:
        """Escribe enable_ext=True al arrancar / False al detener.

        No aborta el arranque/parada si la escritura falla: el motor puede
        arrancar (aunque el fail-closed impedira escribir SP hasta que el
        DCS confirme). Se registra en alertas para que se vea en la UI.
        """
        if not self._handshake_enabled or not self._handshake_ext_tag:
            return
        accion = "solicitar control externo" if pedir else "soltar control externo"
        try:
            res = _kep.write_tag(self._handshake_ext_tag, bool(pedir), "Boolean")
            if not res.get("ok"):
                _alerts.add("handshake",
                            f"No se pudo {accion} ({self._handshake_ext_tag}={pedir}): "
                            f"{res.get('error') or 'error desconocido'}. "
                            "Los SP seguiran inhibidos hasta que el DCS confirme.")
        except Exception as e:
            _alerts.add("handshake",
                        f"Excepcion al {accion} ({self._handshake_ext_tag}={pedir}): {e}")

    def _write_setpoints(self) -> dict:
        """Escribe al KEPserver los setpoints QUE CAMBIARON (write-on-change).

        Con el lazo en ciclo libre, reescribir el mismo valor en cada vuelta
        significaria golpear el DCS decenas de veces por segundo sin cambiar
        nada. Se compara contra el ultimo valor efectivamente escrito y solo
        se manda la diferencia; si la escritura falla, no se marca como
        escrito, asi el proximo tick lo reintenta.

        La primera pasada tras arrancar escribe todo (`_sp_escritos` vacio),
        que es lo que deja al DCS alineado con el arranque bumpless.

        Devuelve el detalle para la traza.
        """
        # Familias inhibidas: se calculan y se grafican, pero NO se escriben.
        # Es lo que reemplaza al viejo "el SE no arranca": el problema queda
        # acotado a su setpoint en vez de dejar la planta sin experto.
        bloqueadas = self._familias_sin_escritura()

        inhibidos = {sp_key: tag for sp_key, tag in self._mapeo["sp_to_tag"].items()
                     if sp_key in bloqueadas}
        sp_vals = {tag: float(self._setpoints.get(sp_key, 0.0))
                   for sp_key, tag in self._mapeo["sp_to_tag"].items()
                   if sp_key not in inhibidos}
        _record_tag_values(sp_vals)
        detalle_inhibidos = [{"sp": k, "tag": t, "motivo": bloqueadas[k]}
                             for k, t in sorted(inhibidos.items())]

        cambiados = {tag: val for tag, val in sp_vals.items()
                     if tag not in self._sp_escritos
                     or abs(val - self._sp_escritos[tag]) > SP_DEADBAND}
        sin_cambio = [t for t in sp_vals if t not in cambiados]
        self._sp_omitidos += len(sin_cambio)

        cambiados, rampas = self._aplicar_rate_limit(cambiados)
        # Una rampa puede recortar el paso a menos que la banda muerta: entonces
        # no hay nada que escribir en este tick, pero SIGUE habiendo un objetivo
        # pendiente. No se cuenta como "sin cambio" — el proximo tick reintenta.
        cambiados = {t: v for t, v in cambiados.items()
                     if t not in self._sp_escritos
                     or abs(v - self._sp_escritos[t]) > SP_DEADBAND}

        if not cambiados:
            return {"escritos": [], "sin_cambio": sin_cambio, "error": None,
                    "inhibidos": detalle_inhibidos, "rampas": rampas}

        lic = _license_check()
        if not lic["valid"]:
            self._last_error = f"SE bloqueado: {lic['reason']}"
            _alerts.add("licencia", f"Escritura SP bloqueada: {lic['reason']}")
            return {"escritos": [], "sin_cambio": sin_cambio, "rampas": rampas,
                    "inhibidos": detalle_inhibidos, "error": self._last_error}
        try:
            res = _kep.write_float_batch(cambiados) or {}
        except Exception as e:
            self._last_error = f"Write SP: {e}"
            _alerts.add("kep", f"SE Write SP: {e}", traceback.format_exc())
            return {"escritos": [], "sin_cambio": sin_cambio, "rampas": rampas,
                    "inhibidos": detalle_inhibidos, "error": self._last_error}

        # Solo se mueve la referencia de los tags que el DCS ACEPTO. Los que
        # fallaron quedan fuera de _sp_escritos, asi que el write-on-change los
        # ve como pendientes y el proximo tick los reintenta.
        escritos = list(res.get("escritos", list(cambiados)))
        fallidos = dict(res.get("fallidos", {}))
        # Foto del valor previo ANTES de pisar `_sp_escritos`: es lo que
        # permite mostrar "de 50.13 a 50.26" en el historial.
        antes_de_escribir = {t: self._sp_escritos[t]
                             for t in set(escritos) | set(fallidos)
                             if t in self._sp_escritos}
        self._sp_escritos.update({t: cambiados[t] for t in escritos if t in cambiados})
        # Reloj del rate limit: solo avanza cuando la escritura se concreto. Si
        # el DCS rechazo el tag, su presupuesto de rampa sigue acumulando — que
        # es lo correcto, porque el setpoint no se movio.
        for t in escritos:
            self._sp_rate_t[t] = self._t_s
        self._sp_escrituras += len(escritos)

        # --- Auditoria: que se le mando al DCS, cuando, y desde que valor ---
        # Se registran tambien los RECHAZADOS: "el experto quiso mover el SP y
        # el DCS no lo acepto" es justamente el evento que hay que poder
        # reconstruir despues, y si solo se guardaran los exitosos no quedaria
        # ni rastro.
        tag_a_sp = {t: k for k, t in self._mapeo.get("sp_to_tag", {}).items()}
        for tag in sorted(set(escritos) | set(fallidos)):
            ok = tag in escritos
            valor = cambiados.get(tag)
            self._historial_escrituras.append({
                "ts_wall": time.time(),
                "t_s":     round(self._t_s, 2),
                "tick":    self._tick,
                "tag":     tag,
                "sp":      tag_a_sp.get(tag, ""),
                "valor":   round(float(valor), 4) if valor is not None else None,
                "anterior": (round(float(antes_de_escribir[tag]), 4)
                             if tag in antes_de_escribir else None),
                "delta":   (round(float(valor) - float(antes_de_escribir[tag]), 4)
                            if valor is not None and tag in antes_de_escribir else None),
                "ok":      ok,
                "error":   None if ok else fallidos.get(tag),
            })

        error = None
        if fallidos:
            detalle = "; ".join(f"{t}: {m}" for t, m in sorted(fallidos.items()))
            error = f"El DCS rechazo la escritura de {len(fallidos)} setpoint(s). {detalle}"
            self._last_error = error
            _alerts.add("kep", f"SE Write SP: {error}")

        return {"escritos": sorted(escritos), "sin_cambio": sin_cambio,
                "fallidos": sorted(fallidos), "inhibidos": detalle_inhibidos,
                "rampas": rampas, "error": error}

    def _aplicar_rate_limit(self, objetivos: dict) -> tuple[dict, list]:
        """Limita la VELOCIDAD de cambio de cada SP al escribir. Rampa, no descarta.

        Recibe {tag: valor objetivo} y devuelve {tag: valor a escribir ahora}
        mas el detalle para la traza.

        **Rampea, no descarta**, y ahi esta toda la decision. El tracking si
        descarta el paso — a proposito, porque el proceso quedo atras y acumular
        mandaria un salto de varios pasos juntos al liberarse. Un rate limit es
        lo contrario: la accion es valida y el objetivo es correcto, lo unico
        que no se acepta es llegar de un salto. Descartar aca perderia la
        decision del experto; lo que se hace es entregarla en varios ticks.

        Consecuencias buscadas de rampear al ESCRIBIR y no al calcular:

        - `self._setpoints` (el objetivo interno) avanza completo, asi que el
          mecanismo de *el wait cuenta solo si la regla actuo* ve que el SP se
          movio y NO revierte el wait. Correcto: la regla actuo de verdad, solo
          que su efecto viaja en rampa.
        - `_sp_escritos` guarda lo que el DCS acepto, que es lo que compara
          `_reconciliar_sp_con_dcs`. Un SP a mitad de rampa no se lee como
          intervencion manual del operador.
        - El write-on-change reintenta solo: mientras quede diferencia entre el
          objetivo y lo escrito, el tag sigue apareciendo como cambiado.
        """
        from config import RATE_SP_CONTRATO
        if not RATE_SP_CONTRATO or not objetivos:
            return objetivos, []

        # Rate por TAG: el contrato lo declara por familia de SP.
        rate_por_tag = {tag: RATE_SP_CONTRATO[fam]
                        for fam, tag in self._mapeo["sp_to_tag"].items()
                        if fam in RATE_SP_CONTRATO}
        if not rate_por_tag:
            return objetivos, []

        # Presupuesto = rate x tiempo desde la ultima escritura de ESTE tag,
        # PERO acotado a `RATE_DT_MAX_S`. El tope es la parte importante y se
        # descubrio verificando en el contenedor: sin el, el presupuesto se
        # acumula durante las pausas — tracking reteniendo, handshake denegado,
        # write rechazado, motor recien arrancado — y al liberarse el DCS recibe
        # de una sola vez todo lo acumulado. Medido: tras 5 s retenido por
        # tracking, el SP saltaba 3.94 unidades en un solo write. Eso es
        # exactamente lo que un limite de velocidad existe para impedir, y la
        # pausa es cuando mas dana: el equipo lleva un rato quieto.
        #
        # Con el tope en 1.0 s, `rate_sp` gana una segunda lectura util y facil
        # de explicar: es tambien **el paso maximo de un solo write**. La rampa
        # sigue avanzando a `rate` u/s porque el tick dura decimas de segundo.
        # (Si alguien subiera `piso_s` por encima de 1 s, la rampa iria mas
        # lenta que el rate declarado. Es el lado conservador del error.)
        salida = dict(objetivos)
        rampas = []
        for tag, objetivo in objetivos.items():
            rate = rate_por_tag.get(tag)
            if rate is None:
                continue
            actual = self._sp_escritos.get(tag)
            if actual is None:
                # Primera escritura tras arrancar. NO se rampea: es el arranque
                # bumpless, que justamente parte del valor vigente en el DCS —
                # rampear hacia el valor que el DCS ya tiene no tiene sentido.
                continue
            t_prev = self._sp_rate_t.get(tag)
            dt = self._t_s if t_prev is None else max(0.0, self._t_s - t_prev)
            paso_max = rate * min(dt, RATE_DT_MAX_S)
            delta = float(objetivo) - float(actual)
            if paso_max <= 0 or abs(delta) <= paso_max:
                continue
            permitido = actual + (paso_max if delta > 0 else -paso_max)
            salida[tag] = permitido
            rampas.append({"tag": tag, "objetivo": round(float(objetivo), 4),
                           "escrito": round(float(permitido), 4),
                           "falta": round(float(objetivo) - permitido, 4),
                           "rate": rate, "dt_s": round(dt, 3)})
        return salida, rampas

    def _run_tick(self):
        """Ejecuta un tick del pipeline del SE."""
        from runner import _evaluar_estado_fuzzy, extraer_inputs_desde_row
        from permisivos import evaluar_permisivos, inyectar_permisivos_en_fuzzy_out
        import motor as motor_mod
        from core.engine.defuzzy import apply_actions_tabla
        from config import VARIABLES_PROCESO, COLUMNAS_ENTRADA
        import pandas as pd

        # Reloj REAL del motor. Antes era `self._t_s += self._intervalo_s`, un
        # contador ficticio que avanzaba 5.0 por tick sin importar cuanto
        # habia tardado: los waits de 900 s no duraban 900 s y la ventana de
        # pendientes no medía 60 s. Con el lazo en ciclo libre eso ya no tiene
        # ni siquiera un valor que sumar. Se toma UNA vez al principio del
        # tick para que todas las etapas fechen con el mismo instante.
        self._t_s = time.monotonic() - self._t0
        # Lo que salio mal en ESTE tick. Al final se publica en _last_error en
        # vez de borrarlo a ciegas: el `self._last_error = None` incondicional
        # borraba, entre otras cosas, los fallos de escritura al DCS.
        tick_error: str | None = None

        # --- Traza del tick (observabilidad; no altera ninguna decision) ---
        tz = _nueva_traza(self._tick, self._t_s, self._mapeo)

        inputs_raw, crudas, limites = self._read_tags()
        tz["lectura"] = _traza_lectura(self._mapeo, self._last_read,
                                       self._last_fallas, self._last_retenidos)
        tz["estancadas"] = self._last_estancadas
        tz["retenidos"] = self._last_retenidos

        # Limites de los SP con lo leido en ESTE tick. Va antes de todo lo que
        # decide sobre setpoints: el clipeo, el rango bumpless del reintento y
        # el defuzzy miran `self._limites_sp`.
        self._resolver_limites_sp(limites)
        tz["limites_sp"] = {
            "vigentes": {k: [round(v[0], 4), round(v[1], 4)]
                         for k, v in sorted(self._limites_sp.items())},
            "bloqueadas": dict(sorted(self._sp_sin_limite.items())),
            "cableados": {f"{k[0]}_{k[1]}": v
                          for k, v in sorted(self._bindings_sp.items())},
        }

        # Reintento de los SP que no se pudieron leer al arrancar. Sin esto,
        # un KEPserver que tardo un segundo de mas en responder dejaba la
        # familia inhibida hasta que alguien reiniciara el motor a mano.
        self._reintentar_sp_inhibidos()

        # Resincronizacion con el DCS: si el operador movio un SP a mano en
        # el HMI, el SE lo adopta como propio. Va ANTES del defuzzy para que
        # las reglas partan del valor real del DCS, no del ultimo que el SE
        # escribio.
        tz["resync_sp"] = self._reconciliar_sp_con_dcs()

        if self._last_fallas.get("cruda"):
            roles = [f["rol"] for f in self._last_fallas["cruda"]
                     if f["rol"] in self._en_uso.get("cruda", set())]
            if roles:
                self._hubo_aviso_calidad = True
                _alerts.add("calidad", "Sensores crudos no legibles (valen 0.0 y degradan "
                                       f"permisivos y variables calculadas): "
                                       + ", ".join(sorted(roles)))
        # El filtro pesa cada muestra por su edad en segundos, asi que el
        # suavizado configurado en filtros.json vale igual corra el lazo a
        # 5 s o a 50 ms. Sin filtro (contrato sin ninguna PV) el tick sigue
        # con las manos vacias en vez de abortar: no hay nada que suavizar.
        inputs = (self._filtro.actualizar(inputs_raw, t_s=self._t_s)
                  if self._filtro is not None else dict(inputs_raw))

        tz["filtro"] = [
            {"rol": v, "crudo": round(float(inputs_raw[v]), 4),
             "filtrado": round(float(inputs.get(v, inputs_raw[v])), 4)}
            for v in sorted(inputs_raw)
        ]
        tz["limites"] = {v: {b: round(float(x), 4) for b, x in bounds.items()}
                         for v, bounds in limites.items()}

        # `inputs` va DESPUES a proposito: si un nombre existe como PV y como
        # entrada cruda, manda el valor de la PV, que viene filtrado por Exp-Q
        # y leido de su propio tag. Al reves, un 0.0 de una cruda sin mapear
        # pisaria silenciosamente la medicion buena.
        # --- Variables calculadas, EN VIVO ---
        # Se calculan sobre la PV FILTRADA (`inputs`), que es lo que ve el
        # resto del pipeline: una calculada no deberia reaccionar a ruido que
        # el filtro Exp-Q ya decidio ignorar. Tambien ven las crudas y los
        # setpoints, y se encadenan en el orden de variables.json.
        base_calc = {**crudas, **inputs, **self._setpoints, "t_s": self._t_s}
        calculadas, calc_omitidas = self._vars_calc.actualizar(base_calc, self._t_s)
        tz["derivadas"] = {k: round(float(v), 4) for k, v in calculadas.items()}
        tz["derivadas_omitidas"] = calc_omitidas

        # Una calculada es una PV mas aguas abajo: se fuzzifica, la nombran
        # las reglas y los permisivos la leen.
        valores = {**inputs, **calculadas}

        # --- Entrega de lazo: el SP arranca en su PV ---
        # El flanco del ENABLE arma la siembra y la siembra la aplica, las dos
        # ANTES del tracking y de las reglas: si no, el tick evaluaria contra
        # un objetivo que ya no es el que va a salir al DCS.
        if self._armar_semilla_por_enable():
            tz["semilla_flanco_enable"] = True
        semilla = self._sembrar_setpoints(valores)
        if semilla:
            tz["semilla_sp"] = semilla
        if self._sp_semilla_pendiente:
            tz["semilla_pendiente"] = [
                {"sp": f,
                 "fuente": (self._sp_semilla_cfg.get(f) or {}).get("fuente", ""),
                 "origen": ((self._sp_semilla_cfg.get(f) or {}).get("tag")
                            if (self._sp_semilla_cfg.get(f) or {}).get("fuente") == "tag"
                            else (self._sp_semilla_cfg.get(f) or {}).get("pv_key", ""))}
                for f in sorted(self._sp_semilla_pendiente)]

        # --- Respaldo numerico de las PV (fuzzy.json -> limites_num) ---
        # `setdefault`: el tag SIEMPRE gana. El respaldo solo cubre el bound
        # que no tiene tag cableado, y solo si alguien lo encendio a mano. Un
        # bound CON tag que no se pudo leer NO cae aca a proposito: el servidor
        # acaba de decir que ese limite no es confiable, y fuzzificar contra un
        # numero viejo seria decidir sobre una escala que el DCS no sostiene.
        for var, bounds in self._limites_num.items():
            destino = limites.setdefault(var, {})
            for bound, val in bounds.items():
                if (var, bound) in self._bindings_pv:
                    continue
                destino.setdefault(bound, val)

        # Limites de las calculadas: manda el tag si esta mapeado y se leyo;
        # si no, el respaldo fijo de variables.json. Sin ninguno de los dos, la
        # variable existe y se puede usar en un permisivo, pero no se fuzzifica.
        for var, bounds in self._limites_calc.items():
            if var not in calculadas:
                continue
            destino = limites.setdefault(var, {})
            for bound, val in bounds.items():
                destino.setdefault(bound, val)

        row_data = {**crudas, **valores}
        for var, bounds in limites.items():
            # Solo se publica el limite que SE LEYO. Rellenar con 0 el que
            # falta es inventar una escala: con lmin=lmax=0 un fuzzy `norm` da
            # OK=1.0 perfecto para siempre.
            for bound in ("lmin", "lmax"):
                if bound in bounds:
                    row_data[f"{var}_{bound}"] = bounds[bound]
        for sp_key, val in self._setpoints.items():
            row_data[sp_key] = val
        row_data["t_s"] = self._t_s
        row = pd.Series(row_data)

        # --- Que se puede fuzzificar en ESTE tick ---
        # Una PV entra al fuzzy solo si llego su valor Y los limites que su
        # tipo de modelo necesita (`high` usa lmax, `low` lmin, `norm` los
        # dos). El registry se recorta a esas variables: asi evaluar_fuzzys
        # nunca ve un limite faltante y las demas reglas siguen corriendo.
        modelos_tick, sin_datos = {}, []
        for var, meta in (self._fuzzy_modelos or {}).items():
            bounds = limites.get(var, {})
            faltan = [b for b in _LIMITES_POR_TIPO_FUZZY.get(
                          str(meta.get("type", "")).lower(), ("lmin", "lmax"))
                      if b not in bounds]
            if var not in valores:
                sin_datos.append(f"{var} (sin lectura)")
            elif faltan:
                sin_datos.append(f"{var} (sin {', '.join(faltan)})")
            else:
                modelos_tick[var] = meta
        tz["fuzzy_omitidas"] = sin_datos

        # --- Pendientes: una o varias por variable, con su propia ventana ---
        # Se calculan sobre `valores`, asi que una pendiente puede seguir
        # tanto a una PV como a una variable calculada.
        pend_out, pend_omitidas = self._pendientes.actualizar(valores, self._t_s)
        tz["pendientes_omitidas"] = pend_omitidas

        fuzzy_out = _evaluar_estado_fuzzy(
            row, self._hist,
            columnas_entrada=COLUMNAS_ENTRADA,
            meta_flags=None,
            inputs_override=valores,
            # Las calculadas no estan en VARIABLES_PROCESO, asi que hay que
            # nombrarlas explicitamente o `_evaluar_estado_fuzzy` las filtra.
            variables_proceso=list(valores),
            limites_override=limites,
            fuzzy_modelos=modelos_tick,
            # Vacio a proposito: PEND_MODELOS era el dict hardcodeado del
            # espesador. Las pendientes de verdad vienen de pendientes.json y
            # se pasan ya calculadas, para que entren al fuzzy_out ANTES de
            # expandir las etiquetas compuestas (si no, se quedarian sin sus
            # NO-<X> y una regla que use NO-INC no podria evaluarse).
            pend_modelos={},
            pend_extra=pend_out,
        )

        # `tz["derivadas"]` ya se lleno con las calculadas de verdad. Antes
        # listaba los _lmin/_lmax y los setpoints, que no son derivadas de
        # nada: la etapa 3 de la traza mostraba datos y no calculaba ninguno.
        tz["fuzzy"] = _traza_fuzzy(fuzzy_out, valores, limites)

        estados_perm = evaluar_permisivos(
            self._permisivos_config,
            fuzzy_out=fuzzy_out,
            row=row,
            inputs=valores,
            setpoints=self._setpoints,
            columnas_entrada=COLUMNAS_ENTRADA,
        )
        fuzzy_out = inyectar_permisivos_en_fuzzy_out(fuzzy_out, estados_perm)
        tz["permisivos"] = {str(k): str(v) for k, v in (estados_perm or {}).items()}

        # --- Tracking SP -> readback ---
        # Se evalua ANTES de aplicar: una familia cuyo proceso todavia no
        # alcanzo el setpoint no se sigue empujando. Sin esto el valor interno
        # sube mientras el proceso quedo atras, y cuando el tracking libera se
        # manda un salto de varios pasos juntos.
        self._sp_retenidos = self._evaluar_tracking(valores)
        tz["tracking"] = [{"sp": k, "motivo": v}
                          for k, v in sorted(self._sp_retenidos.items())]

        # Fase 2: sin salida al DCS, el objetivo interno no acumula pasos
        # fantasma. Se alinea a lo ultimo aceptado antes de evaluar reglas.
        self._sp_sin_escritura = self._familias_sin_escritura()
        alineados = self._alinear_setpoints_con_escrito(self._sp_sin_escritura)
        if alineados:
            tz["setpoints_alineados"] = alineados

        sp_antes = dict(self._setpoints)

        motor_out = motor_mod.evaluar_reglas(
            self._reglas, fuzzy_out, self._t_s,
            self._last_action_time, min_belief=0.05
        )

        self._last_action_time = motor_out.get("last_action_time", self._last_action_time)
        # Por que NO dispararon las demas reglas (reporte aditivo del motor)
        tz["reglas"] = motor_out.get("evaluadas", [])

        self._last_events = []
        tick_events = []
        escrito_antes_tick = dict(self._sp_escritos)
        live_tick = self._last_read or {}
        for evento in motor_out.get("fired", []):
            acciones_con_belief = [(a, evento.get("belief", 0.5)) for a in evento.get("acciones", [])]
            ev_ok = True
            ev_error = None
            # Estado de los SP JUSTO antes de esta accion. `sp_antes` es del
            # inicio del tick y no sirve: con dos reglas disparando en el
            # mismo barrido, la segunda heredaria el delta de la primera.
            sp_prev = dict(self._setpoints)
            if acciones_con_belief:
                try:
                    # apply_actions NO muta: devuelve una copia con los pasos
                    # aplicados y clipeados. Antes se llamaba tirando el
                    # resultado, asi que la regla disparaba, armaba su wait y
                    # el setpoint no se movia nunca — el DCS jamas veia la
                    # accion. Hay que reasignar lo que devuelve.
                    nuevos = apply_actions_tabla(acciones_con_belief, self._setpoints,
                                                 self._limites_sp, self._defuzzy)
                    # Familias sin escritura (tracking, handshake, inhibidos):
                    # el paso del defuzzy se descarta, no se acumula en memoria.
                    for fam in self._sp_sin_escritura:
                        if fam in nuevos:
                            nuevos[fam] = self._setpoints.get(fam, nuevos[fam])
                    self._setpoints.update(nuevos)
                except Exception as exc:
                    ev_ok = False
                    ev_error = str(exc)
                    # Un disparo que no puede aplicarse tiene que doler: antes
                    # solo quedaba un flag dentro de la traza y /api/se/status
                    # seguia diciendo running=true, last_error=null mientras el
                    # SE no movia un solo setpoint.
                    tick_error = f"Accion no aplicable ({evento.get('id', '?')}): {exc}"
                    _alerts.add("se_engine", tick_error)

            # --- El wait solo cuenta si la regla ACTUO ---
            # El wait le da tiempo al proceso a responder a un cambio. Si no
            # hubo cambio no hay nada que esperar, y dejarlo armado silencia a
            # la regla durante minutos por una accion que no ocurrio. Pasa de
            # verdad: SP pegado a su limite (el clipeo se come el paso), tabla
            # defuzzy que da 0 para ese belief, o accion que fallo.
            movidos = {k: round(float(v) - float(sp_prev.get(k, 0.0)), 6)
                       for k, v in self._setpoints.items()
                       if abs(float(v) - float(sp_prev.get(k, 0.0))) > SP_DEADBAND}
            revertidos = []
            motivo = None
            if not movidos:
                revertidos = motor_mod.revertir_waits(evento, self._last_action_time)
                # El motivo REAL, accion por accion. Se calcula siempre que el
                # disparo no movio nada — no solo cuando ademas revirtio un
                # wait — porque es el dato que explica la traza.
                detalle, causa = self._diagnostico_sin_efecto(
                    acciones_con_belief, sp_prev)
                motivo = ev_error or detalle
                if revertidos and acciones_con_belief:
                    _alerts.add("se_engine",
                                f"Regla {evento.get('id', '?')}: disparo sin efecto, "
                                f"su wait no se reinicia — {causa}.",
                                detail="El detalle con valores esta en el paso 7 "
                                       "de la traza (motivo del disparo sin efecto).")
            tick_events.append({
                "regla_id": evento.get("id", "?"),
                "bloque":   evento.get("bloque", ""),
                "acciones": evento.get("acciones", []),
                "belief":   round(evento.get("belief", 0), 3),
                "ok":       ev_ok,
                "error":    ev_error,
                # Observabilidad del punto anterior: sin esto, "disparo" y
                # "disparo que no hizo nada" se ven exactamente igual.
                "movio_sp": bool(movidos),
                # Por que no movio. Vacio cuando si movio.
                "motivo_sin_efecto": motivo,
                "delta_sp": movidos,
                "waits_revertidos": revertidos,
                "sp_prev": sp_prev,
            })

        if tick_events:
            self._last_events = tick_events[-5:]

        # DESPUES de aplicar las acciones: un disparo sin efecto revierte su
        # wait, y la foto tiene que mostrar los waits que quedaron de verdad.
        tz["waits_activos"] = _traza_waits(self._last_action_time, self._t_s)

        tz["disparadas"] = tick_events

        tz["escritura"] = self._write_setpoints()
        post_alineados = self._alinear_setpoints_con_escrito(
            {d["sp"] for d in (tz["escritura"].get("inhibidos") or [])})
        if post_alineados:
            tz.setdefault("setpoints_alineados", []).extend(post_alineados)

        tz["defuzzy"] = _traza_defuzzy_setpoints(
            self._setpoints, sp_antes, self._mapeo,
            live_tick, self._sp_escritos, self._limites_sp,
        )

        tags_escritos = set(tz["escritura"].get("escritos") or [])
        for ev in tick_events:
            sp_prev_ev = ev.pop("sp_prev", sp_antes)
            ev["setpoints"] = _traza_setpoints_disparo(
                sp_prev_ev, self._setpoints, self._mapeo, live_tick,
                escrito_antes_tick, self._sp_escritos, tags_escritos,
            )
            ev["movio_planta"] = _movio_planta(ev["setpoints"])
            # Se agrupa por REPETICION, igual que el grabador por regla. Una
            # regla que dispara sin efecto no rearma su wait y vuelve a
            # disparar en el tick siguiente: en ciclo libre son ~10 filas por
            # segundo, y el anillo de 50 quedaba lleno de la misma fila en 5 s,
            # tapando todo lo anterior. Mientras la regla, sus acciones y el
            # desenlace no cambien, se cuenta en la misma entrada.
            ahora = time.time()
            entrada = {
                **ev,
                "t_s": round(self._t_s, 2),
                "ts_wall": ahora,
                "ts_wall_ultimo": ahora,
                "tick": self._tick,
                "tick_ultimo": self._tick,
                "repeticiones": 1,
            }
            firma = (
                str(entrada.get("regla_id")),
                tuple(entrada.get("acciones") or []),
                bool(entrada.get("ok")),
                str(entrada.get("error") or ""),
                bool(entrada.get("movio_sp")),
                bool(entrada.get("movio_planta")),
                # Un cambio de motivo (de "tracking" a "limite", por ejemplo)
                # abre fila nueva: si no, el historial mostraria el primer
                # motivo para siempre aunque la causa ya sea otra.
                str(entrada.get("motivo_sin_efecto") or ""),
            )
            ult = self._historial_disparos[-1] if self._historial_disparos else None
            if ult is not None and ult.get("_firma") == firma:
                ult["repeticiones"] = int(ult.get("repeticiones", 1)) + 1
                ult["ts_wall_ultimo"] = ahora
                ult["t_s_ultimo"] = entrada["t_s"]
                ult["tick_ultimo"] = self._tick
                # Se conserva el ULTIMO valor: es el estado actual de la regla.
                ult["belief"] = entrada.get("belief")
                ult["setpoints"] = entrada.get("setpoints")
                ult["delta_sp"] = entrada.get("delta_sp")
                ult["motivo_sin_efecto"] = entrada.get("motivo_sin_efecto")
            else:
                entrada["_firma"] = firma
                entrada["t_s_ultimo"] = entrada["t_s"]
                self._historial_disparos.append(entrada)

        # Grabadores por regla: efecto enriquecido tras la escritura.
        _grabar_evaluaciones(
            tz["reglas"],
            {str(ev["regla_id"]): {
                "acciones": ev.get("acciones", []),
                "ok": ev.get("ok"),
                "error": ev.get("error"),
                "movio_sp": ev.get("movio_sp"),
                "motivo_sin_efecto": ev.get("motivo_sin_efecto"),
                "movio_planta": ev.get("movio_planta"),
                "waits_revertidos": ev.get("waits_revertidos", []),
                "setpoints": ev.get("setpoints", {}),
            } for ev in tick_events},
            self._tick, self._t_s,
        )
        inhibidos_sp = {d["sp"]: d["motivo"]
                        for d in (tz["escritura"].get("inhibidos") or [])}
        for row in tz["defuzzy"]:
            sp_key = row["sp"]
            tag = row.get("tag")
            if sp_key in inhibidos_sp:
                row["inhibido"] = True
                row["inhibido_motivo"] = inhibidos_sp[sp_key]
            if tag and tag in self._sp_escritos:
                row["escrito"] = round(float(self._sp_escritos[tag]), 4)
        # Handshake DCS al final del tick — la traza ya vio si termino
        # bloqueando la escritura o no. Se publica aparte de `escritura` porque
        # el pulso Exp_HB sigue vivo aunque el permiso baje.
        if self._handshake_enabled:
            tz["handshake"] = {
                "enabled": True,
                "fbk_tag": self._handshake_fbk_tag,
                "ext_tag": self._handshake_ext_tag,
                "ultimo":  self._handshake_ultimo,
            }
        if tz["escritura"].get("error"):
            tick_error = tz["escritura"]["error"]
        _traza_push(tz)

        try:
            from web.api.postgres import persist_tick, persistencia_debida
            # La persistencia tiene su propio reloj (ver web/api/postgres.py).
            # Se consulta ANTES de armar el payload porque armarlo implica
            # releer tags.json del disco: en ciclo libre eso serian decenas de
            # lecturas por segundo para tirar el resultado a la basura.
            if not persistencia_debida():
                raise _SaltarPersistencia
            entrada_vals = {}
            for tag, var in self._mapeo["tag_to_pv"].items():
                if var in inputs_raw:
                    entrada_vals[tag] = inputs_raw[var]
            for tag, var in self._mapeo["tag_to_cruda"].items():
                if var in crudas:
                    entrada_vals[tag] = crudas[var]
            for tag, pares in self._mapeo["tag_to_lim"].items():
                for var, bound in pares:
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
        except _SaltarPersistencia:
            pass                      # tick fuera de fotograma: normal
        except Exception as e:
            _alerts.add("general", f"PostgreSQL: {e}")

        # Solo se declara "todo bien" si el tick no tuvo problemas. Antes esto
        # era incondicional y borraba el error de escritura que _write_setpoints
        # acababa de dejar 40 lineas mas arriba.
        self._last_error = tick_error
        if tick_error is None:
            _alerts.resolve_category("se_engine")
            _alerts.resolve_category("kep")
        # La calidad se cierra por su cuenta: un tick que termina bien no dice
        # nada sobre los instrumentos, y un tick que termina mal no significa
        # que la calidad se haya arreglado. Solo se limpia cuando la lectura de
        # este tick no tuvo nada que avisar.
        if getattr(self, "_hubo_aviso_calidad", False) is False:
            _alerts.resolve_category("calidad")

    def _worker(self):
        """Ciclo libre: leer -> pipeline -> escribir SP -> volver a empezar.

        Antes habia un `wait(intervalo_s)` fijo despues de cada tick, con dos
        problemas: el periodo real era `intervalo + duracion del tick` (nunca
        el que se pedia), y el motor se quedaba dormido aunque el proceso
        hubiera cambiado. Ahora el unico retardo es el piso, y se DESCUENTA lo
        que tardo el tick, de modo que el piso es un periodo minimo de verdad
        y no un tiempo muerto que se suma.
        """
        t_prev = time.monotonic()
        try:
            while not self._stop_event.is_set():
                t_ini = time.monotonic()
                try:
                    self._run_tick()
                    self._tick += 1
                except Exception as e:
                    self._last_error = str(e)
                    _alerts.add("se_engine", str(e), traceback.format_exc())

                self._dur_tick_s = time.monotonic() - t_ini
                if self._dur_tick_s > self._dur_tick_max_s:
                    self._dur_tick_max_s = self._dur_tick_s

                # Periodo real medido, suavizado: en ciclo libre no hay periodo
                # nominal que reportar, solo el que se logra.
                periodo = t_ini - t_prev
                t_prev = t_ini
                self._periodo_s = (periodo if self._periodo_s <= 0.0
                                   else 0.9 * self._periodo_s + 0.1 * periodo)

                # Piso: solo se duerme lo que falte para completarlo.
                restante = self._piso_s - self._dur_tick_s
                self._dormido_s = max(0.0, restante)
                if restante > 0:
                    self._stop_event.wait(restante)
        except BaseException as e:
            self._last_error = f"Worker muerto: {e}"
            _alerts.add("se_engine",
                        f"El hilo del motor murio: {e}. Los SP dejan de escribirse.",
                        traceback.format_exc())
            raise
        finally:
            # Marca honesta: si el hilo termina por lo que sea, `running` cae.
            # Antes se dejaba en True y start() lo tomaba como "ya andando",
            # devolvia ok sin arrancar hilo nuevo, y la planta quedaba muda.
            self._running = False
            # La sesion OPC-UA es persistente y vive atada a ESTE hilo (B1.2).
            # Sin cerrarla, parar el motor la dejaria abierta en el KEPserver
            # hasta que venciera su session_timeout de una hora, y cada
            # stop/start iria sumando una sesion huerfana mas.
            _kep.close_thread_client()

    def start(self, piso_s: float | None = None,
              intervalo_s: float | None = None) -> dict:
        """Arranca el motor. Devuelve {"ok": bool, "error": str|None}.

        `piso_s` es el PERIODO MINIMO entre ticks, no el periodo del lazo: el
        motor corre libre y solo respeta ese piso. `intervalo_s` es el nombre
        viejo del parametro, cuando el lazo tenia periodo fijo; se sigue
        aceptando como alias para no romper llamadas existentes.

        El motor ARRANCA DEGRADADO en vez de negarse. Antes se plantaba si el
        mapeo tag<->rol estaba incompleto, si una PV no tenia filtro o si un SP
        no tenia limites — y como el catalogo de roles sale del contrato, bastaba
        con declarar una variable que todavia no estaba instrumentada para dejar
        al SE entero sin arrancar. Ahora cada falta degrada solo lo suyo:

          - PV sin tag o sin limites -> no se fuzzifica; las reglas que la
            nombran quedan `no_evaluable` con el motivo en la traza.
          - PV sin filtro -> se le siembra la sintonizacion por defecto.
          - SP sin limites, o cuyo valor actual no se pudo leer -> se inhibe SU
            escritura al DCS (se sigue calculando), nunca la del resto.

        Lo unico que se sigue exigiendo es no inventar datos: una variable que
        no se puede leer no entra al pipeline, no se rellena con 0.0.
        """
        if piso_s is None:
            piso_s = intervalo_s if intervalo_s is not None else self.PISO_S_DEFAULT
        piso_s = min(max(0.0, float(piso_s)), self.PISO_S_MAX)

        # Un `_running=True` con hilo muerto significa que el worker crasheo
        # entre ticks. Antes se devolvia ok=True y no se arrancaba nada; el
        # operador veia "corriendo" y la planta estaba muda. Ahora se resetea
        # el estado y se arranca limpio.
        if self._running:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": True, "error": None}
            _alerts.add("se_engine",
                        "Se detecto un motor marcado como corriendo pero con el hilo "
                        "muerto. Se rearranca limpio.")
            self._running = False
            self._thread = None

        self._piso_s = piso_s
        self._init_state()

        # `_problemas_arranque` queda para lo que de verdad haga imposible
        # correr. Hoy no lo llena nadie: todo lo que antes bloqueaba ahora
        # degrada. Se conserva el camino por si aparece un caso nuevo.
        if self._problemas_arranque:
            msg = "El SE no puede arrancar. " + " | ".join(self._problemas_arranque)
            self._last_error = msg
            _alerts.add("se_engine", msg)
            return {"ok": False, "error": msg}

        # Las advertencias no frenan el arranque, pero tienen que verse: el
        # operador debe saber que hay una PV que el SE no esta fuzzificando o
        # un SP que se calcula y no se escribe.
        if self._advertencias_arranque:
            _alerts.add("se_engine",
                        "El SE arranco degradado. " + " | ".join(self._advertencias_arranque))

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._running = True
        self._last_error = None
        # Handshake: pedir al DCS el control externo. El primer tick del motor
        # leera enable_fbk; si el DCS todavia no confirmo, ese tick no escribe SP.
        self._pedir_control_dcs(True)
        return {"ok": True, "error": None}

    def stop(self) -> dict:
        """Detiene el motor. Devuelve {"ok": bool, "error": str|None}.

        Si el hilo no responde al join, NO se marca `_running=False`. Con esa
        marca, un start() posterior lanzaria un segundo hilo mientras el
        primero sigue escribiendo SP al DCS — dos motores compitiendo por los
        mismos tags. Es preferible dejar `_running=True` con un error visible:
        el operador reintenta stop() o reinicia el proceso, pero no queda un
        motor zombie escribiendo en la sombra.
        """
        if not self._running:
            return {"ok": True, "error": None}
        # Handshake: soltar el control ANTES de matar el hilo. Asi el DCS ve
        # enable_ext=false y no queda esperando un experto muerto.
        self._pedir_control_dcs(False)
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                err = ("El hilo del motor no respondio al stop (join timeout). "
                       "Se mantiene `running=True` para impedir que un nuevo "
                       "start() lance un segundo motor. Reintente detener o "
                       "reinicie el proceso.")
                self._last_error = err
                _alerts.add("se_engine", err)
                return {"ok": False, "error": err}
        # El finally del worker ya puso _running=False; se conserva por si el
        # hilo termino antes de ser lanzado (raro pero posible).
        self._running = False
        self._thread = None
        return {"ok": True, "error": None}

    def status(self) -> dict:
        # En ciclo libre no hay periodo nominal que informar: el periodo es
        # una MEDICION. `piso_s` es el unico parametro; el resto sale del lazo.
        thread_alive = bool(self._thread is not None and self._thread.is_alive())
        return {
            "running":      self._running,
            "thread_alive": thread_alive,
            "tick":         self._tick,
            "t_s":          round(self._t_s, 1),
            "setpoints":    {k: round(v, 3) for k, v in self._setpoints.items()},
            "last_events":  self._last_events,
            "last_error":   self._last_error,
            # Degradaciones aceptadas (ej. PV sin fuzzy): el SE corre igual.
            "advertencias": list(self._advertencias_arranque),
            # `_firma` es interna (agrupacion de repetidos): no viaja a la API.
            "ultimos_disparos": [{k: v for k, v in d.items() if k != "_firma"}
                                 for d in list(self._historial_disparos)[-10:]],
            "piso_s":       self._piso_s,
            "piso_ms":      int(round(self._piso_s * 1000)),
            "periodo_ms":   round(self._periodo_s * 1000, 1),
            "ticks_por_s":  round(1.0 / self._periodo_s, 2) if self._periodo_s > 0 else 0.0,
            "dur_tick_ms":  round(self._dur_tick_s * 1000, 1),
            "dur_tick_max_ms": round(self._dur_tick_max_s * 1000, 1),
            "dormido_ms":   round(self._dormido_s * 1000, 1),
            "sp_escrituras": self._sp_escrituras,
            "sp_omitidos":   self._sp_omitidos,
            # Mas nuevas primero: es como se lee un registro de auditoria.
            "historial_escrituras": list(self._historial_escrituras)[::-1],
            "handshake": {
                "enabled": bool(getattr(self, "_handshake_enabled", False)),
                "enable_fbk_tag": getattr(self, "_handshake_fbk_tag", ""),
                "enable_ext_tag": getattr(self, "_handshake_ext_tag", ""),
                "ultimo": getattr(self, "_handshake_ultimo", None),
            },
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
EXPORT_IMPORT_PAGE = _load_template("export_import.html")
GRAFICOS_PAGE   = _load_template("graficos.html")
TRAZA_PAGE      = _load_template("traza.html")
HISTORIAL_PAGE  = _load_template("historial.html")
ALERTAS_HIST_PAGE = _load_template("alertas_historial.html")
ESCRITURAS_PAGE = _load_template("escrituras.html")

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

    # 2b. Migracion de los roles LIM viejos al cableado nuevo (idempotente).
    #     Va DESPUES de validar los JSON: si fuzzy.json esta corrupto, adoptar
    #     bindings sobre lo que _leer_json devuelve ({}) borraria roles sin
    #     migrar nada.
    try:
        mig = migrar_roles_lim_a_bindings()
        if mig["migrados"]:
            _alerts.add("config",
                        f"Se migraron {len(mig['migrados'])} limite(s) del campo 'rol' "
                        "del tag al fuzzy/defuzzy que los usa.",
                        detail="; ".join(f"{m['tag']} -> {m['rol']}"
                                         for m in mig["migrados"]))
        if mig["pendientes"]:
            _alerts.add("config",
                        "Tags LIM con rol viejo cuya variable todavia no tiene fuzzy ni "
                        "tabla defuzzy: se adoptan solos cuando la crees.",
                        detail="; ".join(f"{m['tag']} -> {m['rol']}"
                                         for m in mig["pendientes"]))
        mig_sp = migrar_limites_sp_del_contrato()
        if mig_sp:
            _alerts.add("config",
                        "Los limites de escritura de los setpoints se movieron de "
                        "contrato.json al defuzzy que los usa, y ahora se editan en "
                        "la pagina de Defuzzificacion.",
                        detail="; ".join(f"{m['familia']}: {m['limites']}"
                                         for m in mig_sp))
    except Exception as e:
        _alerts.add("config", f"No se pudo migrar los limites: {e}",
                    traceback.format_exc())

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
