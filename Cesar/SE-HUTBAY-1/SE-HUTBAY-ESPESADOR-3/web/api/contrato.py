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
from web.state import _load_tags, _save_tags, _se_engine

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
            "limites_sp": {},
        }
    return {
        "variables_proceso": list(data.get("variables_proceso")
                                  or cfg_mod.VARIABLES_PROCESO_DEFAULT),
        "setpoints": list(data.get("setpoints") or cfg_mod.SETPOINT_KEYS_DEFAULT),
        "descripciones": data.get("descripciones") or {},
        # Limites de ingenieria por SP: es lo que clipea la escritura al DCS.
        "limites_sp": data.get("limites_sp") or {},
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

    # Un SP sin limites declarados no se clipea al escribir al DCS. No es
    # bloqueante para guardar el contrato (la UI todavia no edita el campo),
    # pero el motor se niega a arrancar asi.
    sp_sin_limites = [s for s in nuevo["setpoints"]
                      if s not in (nuevo.get("limites_sp") or {})]

    return {
        "quitadas": quitadas_detalle,
        "agregadas": {"pv": sorted(agregadas_pv), "sp": sorted(agregadas_sp)},
        "faltantes_nuevas": faltantes_nuevas,
        "sp_sin_limites": sp_sin_limites,
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

    # Limites de SP. Se validan aqui y no en el motor porque un limite mal
    # puesto (invertido, no numerico) se traduce en escrituras sin tope al
    # DCS; mejor rechazar el guardado que descubrirlo en planta.
    # Si el payload NO trae la clave, se conserva lo guardado. La UI todavia
    # no edita este campo, y sin esto cualquier guardado desde la pagina
    # borraria los limites en silencio — que es justo el modo de falla
    # peligroso: el SE seguiria corriendo, pero escribiendo sin tope.
    if "limites_sp" not in data:
        limites = contrato_vigente().get("limites_sp") or {}
    else:
        limites = data.get("limites_sp") or {}
    if not isinstance(limites, dict):
        return None, "'limites_sp' debe ser un objeto { <setpoint>: [min, max] }."
    lim_out = {}
    for sp, par in limites.items():
        sp = str(sp).strip()
        if sp not in out["setpoints"]:
            return None, f"'{sp}' tiene limites pero no es un setpoint del contrato."
        if not isinstance(par, (list, tuple)) or len(par) != 2:
            return None, f"'{sp}': los limites deben ser [min, max]."
        try:
            lo, hi = float(par[0]), float(par[1])
        except (TypeError, ValueError):
            return None, f"'{sp}': los limites deben ser numericos."
        if lo >= hi:
            return None, f"'{sp}': el minimo ({lo}) debe ser menor que el maximo ({hi})."
        lim_out[sp] = [lo, hi]
    out["limites_sp"] = lim_out
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

    # Que le falta a cada PV/SP del contrato para ser usable de verdad
    filtros = _leer_json(FILTROS_JSON, {}) or {}
    defuzzy = _leer_json(os.path.join(_CFG_DIR, "defuzzy.json"), {}) or {}
    sug = _sugerencia_desde_tags()

    return jsonify({
        "actual": actual,
        "sugerido": {"variables_proceso": sug["variables_proceso"],
                     "setpoints": sug["setpoints"]},
        "colisiones": sug["colisiones"],
        "sin_defuzzy": [s for s in actual["setpoints"] if s not in defuzzy],
        "defaults": {
            "variables_proceso": list(cfg_mod.VARIABLES_PROCESO_DEFAULT),
            "setpoints": list(cfg_mod.SETPOINT_KEYS_DEFAULT),
        },
        "descripciones_conocidas": cfg_mod.DESCRIPCION_ROLES,
        "salud": salud,
        "crudas": list(cfg_mod.VARIABLES_CRUDAS_REQUERIDAS),
        "aviso_reinicio": True,
    })


def _sugerencia_desde_tags() -> dict:
    """Propone el contrato leyendo los tags PV y SP.

    El contrato sigue siendo una lista MANUAL y editable — puede cambiar por
    cliente, o desaparecer del producto. Pero llenarlo a mano contra nombres
    de otra planta no tiene sentido, asi que se ofrece esta propuesta como
    punto de partida: PV = identificadores de los tags PV, SP = idem con SP.
    """
    from web.api.config import entradas_pv_disponibles, salidas_sp_disponibles

    pv_items, colisiones = entradas_pv_disponibles()
    sp_items = salidas_sp_disponibles()
    pv = list(dict.fromkeys(i["identificador"] for i in pv_items))
    sp = list(dict.fromkeys(s["identificador"] for s in sp_items))

    # Cruce PV/SP: el mismo identificador en ambos lados. Pasa cuando dos
    # tags distintos (la medicion y el setpoint) comparten pseudonimo.
    # Es bloqueante: el motor no sabria si esa variable se lee o se escribe.
    cruce = {}
    for ident in set(pv) & set(sp):
        cruce[ident] = {
            "pv": [i["tag"] for i in pv_items if i["identificador"] == ident],
            "sp": [s["tag"] for s in sp_items if s["identificador"] == ident],
        }

    return {
        "variables_proceso": pv,
        "setpoints": sp,
        "descripciones": {**{i["identificador"]: i["pseudonimo"] for i in pv_items},
                          **{s["identificador"]: s["pseudonimo"] for s in sp_items}},
        "colisiones": colisiones,
        "cruce_pv_sp": cruce,
    }


def _asignar_roles_automaticos(contrato: dict) -> list[dict]:
    """Pone rol = su propio identificador a los tags PV y SP del contrato.

    Solo PV y SP: para un tag de esas categorias, el rol es el identificador
    que el mismo genera, asi que pedirlo a mano seria burocracia.
    Los LIM se dejan en paz a proposito: cual PV acota un limite es
    informacion real que nadie puede adivinar del nombre.
    """
    from web.api.config import entradas_pv_disponibles, salidas_sp_disponibles

    ident_por_tag = {}
    pv_items, _ = entradas_pv_disponibles()
    for i in pv_items:
        ident_por_tag[i["tag"]] = ("pv", i["identificador"])
    for s in salidas_sp_disponibles():
        ident_por_tag[s["tag"]] = ("sp", s["identificador"])

    validos = {"pv": set(contrato["variables_proceso"]), "sp": set(contrato["setpoints"])}

    store = _load_tags()
    asignados = []
    for t in store.get("tags", []):
        par = ident_por_tag.get(t.get("name"))
        if not par:
            continue
        cat, ident = par
        if ident in validos[cat] and t.get("rol") != ident:
            t["rol"] = ident
            asignados.append({"tag": t["name"], "rol": ident})
    if asignados:
        _save_tags(store)
    return asignados


@bp_contrato.route("/api/contrato/sincronizar", methods=["POST"])
def api_sincronizar_contrato():
    """Propone (o aplica) el contrato derivado de los tags PV y SP.

    Sin `confirmar` devuelve la propuesta y su impacto para revisar.
    Con `confirmar=true` lo guarda y asigna los roles de PV y SP.
    """
    body = request.get_json(force=True) if request.data else {}
    sug = _sugerencia_desde_tags()

    if sug["cruce_pv_sp"]:
        detalle = []
        for ident, quienes in sug["cruce_pv_sp"].items():
            detalle.append(f"'{ident}': PV {quienes['pv']} y SP {quienes['sp']}")
        return jsonify({
            "error": ("Hay tags de PV y de SP que generan el mismo identificador, "
                      "asi que el motor no sabria si esa variable se lee o se escribe. "
                      "Cambia el pseudonimo de uno de ellos y vuelve a sincronizar. "
                      + " | ".join(detalle)),
            "cruce_pv_sp": sug["cruce_pv_sp"],
            "colisiones": sug["colisiones"],
        }), 409

    propuesta = {
        "variables_proceso": sug["variables_proceso"],
        "setpoints": sug["setpoints"],
        "descripciones": sug["descripciones"],
    }
    norm, error = _normalizar(propuesta)
    if error:
        return jsonify({"error": error}), 400

    impacto = analizar_impacto(norm)
    if not (body or {}).get("confirmar"):
        return jsonify({"propuesta": norm, "impacto": impacto,
                        "colisiones": sug["colisiones"], "aplicado": False})

    if impacto["errores"]:
        return jsonify({"error": " ".join(impacto["errores"]), "impacto": impacto}), 400

    _guardar_contrato(norm)
    asignados = _asignar_roles_automaticos(norm)
    return jsonify({
        "aplicado": True, "contrato": norm, "impacto": impacto,
        "roles_asignados": asignados,
        "colisiones": sug["colisiones"],
        "aviso": ("Contrato sincronizado y roles de PV/SP asignados. "
                  "Los LIM siguen requiriendo enlace manual. "
                  "Reinicia la aplicacion para que el nucleo tome la nueva lista."),
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

    # Recarga en caliente: hasta el 2026-08-21 el nucleo leia contrato.json una
    # sola vez, al importarse, y guardar desde aqui obligaba a reiniciar el
    # servicio. Peor que incomodo: la pantalla mostraba el contrato nuevo y el
    # catalogo de roles seguia exigiendo el viejo, sin ninguna pista de cual
    # de los dos era el real.
    vigente = cfg_mod.recargar_contrato()

    corriendo = False
    try:
        corriendo = bool(_se_engine.status().get("running"))
    except Exception:
        pass

    aviso = ("El contrato se guardo y el nucleo ya lo tomo: los catalogos de "
             "roles, variables y reglas usan la lista nueva sin reiniciar.")
    if corriendo:
        # Cambiar el contrato bajo un lazo en marcha significaria fuzzificar
        # contra otra escala y escribir a otro setpoint a mitad de tick.
        aviso += (" El motor esta CORRIENDO con el contrato anterior: detenelo "
                  "y volve a arrancarlo para que tome este.")

    return jsonify({
        "ok": True,
        "contrato": norm,
        "vigente": vigente,
        "impacto": impacto,
        "roles_limpiados": limpiados,
        "motor_corriendo": corriendo,
        "aviso": aviso,
    })


@bp_contrato.route("/api/contrato/reset", methods=["POST"])
def api_reset_contrato():
    norm = {
        "variables_proceso": list(cfg_mod.VARIABLES_PROCESO_DEFAULT),
        "setpoints": list(cfg_mod.SETPOINT_KEYS_DEFAULT),
        "descripciones": {},
    }
    _guardar_contrato(norm)
    vigente = cfg_mod.recargar_contrato()
    return jsonify({"ok": True, "contrato": norm, "vigente": vigente,
                    "aviso": "Contrato restaurado a la plantilla estandar. El nucleo "
                             "ya lo tomo; si el motor esta corriendo, reinicialo."})
