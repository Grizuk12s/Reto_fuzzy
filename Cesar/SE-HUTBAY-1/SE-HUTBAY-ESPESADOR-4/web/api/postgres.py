# -*- coding: utf-8 -*-
"""Blueprint bp_postgres — Conexion y persistencia PostgreSQL.

Rutas:
  GET  /api/postgres/config         Lee la configuracion guardada
  PUT  /api/postgres/config         Guarda la configuracion
  POST /api/postgres/test           Prueba la conexion
  GET  /api/postgres/tables         Estado de las tablas
  POST /api/postgres/ensure-tables  Crea tablas si no existen

Funciones de persistencia (llamadas desde SEEngine):
  ensure_tables(proceso)   Crea tablas entrada/salida si no existen
  persist_tick(...)        Inserta valores de un tick del SE
"""
from __future__ import annotations

import json
import os
import threading
import time

from flask import Blueprint, jsonify, request

from core.jsonio import escribir_json_atomico
from web.state import _CFG_DIR

bp_postgres = Blueprint("postgres", __name__)

POSTGRES_JSON = os.path.join(_CFG_DIR, "postgres.json")

VALID_PROCESOS = {"espesadores", "molienda", "flotacion"}

# ------------------------------------------------------------
# Periodo de persistencia
# ------------------------------------------------------------
# El SEEngine corre en CICLO LIBRE: llama a persist_tick apenas termina de
# escribir los setpoints, decenas de veces por segundo. Insertar una fila por
# tag en cada vuelta seria, con 27 tags a 20 tick/s, del orden de 46 millones
# de filas por dia — y, peor, la base pasaria a fijar la velocidad del lazo de
# control. Por eso la persistencia tiene su PROPIO reloj: el motor gira libre y
# la BD recibe un fotograma cada `persist_periodo_ms`.
#
# Los ticks descartados no pierden decisiones: el SE ya actuo sobre ellos. Lo
# que se ralea es el registro historico, que para un espesador no necesita
# resolucion de milisegundos.
PERIODOS_PERSISTENCIA_MS = (500, 1000)
PERSIST_PERIODO_MS_DEFAULT = 1000

_DEFAULTS = {
    "host": "localhost",
    "port": 5432,
    "database": "",
    "user": "",
    "password": "",
    "last_status": "unconfigured",
    "last_message": "",
    "persist_enabled": True,
    "persist_periodo_ms": PERSIST_PERIODO_MS_DEFAULT,
}

# ------------------------------------------------------------
# Estado vivo de la persistencia
# ------------------------------------------------------------
# La pagina /espesador/postgres validaba solo la CONEXION, que puede estar
# perfecta mientras no se guarda una sola fila (motor detenido, tablas
# faltantes, periodo mal puesto). Esto es lo que permite validar tambien que
# la persistencia efectivamente esta ocurriendo.
_persist_lock = threading.Lock()
_persist_stats: dict = {
    "ultima_escritura_ts": None,   # epoch de la ultima insercion exitosa
    "ultimo_error": None,
    "ultimo_error_ts": None,
    "escrituras_ok": 0,
    "escrituras_error": 0,
    "ticks_recibidos": 0,          # cuantas veces la llamo el motor
    "ticks_omitidos": 0,           # cuantas se saltaron por el periodo
    "filas_insertadas": 0,
    "ultima_duracion_ms": None,
}
_persist_last_t = 0.0              # time.monotonic() de la ultima insercion


def _persist_periodo_s() -> float:
    cfg = _load_pg_config()
    try:
        ms = int(cfg.get("persist_periodo_ms", PERSIST_PERIODO_MS_DEFAULT))
    except (TypeError, ValueError):
        ms = PERSIST_PERIODO_MS_DEFAULT
    if ms not in PERIODOS_PERSISTENCIA_MS:
        ms = PERSIST_PERIODO_MS_DEFAULT
    return ms / 1000.0


# ============================================================
# Config load / save
# ============================================================

# Cache con testigo de mtime: `persist_tick` consulta la config en cada vuelta
# del motor y en ciclo libre eso serian decenas de lecturas de disco por
# segundo. Se relee solo cuando el archivo cambia de verdad.
_cfg_cache: dict | None = None
_cfg_cache_mtime: float = -1.0
_cfg_cache_lock = threading.Lock()


def _load_pg_config() -> dict:
    global _cfg_cache, _cfg_cache_mtime
    try:
        mtime = os.path.getmtime(POSTGRES_JSON)
    except OSError:
        return dict(_DEFAULTS)

    with _cfg_cache_lock:
        if _cfg_cache is not None and mtime == _cfg_cache_mtime:
            return dict(_cfg_cache)

    try:
        with open(POSTGRES_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(_DEFAULTS)
    if not isinstance(data, dict):
        return dict(_DEFAULTS)

    # Un postgres.json anterior a la decimacion no trae estos campos.
    cfg = dict(_DEFAULTS)
    cfg.update(data)

    with _cfg_cache_lock:
        _cfg_cache = dict(cfg)
        _cfg_cache_mtime = mtime
    return cfg


def _save_pg_config(data: dict) -> None:
    global _cfg_cache, _cfg_cache_mtime
    escribir_json_atomico(POSTGRES_JSON, data)
    with _cfg_cache_lock:      # invalida: el proximo load relee
        _cfg_cache = None
        _cfg_cache_mtime = -1.0


# ============================================================
# Connection helper (creates a fresh connection each call)
# ============================================================

def _connect():
    cfg = _load_pg_config()
    if not cfg.get("database") or not cfg.get("user"):
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=cfg.get("host", "localhost"),
            port=cfg.get("port", 5432),
            dbname=cfg["database"],
            user=cfg["user"],
            password=cfg.get("password", ""),
            connect_timeout=5,
        )
        conn.autocommit = True
        return conn
    except Exception:
        return None


# ============================================================
# Table management
# ============================================================

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    id            BIGSERIAL      PRIMARY KEY,
    tag_name      VARCHAR(200)   NOT NULL,
    pseudonimo    VARCHAR(200),
    proceso       VARCHAR(100),
    instrumento   VARCHAR(200),
    unidad_ing    VARCHAR(100),
    equipo        VARCHAR(200),
    valor         DOUBLE PRECISION,
    timestamp     TIMESTAMPTZ    DEFAULT NOW()
)
"""


def ensure_tables(proceso: str) -> bool:
    if proceso not in VALID_PROCESOS:
        return False
    conn = _connect()
    if conn is None:
        return False
    try:
        cur = conn.cursor()
        for suffix in ("entrada", "salida"):
            cur.execute(_TABLE_DDL.format(table=f"{proceso}_{suffix}"))
        cur.close()
        conn.close()
        return True
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return False


# ============================================================
# Persistence (called from SEEngine per tick)
# ============================================================

def persist_tick(
    entrada_values: dict[str, float],
    salida_values: dict[str, float],
    tag_meta: dict[str, dict],
    proceso: str = "espesadores",
    forzar: bool = False,
) -> bool:
    """Guarda un fotograma del tick, respetando el periodo de persistencia.

    El motor la llama en CADA vuelta del ciclo libre; esta funcion decide si
    esa vuelta se guarda o se descarta. `forzar=True` salta el periodo (lo usa
    la validacion de la pagina, que necesita escribir ya).

    Devuelve True solo si realmente inserto filas.
    """
    global _persist_last_t
    if proceso not in VALID_PROCESOS:
        return False

    cfg = _load_pg_config()
    ahora = time.monotonic()

    with _persist_lock:
        _persist_stats["ticks_recibidos"] += 1
        if not forzar:
            if not cfg.get("persist_enabled", True):
                _persist_stats["ticks_omitidos"] += 1
                return False
            if (ahora - _persist_last_t) < _persist_periodo_s():
                _persist_stats["ticks_omitidos"] += 1
                return False
        # Se reserva el turno ANTES de escribir: si la insercion tarda mas que
        # el periodo, el motor no encola una escritura por tick encima.
        _persist_last_t = ahora

    t_ini = time.monotonic()
    conn = _connect()
    if conn is None:
        _registrar_error("Sin conexion a PostgreSQL (revisa la configuracion).")
        return False
    try:
        from psycopg2.extras import execute_values
        cur = conn.cursor()
        filas = 0
        for suffix, values in [("entrada", entrada_values), ("salida", salida_values)]:
            if not values:
                continue
            records = []
            for tag_name, valor in values.items():
                meta = tag_meta.get(tag_name, {})
                records.append((
                    tag_name,
                    meta.get("pseudonimo"),
                    proceso,
                    meta.get("instrumento"),
                    meta.get("unidad_ing"),
                    meta.get("equipo"),
                    valor,
                ))
            execute_values(
                cur,
                f"INSERT INTO {proceso}_{suffix} "
                "(tag_name, pseudonimo, proceso, instrumento, unidad_ing, equipo, valor) "
                "VALUES %s",
                records,
            )
            filas += len(records)
        cur.close()
        conn.close()
        with _persist_lock:
            _persist_stats["escrituras_ok"] += 1
            _persist_stats["filas_insertadas"] += filas
            _persist_stats["ultima_escritura_ts"] = time.time()
            _persist_stats["ultima_duracion_ms"] = round(
                (time.monotonic() - t_ini) * 1000, 1)
            _persist_stats["ultimo_error"] = None
        return True
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        _registrar_error(str(e))
        return False


def persistencia_debida() -> bool:
    """¿Le toca guardar a este tick? Consulta barata, sin efectos.

    Existe para que el motor no arme el payload (que implica releer tags.json)
    en cada vuelta del ciclo libre solo para que persist_tick lo descarte.
    `persist_tick` vuelve a chequear el periodo, asi que si dos hilos entran a
    la vez el resultado sigue siendo correcto: como mucho se pierde un
    fotograma, nunca se duplica.
    """
    cfg = _load_pg_config()
    if not cfg.get("persist_enabled", True):
        return False
    return (time.monotonic() - _persist_last_t) >= _persist_periodo_s()


def _registrar_error(msg: str) -> None:
    with _persist_lock:
        _persist_stats["escrituras_error"] += 1
        _persist_stats["ultimo_error"] = msg
        _persist_stats["ultimo_error_ts"] = time.time()


def estado_persistencia() -> dict:
    """Foto del estado de la persistencia, para la pagina de PostgreSQL."""
    cfg = _load_pg_config()
    with _persist_lock:
        st = dict(_persist_stats)

    periodo_ms = int(round(_persist_periodo_s() * 1000))
    ahora = time.time()
    edad_s = (ahora - st["ultima_escritura_ts"]) if st["ultima_escritura_ts"] else None

    # "Al dia" = escribio hace menos de 3 periodos. Con margen, para no marcar
    # en rojo por una demora puntual de la base.
    if st["ultima_escritura_ts"] is None:
        salud = "sin_datos"
    elif edad_s is not None and edad_s <= (periodo_ms / 1000.0) * 3:
        salud = "ok"
    else:
        salud = "atrasada"

    total = st["ticks_recibidos"] or 1
    return {
        "enabled": bool(cfg.get("persist_enabled", True)),
        "periodo_ms": periodo_ms,
        "periodos_disponibles": list(PERIODOS_PERSISTENCIA_MS),
        "salud": salud,
        "edad_ultima_escritura_s": round(edad_s, 2) if edad_s is not None else None,
        "ultima_escritura_ts": st["ultima_escritura_ts"],
        "ultima_duracion_ms": st["ultima_duracion_ms"],
        "escrituras_ok": st["escrituras_ok"],
        "escrituras_error": st["escrituras_error"],
        "filas_insertadas": st["filas_insertadas"],
        "ticks_recibidos": st["ticks_recibidos"],
        "ticks_omitidos": st["ticks_omitidos"],
        "pct_omitidos": round(100.0 * st["ticks_omitidos"] / total, 1),
        "ultimo_error": st["ultimo_error"],
        "ultimo_error_ts": st["ultimo_error_ts"],
    }


# ============================================================
# API routes
# ============================================================

@bp_postgres.route("/api/postgres/config", methods=["GET"])
def get_config():
    return jsonify(_load_pg_config())


@bp_postgres.route("/api/postgres/config", methods=["PUT"])
def save_config():
    body = request.get_json(force=True)
    cfg = _load_pg_config()
    for key in ("host", "port", "database", "user", "password"):
        if key in body:
            cfg[key] = body[key]
    _save_pg_config(cfg)
    return jsonify({"ok": True})


@bp_postgres.route("/api/postgres/test", methods=["POST"])
def test_connection():
    body = request.get_json(force=True)
    host = body.get("host", "localhost")
    port = int(body.get("port", 5432))
    database = body.get("database", "")
    user = body.get("user", "")
    password = body.get("password", "")

    if not database or not user:
        return jsonify({"ok": False, "error": "Faltan campos obligatorios (database, user)."})

    try:
        import psycopg2
    except ImportError:
        return jsonify({
            "ok": False,
            "error": "psycopg2 no esta instalado. Ejecuta: pip install psycopg2-binary",
        })

    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=database,
            user=user, password=password, connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT version();")
        version = cur.fetchone()[0]
        cur.close()
        conn.close()

        cfg = _load_pg_config()
        cfg.update({
            "host": host, "port": port, "database": database,
            "user": user, "password": password,
            "last_status": "connected", "last_message": "Conexion verificada",
        })
        _save_pg_config(cfg)

        ensure_tables("espesadores")

        return jsonify({"ok": True, "detail": f"Servidor: {version}"})
    except Exception as e:
        cfg = _load_pg_config()
        cfg["last_status"] = "error"
        cfg["last_message"] = str(e)
        _save_pg_config(cfg)
        return jsonify({"ok": False, "error": str(e)})


@bp_postgres.route("/api/postgres/persistencia", methods=["GET"])
def api_get_persistencia():
    """Estado vivo de la persistencia (no de la conexion)."""
    return jsonify(estado_persistencia())


@bp_postgres.route("/api/postgres/persistencia", methods=["PUT"])
def api_put_persistencia():
    """Cambia el periodo de persistencia. Aplica en caliente, sin reiniciar."""
    body = request.get_json(force=True) if request.data else {}
    cfg = _load_pg_config()

    if "periodo_ms" in body:
        try:
            ms = int(body["periodo_ms"])
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "periodo_ms debe ser un entero."}), 400
        if ms not in PERIODOS_PERSISTENCIA_MS:
            return jsonify({
                "ok": False,
                "error": (f"periodo_ms invalido: {ms}. "
                          f"Valores permitidos: {list(PERIODOS_PERSISTENCIA_MS)}."),
            }), 400
        cfg["persist_periodo_ms"] = ms

    if "enabled" in body:
        cfg["persist_enabled"] = bool(body["enabled"])

    _save_pg_config(cfg)
    return jsonify({"ok": True, "estado": estado_persistencia()})


@bp_postgres.route("/api/postgres/validar-persistencia", methods=["POST"])
def api_validar_persistencia():
    """Valida la persistencia de punta a punta, no solo la conexion.

    Una conexion sana no garantiza que se este guardando: pueden faltar las
    tablas, faltar permiso de INSERT, o el motor puede estar detenido. Esta
    ruta escribe una fila sonda real, la lee de vuelta y la borra, midiendo
    cuanto tarda el viaje completo.
    """
    body = request.get_json(force=True) if request.data else {}
    proceso = body.get("proceso", "espesadores")
    if proceso not in VALID_PROCESOS:
        return jsonify({"ok": False, "error": f"Proceso invalido: {proceso}"}), 400

    conn = _connect()
    if conn is None:
        return jsonify({
            "ok": False,
            "error": "Sin conexion a PostgreSQL. Prueba primero la conexion.",
            "estado": estado_persistencia(),
        })

    sonda = f"__sonda_persistencia__{int(time.time() * 1000)}"
    pasos = []
    try:
        cur = conn.cursor()
        t0 = time.monotonic()

        tabla = f"{proceso}_entrada"
        cur.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
            (tabla,),
        )
        if not cur.fetchone()[0]:
            cur.close(); conn.close()
            return jsonify({
                "ok": False,
                "error": f"La tabla {tabla} no existe. Usa 'Crear / Verificar Tablas'.",
                "estado": estado_persistencia(),
            })
        pasos.append({"paso": "tabla existe", "ok": True, "detalle": tabla})

        cur.execute(
            f"INSERT INTO {tabla} (tag_name, pseudonimo, proceso, valor) "
            "VALUES (%s, %s, %s, %s)",
            (sonda, "sonda de validacion", proceso, 1.0),
        )
        pasos.append({"paso": "INSERT", "ok": True, "detalle": "fila sonda escrita"})

        cur.execute(f"SELECT valor, timestamp FROM {tabla} WHERE tag_name = %s", (sonda,))
        fila = cur.fetchone()
        if fila is None:
            cur.close(); conn.close()
            return jsonify({
                "ok": False,
                "error": "La fila sonda se inserto pero no se pudo leer de vuelta.",
                "pasos": pasos,
                "estado": estado_persistencia(),
            })
        pasos.append({"paso": "SELECT", "ok": True,
                      "detalle": f"valor={fila[0]}, timestamp={fila[1]}"})

        cur.execute(f"DELETE FROM {tabla} WHERE tag_name = %s", (sonda,))
        pasos.append({"paso": "DELETE", "ok": True, "detalle": "sonda limpiada"})

        ida_vuelta_ms = round((time.monotonic() - t0) * 1000, 1)
        cur.close()
        conn.close()

        estado = estado_persistencia()
        periodo_ms = estado["periodo_ms"]
        avisos = []
        # El dato util no es solo "la BD responde", sino si responde lo bastante
        # rapido para el periodo elegido.
        if ida_vuelta_ms > periodo_ms:
            avisos.append(
                f"El viaje de ida y vuelta tardo {ida_vuelta_ms} ms, mas que el "
                f"periodo de {periodo_ms} ms: la base no alcanza a seguir ese "
                "ritmo y se van a perder fotogramas. Considera 1000 ms.")
        if not estado["enabled"]:
            avisos.append("La persistencia esta deshabilitada: el motor no guardara nada.")
        if estado["salud"] == "sin_datos":
            avisos.append("El motor todavia no persistio ningun tick "
                          "(¿esta detenido el SE?).")
        elif estado["salud"] == "atrasada":
            avisos.append(
                f"La ultima escritura del motor fue hace "
                f"{estado['edad_ultima_escritura_s']} s, mas de lo esperado "
                f"para un periodo de {periodo_ms} ms.")

        return jsonify({
            "ok": True,
            "ida_vuelta_ms": ida_vuelta_ms,
            "pasos": pasos,
            "avisos": avisos,
            "estado": estado,
        })
    except Exception as e:
        try:
            # Mejor esfuerzo: no dejar la sonda tirada en la tabla.
            cur2 = conn.cursor()
            cur2.execute(f"DELETE FROM {proceso}_entrada WHERE tag_name = %s", (sonda,))
            cur2.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e), "pasos": pasos,
                        "estado": estado_persistencia()})


@bp_postgres.route("/api/postgres/ensure-tables", methods=["POST"])
def api_ensure_tables():
    body = request.get_json(force=True) if request.data else {}
    proceso = body.get("proceso", "espesadores")
    if proceso not in VALID_PROCESOS:
        return jsonify({"ok": False, "error": f"Proceso invalido: {proceso}"})
    if ensure_tables(proceso):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No se pudo conectar o crear las tablas."})


@bp_postgres.route("/api/postgres/tables", methods=["GET"])
def get_tables_status():
    conn = _connect()
    if conn is None:
        return jsonify({"ok": False, "error": "Sin conexion a PostgreSQL"})
    try:
        cur = conn.cursor()
        tables = {}
        for proceso in sorted(VALID_PROCESOS):
            for suffix in ("entrada", "salida"):
                table = f"{proceso}_{suffix}"
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                    (table,),
                )
                exists = cur.fetchone()[0]
                count = 0
                if exists:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cur.fetchone()[0]
                tables[table] = {"exists": exists, "count": count}
        cur.close()
        conn.close()
        return jsonify({"ok": True, "tables": tables})
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)})
