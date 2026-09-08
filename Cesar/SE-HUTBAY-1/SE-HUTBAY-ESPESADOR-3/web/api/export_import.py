# -*- coding: utf-8 -*-
"""Blueprint bp_export_import — paquetes de configuracion export / import."""
from __future__ import annotations

import copy
import os
import shutil
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

import config as cfg_mod
from connectors import kepserver as kep_mod
from core.jsonio import escribir_json_atomico
from web.api.contrato import (
    CONTRATO_JSON,
    _guardar_contrato,
    _normalizar as _normalizar_contrato,
    analizar_impacto,
)
from web.state import (
    _leer_json,
    _load_tags,
    _save_tags,
    _tags_lock,
    _se_engine,
    REGLAS_JSON,
    FILTROS_JSON,
    DEFUZZY_JSON,
    FUZZY_JSON,
    VARIABLES_JSON,
    PERMISIVOS_JSON,
    ESTADOS_JSON_PATH,
    WAITS_JSON_PATH,
    TRACKING_JSON,
    PENDIENTES_JSON,
    TAGS_JSON,
)

bp_export_import = Blueprint("export_import", __name__)

FORMAT_ID = "se-hutbay-export"
FORMAT_VERSION = 2

_CFG_DIR = os.path.dirname(CONTRATO_JSON)

MODULOS = {
    "tags": {
        "label": "Tags",
        "hint": "OPC-UA, pseudonimos, generador, heartbeat, handshake (tags.json)",
        "grupo": "Conexion",
    },
    "contrato": {
        "label": "Contrato de variables",
        "hint": "PV, SP, rate_sp, descripciones (limites_sp es legado)",
        "grupo": "Conexion",
    },
    "kepserver": {
        "label": "KEPserver (OPC-UA)",
        "hint": "Timeout, calidad, retencion (host/port solo en Reemplazar)",
        "grupo": "Conexion",
    },
    "variables": {
        "label": "Variables calculadas",
        "hint": "variables.json",
        "grupo": "Pipeline",
    },
    "filtros": {
        "label": "Filtros Exp-Q",
        "hint": "filtros.json",
        "grupo": "Pipeline",
    },
    "fuzzy": {
        "label": "Fuzzificacion",
        "hint": "fuzzy.json — membresias, tags de limite y respaldo numerico",
        "grupo": "Pipeline",
    },
    "pendientes": {
        "label": "Pendientes fuzzy",
        "hint": "pendientes.json",
        "grupo": "Pipeline",
    },
    "estados": {
        "label": "Estados",
        "hint": "estados.json",
        "grupo": "Pipeline",
    },
    "tracking": {
        "label": "Tracking PV-SP",
        "hint": "tracking.json",
        "grupo": "Pipeline",
    },
    "permisivos": {
        "label": "Permisivos",
        "hint": "permisivos.json",
        "grupo": "Pipeline",
    },
    "waits": {
        "label": "Waits",
        "hint": "waits.json",
        "grupo": "Pipeline",
    },
    "reglas": {
        "label": "Motor de reglas",
        "hint": "reglas.json",
        "grupo": "Pipeline",
    },
    "defuzzy": {
        "label": "Defuzzificacion",
        "hint": "defuzzy.json — tablas Sugeno y limites de escritura del SP",
        "grupo": "Pipeline",
    },
}

# Orden fijo de aplicacion: tags y contrato antes del pipeline.
ORDEN_MODULOS = [
    "tags",
    "contrato",
    "variables",
    "filtros",
    "fuzzy",
    "pendientes",
    "estados",
    "tracking",
    "permisivos",
    "waits",
    "reglas",
    "defuzzy",
    "kepserver",
]

_MODULO_A_RUTA: dict[str, str] = {
    "tags": TAGS_JSON,
    "contrato": CONTRATO_JSON,
    "variables": VARIABLES_JSON,
    "filtros": FILTROS_JSON,
    "fuzzy": FUZZY_JSON,
    "pendientes": PENDIENTES_JSON,
    "estados": ESTADOS_JSON_PATH,
    "tracking": TRACKING_JSON,
    "permisivos": PERMISIVOS_JSON,
    "waits": WAITS_JSON_PATH,
    "reglas": REGLAS_JSON,
    "defuzzy": DEFUZZY_JSON,
    "kepserver": kep_mod.CONFIG_JSON,
}

_KEP_EXPORT_KEYS = (
    "host", "port", "timeout_s", "aceptar_uncertain",
    "estancado_alerta_s", "retencion_s",
)
_KEP_SOLO_MERGE = ("timeout_s", "aceptar_uncertain", "estancado_alerta_s", "retencion_s")

ULTIMO_IMPORT_JSON = os.path.join(_CFG_DIR, ".backup", "ultimo_import.json")
HISTORIAL_IMPORT_JSON = os.path.join(_CFG_DIR, ".backup", "historial_import.json")
MAX_BACKUPS_HISTORIAL = 10

_LEGACY_TAG_KEYS = ("tags_kepserver", "entrada_datos")

_DICT_PREVIEW = (
    "filtros", "fuzzy", "pendientes", "estados", "tracking", "permisivos", "defuzzy",
)

_TAG_KEP_FIELDS = ("id", "name", "data_type", "enabled", "categoria", "rol")
_TAG_ENTRADA_FIELDS = (
    "id", "name", "pseudonimo", "unidad_ing", "equipo", "instrumento",
    "enabled", "categoria",
)
# `handshake` estaba fuera hasta el 2026-09-03: el permiso del DCS para
# escribir —que es fail-closed y decide si el SE mueve algo— no viajaba en el
# paquete, asi que una planta clonada arrancaba sin el y nadie se enteraba
# hasta que no se escribia ningun setpoint.
# "grafico" son las preferencias del Explorador de Series (color y limites
# por tag). Viajan con el export a proposito: son parte de como se lee la
# planta, y una planta clonada sin ellas obliga a recolorear todo a mano.
_TAG_STORE_KEYS = ("catalogs", "simulation_mode", "generator", "heartbeat",
                   "handshake", "grafico")


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _motor_corriendo() -> bool:
    try:
        return bool(_se_engine.status().get("running"))
    except Exception:
        return False


def _tag_index(tags: list[dict]) -> dict[int | str, dict]:
    idx: dict[int | str, dict] = {}
    for t in tags:
        tid = t.get("id")
        if tid is not None:
            idx[tid] = t
        name = t.get("name")
        if name:
            idx[str(name)] = t
    return idx


def _max_tag_id(tags: list[dict]) -> int:
    mx = 0
    for t in tags:
        try:
            mx = max(mx, int(t.get("id") or 0))
        except (TypeError, ValueError):
            pass
    return mx


def _asignar_id_nuevo(store: dict) -> int:
    siguiente = max(int(store.get("next_id") or 0), _max_tag_id(store.get("tags", []))) + 1
    store["next_id"] = siguiente + 1
    return siguiente


def _export_tags_kep(store: dict) -> dict:
    tags = []
    for t in store.get("tags", []):
        tags.append({k: t[k] for k in _TAG_KEP_FIELDS if k in t})
    out = {"tags": tags}
    if "next_id" in store:
        out["next_id"] = store["next_id"]
    return out


def _export_entrada(store: dict) -> dict:
    tags = []
    for t in store.get("tags", []):
        row = {k: t.get(k) for k in _TAG_ENTRADA_FIELDS}
        tags.append(row)
    out = {"tags": tags}
    for k in _TAG_STORE_KEYS:
        if k in store:
            out[k] = copy.deepcopy(store[k])
    return out


def _export_tags_completo(store: dict) -> dict:
    out: dict = {"tags": copy.deepcopy(store.get("tags", []))}
    if "next_id" in store:
        out["next_id"] = store["next_id"]
    for k in _TAG_STORE_KEYS:
        if k in store:
            out[k] = copy.deepcopy(store[k])
    return out


def _leer_modulo(clave: str):
    if clave == "tags":
        return _export_tags_completo(_load_tags())
    if clave in _LEGACY_TAG_KEYS:
        store = _load_tags()
        return _export_tags_kep(store) if clave == "tags_kepserver" else _export_entrada(store)
    if clave == "contrato":
        return copy.deepcopy(_leer_json(CONTRATO_JSON, {}))
    if clave == "kepserver":
        cfg = kep_mod.get_config()
        return {k: cfg[k] for k in _KEP_EXPORT_KEYS if k in cfg}
    if clave == "variables":
        return copy.deepcopy(_leer_json(VARIABLES_JSON, {"crudas": {}, "definiciones": []}))
    if clave == "tracking":
        return copy.deepcopy(_leer_json(TRACKING_JSON, {}))
    if clave == "filtros":
        return copy.deepcopy(_leer_json(FILTROS_JSON, {}))
    if clave == "fuzzy":
        return copy.deepcopy(_leer_json(FUZZY_JSON, {}))
    if clave == "pendientes":
        return copy.deepcopy(_leer_json(PENDIENTES_JSON, {}))
    if clave == "estados":
        return copy.deepcopy(_leer_json(ESTADOS_JSON_PATH, {}))
    if clave == "waits":
        return copy.deepcopy(_leer_json(WAITS_JSON_PATH, []))
    if clave == "permisivos":
        return copy.deepcopy(_leer_json(PERMISIVOS_JSON, {}))
    if clave == "reglas":
        return copy.deepcopy(_leer_json(REGLAS_JSON, []))
    if clave == "defuzzy":
        return copy.deepcopy(_leer_json(DEFUZZY_JSON, {}))
    raise KeyError(clave)


def _normalizar_selected(raw: dict | None) -> dict[str, bool]:
    sel = {k: False for k in MODULOS}
    if not isinstance(raw, dict):
        return sel
    for k in MODULOS:
        sel[k] = bool(raw.get(k))
    return sel


def _selected_ui(raw: dict | None, data: dict | None = None) -> dict[str, bool]:
    """Casillas para la UI: paquetes viejos con tags_kepserver/entrada_datos -> tags."""
    sel = _normalizar_selected(raw)
    if not isinstance(raw, dict):
        return sel
    legacy = any(raw.get(k) for k in _LEGACY_TAG_KEYS)
    legacy_data = isinstance(data, dict) and any(k in data for k in _LEGACY_TAG_KEYS)
    if (legacy or legacy_data) and not sel.get("tags"):
        sel["tags"] = True
    return sel


def _tags_en_paquete(raw_sel: dict, data: dict) -> bool:
    if raw_sel.get("tags") and "tags" in data:
        return True
    return any(raw_sel.get(k) and k in data for k in _LEGACY_TAG_KEYS)


def _bloques_tags_desde_paquete(raw_sel: dict, data: dict) -> tuple[dict | None, dict | None]:
    if raw_sel.get("tags") and isinstance(data.get("tags"), dict):
        bloque = data["tags"]
        tags_list = bloque.get("tags") or []
        kep = {
            "tags": [{k: t[k] for k in _TAG_KEP_FIELDS if k in t} for t in tags_list
                     if isinstance(t, dict)],
        }
        if "next_id" in bloque:
            kep["next_id"] = bloque["next_id"]
        entrada: dict = {"tags": tags_list}
        for k in _TAG_STORE_KEYS:
            if k in bloque:
                entrada[k] = bloque[k]
        return kep, entrada
    kep = data.get("tags_kepserver") if raw_sel.get("tags_kepserver") else None
    ent = data.get("entrada_datos") if raw_sel.get("entrada_datos") else None
    return kep, ent


def build_export_package(selected: dict[str, bool]) -> dict:
    sel = _normalizar_selected(selected)
    data = {}
    for clave, marcado in sel.items():
        if marcado:
            data[clave] = _leer_modulo(clave)
    return {
        "format": FORMAT_ID,
        "version": FORMAT_VERSION,
        "exported_at": _ahora_iso(),
        "selected": sel,
        "data": data,
    }


def _validar_paquete(pkg: dict) -> str | None:
    if not isinstance(pkg, dict):
        return "El archivo no es un objeto JSON valido."
    if pkg.get("format") != FORMAT_ID:
        return "No es un paquete de exportacion de SE HUTBAY (campo 'format' invalido)."
    if not isinstance(pkg.get("selected"), dict):
        return "Falta el mapa 'selected' con las casillas marcadas."
    if not isinstance(pkg.get("data"), dict):
        return "Falta la seccion 'data'."
    return None


def _ids_reglas(reglas: list) -> set[str]:
    return {str(r.get("id")) for r in reglas if isinstance(r, dict) and r.get("id")}


def _nombre_copia(base: str, ocupados: set[str]) -> str:
    if base not in ocupados:
        return base
    n = 1
    while f"{base}_Copia_{n}" in ocupados:
        n += 1
    return f"{base}_Copia_{n}"


def _preview_reglas(importadas: list, existentes: list) -> dict:
    return _preview_lista_por_id(importadas, existentes, "id")


def _preview_lista_por_id(importados: list, existentes: list, campo_id: str) -> dict:
    ids_exist = {
        str(x.get(campo_id)) for x in existentes
        if isinstance(x, dict) and x.get(campo_id)
    }
    ids_imp = {
        str(x.get(campo_id)) for x in importados
        if isinstance(x, dict) and x.get(campo_id)
    }
    duplicados = sorted(ids_imp & ids_exist)
    nuevos = sorted(ids_imp - ids_exist)
    return {"total": len(ids_imp), "duplicados": duplicados, "nuevos": nuevos}


def _preview_dict(importado: dict, existente: dict) -> dict:
    keys_imp = set(importado.keys()) if isinstance(importado, dict) else set()
    keys_exist = set(existente.keys()) if isinstance(existente, dict) else set()
    duplicados = sorted(keys_imp & keys_exist)
    nuevos = sorted(keys_imp - keys_exist)
    return {"total": len(keys_imp), "duplicados": duplicados, "nuevos": nuevos}


def _preview_variables(importado: dict, existente: dict) -> dict:
    def _nombres(data: dict) -> set[str]:
        out = set((data.get("crudas") or {}).keys())
        for d in data.get("definiciones") or []:
            if isinstance(d, dict) and d.get("nombre"):
                out.add(str(d["nombre"]))
        return out

    imp = _nombres(importado if isinstance(importado, dict) else {})
    ex = _nombres(existente if isinstance(existente, dict) else {})
    return {
        "total": len(imp),
        "duplicados": sorted(imp & ex),
        "nuevos": sorted(imp - ex),
    }


def _preview_kepserver(importado: dict) -> dict:
    actual = kep_mod.get_config()
    diffs = []
    for k in _KEP_EXPORT_KEYS:
        if k in importado and importado.get(k) != actual.get(k):
            diffs.append(k)
    return {
        "diferencias": diffs,
        "host_actual": actual.get("host"),
        "port_actual": actual.get("port"),
        "nota": "En Agregar/Copias no se cambia host ni port del destino.",
    }


def _preview_tags(kep: dict | None, entrada: dict | None) -> dict:
    idx = _tag_index(_load_tags().get("tags", []))
    nombres_exist = {str(t.get("name")) for t in idx.values() if t.get("name")}
    vistos: set[str] = set()
    nuevos, existentes = [], []

    def _registrar(t: dict) -> None:
        name = str(t.get("name") or "").strip()
        if not name or name in vistos:
            return
        vistos.add(name)
        ref = idx.get(t.get("id")) or idx.get(name)
        if ref:
            existentes.append(name)
        else:
            nuevos.append(name)

    for t in (kep or {}).get("tags", []):
        if isinstance(t, dict):
            _registrar(t)
    for t in (entrada or {}).get("tags", []):
        if isinstance(t, dict):
            _registrar(t)

    duplicados = sorted(vistos & nombres_exist)
    return {
        "total": len(vistos),
        "nuevos": sorted(set(nuevos) - set(duplicados)),
        "duplicados": duplicados,
    }


def _merge_dict(dest: dict, src: dict, modo: str) -> dict:
    out = copy.deepcopy(dest)
    for k, v in src.items():
        if k not in out:
            out[k] = copy.deepcopy(v)
            continue
        if modo == "reemplazar":
            out[k] = copy.deepcopy(v)
        elif modo == "copias":
            nk = _nombre_copia(str(k), set(out.keys()))
            out[nk] = copy.deepcopy(v)
    return out


def _aplicar_lista_por_id(ruta: str, importados: list, campo_id: str, modo: str) -> dict:
    existentes = _leer_json(ruta, [])
    if not isinstance(existentes, list):
        existentes = []
    if not isinstance(importados, list):
        return {"error": "el bloque importado no es una lista"}

    ids_exist = {str(x.get(campo_id)) for x in existentes if x.get(campo_id)}
    agregadas, reemplazadas, copiadas = [], [], []

    for item in importados:
        if not isinstance(item, dict):
            continue
        iid = str(item.get(campo_id, "")).strip()
        if not iid:
            continue
        nuevo = copy.deepcopy(item)
        if iid not in ids_exist:
            existentes.append(nuevo)
            ids_exist.add(iid)
            agregadas.append(iid)
            continue
        if modo == "agregar":
            continue
        if modo == "reemplazar":
            idx = next(i for i, x in enumerate(existentes) if str(x.get(campo_id)) == iid)
            existentes[idx] = nuevo
            reemplazadas.append(iid)
            continue
        if modo == "copias":
            nid = _nombre_copia(iid, ids_exist)
            nuevo[campo_id] = nid
            existentes.append(nuevo)
            ids_exist.add(nid)
            copiadas.append(nid)

    escribir_json_atomico(ruta, existentes)
    return {"agregadas": agregadas, "reemplazadas": reemplazadas, "copiadas": copiadas}


def _fusionar_tags_importados(kep: dict | None, entrada: dict | None) -> list[dict]:
    fusion: dict[str, dict] = {}

    def _key(t: dict) -> str:
        if t.get("id") is not None:
            return f"id:{t['id']}"
        name = str(t.get("name") or "").strip()
        return f"name:{name}" if name else ""

    for t in (kep or {}).get("tags", []):
        if not isinstance(t, dict):
            continue
        k = _key(t)
        if not k:
            continue
        fusion.setdefault(k, {})["kep"] = t
    for t in (entrada or {}).get("tags", []):
        if not isinstance(t, dict):
            continue
        k = _key(t)
        if not k:
            continue
        fusion.setdefault(k, {})["entrada"] = t

    out = []
    for parts in fusion.values():
        kep_t = parts.get("kep", {})
        ent_t = parts.get("entrada", {})
        row = {}
        for k in _TAG_KEP_FIELDS:
            if k in kep_t:
                row[k] = kep_t[k]
            elif k in ent_t:
                row[k] = ent_t[k]
        for k in _TAG_ENTRADA_FIELDS:
            if k in ent_t and k not in row:
                row[k] = ent_t[k]
        if row.get("name") or row.get("id") is not None:
            out.append(row)
    return out


def _aplicar_tags_fusionado(kep: dict | None, entrada: dict | None, modo: str) -> dict:
    importados = _fusionar_tags_importados(kep, entrada)
    agregados, actualizados, copiados, omitidos = [], [], [], []

    with _tags_lock:
        store = _load_tags()
        tags = store.setdefault("tags", [])
        idx = _tag_index(tags)
        nombres = {str(t.get("name")) for t in tags if t.get("name")}

        for row in importados:
            ref = None
            if row.get("id") is not None:
                ref = idx.get(row["id"])
            if ref is None and row.get("name"):
                ref = idx.get(str(row["name"]))

            if ref is None:
                if modo == "agregar" or modo == "reemplazar":
                    nuevo = copy.deepcopy(row)
                    if not nuevo.get("id"):
                        nuevo["id"] = _asignar_id_nuevo(store)
                    elif int(nuevo["id"]) >= int(store.get("next_id") or 0):
                        store["next_id"] = int(nuevo["id"]) + 1
                    tags.append(nuevo)
                    idx[nuevo["id"]] = nuevo
                    if nuevo.get("name"):
                        idx[str(nuevo["name"])] = nuevo
                        nombres.add(str(nuevo["name"]))
                    agregados.append(str(nuevo.get("name") or nuevo.get("id")))
                elif modo == "copias":
                    nuevo = copy.deepcopy(row)
                    base = str(nuevo.get("name") or f"tag_{nuevo.get('id', 'x')}")
                    nuevo["name"] = _nombre_copia(base, nombres)
                    nuevo["id"] = _asignar_id_nuevo(store)
                    nuevo["rol"] = ""
                    tags.append(nuevo)
                    idx[nuevo["id"]] = nuevo
                    idx[nuevo["name"]] = nuevo
                    nombres.add(nuevo["name"])
                    copiados.append(nuevo["name"])
                else:
                    omitidos.append(str(row.get("name") or row.get("id")))
                continue

            for k in _TAG_KEP_FIELDS:
                if k in row and k != "id":
                    ref[k] = row[k]
            for k in _TAG_ENTRADA_FIELDS:
                if k in row and k not in ("id", "name"):
                    ref[k] = row[k]
            actualizados.append(str(ref.get("name") or ref.get("id")))

        if entrada:
            for k in _TAG_STORE_KEYS:
                if k not in entrada:
                    continue
                if modo == "reemplazar" or k not in store:
                    store[k] = copy.deepcopy(entrada[k])
                elif modo == "agregar" and isinstance(entrada[k], dict) and isinstance(store.get(k), dict):
                    store[k] = {**store[k], **copy.deepcopy(entrada[k])}
                else:
                    store[k] = copy.deepcopy(entrada[k])

        if kep and "next_id" in kep:
            store["next_id"] = max(
                int(store.get("next_id") or 0),
                int(kep["next_id"]),
                _max_tag_id(tags) + 1,
            )

        _save_tags(store)

    return {
        "agregados": agregados,
        "actualizados": actualizados,
        "copiados": copiados,
        "omitidos": omitidos,
    }


def _limpiar_roles_tras_contrato(norm: dict) -> list[dict]:
    roles_validos = set(norm["variables_proceso"]) | set(norm["setpoints"])
    for v in norm["variables_proceso"]:
        roles_validos |= {f"{v}_lmin", f"{v}_lmax"}
    roles_validos |= set(cfg_mod.VARIABLES_CRUDAS_REQUERIDAS)

    limpiados = []
    with _tags_lock:
        store = _load_tags()
        for t in store.get("tags", []):
            if t.get("rol") and t["rol"] not in roles_validos:
                limpiados.append({"tag": t["name"], "rol": t["rol"]})
                t["rol"] = ""
        if limpiados:
            _save_tags(store)
    return limpiados


def _aplicar_contrato(bloque: dict) -> dict:
    norm, error = _normalizar_contrato(bloque)
    if error:
        return {"error": error}
    impacto = analizar_impacto(norm)
    if impacto["errores"]:
        return {"error": " ".join(impacto["errores"]), "impacto": impacto}

    _guardar_contrato(norm)
    limpiados = _limpiar_roles_tras_contrato(norm)
    cfg_mod.recargar_contrato()
    return {"ok": True, "impacto": impacto, "roles_limpiados": limpiados}


def _aplicar_kepserver(bloque: dict, modo: str) -> dict:
    if not isinstance(bloque, dict):
        return {"error": "bloque kepserver invalido"}
    actual = kep_mod.get_config()
    nuevo = dict(actual)
    if modo == "reemplazar":
        for k in _KEP_EXPORT_KEYS:
            if k in bloque:
                nuevo[k] = bloque[k]
        kep_mod._save_config(nuevo)
        return {"ok": True, "host_port_actualizados": True}
    for k in _KEP_SOLO_MERGE:
        if k in bloque:
            nuevo[k] = bloque[k]
    kep_mod._save_config(nuevo)
    return {"ok": True, "host_port_conservados": True}


def _sincronizar_filtros_post() -> dict:
    from web.api.config import _load_filtros, _save_filtros, entradas_pv_disponibles
    from web.state import FILTRO_NUEVO_DEFAULT

    actual = _load_filtros()
    items, _ = entradas_pv_disponibles()
    idents = [i["identificador"] for i in items]
    agregadas = [i for i in idents if i not in actual]
    quitadas = [v for v in actual if v not in idents]
    if not agregadas and not quitadas:
        return {"agregadas": [], "quitadas": []}
    nuevo = {v: c for v, c in actual.items() if v in idents}
    for i in agregadas:
        nuevo[i] = dict(FILTRO_NUEVO_DEFAULT)
    _save_filtros(nuevo)
    return {"agregadas": agregadas, "quitadas": quitadas}


def _sincronizar_tracking_post() -> dict:
    from web.api.config import _load_tracking, _save_tracking, salidas_sp_disponibles

    actual = _load_tracking()
    idents = [s["identificador"] for s in salidas_sp_disponibles()]
    agregadas = [i for i in idents if i not in actual]
    quitadas = [f for f in actual if f not in idents]
    if not agregadas and not quitadas:
        return {"agregadas": [], "quitadas": []}
    nuevo = {f: v for f, v in actual.items() if f in idents}
    for i in agregadas:
        nuevo[i] = {"pv_key": "", "rango": 0.0, "habilitado": False}
    _save_tracking(nuevo)
    return {"agregadas": agregadas, "quitadas": quitadas}


def _aplicar_variables(importado: dict, modo: str) -> dict:
    from web.api.config import _load_variables, _save_variables, _normalizar_variables_payload

    norm, error = _normalizar_variables_payload(importado)
    if error:
        return {"error": error}

    if modo == "reemplazar":
        _save_variables(norm)
        return {"ok": True, "reemplazado": True}

    actual = _load_variables()
    crudas_out = dict(actual.get("crudas") or {})
    ag_crudas, re_crudas, co_crudas = [], [], []

    for nombre, descr in (norm.get("crudas") or {}).items():
        if nombre not in crudas_out:
            crudas_out[nombre] = descr
            ag_crudas.append(nombre)
            continue
        if modo == "agregar":
            continue
        if modo == "reemplazar":
            crudas_out[nombre] = descr
            re_crudas.append(nombre)
            continue
        if modo == "copias":
            nid = _nombre_copia(str(nombre), set(crudas_out.keys()))
            crudas_out[nid] = descr
            co_crudas.append(nid)

    defs_out = [copy.deepcopy(d) for d in (actual.get("definiciones") or []) if isinstance(d, dict)]
    nombres_defs = {str(d.get("nombre")) for d in defs_out if d.get("nombre")}
    ocupados = nombres_defs | set(crudas_out.keys())
    ag_defs, re_defs, co_defs = [], [], []

    for item in norm.get("definiciones") or []:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("nombre", "")).strip()
        if not nombre:
            continue
        nuevo = copy.deepcopy(item)
        if nombre not in nombres_defs:
            defs_out.append(nuevo)
            nombres_defs.add(nombre)
            ag_defs.append(nombre)
            continue
        if modo == "agregar":
            continue
        if modo == "reemplazar":
            idx = next(i for i, d in enumerate(defs_out) if str(d.get("nombre")) == nombre)
            defs_out[idx] = nuevo
            re_defs.append(nombre)
            continue
        if modo == "copias":
            nid = _nombre_copia(nombre, ocupados)
            nuevo["nombre"] = nid
            defs_out.append(nuevo)
            ocupados.add(nid)
            nombres_defs.add(nid)
            co_defs.append(nid)

    resultado = {"crudas": crudas_out, "definiciones": defs_out}
    validado, err2 = _normalizar_variables_payload(resultado)
    if err2:
        return {"error": err2}
    _save_variables(validado)
    return {
        "crudas_agregadas": ag_crudas,
        "crudas_reemplazadas": re_crudas,
        "crudas_copiadas": co_crudas,
        "definiciones_agregadas": ag_defs,
        "definiciones_reemplazadas": re_defs,
        "definiciones_copiadas": co_defs,
    }


def _sincronizar_post_import(sel: dict[str, bool], raw_sel: dict, aplicados: dict) -> dict:
    """Tras tags/contrato, alinea filtros y tracking si no vinieron en el paquete."""
    if "tags" not in aplicados and "contrato" not in aplicados:
        return {}
    extra = {}
    if not sel.get("filtros"):
        r = _sincronizar_filtros_post()
        if r.get("agregadas") or r.get("quitadas"):
            extra["filtros_sync"] = r
    if not sel.get("tracking"):
        r = _sincronizar_tracking_post()
        if r.get("agregadas") or r.get("quitadas"):
            extra["tracking_sync"] = r
    return extra


_MODO_LABELS = {
    "agregar": "Agregar (omitir duplicados)",
    "reemplazar": "Reemplazar duplicados",
    "copias": "Guardar copias",
}


def _label_modulo(mod_id: str) -> str:
    meta = MODULOS.get(mod_id)
    return meta["label"] if meta else mod_id


def _archivos_en_backup(backup_dir: str) -> list[str]:
    if not backup_dir or not os.path.isdir(backup_dir):
        return []
    return sorted(f for f in os.listdir(backup_dir) if f.endswith(".json"))


def _enriquecer_entrada_historial(entrada: dict) -> dict:
    backup_dir = str(entrada.get("backup_dir") or "")
    modulos = list(entrada.get("modulos") or [])
    archivos = list(entrada.get("archivos") or [])
    if not archivos:
        archivos = _archivos_en_backup(backup_dir)
    modo = str(entrada.get("modo") or "agregar")
    return {
        **entrada,
        "backup_nombre": os.path.basename(backup_dir) if backup_dir else "",
        "modulos_labels": [_label_modulo(m) for m in modulos],
        "modo_label": _MODO_LABELS.get(modo, modo),
        "archivos": archivos,
        "n_archivos": len(archivos),
    }


def _guardar_manifest_backup(
    backup_dir: str,
    sel: dict[str, bool],
    data: dict,
    raw_sel: dict,
    *,
    modo: str = "agregar",
    exported_at: str | None = None,
) -> None:
    os.makedirs(os.path.dirname(ULTIMO_IMPORT_JSON), exist_ok=True)
    entrada = {
        "backup_dir": backup_dir,
        "applied_at": _ahora_iso(),
        "modulos": _modulos_a_aplicar(sel, data, raw_sel),
        "modo": modo,
        "exported_at": exported_at,
        "archivos": _archivos_en_backup(backup_dir),
    }
    escribir_json_atomico(ULTIMO_IMPORT_JSON, entrada)

    historial = _leer_json(HISTORIAL_IMPORT_JSON, {"entradas": []})
    entradas = list(historial.get("entradas") or [])
    entradas = [e for e in entradas if e.get("backup_dir") != backup_dir]
    entradas.insert(0, entrada)
    escribir_json_atomico(HISTORIAL_IMPORT_JSON, {
        "entradas": entradas[:MAX_BACKUPS_HISTORIAL],
    })


def listar_backups_historial(enriquecer: bool = False) -> list[dict]:
    historial = _leer_json(HISTORIAL_IMPORT_JSON, {"entradas": []})
    out = []
    for e in historial.get("entradas") or []:
        if not isinstance(e, dict):
            continue
        backup_dir = e.get("backup_dir")
        if backup_dir and os.path.isdir(backup_dir):
            base = {
                "backup_dir": backup_dir,
                "applied_at": e.get("applied_at"),
                "exported_at": e.get("exported_at"),
                "modulos": e.get("modulos") or [],
                "modo": e.get("modo") or "agregar",
                "archivos": e.get("archivos") or _archivos_en_backup(backup_dir),
            }
            out.append(_enriquecer_entrada_historial(base) if enriquecer else base)
    return out


def _info_ultimo_backup() -> dict:
    entradas = listar_backups_historial()
    if not entradas:
        manifest = _leer_json(ULTIMO_IMPORT_JSON, {})
        if isinstance(manifest, dict) and manifest.get("backup_dir"):
            if os.path.isdir(manifest["backup_dir"]):
                return {
                    "disponible": True,
                    "backup_dir": manifest["backup_dir"],
                    "applied_at": manifest.get("applied_at"),
                    "modulos": manifest.get("modulos") or [],
                }
        return {"disponible": False}
    ult = entradas[0]
    return {"disponible": True, **ult, "historial": entradas}


def restaurar_backup(backup_dir: str) -> tuple[dict, str | None]:
    if not backup_dir or not os.path.isdir(backup_dir):
        return {}, "Respaldo no encontrado en disco."
    restaurados = []
    for fname in os.listdir(backup_dir):
        if not fname.endswith(".json"):
            continue
        shutil.copy2(os.path.join(backup_dir, fname), os.path.join(_CFG_DIR, fname))
        restaurados.append(fname)
    if "contrato.json" in restaurados:
        cfg_mod.recargar_contrato()
    return {"restaurados": restaurados, "backup_dir": backup_dir}, None


def restaurar_ultimo_backup() -> tuple[dict, str | None]:
    info = _info_ultimo_backup()
    if not info.get("disponible"):
        return {}, "No hay importacion previa para deshacer."
    return restaurar_backup(info["backup_dir"])


def _aplicar_modulo(clave: str, bloque, modo: str) -> dict | None:
    if clave == "kepserver":
        return _aplicar_kepserver(bloque, modo)
    if clave == "contrato":
        return _aplicar_contrato(bloque)
    if clave == "variables":
        return _aplicar_variables(bloque, modo)
    if clave == "reglas":
        return _aplicar_lista_por_id(REGLAS_JSON, bloque, "id", modo)
    if clave == "waits":
        return _aplicar_lista_por_id(WAITS_JSON_PATH, bloque, "wait_id", modo)
    rutas = {
        "tracking": (TRACKING_JSON, {}),
        "filtros": (FILTROS_JSON, {}),
        "fuzzy": (FUZZY_JSON, {}),
        "pendientes": (PENDIENTES_JSON, {}),
        "estados": (ESTADOS_JSON_PATH, {}),
        "permisivos": (PERMISIVOS_JSON, {}),
        "defuzzy": (DEFUZZY_JSON, {}),
    }
    if clave in rutas:
        ruta, default = rutas[clave]
        if isinstance(default, dict) and isinstance(bloque, dict):
            actual = _leer_json(ruta, copy.deepcopy(default))
            if modo == "reemplazar":
                escribir_json_atomico(ruta, copy.deepcopy(bloque))
            else:
                escribir_json_atomico(ruta, _merge_dict(actual, bloque, modo))
            return {"ok": True}
    return {"error": f"modulo desconocido o tipo no soportado: {clave}"}


def _modulos_a_aplicar(
    sel: dict[str, bool], data: dict, raw_sel: dict | None = None,
) -> list[str]:
    raw = raw_sel if isinstance(raw_sel, dict) else sel
    out = []
    for clave in ORDEN_MODULOS:
        if clave == "tags":
            if _tags_en_paquete(raw, data):
                out.append("tags")
            continue
        if sel.get(clave) and clave in data:
            out.append(clave)
    return out


def _crear_backup(sel: dict[str, bool], data: dict, raw_sel: dict | None = None) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(_CFG_DIR, ".backup", f"import_{ts}")
    os.makedirs(dest, exist_ok=True)

    copiados: set[str] = set()
    for clave in _modulos_a_aplicar(sel, data, raw_sel):
        ruta = _MODULO_A_RUTA.get(clave)
        if not ruta or ruta in copiados:
            continue
        if os.path.isfile(ruta):
            shutil.copy2(ruta, os.path.join(dest, os.path.basename(ruta)))
            copiados.add(ruta)
    return dest


def _aplicar_paquete(
    sel: dict[str, bool], data: dict, modo: str, raw_sel: dict | None = None,
) -> tuple[dict, list[str]]:
    aplicados: dict = {}
    errores: list[str] = []
    raw = raw_sel if isinstance(raw_sel, dict) else sel

    for clave in ORDEN_MODULOS:
        if clave == "tags":
            if not _tags_en_paquete(raw, data):
                continue
            kep, ent = _bloques_tags_desde_paquete(raw, data)
            try:
                res = _aplicar_tags_fusionado(kep, ent, modo)
                aplicados["tags"] = res
            except Exception as e:
                errores.append(f"tags: {e}")
            continue

        if not sel.get(clave) or clave not in data:
            continue

        try:
            res = _aplicar_modulo(clave, data[clave], modo)
            if res and res.get("error"):
                errores.append(f"{clave}: {res['error']}")
            else:
                aplicados[clave] = res or {"ok": True}
        except Exception as e:
            errores.append(f"{clave}: {e}")

    return aplicados, errores


@bp_export_import.route("/api/export-import/modulos", methods=["GET"])
def api_modulos():
    items = [
        {"id": k, "label": v["label"], "hint": v["hint"], "grupo": v["grupo"]}
        for k, v in MODULOS.items()
    ]
    return jsonify({
        "modulos": items,
        "motor_corriendo": _motor_corriendo(),
        "historial_backups": listar_backups_historial(enriquecer=True),
    })


@bp_export_import.route("/api/export-import/export", methods=["POST"])
def api_export():
    body = request.get_json(silent=True) or {}
    selected = body.get("selected") or {}
    if not any(_normalizar_selected(selected).values()):
        return jsonify({"ok": False, "error": "Marca al menos un bloque para exportar."}), 400
    return jsonify({"ok": True, "package": build_export_package(selected)})


def _preview_limites(data: dict, sel: dict) -> dict:
    """Cableados de limite que quedarian colgados despues de importar.

    Un paquete traido de otra planta nombra los tags de ESA planta. El import
    no falla —el binding entra como huerfano y la pagina lo pinta en rojo— pero
    enterarse recien despues de aplicar es tarde: mejor decirlo en el preview,
    que es donde alguien todavia puede elegir no importar ese modulo.

    Los tags que van a existir son los de aca mas los que traiga el paquete: el
    import de tags fusiona, no reemplaza.
    """
    nombres = {t["name"] for t in _load_tags().get("tags", [])
               if t.get("enabled", True) and t.get("categoria") == "lim"}
    bloque_tags = (data.get("tags") or {}) if sel.get("tags") else {}
    for t in (bloque_tags.get("tags") or []):
        if isinstance(t, dict) and t.get("categoria") == "lim" and t.get("enabled", True):
            nombres.add(t.get("name"))

    colgados = []
    for clave in ("fuzzy", "defuzzy"):
        if not sel.get(clave) or not isinstance(data.get(clave), dict):
            continue
        for var, spec in data[clave].items():
            lims = (spec or {}).get("limites")
            if not isinstance(lims, dict):
                continue
            for bound, tag in lims.items():
                tag = str(tag or "").strip()
                if tag and tag not in nombres:
                    colgados.append({"modulo": clave, "variable": str(var),
                                     "bound": str(bound), "tag": tag})
    return {"huerfanos": colgados}


@bp_export_import.route("/api/export-import/preview", methods=["POST"])
def api_preview():
    pkg = request.get_json(silent=True) or {}
    err = _validar_paquete(pkg)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    sel = _normalizar_selected(pkg.get("selected"))
    raw_sel = pkg.get("selected") if isinstance(pkg.get("selected"), dict) else {}
    data = pkg.get("data") or {}
    sel_ui = _selected_ui(raw_sel, data)
    resumen = {}
    conflictos = {}

    if _tags_en_paquete(raw_sel, data):
        kep, ent = _bloques_tags_desde_paquete(raw_sel, data)
        conflictos["tags"] = _preview_tags(kep, ent)
        resumen["tags"] = f"{conflictos['tags']['total']} tag(s)"

    for clave, marcado in sel_ui.items():
        if not marcado or clave not in data:
            continue
        if clave == "tags":
            continue
        bloque = data[clave]
        if clave == "reglas" and isinstance(bloque, list):
            conflictos["reglas"] = _preview_reglas(bloque, _leer_json(REGLAS_JSON, []))
            resumen[clave] = f"{len(bloque)} regla(s)"
        elif clave == "waits" and isinstance(bloque, list):
            conflictos["waits"] = _preview_lista_por_id(
                bloque, _leer_json(WAITS_JSON_PATH, []), "wait_id")
            resumen[clave] = f"{len(bloque)} elemento(s)"
        elif clave == "variables" and isinstance(bloque, dict):
            existente = _leer_json(VARIABLES_JSON, {"crudas": {}, "definiciones": []})
            conflictos["variables"] = _preview_variables(bloque, existente)
            n = len(bloque.get("definiciones") or []) + len((bloque.get("crudas") or {}))
            resumen[clave] = f"{n} entrada(s)"
        elif clave == "kepserver" and isinstance(bloque, dict):
            conflictos["kepserver"] = _preview_kepserver(bloque)
            resumen[clave] = "config OPC-UA"
        elif clave == "contrato" and isinstance(bloque, dict):
            norm, err_c = _normalizar_contrato(bloque)
            if err_c:
                conflictos["contrato"] = {"error": err_c}
                resumen[clave] = "invalido"
            else:
                conflictos["contrato"] = analizar_impacto(norm)
                resumen[clave] = (
                    f"{len(norm['variables_proceso'])} PV, {len(norm['setpoints'])} SP"
                )
        elif clave in _DICT_PREVIEW and isinstance(bloque, dict):
            ruta = _MODULO_A_RUTA[clave]
            existente = _leer_json(ruta, {})
            conflictos[clave] = _preview_dict(bloque, existente if isinstance(existente, dict) else {})
            resumen[clave] = f"{len(bloque)} entrada(s)"
        elif isinstance(bloque, dict):
            resumen[clave] = f"{len(bloque)} entrada(s)"
        elif isinstance(bloque, list):
            resumen[clave] = f"{len(bloque)} elemento(s)"
        else:
            resumen[clave] = "presente"

    lim = _preview_limites(data, sel_ui)
    if lim["huerfanos"]:
        conflictos["limites"] = lim

    resumen = {k: v for k, v in resumen.items() if v}

    return jsonify({
        "ok": True,
        "exported_at": pkg.get("exported_at"),
        "selected": sel_ui,
        "resumen": resumen,
        "conflictos": conflictos,
        "motor_corriendo": _motor_corriendo(),
    })


@bp_export_import.route("/api/export-import/apply", methods=["POST"])
def api_apply():
    if _motor_corriendo():
        return jsonify({
            "ok": False,
            "error": "El motor SE esta corriendo. Detenlo antes de importar configuracion.",
            "motor_corriendo": True,
        }), 409

    body = request.get_json(silent=True) or {}
    pkg = body.get("package") or body
    modo = str(body.get("modo") or "agregar").strip().lower()
    if modo not in ("agregar", "reemplazar", "copias"):
        return jsonify({"ok": False, "error": "Modo invalido. Use agregar, reemplazar o copias."}), 400
    err = _validar_paquete(pkg)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    sel = _normalizar_selected(pkg.get("selected"))
    raw_sel = pkg.get("selected") if isinstance(pkg.get("selected"), dict) else {}
    data = pkg.get("data") or {}
    if not _modulos_a_aplicar(sel, data, raw_sel):
        return jsonify({"ok": False, "error": "No hay bloques marcados para importar."}), 400

    backup_dir = _crear_backup(sel, data, raw_sel)
    _guardar_manifest_backup(
        backup_dir, sel, data, raw_sel,
        modo=modo,
        exported_at=pkg.get("exported_at"),
    )
    aplicados, errores = _aplicar_paquete(sel, data, modo, raw_sel)

    if not errores:
        sync = _sincronizar_post_import(sel, raw_sel, aplicados)
        if sync:
            aplicados["post_sync"] = sync

    aviso = "Importacion aplicada. Arranca el motor SE para usar la configuracion nueva."
    if aplicados.get("post_sync"):
        aviso += " Filtros y/o tracking se sincronizaron con los tags/contrato vigentes."

    return jsonify({
        "ok": not errores,
        "aplicados": aplicados,
        "errores": errores,
        "backup_dir": backup_dir,
        "undo_disponible": not errores,
        "motor_corriendo": False,
        "aviso": aviso if not errores else None,
    }), (200 if not errores else 409)


@bp_export_import.route("/api/export-import/historial", methods=["GET"])
def api_historial():
    return jsonify({
        "ok": True,
        "entradas": listar_backups_historial(enriquecer=True),
        "motor_corriendo": _motor_corriendo(),
        "max_entradas": MAX_BACKUPS_HISTORIAL,
    })


@bp_export_import.route("/api/export-import/undo", methods=["GET"])
def api_undo_info():
    info = _info_ultimo_backup()
    info["historial"] = listar_backups_historial(enriquecer=True)
    return jsonify(info)


@bp_export_import.route("/api/export-import/undo", methods=["POST"])
def api_undo():
    if _motor_corriendo():
        return jsonify({
            "ok": False,
            "error": "El motor SE esta corriendo. Detenlo antes de deshacer la importacion.",
            "motor_corriendo": True,
        }), 409
    body = request.get_json(silent=True) or {}
    backup_dir = str(body.get("backup_dir") or "").strip()
    if backup_dir:
        resultado, err = restaurar_backup(backup_dir)
    else:
        resultado, err = restaurar_ultimo_backup()
    if err:
        return jsonify({"ok": False, "error": err}), 404
    return jsonify({
        "ok": True,
        "restaurados": resultado.get("restaurados", []),
        "backup_dir": resultado.get("backup_dir"),
        "aviso": "Configuracion restaurada desde el respaldo. Arranca el motor SE.",
    })
