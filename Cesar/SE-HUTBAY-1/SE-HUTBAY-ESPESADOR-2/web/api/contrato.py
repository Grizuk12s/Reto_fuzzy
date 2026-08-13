# -*- coding: utf-8 -*-
"""Blueprint bp_contrato — contrato de variables del SE, editable por cliente.

Este proyecto es un producto estandar que se moldea a cada planta: las
variables que el experto controla dependen de que instrumentacion exista.
Antes esa lista vivia en config.py, asi que cada cliente exigia tocar
codigo y redesplegar. Aqui pasa a ser configuracion.

Rutas:
  GET   /api/contrato            contrato vigente + defaults + salud
  POST  /api/contrato/impacto    simula un cambio y devuelve el radio de impacto
  PUT   /api/contrato            guarda (exige confirmar si hay impacto grave)
  POST  /api/contrato/reset      vuelve a la plantilla estandar

IMPORTANTE: quitar una variable no es borrar una fila. Cada variable
arrastra modelo difuso, filtro, limites, reglas, permisivos, derivadas y
el mapeo de tags. Por eso /impacto existe y la UI lo muestra antes de
dejar guardar.
"""
from __future__ import annotations

import json
import os
import re

from flask import Blueprint, jsonify, request

import config as cfg_mod
from web.state import _load_tags, _save_tags

bp_contrato = Blueprint("contrato", __name__)

_CFG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "espesador",
)
CONTRATO_JSON = os.path.join(_CFG_DIR, "contrato.json")
FUZZY_JSON    = os.path.join(_CFG_DIR, "fuzzy.json")
FILTROS_JSON  = os.path.join(_CFG_DIR, "filtros.json")
REGLAS_JSON   = os.path.join(_CFG_DIR, "reglas.json")
VARIABLES_JSON = os.path.join(_CFG_DIR, "variables.json")
PERMISIVOS_JSON = os.path.join(_CFG_DIR, "permisivos.json")

NOMBRE_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")

# Nombres que el nucleo usa para otra cosa: no pueden ser variables.
RESERVADOS = {"t_s", "tick", "timestamp"}


# ============================================================
# Persistencia
# ============================================================
def _leer_json(path, defecto):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def contrato_vigente() -> dict:
    data = _leer_json(CONTRATO_JSON, None)
    if not isinstance(data, dict):
        return {
            "variables_proceso": list(cfg_mod.VARIABLES_PROCESO_DEFAULT),
            "setpoints": list(cfg_mod.SETPOINT_KEYS_DEFAULT),
            "descripciones": {},
        }
    return {
        "variables_proceso": list(data.get("variables_proceso")
                                  or cfg_mod.VARIABLES_PROCESO_DEFAULT),
        "setpoints": list(data.get("setpoints") or cfg_mod.SETPOINT_KEYS_DEFAULT),
        "descripciones": data.get("descripciones") or {},
    }


def _guardar_contrato(c: dict) -> None:
    os.makedirs(_CFG_DIR, exist_ok=True)
    with open(CONTRATO_JSON, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


# ============================================================
# Analisis de impacto
# ============================================================
def _reglas_por_variable(variables: set) -> dict:
    """Que reglas referencian cada variable (incluye su pendiente pend_<var>)."""
    from core.engine.motor import variables_de_regla
    from runner import cargar_reglas_json

    try:
        reglas = cargar_reglas_json()
    except Exception:
        return {}

    afectadas: dict[str, list] = {v: [] for v in variables}
    for r in reglas:
        refs = variables_de_regla(r)
        for v in variables:
            if v in refs or f"pend_{v}" in refs:
                afectadas[v].append(str(r.get("id", "?")))
    return afectadas


def _derivadas_por_variable(variables: set) -> dict:
    """Que variables derivadas consumen cada variable."""
    data = _leer_json(VARIABLES_JSON, {}) or {}
    afectadas: dict[str, list] = {v: [] for v in variables}
    for d in data.get("definiciones", []) or []:
        refs = set(d.get("args") or [])
        if d.get("arg"):
            refs.add(d["arg"])
        for v in variables:
            if v in refs:
                afectadas[v].append(d.get("nombre", "?"))
    return afectadas


def _tags_por_rol(roles: set) -> dict:
    """Que tag esta mapeado a cada rol (para avisar que quedara huerfano)."""
    out: dict[str, list] = {r: [] for r in roles}
    for t in _load_tags().get("tags", []):
        rol = t.get("rol")
        if not rol:
            continue
        # Un PV `x` arrastra tambien sus limites x_lmin / x_lmax
        for r in roles:
            if rol == r or rol in (f"{r}_lmin", f"{r}_lmax"):
                out[r].append(t["name"])
    return out


def analizar_impacto(nuevo: dict) -> dict:
    """Compara el contrato propuesto con el vigente y reporta consecuencias."""
    actual = contrato_vigente()
    pv_ant, pv_new = set(actual["variables_proceso"]), set(nuevo["variables_proceso"])
    sp_ant, sp_new = set(actual["setpoints"]), set(nuevo["setpoints"])

    quitadas_pv = pv_ant - pv_new
    agregadas_pv = pv_new - pv_ant
    quitadas_sp = sp_ant - sp_new
    agregadas_sp = sp_new - sp_ant

    fuzzy = _leer_json(FUZZY_JSON, {}) or {}
    filtros = _leer_json(FILTROS_JSON, {}) or {}

    reglas_af = _reglas_por_variable(quitadas_pv | quitadas_sp)
    derivadas_af = _derivadas_por_variable(quitadas_pv)
    tags_af = _tags_por_rol(quitadas_pv | quitadas_sp)

    # Lo que le falta a una variable NUEVA para poder evaluarse.
    faltantes_nuevas = []
    for v in sorted(agregadas_pv):
        falta = []
        if v not in fuzzy:
            falta.append("modelo difuso (fuzzy.json)")
        if v not in filtros:
            falta.append("config de filtro Exp-Q (filtros.json)")
        if falta:
            faltantes_nuevas.append({"variable": v, "falta": falta})

    quitadas_detalle = []
    for v in sorted(quitadas_pv | quitadas_sp):
        quitadas_detalle.append({
            "variable": v,
            "tipo": "pv" if v in quitadas_pv else "sp",
            "reglas": sorted(reglas_af.get(v, [])),
            "derivadas": sorted(derivadas_af.get(v, [])),
            "tags": sorted(tags_af.get(v, [])),
        })

    total_reglas = sorted({r for d in quitadas_detalle for r in d["reglas"]})

    # Bloqueante: dejar el SE sin PV o sin SP no tiene sentido.
    errores = []
    if not nuevo["variables_proceso"]:
        errores.append("El contrato debe tener al menos una variable de proceso.")
    if not nuevo["setpoints"]:
        errores.append("El contrato debe tener al menos un setpoint.")

    return {
        "quitadas": quitadas_detalle,
        "agregadas": {"pv": sorted(agregadas_pv), "sp": sorted(agregadas_sp)},
        "faltantes_nuevas": faltantes_nuevas,
        "reglas_impactadas": total_reglas,
        "n_reglas_impactadas": len(total_reglas),
        "errores": errores,
        "requiere_confirmacion": bool(quitadas_detalle or faltantes_nuevas),
        "sin_cambios": not (quitadas_pv or agregadas_pv or quitadas_sp or agregadas_sp),
    }


def _normalizar(data: dict) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, "Payload invalido."
    out = {}
    for clave in ("variables_proceso", "setpoints"):
        xs = data.get(clave)
        if not isinstance(xs, list):
            return None, f"'{clave}' debe ser una lista."
        limpio = []
        for x in xs:
            n = str(x or "").strip()
            if not NOMBRE_RE.match(n):
                return None, (f"'{n}' no es un nombre valido: minusculas, numeros y "
                              "guion bajo, empezando por letra.")
            if n in RESERVADOS:
                return None, f"'{n}' es un nombre reservado del nucleo."
            if n not in limpio:
                limpio.append(n)
        out[clave] = limpio

    cruce = set(out["variables_proceso"]) & set(out["setpoints"])
    if cruce:
        return None, f"Una variable no puede ser PV y SP a la vez: {', '.join(sorted(cruce))}."

    out["descripciones"] = {
        str(k): str(v) for k, v in (data.get("descripciones") or {}).items()
    }
    return out, None


# ============================================================
# Rutas
# ============================================================
@bp_contrato.route("/api/contrato", methods=["GET"])
def api_get_contrato():
    actual = contrato_vigente()
    fuzzy = _leer_json(FUZZY_JSON, {}) or {}
    filtros = _leer_json(FILTROS_JSON, {}) or {}

    # Salud: una PV sin modelo difuso o sin filtro no se puede evaluar.
    salud = []
    for v in actual["variables_proceso"]:
        problemas = []
        if v not in fuzzy:
            problemas.append("sin modelo difuso")
        if v not in filtros:
            problemas.append("sin config de filtro")
        if problemas:
            salud.append({"variable": v, "problemas": problemas})

    return jsonify({
        "actual": actual,
        "defaults": {
            "variables_proceso": list(cfg_mod.VARIABLES_PROCESO_DEFAULT),
            "setpoints": list(cfg_mod.SETPOINT_KEYS_DEFAULT),
        },
        "descripciones_conocidas": cfg_mod.DESCRIPCION_ROLES,
        "salud": salud,
        "crudas": list(cfg_mod.VARIABLES_CRUDAS_REQUERIDAS),
        "aviso_reinicio": True,
    })


@bp_contrato.route("/api/contrato/impacto", methods=["POST"])
def api_impacto_contrato():
    norm, error = _normalizar(request.get_json(force=True) or {})
    if error:
        return jsonify({"error": error}), 400
    return jsonify(analizar_impacto(norm))


@bp_contrato.route("/api/contrato", methods=["PUT"])
def api_put_contrato():
    body = request.get_json(force=True) or {}
    norm, error = _normalizar(body)
    if error:
        return jsonify({"error": error}), 400

    impacto = analizar_impacto(norm)
    if impacto["errores"]:
        return jsonify({"error": " ".join(impacto["errores"]), "impacto": impacto}), 400
    if impacto["requiere_confirmacion"] and not body.get("confirmar"):
        return jsonify({
            "error": "El cambio tiene impacto. Revisalo y reenvia con confirmar=true.",
            "impacto": impacto,
        }), 409

    _guardar_contrato(norm)

    # Los roles de tags que ya no existen en el contrato quedan invalidos:
    # se limpian para que el panel de cobertura no muestre fantasmas.
    roles_validos = set(norm["variables_proceso"]) | set(norm["setpoints"])
    for v in norm["variables_proceso"]:
        roles_validos |= {f"{v}_lmin", f"{v}_lmax"}
    roles_validos |= set(cfg_mod.VARIABLES_CRUDAS_REQUERIDAS)

    store = _load_tags()
    limpiados = []
    for t in store.get("tags", []):
        if t.get("rol") and t["rol"] not in roles_validos:
            limpiados.append({"tag": t["name"], "rol": t["rol"]})
            t["rol"] = ""
    if limpiados:
        _save_tags(store)

    return jsonify({
        "ok": True,
        "contrato": norm,
        "impacto": impacto,
        "roles_limpiados": limpiados,
        "aviso": ("El contrato se guardo. Reinicia la aplicacion para que el "
                  "nucleo tome la nueva lista de variables."),
    })


@bp_contrato.route("/api/contrato/reset", methods=["POST"])
def api_reset_contrato():
    norm = {
        "variables_proceso": list(cfg_mod.VARIABLES_PROCESO_DEFAULT),
        "setpoints": list(cfg_mod.SETPOINT_KEYS_DEFAULT),
        "descripciones": {},
    }
    _guardar_contrato(norm)
    return jsonify({"ok": True, "contrato": norm,
                    "aviso": "Contrato restaurado a la plantilla estandar. Reinicia la app."})
