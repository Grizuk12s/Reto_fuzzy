# -*- coding: utf-8 -*-
"""Registro de los SAG de este contenedor (``config/sags.json``).

Cada SAG tiene un id (``sag1``), un nombre visible, un color y el puerto
INTERNO en el que escucha su proceso del SE. El router lee este registro para
saber qué procesos lanzar y a dónde mandar cada petición.

La primera vez que se arranca sin registro se migra lo existente:
    config/espesador/  ->  config/sags/sag1/   (se COPIA; el original queda)

El SAG 1 es fijo: existe siempre y no se puede eliminar. Los demás los crea
el usuario desde la página de gestión (``/gestion``), vacíos o clonando la
lógica de otro SAG. Eliminar un SAG lo ARCHIVA en ``config/sags_archivo/``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time

from core.jsonio import escribir_json_atomico
from rutas import CONFIG_RAIZ, CFG_DIR_LEGADO, id_valido

MAX_SAGS = 4            # tope de SAG por contenedor; se sube cambiando este número
PUERTO_BASE = 5101      # sag N -> PUERTO_BASE + N - 1 (solo dentro del contenedor)
COLORES = ["#14b8a6", "#a78bfa", "#f472b6", "#38bdf8"]

# Archivos que un SAG nuevo hereda del que se toma como base: conexiones y
# licencia. Todo lo demás (tags, reglas, fuzzy...) nace vacío.
ARCHIVOS_CONEXION = ("kepserver.json", "postgres.json", "licencia.json", "motor.json")

SAG_FIJO = "sag1"

# Lo que se copia al CLONAR un SAG: la lógica, no la conexión con el DCS.
ARCHIVOS_LOGICA = ("reglas.json", "filtros.json", "fuzzy.json", "pendientes.json",
                   "aceleraciones.json", "estados.json", "waits.json", "permisivos.json",
                   "defuzzy.json", "tracking.json", "variables.json", "contrato.json")
# Al clonar solo viajan los tags de LECTURA: un SP o un tag de handshake no
# puede quedar en dos SAG (sería escribirle al molino del otro).
CATEGORIAS_LECTURA = ("pv", "cruda", "lim")

_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")

_lock = threading.RLock()


class ErrorSag(ValueError):
    """Error de validación con un mensaje que se puede mostrar tal cual."""


def ruta_registro(config_raiz: str = CONFIG_RAIZ) -> str:
    return os.path.join(config_raiz, "sags.json")


def dir_sag(sag_id: str, config_raiz: str = CONFIG_RAIZ) -> str:
    if not id_valido(sag_id):
        raise ValueError(f"id de SAG invalido: {sag_id!r}")
    return os.path.join(config_raiz, "sags", sag_id)


# ---------------------------------------------------------------------------
# Configuración vacía
# ---------------------------------------------------------------------------
def _w(path: str, data) -> None:
    escribir_json_atomico(path, data)


def crear_config_vacia(destino: str, conexiones_desde: str | None = None) -> None:
    """Deja en ``destino`` una configuración vacía pero VÁLIDA del SE.

    - Contrato con listas vacías (válido desde 2026-10-01: no se rellena con
      la plantilla del espesador).
    - Handshake ENCENDIDO y sin tags: fail-closed. El SAG no escribe ningún
      setpoint al DCS hasta que se configuren SUS Enable_Ext / Enable_FBK.
    - Conexiones (KEPserver, PostgreSQL), licencia y piso del motor se copian
      de ``conexiones_desde`` si existe.
    """
    os.makedirs(destino, exist_ok=True)
    for n in ("aceleraciones", "pendientes", "filtros", "fuzzy", "defuzzy",
              "estados", "permisivos", "tracking"):
        _w(os.path.join(destino, n + ".json"), {})
    _w(os.path.join(destino, "reglas.json"), [])
    _w(os.path.join(destino, "waits.json"), [])
    _w(os.path.join(destino, "variables.json"), {"crudas": {}, "definiciones": []})
    _w(os.path.join(destino, "contrato.json"),
       {"variables_proceso": [], "setpoints": [], "descripciones": {},
        "limites_sp": {}, "rate_sp": {}})
    _w(os.path.join(destino, "tags.json"), {
        "tags": [], "next_id": 1,
        "catalogs": {"equipos": [], "instrumentos": [], "unidades_ing": []},
        "simulation_mode": False,
        "generator": {"intervalo_s": 0.5, "n_ciclo": 5000, "ranges": {}},
        "heartbeat": {"enabled": False, "tag_out": "", "tag_in": "",
                      "intervalo_s": 2.0, "value_a": 1, "value_b": 0,
                      "data_type": "Boolean"},
        "handshake": {"enabled": True, "enable_fbk_tag": "", "enable_ext_tag": ""},
        "grafico": {"colores": {}, "limites": {}},
    })
    _w(os.path.join(destino, "tags_planta_pcs7.json"),
       {"_comentario": ["Catalogo para el seed inicial. Vacio: los tags se cargan desde la pagina de Tags."],
        "prefijo": "", "tags": []})
    _w(os.path.join(destino, "activity_log.json"),
       {"version": 1, "next_id": 1, "unsaved": {}, "restart": {}, "issues": {}, "entries": []})
    if conexiones_desde and os.path.isdir(conexiones_desde):
        for n in ARCHIVOS_CONEXION:
            src = os.path.join(conexiones_desde, n)
            if os.path.isfile(src):
                with open(src, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    # El estado de la ultima prueba de conexion es del otro SAG.
                    data.pop("last_status", None)
                    data.pop("last_message", None)
                _w(os.path.join(destino, n), data)


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def _entrada(n: int) -> dict:
    return {"id": f"sag{n}", "nombre": f"SAG {n}",
            "color": COLORES[(n - 1) % len(COLORES)],
            "puerto": PUERTO_BASE + n - 1}


def migrar(config_raiz: str = CONFIG_RAIZ, legado: str = CFG_DIR_LEGADO) -> dict:
    """Crea el registro con el SAG 1 (copia de la config existente).
    No hace nada si el registro ya existe. Nunca borra la carpeta legada."""
    reg_path = ruta_registro(config_raiz)
    if os.path.exists(reg_path):
        return cargar_registro(config_raiz)
    sags = [_entrada(1)]
    d1 = dir_sag("sag1", config_raiz)
    if not os.path.isdir(d1):
        if os.path.isdir(legado):
            shutil.copytree(legado, d1, ignore=shutil.ignore_patterns(
                "__init__.py", "__pycache__", ".backup", "*.bak"))
        else:
            crear_config_vacia(d1)
    reg = {"version": 1, "max_sags": MAX_SAGS, "sags": sags}
    escribir_json_atomico(reg_path, reg)
    return reg


def cargar_registro(config_raiz: str = CONFIG_RAIZ) -> dict:
    """Lee y valida ``sags.json``. Descarta entradas mal formadas o repetidas
    en vez de fallar: el router tiene que poder arrancar igual."""
    with _lock:
        with open(ruta_registro(config_raiz), encoding="utf-8") as f:
            reg = json.load(f)
    vistos_id, vistos_puerto, sags = set(), set(), []
    for s in reg.get("sags") or []:
        try:
            sid, puerto = str(s["id"]), int(s["puerto"])
        except (KeyError, TypeError, ValueError):
            continue
        if not id_valido(sid) or sid in vistos_id or puerto in vistos_puerto:
            continue
        if not (1024 < puerto < 65536):
            continue
        vistos_id.add(sid); vistos_puerto.add(puerto)
        sags.append({"id": sid, "nombre": str(s.get("nombre") or sid),
                     "color": str(s.get("color") or COLORES[0]), "puerto": puerto})
    return {"version": reg.get("version", 1),
            # El tope lo manda el codigo (MAX_SAGS), no el archivo: asi subirlo
            # no exige editar sags.json en cada planta.
            "max_sags": MAX_SAGS, "sags": sags}


def guardar_registro(reg: dict, config_raiz: str = CONFIG_RAIZ) -> None:
    with _lock:
        escribir_json_atomico(ruta_registro(config_raiz), reg)


# ---------------------------------------------------------------------------
# Crear / actualizar / eliminar
# ---------------------------------------------------------------------------
def _validar_nombre(reg: dict, nombre: str, excepto: str | None = None) -> str:
    nombre = " ".join(str(nombre or "").split())
    if not nombre:
        raise ErrorSag("El nombre es obligatorio.")
    if len(nombre) > 40:
        raise ErrorSag("El nombre no puede pasar de 40 caracteres.")
    if any(s["nombre"].lower() == nombre.lower() and s["id"] != excepto for s in reg["sags"]):
        raise ErrorSag(f"Ya existe un SAG llamado '{nombre}'.")
    return nombre


def _validar_color(color: str) -> str:
    color = str(color or "").strip()
    if not _COLOR_RE.fullmatch(color):
        raise ErrorSag("Color invalido: usa el formato #RRGGBB.")
    return color.lower()


def crear_sag(nombre: str, color: str | None = None, base: str = "vacio",
              config_raiz: str = CONFIG_RAIZ) -> dict:
    """Agrega un SAG al registro y crea su carpeta.

    ``base``: ``"vacio"`` o ``"clonar:<id>"`` (copia la lógica y los tags de
    lectura de ese SAG; nunca SP, handshake ni heartbeat)."""
    with _lock:
        reg = cargar_registro(config_raiz)
        if len(reg["sags"]) >= reg["max_sags"]:
            raise ErrorSag(f"Ya hay {len(reg['sags'])} SAG: es el maximo permitido ({reg['max_sags']}).")
        nombre = _validar_nombre(reg, nombre)
        ids = {s["id"] for s in reg["sags"]}
        n = 1
        while f"sag{n}" in ids or os.path.exists(dir_sag(f"sag{n}", config_raiz)):
            n += 1
        sid = f"sag{n}"
        usados = {s["puerto"] for s in reg["sags"]}
        puerto = PUERTO_BASE
        while puerto in usados:
            puerto += 1
        color = _validar_color(color) if color else COLORES[(n - 1) % len(COLORES)]

        fuente = None
        if base and base != "vacio":
            if not base.startswith("clonar:"):
                raise ErrorSag("Base invalida: usa 'vacio' o 'clonar:<id>'.")
            fuente = base.split(":", 1)[1]
            if fuente not in ids:
                raise ErrorSag(f"No existe el SAG '{fuente}' para clonar.")

        destino = dir_sag(sid, config_raiz)
        conexiones = dir_sag(fuente or SAG_FIJO, config_raiz)
        crear_config_vacia(destino, conexiones_desde=conexiones)
        if fuente:
            _clonar_logica(dir_sag(fuente, config_raiz), destino)

        entrada = {"id": sid, "nombre": nombre, "color": color, "puerto": puerto}
        reg["sags"].append(entrada)
        guardar_registro(reg, config_raiz)
        return entrada


def _clonar_logica(origen: str, destino: str) -> None:
    for n in ARCHIVOS_LOGICA:
        src = os.path.join(origen, n)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(destino, n))
    try:
        with open(os.path.join(origen, "tags.json"), encoding="utf-8") as f:
            tags_src = json.load(f)
    except (OSError, ValueError):
        return
    with open(os.path.join(destino, "tags.json"), encoding="utf-8") as f:
        tags_dst = json.load(f)
    lectura = [dict(t) for t in tags_src.get("tags", [])
               if str(t.get("categoria", "")).lower() in CATEGORIAS_LECTURA]
    for i, t in enumerate(lectura, start=1):
        t["id"] = i
    tags_dst["tags"] = lectura
    tags_dst["next_id"] = len(lectura) + 1
    tags_dst["catalogs"] = tags_src.get("catalogs", tags_dst["catalogs"])
    escribir_json_atomico(os.path.join(destino, "tags.json"), tags_dst)


def actualizar_sag(sid: str, nombre: str | None = None, color: str | None = None,
                   config_raiz: str = CONFIG_RAIZ) -> dict:
    with _lock:
        reg = cargar_registro(config_raiz)
        sag = next((s for s in reg["sags"] if s["id"] == sid), None)
        if sag is None:
            raise ErrorSag(f"El SAG '{sid}' no existe.")
        if nombre is not None:
            sag["nombre"] = _validar_nombre(reg, nombre, excepto=sid)
        if color is not None:
            sag["color"] = _validar_color(color)
        guardar_registro(reg, config_raiz)
        return sag


def eliminar_sag(sid: str, config_raiz: str = CONFIG_RAIZ) -> str:
    """Quita el SAG del registro y ARCHIVA su carpeta. Devuelve la ruta del
    archivo. El SAG 1 no se puede eliminar."""
    with _lock:
        if sid == SAG_FIJO:
            raise ErrorSag("El SAG 1 es el SAG por defecto y no se puede eliminar.")
        reg = cargar_registro(config_raiz)
        if not any(s["id"] == sid for s in reg["sags"]):
            raise ErrorSag(f"El SAG '{sid}' no existe.")
        archivo = os.path.join(config_raiz, "sags_archivo",
                               f"{sid}_{time.strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(os.path.dirname(archivo), exist_ok=True)
        origen = dir_sag(sid, config_raiz)
        if os.path.isdir(origen):
            shutil.move(origen, archivo)
        reg["sags"] = [s for s in reg["sags"] if s["id"] != sid]
        guardar_registro(reg, config_raiz)
        return archivo
