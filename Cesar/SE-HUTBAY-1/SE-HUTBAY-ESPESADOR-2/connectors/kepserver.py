# -*- coding: utf-8 -*-
"""Conector KEPserver — toda la lógica OPC-UA en un solo lugar.

IT-8: extraído de web/state.py.

API pública
-----------
URL : str
    Valor inicial de la URL OPC-UA (alias legacy). Preferir `get_url()`.

get_url() -> str
    Devuelve la URL actual (leida de config/espesador/kepserver.json).

get_config() / set_config(host, port) -> dict
    Lee o actualiza host/puerto persistidos.

read_tags_batch(tag_names) -> dict[str, dict]
    Lee múltiples tags en una sesión OPC-UA.
    Devuelve {tag_name: {"connected", "exists", "value", "quality"}}.

write_tag(tag_name, value, data_type) -> dict
    Escribe un valor a un tag. Devuelve {"ok", "error"}.

write_float_batch(tag_values) -> None
    Escribe un dict {tag_name: float} en una sesión. Lanza Exception si no conecta.

enrich_tags(tags) -> list[dict]
    Agrega datos live de KEPserver a una lista de dicts de tags.

check_connection(url=None) -> tuple[bool, str]
    Verifica conectividad. Devuelve (ok, mensaje_error).
"""
from __future__ import annotations

import json
import os

# Config persistida en JSON. La primera vez se genera con defaults + env.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_JSON = os.path.join(_HERE, "config", "espesador", "kepserver.json")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 49320

# Precedencia: JSON > env var > default.
_ENV_URL = os.environ.get("KEPSERVER_URL", "").strip()


def _parse_url(url: str) -> tuple[str, int]:
    """opc.tcp://host:port -> (host, port). Devuelve defaults si falla."""
    try:
        raw = url.strip()
        if raw.startswith("opc.tcp://"):
            raw = raw[len("opc.tcp://"):]
        raw = raw.split("/")[0]
        host, _, port_str = raw.partition(":")
        host = (host or _DEFAULT_HOST).strip()
        port = int(port_str) if port_str else _DEFAULT_PORT
        return host, port
    except (ValueError, AttributeError):
        return _DEFAULT_HOST, _DEFAULT_PORT


def build_url(host: str, port: int) -> str:
    return f"opc.tcp://{host}:{int(port)}"


def _defaults() -> dict:
    if _ENV_URL:
        h, p = _parse_url(_ENV_URL)
    else:
        h, p = _DEFAULT_HOST, _DEFAULT_PORT
    return {
        "host": h,
        "port": p,
        "last_status": "unconfigured",
        "last_message": "",
    }


def _load_config() -> dict:
    if not os.path.exists(CONFIG_JSON):
        cfg = _defaults()
        try:
            _save_config(cfg)
        except OSError:
            pass
        return cfg
    try:
        with open(CONFIG_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return _defaults()
    if not isinstance(data, dict):
        return _defaults()
    return {**_defaults(), **data}


def _save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_JSON), exist_ok=True)
    with open(CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_config() -> dict:
    return _load_config()


def get_url() -> str:
    cfg = _load_config()
    return build_url(cfg.get("host", _DEFAULT_HOST), cfg.get("port", _DEFAULT_PORT))


def set_config(host: str, port: int, last_status: str | None = None, last_message: str | None = None) -> dict:
    cfg = _load_config()
    cfg["host"] = str(host).strip() or _DEFAULT_HOST
    cfg["port"] = int(port)
    if last_status is not None:
        cfg["last_status"] = last_status
    if last_message is not None:
        cfg["last_message"] = last_message
    _save_config(cfg)
    return cfg


# Alias legacy: algunas partes del codigo leen `URL` como constante. Se refresca
# en cada acceso via get_url() cuando lo necesitan; este valor es solo el inicial.
URL = get_url()

_TYPE_MAP = {
    "Float":   "Float",
    "Int":     "Int32",
    "Boolean": "Boolean",
    "String":  "String",
}


def read_tags_batch(tag_names: list[str]) -> dict[str, dict]:
    """Lee múltiples tags del KEPserver en una sola sesión OPC-UA."""
    def _default() -> dict:
        return {"connected": False, "exists": False, "value": None, "quality": "Unknown"}

    results: dict[str, dict] = {n: _default() for n in tag_names}
    if not tag_names:
        return results

    try:
        from opcua import Client  # type: ignore
        client = Client(get_url())
        client.connect()
        try:
            for name in tag_names:
                try:
                    node = client.get_node(f"ns=2;s={name}")
                    val = node.get_value()
                    results[name] = {
                        "connected": True,
                        "exists":    True,
                        "value":     val,
                        "quality":   "Good",
                    }
                except Exception:
                    results[name] = {
                        "connected": True,
                        "exists":    False,
                        "value":     None,
                        "quality":   "Bad",
                    }
        finally:
            client.disconnect()
    except Exception:
        pass  # results permanecen con connected=False

    return results


def write_tag(tag_name: str, value, data_type: str) -> dict:
    """Escribe un valor a un tag KEPserver via OPC-UA.

    Devuelve {"ok": bool, "error": str|None}.
    """
    try:
        from opcua import Client, ua  # type: ignore
        client = Client(get_url())
        client.connect()
        try:
            node = client.get_node(f"ns=2;s={tag_name}")
            vtype_name = _TYPE_MAP.get(data_type, "Float")
            vtype = getattr(ua.VariantType, vtype_name)
            node.set_value(ua.DataValue(ua.Variant(value, vtype)))
            return {"ok": True, "error": None}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            client.disconnect()
    except Exception as e:
        return {"ok": False, "error": f"Sin conexion OPC-UA: {e}"}


def write_float_batch(tag_values: dict[str, float]) -> None:
    """Escribe múltiples tags Float en una sola sesión OPC-UA.

    Lanza Exception si no puede conectar. Ignora errores por tag individual.
    """
    if not tag_values:
        return

    from opcua import Client, ua  # type: ignore  (ImportError se propaga)

    client = Client(get_url())
    client.connect()
    try:
        for tag_name, val in tag_values.items():
            try:
                node = client.get_node(f"ns=2;s={tag_name}")
                node.set_value(
                    ua.DataValue(ua.Variant(float(val), ua.VariantType.Float))
                )
            except Exception:
                pass
    finally:
        client.disconnect()


def enrich_tags(tags: list[dict]) -> list[dict]:
    """Agrega datos live de KEPserver a cada tag dict.

    Tags con enabled=False reciben quality='Suspended' sin consultar al servidor.
    """
    enabled = {t["name"]: t for t in tags if t.get("enabled", True)}
    live = read_tags_batch(list(enabled.keys()))

    results = []
    for tag in tags:
        if tag.get("enabled", True) and tag["name"] in live:
            results.append({**tag, **live[tag["name"]]})
        else:
            results.append({**tag, "connected": False, "exists": False,
                            "value": None, "quality": "Suspended"})
    return results


def check_connection(url: str | None = None, timeout_s: float = 5.0) -> tuple[bool, str]:
    """Verifica conectividad con el KEPserver.

    Si `url` viene, se usa esa URL sin persistir (util para probar antes de guardar).
    Devuelve (True, "") si OK, o (False, mensaje_error) si falla.
    """
    target = url or get_url()
    try:
        from opcua import Client  # type: ignore
        c = Client(target)
        try:
            c.session_timeout = int(timeout_s * 1000)
        except (AttributeError, TypeError):
            pass
        c.connect()
        c.disconnect()
        return True, ""
    except ImportError:
        return False, "Modulo 'opcua' no instalado. Ejecuta: pip install opcua"
    except Exception as e:
        return False, str(e)
