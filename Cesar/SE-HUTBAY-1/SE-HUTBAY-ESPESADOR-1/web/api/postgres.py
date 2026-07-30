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

from flask import Blueprint, jsonify, request

from web.state import _CFG_DIR

bp_postgres = Blueprint("postgres", __name__)

POSTGRES_JSON = os.path.join(_CFG_DIR, "postgres.json")

VALID_PROCESOS = {"espesadores", "molienda", "flotacion"}

_DEFAULTS = {
    "host": "localhost",
    "port": 5432,
    "database": "",
    "user": "",
    "password": "",
    "last_status": "unconfigured",
    "last_message": "",
}


# ============================================================
# Config load / save
# ============================================================

def _load_pg_config() -> dict:
    if not os.path.exists(POSTGRES_JSON):
        return dict(_DEFAULTS)
    try:
        with open(POSTGRES_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return dict(_DEFAULTS)


def _save_pg_config(data: dict) -> None:
    with open(POSTGRES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
) -> bool:
    if proceso not in VALID_PROCESOS:
        return False
    conn = _connect()
    if conn is None:
        return False
    try:
        from psycopg2.extras import execute_values
        cur = conn.cursor()
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
