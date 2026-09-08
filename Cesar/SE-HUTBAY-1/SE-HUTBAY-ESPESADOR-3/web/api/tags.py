# -*- coding: utf-8 -*-
"""Blueprint bp_tags — CRUD de Tags KEPserver + Generador.

Rutas:
  GET/POST             /api/tags
  PUT/DELETE           /api/tags/<tag_id>
  POST                 /api/tags/<tag_id>/write
  POST                 /api/tags/refresh
  GET/PUT              /api/tags/simulation
  GET/PUT              /api/tags/generator
  POST                 /api/tags/generator/start
  POST                 /api/tags/generator/stop

IT-7: extraído de app.py.
"""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request

from web.state import (
    CATEGORIAS_TAG, normalizar_categoria,
    catalogo_roles, normalizar_rol, construir_mapeo,
    estado_contrato, roles_huerfanos,
    _load_tags, _save_tags, _tags_lock,
    _read_kepserver_tags_batch, _try_write_kepserver_tag, _enrich_tags_with_kepserver,
    _record_tag_values, _get_tag_history,
    _tag_generator,
    _heartbeat,
    _se_engine,
    _license_check,
)

bp_tags = Blueprint("tags", __name__)


def _require_license(fn):
    """Bloquea mutaciones (crear/editar/escribir tags) si la licencia demo expiro."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        lic = _license_check()
        if not lic["valid"]:
            return jsonify({
                "error": "Licencia demo expirada o inactiva. Solo se permite lectura de tags.",
                "license_expired": True,
                "reason": lic["reason"],
                "expires_at": lic["expires_at"],
            }), 403
        return fn(*args, **kwargs)
    return wrapper


def _autosync_contrato(motivo: str) -> dict:
    """Mantiene el contrato al dia con los tags, agregando lo nuevo.

    Se llama despues de cada alta o edicion de tag que pueda cambiar la lista
    de PV/SP. Es ADITIVO a proposito: deshabilitar un tag no puede borrar del
    contrato una variable con fuzzy, filtro y reglas colgando. Nunca hace
    fallar la operacion del tag: si la sincronizacion no se puede hacer, el
    tag igual se guarda y el desfase queda como alerta.
    """
    try:
        from web.api.contrato import sincronizar_aditivo, revisar_desfases
        res = sincronizar_aditivo(motivo)
        revisar_desfases()
        return res
    except Exception as exc:   # noqa: BLE001 - el alta del tag manda
        return {"ok": False, "agregadas_pv": [], "agregadas_sp": [],
                "error": f"{type(exc).__name__}: {exc}"}


@bp_tags.route("/api/tags", methods=["GET"])
def api_get_tags():
    data = _load_tags()
    return jsonify({
        "tags": _enrich_tags_with_kepserver(data["tags"]),
        "simulation_mode": data.get("simulation_mode", True),
        "categorias": list(CATEGORIAS_TAG),
    })


@bp_tags.route("/api/tags", methods=["POST"])
@_require_license
def api_create_tag():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "El nombre del tag es obligatorio."}), 400
    data_type = body.get("data_type", "Float")
    if data_type not in ("Float", "Int", "Boolean", "String"):
        return jsonify({"error": "Tipo de dato no soportado."}), 400
    # El lock cubre el read-modify-write completo. Sin el, dos altas
    # simultaneas leen el mismo `next_id` y una de las dos se pierde.
    with _tags_lock:
        store = _load_tags()
        for t in store["tags"]:
            if t["name"] == name:
                return jsonify({"error": f"Ya existe un tag con nombre '{name}'."}), 409
        categoria = normalizar_categoria(body.get("categoria"), name)
        new_tag = {
            "id": store["next_id"],
            "name": name,
            "data_type": data_type,
            "enabled": True,
            "categoria": categoria,
            "rol": normalizar_rol(body.get("rol"), categoria),
        }
        store["tags"].append(new_tag)
        store["next_id"] += 1
        _save_tags(store)
    sync = _autosync_contrato(f"alta del tag {name}")
    live = _read_kepserver_tags_batch([name])
    return jsonify({"ok": True, "tag": {**new_tag, **live.get(name, {})},
                    "contrato": sync}), 201


@bp_tags.route("/api/tags/<int:tag_id>", methods=["PUT"])
@_require_license
def api_update_tag(tag_id: int):
    body = request.get_json(force=True)
    # El lock envuelve las validaciones tambien: la unicidad de nombre y de rol
    # se comprueba contra `store`, y si otro hilo guarda entre el chequeo y el
    # save, el duplicado entra igual.
    with _tags_lock:
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
            habilitar = bool(body["enabled"])
            # Habilitar tambien puede crear un rol duplicado, y por ese lado no
            # habia control: la validacion de mas abajo solo mira cuando se
            # ASIGNA el rol. Un tag viejo (importado, o de antes de esa
            # validacion) que comparte rol con el de produccion se volvia
            # ambiguo con un solo click — y en un SP eso es escribirle a otro
            # equipo. Se comprueba contra los tags HABILITADOS: un duplicado
            # dormido puede seguir guardado, lo que no puede es despertarse.
            rol_actual = str(tag.get("rol") or "").strip()
            if habilitar and not tag.get("enabled", True) and rol_actual:
                dup = next((t for t in store["tags"]
                            if t["id"] != tag_id
                            and t.get("enabled", True)
                            and str(t.get("rol") or "").strip() == rol_actual
                            and t.get("categoria") == tag.get("categoria")), None)
                if dup:
                    return jsonify({
                        "error": f"No se puede habilitar: el rol '{rol_actual}' ya lo "
                                 f"tiene el tag habilitado '{dup['name']}'. Dejá uno "
                                 f"solo con ese rol (o vaciá el rol de este) antes de "
                                 f"habilitarlo.",
                    }), 409
            tag["enabled"] = habilitar
        if "categoria" in body:
            cat = str(body["categoria"] or "").strip().lower()
            if cat not in CATEGORIAS_TAG:
                return jsonify({
                    "error": f"Categoria invalida. Validas: {', '.join(CATEGORIAS_TAG)}."
                }), 400
            # Cambiar de categoria invalida el rol anterior (pertenece a otro catalogo)
            if cat != tag.get("categoria"):
                tag["rol"] = ""
            tag["categoria"] = cat
        if "rol" in body:
            rol = str(body["rol"] or "").strip()
            cat_actual = tag.get("categoria", "otro")
            # El limite de una variable ya no se declara aca: lo declara quien
            # lo usa, en la pagina de Fuzzy (PV) o de Defuzzificacion (SP). Ver
            # `bindings_limites()` en web/state.py. Se permite BORRARLO, para
            # poder limpiar un resto sin migrar.
            if cat_actual == "lim" and rol:
                return jsonify({
                    "error": "Los limites ya no se asignan desde Tags. Elegi este tag "
                             "en la tabla de limites del fuzzy (para una PV) o de la "
                             "familia de defuzzificacion (para un SP): asi un mismo "
                             "tag puede acotar varias variables."
                }), 400
            validos = catalogo_roles().get(cat_actual, [])
            if rol and rol not in validos:
                return jsonify({
                    "error": f"Rol '{rol}' no valido para la categoria {cat_actual.upper()}."
                }), 400
            # Un rol solo puede estar asignado a un tag: evita lecturas ambiguas.
            if rol:
                dup = next((t for t in store["tags"]
                            if t["id"] != tag_id and t.get("rol") == rol
                            and t.get("categoria") == cat_actual), None)
                if dup:
                    return jsonify({
                        "error": f"El rol '{rol}' ya esta asignado al tag '{dup['name']}'."
                    }), 409
            tag["rol"] = rol
        for field in ("pseudonimo", "instrumento", "unidad_ing", "equipo"):
            if field in body:
                tag[field] = (body[field] or "").strip()
        _save_tags(store)
    # Solo lo que puede cambiar la lista de PV/SP del contrato. Renombrar el
    # pseudonimo ya NO entra aca: desde que el identificador se congela en el
    # rol, el pseudonimo es solo la etiqueta que se muestra.
    sync = None
    if any(k in body for k in ("categoria", "enabled", "rol", "name")):
        sync = _autosync_contrato(f"edicion del tag {tag['name']}")
    elif "pseudonimo" in body:
        # Un tag cuyo identificador todavia NO esta congelado en el rol sigue
        # tomandolo del pseudonimo, asi que renombrarlo puede dejar huerfana
        # su tabla de defuzzy, su filtro o su fuzzy. No se bloquea el cambio:
        # se revisa y, si quedo algo colgado, aparece la alerta roja en la
        # pagina donde se ocupa.
        try:
            from web.api.contrato import revisar_desfases
            revisar_desfases()
        except Exception:   # noqa: BLE001
            pass
    live = _read_kepserver_tags_batch([tag["name"]]) if tag["enabled"] else {}
    info = live.get(tag["name"], {"connected": False, "exists": False, "value": None, "quality": "Suspended"})
    return jsonify({"ok": True, "tag": {**tag, **info}, "contrato": sync})


@bp_tags.route("/api/tags/<int:tag_id>", methods=["DELETE"])
@_require_license
def api_delete_tag(tag_id: int):
    with _tags_lock:
        store = _load_tags()
        before = len(store["tags"])
        store["tags"] = [t for t in store["tags"] if t["id"] != tag_id]
        if len(store["tags"]) == before:
            return jsonify({"error": "Tag no encontrado."}), 404
        _save_tags(store)
    # Borrar un tag NO quita su variable del contrato: eso arrastraria fuzzy,
    # filtro y reglas. Se revisa el desfase y queda la alerta.
    try:
        from web.api.contrato import revisar_desfases
        revisar_desfases()
    except Exception:   # noqa: BLE001
        pass
    return jsonify({"ok": True})


@bp_tags.route("/api/tags/<int:tag_id>/write", methods=["POST"])
@_require_license
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


@bp_tags.route("/api/tags/refresh", methods=["POST"])
def api_refresh_tags():
    store = _load_tags()
    return jsonify({"tags": _enrich_tags_with_kepserver(store["tags"])})


# Solo estas categorias participan del pipeline del SE. Los tags OTRO
# (handshake, heartbeat, selectores) no se leen en el ciclo de lectura.
_CATEGORIAS_LECTURA = ("pv", "cruda", "lim", "sp")


@bp_tags.route("/api/tags/lectura", methods=["GET"])
def api_lectura_en_vivo():
    """Lee del KEPserver solo los tags con rol en el SE (PV/CRUDA/LIM/SP).

    Es el endpoint del ciclo de lectura en vivo de la pagina de Tags.
    Deliberadamente NO lee los tags de categoria OTRO: no alimentan el
    pipeline y encarecen cada ciclo.
    """
    store = _load_tags()
    objetivo = [
        t for t in store.get("tags", [])
        if t.get("enabled", True) and t.get("categoria") in _CATEGORIAS_LECTURA
    ]
    enriquecidos = _enrich_tags_with_kepserver(objetivo)

    valores = {
        t["name"]: {
            "value":     t.get("value"),
            "quality":   t.get("quality"),
            # B1.4: el nombre exacto del StatusCode y hace cuanto que el valor
            # no cambia. La pagina los muestra en el tooltip de la columna
            # QUALITY: "Bad" no manda a nadie a ningun lado, "BadNotConnected"
            # si. Sin esto el enriquecimiento los traia y el endpoint los
            # descartaba en el ultimo paso.
            "status_code": t.get("status_code", ""),
            "source_ts":   t.get("source_ts"),
            "estancado_s": t.get("estancado_s", 0.0),
            "connected": t.get("connected"),
            "exists":    t.get("exists"),
            "categoria": t.get("categoria"),
            "rol":       t.get("rol", ""),
        }
        for t in enriquecidos
    }
    # El historial que alimenta el Explorador de Series se llena AQUI.
    # Antes solo grababan el generador (datos sinteticos) y las escrituras de SP
    # del motor, asi que las señales reales de planta nunca entraban al buffer:
    # se veian en esta pagina y el grafico quedaba vacio.
    _record_tag_values({
        nombre: float(v["value"])
        for nombre, v in valores.items()
        if v["exists"] and isinstance(v["value"], (int, float))
        and not isinstance(v["value"], bool)
    })

    malos = sum(1 for v in valores.values() if not v["exists"])
    return jsonify({
        "valores": valores,
        "leidos":  len(valores),
        "malos":   malos,
        "ts":      __import__("time").time(),
    })


@bp_tags.route("/api/tags/roles", methods=["GET"])
def api_get_roles():
    """Catalogo de roles por categoria + cobertura actual del mapeo."""
    import config as cfg_mod
    cfg_mod.recargar_contrato()
    mapeo = construir_mapeo()
    return jsonify({
        "catalogo": catalogo_roles(),
        "faltantes": mapeo["faltantes"],
        # Lo que falta ENTRE lo que el pipeline realmente consume. El motor ya
        # no se niega a arrancar por un rol sin asignar: arranca degradado. Lo
        # que hay que mirar antes de confiar en el SE es esto, no `faltantes`.
        "faltantes_en_uso": mapeo.get("faltantes_en_uso", {}),
        "en_uso": mapeo.get("en_uso", {}),
        "duplicados": mapeo["duplicados"],
        "listo": mapeo["listo"],
        "listo_en_uso": mapeo.get("listo_en_uso", False),
        # El catalogo de arriba sale del contrato que tiene CARGADO el proceso.
        # Si el archivo en disco ya cambio, la cobertura mostrada es la de la
        # lista vieja: la UI necesita poder decirlo en vez de mentir.
        "contrato": estado_contrato(),
        "huerfanos": roles_huerfanos(),
        "asignados": {
            "pv":    mapeo["tag_to_pv"],
            "cruda": mapeo["tag_to_cruda"],
            # Un tag puede acotar varias variables: se listan todos sus usos.
            "lim":   {t: ", ".join(f"{v}_{b}" for v, b in pares)
                      for t, pares in mapeo["tag_to_lim"].items()},
            "sp":    mapeo["sp_to_tag"],
        },
    })


_CATALOG_TYPES = ("instrumentos", "unidades_ing", "equipos")
_CATALOG_FIELD = {"instrumentos": "instrumento", "unidades_ing": "unidad_ing", "equipos": "equipo"}


@bp_tags.route("/api/tags/catalogs", methods=["GET"])
def api_get_catalogs():
    store = _load_tags()
    return jsonify(store.get("catalogs", {t: [] for t in _CATALOG_TYPES}))


@bp_tags.route("/api/tags/catalogs/<catalog_type>", methods=["POST"])
@_require_license
def api_manage_catalog(catalog_type: str):
    if catalog_type not in _CATALOG_TYPES:
        return jsonify({"error": "Tipo de catalogo invalido."}), 400
    body = request.get_json(force=True)
    action = body.get("action", "add")
    value = (body.get("value") or "").strip()
    if not value:
        return jsonify({"error": "El valor es obligatorio."}), 400

    with _tags_lock:
        store = _load_tags()
        catalogs = store.setdefault("catalogs", {t: [] for t in _CATALOG_TYPES})
        items = catalogs.setdefault(catalog_type, [])

        if action == "add":
            if value in items:
                return jsonify({"error": f"'{value}' ya existe."}), 409
            items.append(value)
            _save_tags(store)
            return jsonify({"ok": True, "items": items}), 201

        if action == "remove":
            if value not in items:
                return jsonify({"error": "Item no encontrado."}), 404
            items.remove(value)
            field = _CATALOG_FIELD[catalog_type]
            for tag in store.get("tags", []):
                if tag.get(field) == value:
                    tag[field] = ""
            _save_tags(store)
            return jsonify({"ok": True, "items": items})

    return jsonify({"error": "Accion invalida (add|remove)."}), 400


@bp_tags.route("/api/tags/simulation", methods=["GET"])
def api_get_simulation_mode():
    store = _load_tags()
    return jsonify({"simulation_mode": store.get("simulation_mode", True)})


@bp_tags.route("/api/tags/simulation", methods=["PUT"])
@_require_license
def api_set_simulation_mode():
    body = request.get_json(force=True)
    mode = bool(body.get("simulation_mode", True))
    with _tags_lock:
        store = _load_tags()
        store["simulation_mode"] = mode
        _save_tags(store)
    return jsonify({"ok": True, "simulation_mode": mode})


# ============================================================
# Handshake con el DCS — quien autoriza el control externo
# ============================================================
# Es el permiso del DCS para que el SE escriba: si `enable_fbk_tag` no dice
# que si, ningun setpoint sale (fail-closed, ver `_chequear_handshake_dcs`).
# La configuracion siempre vivio en tags.json bajo "handshake", pero **no
# habia ni endpoint ni pagina**: se editaba a mano el archivo. Era el mismo
# agujero que tenian los limites de SP en `contrato.json`.

@bp_tags.route("/api/tags/handshake", methods=["GET"])
def api_get_handshake():
    hs = (_load_tags().get("handshake") or {})
    estado, corriendo = {}, False
    try:
        st_motor = _se_engine.status()
        corriendo = bool(st_motor.get("running"))
        estado = (st_motor.get("handshake") or {})
    except Exception:
        pass
    return jsonify({
        "enabled": bool(hs.get("enabled")),
        "enable_fbk_tag": str(hs.get("enable_fbk_tag") or ""),
        "enable_ext_tag": str(hs.get("enable_ext_tag") or ""),
        # Lo que el motor esta viendo AHORA, para no tener que ir a la traza.
        "ultimo": estado.get("ultimo"),
        "motor_corriendo": corriendo,
    })


@bp_tags.route("/api/tags/handshake", methods=["PUT"])
@_require_license
def api_put_handshake():
    """Guarda que tags llevan el permiso del DCS.

    `enable_fbk_tag` (FBK, lectura) es el que el DCS pone en true cuando acepta
    que el experto mande; `enable_ext_tag` (EXT, escritura) es con el que el SE
    lo pide. Habilitar el handshake sin FBK seria fail-closed permanente — el
    SE no escribiria nunca — asi que se rechaza en vez de dejarlo mudo.
    """
    body = request.get_json(force=True) or {}
    enabled = bool(body.get("enabled"))
    fbk = str(body.get("enable_fbk_tag") or "").strip()
    ext = str(body.get("enable_ext_tag") or "").strip()

    if enabled and not fbk:
        return jsonify({"error": "Con el handshake habilitado hace falta el tag FBK: "
                                 "sin el, el SE no puede confirmar que el DCS lo "
                                 "autoriza y no escribiria ningun setpoint."}), 400
    if fbk and ext and fbk == ext:
        return jsonify({"error": "El tag FBK (lo que el DCS responde) y el EXT (lo que "
                                 "el SE pide) tienen que ser distintos."}), 400

    with _tags_lock:
        store = _load_tags()
        nombres = {t["name"] for t in store.get("tags", []) if t.get("enabled", True)}
        for etiqueta, tag in (("FBK", fbk), ("EXT", ext)):
            if tag and tag not in nombres:
                return jsonify({"error": f"El tag {etiqueta} '{tag}' no existe o esta "
                                         "suspendido."}), 400
        store["handshake"] = {"enabled": enabled,
                              "enable_fbk_tag": fbk, "enable_ext_tag": ext}
        _save_tags(store)

    corriendo = False
    try:
        corriendo = bool(_se_engine.status().get("running"))
    except Exception:
        pass
    return jsonify({"ok": True, "enabled": enabled, "enable_fbk_tag": fbk,
                    "enable_ext_tag": ext, "motor_corriendo": corriendo})


@bp_tags.route("/api/tags/catalogo", methods=["GET"])
def api_tags_catalogo():
    """Catalogo liviano de tags graficables: NO lee el KEPserver.

    El Explorador de Series lo consulta cada pocos segundos para enterarse de
    altas, bajas, suspensiones y cambios de pseudonimo sin que haya que
    recargar la pagina. `/api/entrada` sirve lo mismo pero hace una lectura
    OPC completa de todos los tags habilitados: usarlo como sonda periodica
    encarecia el refresco sin necesidad, porque aca solo hace falta la ficha.
    """
    store = _load_tags()
    out = []
    for t in store.get("tags", []):
        if not t.get("enabled", True):
            continue
        categoria = (t.get("categoria") or "").strip().lower()
        group = categoria if categoria in ("pv", "cruda", "lim", "sp") else "other"
        out.append({
            "name":       t.get("name", ""),
            "group":      group,
            "pseudonimo": t.get("pseudonimo", "") or "",
            "unidad_ing": t.get("unidad_ing", "") or "",
            "enabled":    True,
        })
    return jsonify(out)


# ============================================================
# Preferencias del Explorador de Series (color y limites por tag)
# ============================================================
# Viven en tags.json bajo "grafico" y no en el navegador: el color con el que
# uno reconoce una variable, y la banda contra la que la mira, no deberian
# depender de en que maquina se abrio la pagina ni perderse al limpiar el
# cache. No pasan por _require_license a proposito: son preferencias de
# visualizacion, no configuracion del SE.

@bp_tags.route("/api/tags/grafico/preferencias", methods=["GET"])
def api_get_prefs_grafico():
    g = (_load_tags().get("grafico") or {})
    return jsonify({"colores": g.get("colores") or {},
                    "limites": g.get("limites") or {}})


@bp_tags.route("/api/tags/grafico/preferencias", methods=["PUT"])
def api_put_prefs_grafico():
    """Merge parcial: solo toca las claves que vengan en el body.

    Para borrar una preferencia se manda su valor en null; asi la pagina puede
    decir "este tag vuelve al color de la paleta" sin tener que reenviar el
    diccionario entero y arriesgarse a pisar lo que otro haya guardado.
    """
    import re as _re

    body = request.get_json(force=True) or {}
    colores_in = body.get("colores")
    limites_in = body.get("limites")
    hexcol = _re.compile(r"^#[0-9a-fA-F]{6}$")

    with _tags_lock:
        store = _load_tags()
        g = store.get("grafico") or {}
        cols = dict(g.get("colores") or {})
        lims = dict(g.get("limites") or {})

        if isinstance(colores_in, dict):
            for k, v in colores_in.items():
                k = str(k).strip()
                if not k:
                    continue
                if v is None or v == "":
                    cols.pop(k, None)
                elif hexcol.match(str(v)):
                    cols[k] = str(v).lower()

        if isinstance(limites_in, dict):
            for k, v in limites_in.items():
                k = str(k).strip()
                if not k:
                    continue
                if not v:
                    lims.pop(k, None)
                    continue
                try:
                    mn = float(v.get("min"))
                    mx = float(v.get("max"))
                except (AttributeError, TypeError, ValueError):
                    continue
                # Un rango invertido o de ancho cero dividiria por cero al
                # normalizar: no se guarda en vez de romper el grafico.
                if mx > mn:
                    lims[k] = {"min": mn, "max": mx}

        store["grafico"] = {"colores": cols, "limites": lims}
        _save_tags(store)

    return jsonify({"ok": True, "colores": cols, "limites": lims})


@bp_tags.route("/api/tags/handshake/historial", methods=["GET"])
def api_handshake_historial():
    """Lee el FBK del handshake, lo graba en el historial y devuelve su serie.

    El Explorador de Series necesita saber *cuando* el DCS autorizo la
    escritura, no solo si la autoriza ahora. El ciclo de lectura en vivo
    (`/api/tags/lectura`) deja fuera los tags OTRO a proposito y ademas
    descarta los booleanos del historial, asi que el FBK nunca entraba al
    buffer. Este endpoint lo lee aparte -- un tag por refresco, solo mientras
    alguien tenga el grafico abierto -- y lo guarda como 1.0 / 0.0.

    Query: from_ms, to_ms (epoch ms). Respuesta:
      {tag, enabled, activo, motivo, serie: [[t_ms, 0|1], ...]}
    donde `activo` es el estado leido AHORA (None si no se pudo leer).
    """
    import time as _time

    store = _load_tags()
    hs = (store.get("handshake") or {})
    tag = str(hs.get("enable_fbk_tag") or "").strip()
    enabled = bool(hs.get("enabled"))

    try:
        from_ms = float(request.args.get("from_ms", 0))
        to_ms = float(request.args.get("to_ms", _time.time() * 1000.0))
    except ValueError:
        return jsonify({"error": "from_ms / to_ms deben ser numericos."}), 400

    if not tag:
        return jsonify({"tag": "", "enabled": enabled, "activo": None,
                        "motivo": "sin tag FBK configurado", "serie": []})

    fila = next((t for t in store.get("tags", []) if t.get("name") == tag), None)
    activo, motivo = None, ""
    if fila is None or not fila.get("enabled", True):
        motivo = "el tag FBK no existe o esta suspendido"
    else:
        try:
            enr = _enrich_tags_with_kepserver([fila])
            leido = enr[0] if enr else {}
            val = leido.get("value")
            # Fail-closed en la lectura, igual que el motor: si no se pudo
            # leer, no se afirma nada -- ni autorizado ni denegado.
            if leido.get("exists") and val is not None:
                if isinstance(val, bool):
                    activo = val
                elif isinstance(val, (int, float)):
                    activo = float(val) != 0.0
                else:
                    txt = str(val).strip().lower()
                    activo = txt in ("true", "1", "on", "si", "yes")
                _record_tag_values({tag: 1.0 if activo else 0.0})
            else:
                motivo = "no se pudo leer el FBK"
        except Exception as e:  # noqa: BLE001
            motivo = f"error leyendo el FBK: {e}"

    pts = _get_tag_history().get(tag, [])
    serie = [[int(p["t"] * 1000.0), float(p["v"])]
             for p in pts if from_ms <= p["t"] * 1000.0 <= to_ms]

    return jsonify({"tag": tag, "enabled": enabled, "activo": activo,
                    "motivo": motivo, "serie": serie})


@bp_tags.route("/api/tags/generator", methods=["GET"])
def api_get_generator():
    return jsonify(_tag_generator.status())


@bp_tags.route("/api/tags/generator", methods=["PUT"])
@_require_license
def api_put_generator():
    body = request.get_json(force=True)
    # La UI trabaja en milisegundos; el hilo internamente usa segundos.
    intervalo_s = body.get("intervalo_s")
    if body.get("intervalo_ms") is not None:
        try:
            intervalo_s = max(0.1, float(body["intervalo_ms"]) / 1000.0)
        except (TypeError, ValueError):
            return jsonify({"error": "intervalo_ms invalido."}), 400
    _tag_generator.update_config(
        intervalo_s=intervalo_s,
        n_ciclo=body.get("n_ciclo"),
        ranges=body.get("ranges"),
    )
    return jsonify({"ok": True, **_tag_generator.status()})


@bp_tags.route("/api/tags/generator/start", methods=["POST"])
@_require_license
def api_start_generator():
    _tag_generator.start()
    return jsonify({"ok": True, "running": True})


@bp_tags.route("/api/tags/generator/stop", methods=["POST"])
def api_stop_generator():
    _tag_generator.stop()
    return jsonify({"ok": True, "running": False})


# ============================================================
# Heartbeat KEPserver
# ============================================================

@bp_tags.route("/api/tags/heartbeat", methods=["GET"])
def api_get_heartbeat():
    return jsonify(_heartbeat.status())


@bp_tags.route("/api/tags/heartbeat", methods=["PUT"])
@_require_license
def api_put_heartbeat():
    body = request.get_json(force=True) or {}
    tag_out = (body.get("tag_out") or "").strip()
    tag_in = (body.get("tag_in") or "").strip()
    if tag_out and tag_in and tag_out == tag_in:
        return jsonify({"error": "El tag OUT y el tag IN deben ser distintos."}), 400
    try:
        _heartbeat.update_config(
            tag_out=body.get("tag_out"),
            tag_in=body.get("tag_in"),
            intervalo_s=body.get("intervalo_s"),
            value_a=body.get("value_a"),
            value_b=body.get("value_b"),
            data_type=body.get("data_type"),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Parametro invalido: {e}"}), 400
    return jsonify({"ok": True, **_heartbeat.status()})


@bp_tags.route("/api/tags/heartbeat/start", methods=["POST"])
@_require_license
def api_start_heartbeat():
    _heartbeat.start()
    return jsonify({"ok": True, "running": True, **_heartbeat.status()})


@bp_tags.route("/api/tags/heartbeat/stop", methods=["POST"])
def api_stop_heartbeat():
    _heartbeat.stop()
    return jsonify({"ok": True, "running": False, **_heartbeat.status()})


# ============================================================
# Historial de tags — usado por el Explorador de Series
# ============================================================

@bp_tags.route("/api/tags/history/range", methods=["GET"])
def api_tags_history_range():
    """Devuelve las series de los tags pedidos dentro de un rango temporal.

    Query params:
      - tags:    csv de nombres de tag (ej: "RETO.PV.torque,RETO.PV.bed_level")
      - from_ms: epoch en milisegundos (opcional; por defecto 0)
      - to_ms:   epoch en milisegundos (opcional; por defecto ahora)

    Respuesta:
      {
        "RETO.PV.torque":    [[t_ms, valor], ...],
        "RETO.PV.bed_level": [[t_ms, valor], ...]
      }
    """
    import time as _time

    tags_param = request.args.get("tags", "")
    tag_names = [t.strip() for t in tags_param.split(",") if t.strip()]

    try:
        from_ms = float(request.args.get("from_ms", 0))
        to_ms = float(request.args.get("to_ms", _time.time() * 1000.0))
    except ValueError:
        return jsonify({"error": "from_ms / to_ms deben ser numericos."}), 400

    hist_all = _get_tag_history()
    result = {}
    for name in tag_names:
        pts = hist_all.get(name, [])
        series = []
        for p in pts:
            t_ms = p["t"] * 1000.0
            if from_ms <= t_ms <= to_ms:
                series.append([int(t_ms), float(p["v"])])
        result[name] = series
    return jsonify(result)


@bp_tags.route("/api/tags/history/meta", methods=["GET"])
def api_tags_history_meta():
    """Metadatos del buffer: rango temporal disponible y tags con datos.

    Respuesta:
      {
        "earliest_ms": <int|null>,
        "latest_ms":   <int|null>,
        "tags_with_data": ["RETO.PV.torque", ...]
      }
    """
    hist_all = _get_tag_history()
    earliest = None
    latest = None
    tags_with_data = []
    for name, pts in hist_all.items():
        if not pts:
            continue
        tags_with_data.append(name)
        e = pts[0]["t"] * 1000.0
        l = pts[-1]["t"] * 1000.0
        if earliest is None or e < earliest:
            earliest = e
        if latest is None or l > latest:
            latest = l
    return jsonify({
        "earliest_ms":    int(earliest) if earliest is not None else None,
        "latest_ms":      int(latest) if latest is not None else None,
        "tags_with_data": tags_with_data,
    })
