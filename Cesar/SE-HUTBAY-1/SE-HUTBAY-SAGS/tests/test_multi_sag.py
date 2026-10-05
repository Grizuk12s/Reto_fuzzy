# -*- coding: utf-8 -*-
"""Varios SAG en un contenedor: rutas por SAG, registro, migración y router.

Lo que se protege:
- Cada proceso del SE lee/escribe SOLO la carpeta de su SAG (SE_SAG_ID).
- El SAG nuevo nace vacío pero válido y fail-closed hacia el DCS.
- El router manda cada petición al SAG correcto, y una página queda fijada a
  su SAG aunque otra pestaña cambie la cookie.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import router
import sags as sags_mod

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _py(code: str, **env) -> str:
    e = dict(os.environ, **env)
    e.pop("SE_PROCESO_BD", None)
    return subprocess.check_output([sys.executable, "-c", code], cwd=RAIZ, env=e,
                                   text=True, stderr=subprocess.DEVNULL).strip()


# ---------------------------------------------------------------------------
# rutas.py
# ---------------------------------------------------------------------------
def test_sin_sag_id_se_usa_la_carpeta_de_un_solo_equipo():
    out = _py("import rutas; print(rutas.CFG_DIR)", SE_SAG_ID="")
    assert out.endswith(os.path.join("config", "espesador"))


def test_cada_sag_lee_su_propia_carpeta_y_la_ven_todos_los_modulos():
    code = ("import rutas, config, connectors.kepserver as k, web.state as st;"
            "print(rutas.CFG_DIR); print(config.CONTRATO_JSON); print(k.CONFIG_JSON); print(st.TAGS_JSON)")
    lineas = _py(code, SE_SAG_ID="sag2").splitlines()[-4:]
    carpeta = os.path.join("config", "sags", "sag2")
    assert all(carpeta in l for l in lineas), lineas


def test_las_tablas_de_bd_llevan_el_id_del_sag():
    assert _py("import web.api.postgres as p; print(p.PROCESO_BD)", SE_SAG_ID="sag2").splitlines()[-1] == "sag2"


def test_un_sag_id_invalido_no_arranca():
    with pytest.raises(subprocess.CalledProcessError):
        _py("import rutas", SE_SAG_ID="../hopper")


# ---------------------------------------------------------------------------
# sags.py
# ---------------------------------------------------------------------------
@pytest.fixture()
def raiz_cfg(tmp_path):
    legado = tmp_path / "espesador"
    legado.mkdir()
    (legado / "reglas.json").write_text(json.dumps([{"id": "R1"}]), encoding="utf-8")
    (legado / "kepserver.json").write_text(json.dumps({"host": "kep", "port": 49320,
                                                      "last_status": "connected"}), encoding="utf-8")
    (legado / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path, legado


def test_la_migracion_copia_lo_actual_a_sag1_y_nada_mas(raiz_cfg):
    """El SAG 2 lo crea el usuario desde la gestion, no la migracion."""
    raiz, legado = raiz_cfg
    reg = sags_mod.migrar(str(raiz), str(legado))
    assert [s["id"] for s in reg["sags"]] == ["sag1"]
    s1 = raiz / "sags" / "sag1"
    assert json.loads((s1 / "reglas.json").read_text()) == [{"id": "R1"}]
    assert not (s1 / "__init__.py").exists()
    assert (legado / "reglas.json").exists(), "la carpeta original no se toca"
    assert not (raiz / "sags" / "sag2").exists()


def test_crear_sag_vacio_hereda_solo_las_conexiones(raiz_cfg):
    raiz, legado = raiz_cfg
    sags_mod.migrar(str(raiz), str(legado))
    nuevo = sags_mod.crear_sag("Molino Norte", "#A78BFA", config_raiz=str(raiz))
    assert nuevo["id"] == "sag2" and nuevo["color"] == "#a78bfa"
    assert nuevo["puerto"] != sags_mod.cargar_registro(str(raiz))["sags"][0]["puerto"]
    s2 = raiz / "sags" / "sag2"
    assert json.loads((s2 / "reglas.json").read_text()) == []
    k2 = json.loads((s2 / "kepserver.json").read_text())
    assert k2["host"] == "kep" and "last_status" not in k2


def test_crear_sag_valida_nombre_color_y_maximo(raiz_cfg):
    raiz, legado = raiz_cfg
    sags_mod.migrar(str(raiz), str(legado))
    with pytest.raises(sags_mod.ErrorSag):
        sags_mod.crear_sag("  ", config_raiz=str(raiz))
    with pytest.raises(sags_mod.ErrorSag):
        sags_mod.crear_sag("sag 1", config_raiz=str(raiz))          # repetido (mayusculas da igual)
    with pytest.raises(sags_mod.ErrorSag):
        sags_mod.crear_sag("SAG X", "rojo", config_raiz=str(raiz))
    for n in (2, 3, 4):
        assert sags_mod.crear_sag(f"SAG {n}", config_raiz=str(raiz))["id"] == f"sag{n}"
    puertos = [s["puerto"] for s in sags_mod.cargar_registro(str(raiz))["sags"]]
    assert len(set(puertos)) == 4
    with pytest.raises(sags_mod.ErrorSag, match="maximo"):
        sags_mod.crear_sag("SAG 5", config_raiz=str(raiz))


def test_clonar_copia_la_logica_y_solo_tags_de_lectura(raiz_cfg):
    raiz, legado = raiz_cfg
    sags_mod.migrar(str(raiz), str(legado))
    s1 = raiz / "sags" / "sag1"
    (s1 / "tags.json").write_text(json.dumps({"tags": [
        {"id": 7, "name": "PV_A", "categoria": "pv", "rol": "pot"},
        {"id": 8, "name": "SP_A", "categoria": "sp", "rol": "vel"},
        {"id": 9, "name": "HS", "categoria": "otro"}],
        "handshake": {"enabled": True, "enable_fbk_tag": "HS", "enable_ext_tag": "HS"}}))
    sags_mod.crear_sag("SAG 2", base="clonar:sag1", config_raiz=str(raiz))
    s2 = raiz / "sags" / "sag2"
    assert json.loads((s2 / "reglas.json").read_text()) == [{"id": "R1"}]
    tags = json.loads((s2 / "tags.json").read_text())
    assert [t["name"] for t in tags["tags"]] == ["PV_A"]
    assert tags["handshake"]["enable_ext_tag"] == "", "el clon nunca hereda el handshake"


def test_el_sag_1_no_se_elimina_y_los_demas_se_archivan(raiz_cfg):
    raiz, legado = raiz_cfg
    sags_mod.migrar(str(raiz), str(legado))
    with pytest.raises(sags_mod.ErrorSag, match="no se puede eliminar"):
        sags_mod.eliminar_sag("sag1", config_raiz=str(raiz))
    sags_mod.crear_sag("SAG 2", config_raiz=str(raiz))
    archivo = sags_mod.eliminar_sag("sag2", config_raiz=str(raiz))
    assert os.path.isfile(os.path.join(archivo, "tags.json"))
    assert not (raiz / "sags" / "sag2").exists()
    assert [s["id"] for s in sags_mod.cargar_registro(str(raiz))["sags"]] == ["sag1"]
    # y se puede volver a crear
    assert sags_mod.crear_sag("SAG 2", config_raiz=str(raiz))["id"] == "sag2"


def test_el_sag_nuevo_no_puede_escribir_al_dcs_hasta_tener_su_handshake(raiz_cfg):
    raiz, legado = raiz_cfg
    sags_mod.migrar(str(raiz), str(legado))
    sags_mod.crear_sag("SAG 2", config_raiz=str(raiz))
    tags = json.loads((raiz / "sags" / "sag2" / "tags.json").read_text())
    assert tags["tags"] == []
    assert tags["handshake"] == {"enabled": True, "enable_fbk_tag": "", "enable_ext_tag": ""}
    contrato = json.loads((raiz / "sags" / "sag2" / "contrato.json").read_text())
    assert contrato["variables_proceso"] == [] and contrato["setpoints"] == []


def test_la_migracion_no_pisa_un_registro_existente(raiz_cfg):
    raiz, legado = raiz_cfg
    sags_mod.migrar(str(raiz), str(legado))
    sags_mod.crear_sag("SAG 2", config_raiz=str(raiz))
    reg = json.loads((raiz / "sags.json").read_text())
    reg["sags"][1]["nombre"] = "Molino Norte"
    (raiz / "sags.json").write_text(json.dumps(reg))
    assert sags_mod.migrar(str(raiz), str(legado))["sags"][1]["nombre"] == "Molino Norte"


def test_el_registro_descarta_entradas_invalidas_o_repetidas(tmp_path):
    (tmp_path / "sags.json").write_text(json.dumps({"sags": [
        {"id": "sag1", "puerto": 5101}, {"id": "sag1", "puerto": 5109},
        {"id": "SAG X", "puerto": 5102}, {"id": "sag3", "puerto": 5101},
        {"id": "sag4"}, {"id": "sag5", "puerto": 80}, {"id": "sag2", "puerto": 5102}]}))
    assert [s["id"] for s in sags_mod.cargar_registro(str(tmp_path))["sags"]] == ["sag1", "sag2"]


# ---------------------------------------------------------------------------
# router.py
# ---------------------------------------------------------------------------
def _puerto_libre() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class _Eco(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _responder(self):
        n = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(n).decode() if n else ""
        if self.path.startswith("/pagina"):
            data = (f"<html><head><title>t</title><script>window.cargo=1</script></head>"
                    f"<body><h1>{self.server.nombre}</h1></body></html>").encode()
            ctype = "text/html; charset=utf-8"
        else:
            data = json.dumps({"sag": self.server.nombre, "metodo": self.command, "path": self.path,
                               "x_se_sag": self.headers.get("X-SE-SAG"), "cuerpo": cuerpo}).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _responder


@pytest.fixture()
def cliente_router(tmp_path):
    servidores, sags = [], []
    for n in (1, 2):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Eco)
        srv.nombre = f"sag{n}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servidores.append(srv)
        sags.append({"id": f"sag{n}", "nombre": f"SAG {n}", "color": "#14b8a6",
                     "puerto": srv.server_address[1]})
    (tmp_path / "sags.json").write_text(json.dumps({"sags": sags}))
    app = router.crear_app(str(tmp_path), iniciar_supervisor=False)
    with app.test_client() as c:
        yield c, sags
    for s in servidores:
        s.shutdown()


def test_sin_eleccion_va_al_primer_sag(cliente_router):
    c, _ = cliente_router
    assert c.get("/api/x").get_json()["sag"] == "sag1"


def test_la_cookie_elige_el_sag(cliente_router):
    c, _ = cliente_router
    c.set_cookie(router.COOKIE, "sag2")
    assert c.get("/api/x").get_json()["sag"] == "sag2"


def test_el_encabezado_de_la_pagina_gana_sobre_la_cookie(cliente_router):
    """Lo que protege de la otra pestaña: la cookie dice sag2, pero la
    página se abrió en sag1 y sus fetch() llevan X-SE-SAG: sag1."""
    c, _ = cliente_router
    c.set_cookie(router.COOKIE, "sag2")
    r = c.post("/api/reglas", data='{"a":1}', headers={"X-SE-SAG": "sag1",
                                                     "Content-Type": "application/json"})
    d = r.get_json()
    assert d["sag"] == "sag1" and d["metodo"] == "POST" and d["cuerpo"] == '{"a":1}'
    assert d["x_se_sag"] == "sag1"


def test_el_parametro_sag_elige_y_no_llega_al_se(cliente_router):
    c, _ = cliente_router
    d = c.get("/api/x?a=1&_sag=sag2&b=2", headers={"X-SE-SAG": "sag1"}).get_json()
    assert d["sag"] == "sag2" and d["path"] == "/api/x?a=1&b=2"


def test_un_sag_pedido_que_no_existe_es_error_y_no_se_adivina(cliente_router):
    c, _ = cliente_router
    assert c.post("/api/reglas", headers={"X-SE-SAG": "sag9"}).status_code == 404


def test_las_paginas_salen_fijadas_a_su_sag(cliente_router):
    c, _ = cliente_router
    html = c.get("/pagina?_sag=sag2").get_data(as_text=True)
    assert "<h1>sag2</h1>" in html
    assert 'window.__SE_SAG__={"id": "sag2"' in html
    # Antes que los scripts de la página: sus primeras llamadas ya salen fijadas.
    assert html.index("/gestion/sag.js") < html.index("window.cargo")


def test_seleccionar_pone_la_cookie_y_no_redirige_afuera(cliente_router):
    c, _ = cliente_router
    r = c.get("/gestion/seleccionar/sag2?volver=//malo.com/x")
    assert r.status_code == 302 and r.headers["Location"].endswith("/espesador")
    assert "se_sag=sag2" in r.headers.get("Set-Cookie", "")
    assert c.get("/gestion/seleccionar/sag9").status_code == 404


def test_un_sag_caido_responde_503_sin_tumbar_al_otro(cliente_router, tmp_path):
    c, sags = cliente_router
    reg = {"sags": [sags[0], dict(sags[1], puerto=_puerto_libre())]}
    (tmp_path / "sags.json").write_text(json.dumps(reg))
    app = router.crear_app(str(tmp_path), iniciar_supervisor=False)
    with app.test_client() as c2:
        assert c2.get("/api/x?_sag=sag2").status_code == 503
        assert c2.get("/api/x?_sag=sag1").get_json()["sag"] == "sag1"


def test_el_tope_es_el_del_codigo_aunque_el_archivo_diga_otro(tmp_path):
    (tmp_path / "sags.json").write_text(json.dumps({"max_sags": 2, "sags": [{"id": "sag1", "puerto": 5101}]}))
    assert sags_mod.cargar_registro(str(tmp_path))["max_sags"] == sags_mod.MAX_SAGS == 4


def test_las_paginas_tienen_lugar_para_el_sag():
    """Cada pagina del SE reserva donde mostrar el SAG; si no, el script
    pone una pastilla flotante que tapa contenido."""
    plantillas = os.path.join(RAIZ, "web", "templates")
    # El editor, PostgreSQL, Export / Import y Motor llevan el menu lateral
    # comun, que declara "oculto" (muestran todos los SAG o son del SAG 1).
    assert 'data-se-sag="oculto"' in open(os.path.join(plantillas, "_sidebar.html"), encoding="utf-8").read()
    for f in ("index", "postgres", "export_import", "motor"):
        html = open(os.path.join(plantillas, f + ".html"), encoding="utf-8").read()
        assert "<!--SE_SIDEBAR" in html, f
    # El Explorador de Series tambien muestra todos los SAG (filtro propio).
    assert 'data-se-sag="oculto"' in open(os.path.join(plantillas, "graficos.html"), encoding="utf-8").read()
    for f in ("entrada",
              "traza", "historial", "escrituras", "alertas_historial"):
        html = open(os.path.join(plantillas, f + ".html"), encoding="utf-8").read()
        assert 'data-se-sag="full"' in html, f


def test_el_script_de_la_pagina_es_javascript_valido():
    nodo = subprocess.run(["node", "--version"], capture_output=True) if _hay_node() else None
    if nodo is None:
        pytest.skip("node no disponible")
    r = subprocess.run(["node", "--check", "-"], input=router.SAG_JS, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


def _hay_node() -> bool:
    from shutil import which
    return which("node") is not None


# ---------------------------------------------------------------------------
# Fase 2: a que SAG pertenece cada tag
# ---------------------------------------------------------------------------
import tags_sag  # noqa: E402


class _SagFalso(BaseHTTPRequestHandler):
    """Imita la API de tags de un SAG escribiendo su tags.json de verdad."""

    def log_message(self, *a):
        pass

    def _json(self, status, data):
        b = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _store(self):
        return json.loads(open(self.server.tags_path, encoding="utf-8").read())

    def _save(self, st):
        open(self.server.tags_path, "w", encoding="utf-8").write(json.dumps(st))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/api/se/status":
            return self._json(200, {"running": self.server.running})
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        self._json(404, {})

    def do_POST(self):
        b = self._body()
        st = self._store()
        if any(t["name"] == b["name"] for t in st["tags"]):
            return self._json(409, {"error": "ya existe"})
        nuevo = {"id": st.get("next_id", 1), "name": b["name"], "data_type": b["data_type"],
                 "enabled": True, "categoria": b["categoria"], "rol": ""}
        st["tags"].append(nuevo); st["next_id"] = nuevo["id"] + 1
        self._save(st)
        self._json(201, {"ok": True, "tag": nuevo})

    def do_PUT(self):
        b = self._body()
        tid = int(self.path.rsplit("/", 1)[1])
        st = self._store()
        for t in st["tags"]:
            if t["id"] == tid:
                t.update(b)
        self._save(st)
        self._json(200, {"ok": True})

    def do_DELETE(self):
        tid = int(self.path.rsplit("/", 1)[1])
        st = self._store()
        st["tags"] = [t for t in st["tags"] if t["id"] != tid]
        self._save(st)
        self._json(200, {"ok": True})


@pytest.fixture()
def dos_sags(raiz_cfg):
    raiz, legado = raiz_cfg
    sags_mod.migrar(str(raiz), str(legado))
    sags_mod.crear_sag("SAG 2", config_raiz=str(raiz))
    (raiz / "sags" / "sag1" / "tags.json").write_text(json.dumps({"next_id": 4, "tags": [
        {"id": 1, "name": "PV_POT", "data_type": "Float", "categoria": "pv", "rol": "",
         "pseudonimo": "Potencia", "enabled": True},
        {"id": 2, "name": "SP_VEL", "data_type": "Float", "categoria": "sp", "rol": "", "enabled": True},
        {"id": 3, "name": "PV_USADO", "data_type": "Float", "categoria": "pv", "rol": "potencia",
         "enabled": True}],
        "handshake": {"enabled": True, "enable_fbk_tag": "", "enable_ext_tag": ""}}))
    servidores = []
    reg = sags_mod.cargar_registro(str(raiz))
    for s in reg["sags"]:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _SagFalso)
        srv.tags_path = str(raiz / "sags" / s["id"] / "tags.json")
        srv.running = False
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servidores.append(srv)
        s["puerto"] = srv.server_address[1]
    sags_mod.guardar_registro(reg, str(raiz))
    app = router.crear_app(str(raiz), iniciar_supervisor=False)
    with app.test_client() as c:
        yield c, raiz, servidores
    for s in servidores:
        s.shutdown()


def _nombres(raiz, sid):
    return sorted(t["name"] for t in json.loads((raiz / "sags" / sid / "tags.json").read_text())["tags"])


def test_la_vista_de_tags_dice_de_quien_es_cada_uno(dos_sags):
    c, raiz, _ = dos_sags
    d = c.get("/gestion/api/tags").get_json()
    por = {f["name"]: f for f in d["tags"]}
    assert por["PV_POT"]["dueno"] == "sag1" and not por["PV_POT"]["exclusivo"]
    assert por["SP_VEL"]["dueno"] == "sag1" and por["SP_VEL"]["escribe"] is None


def test_compartir_un_pv_lo_agrega_al_otro_sag_con_sus_datos(dos_sags):
    c, raiz, _ = dos_sags
    r = c.post("/gestion/api/tags/asignar", json={"name": "PV_POT", "dueno": "compartido"})
    assert r.status_code == 200, r.get_json()
    t2 = json.loads((raiz / "sags" / "sag2" / "tags.json").read_text())["tags"]
    assert [(t["name"], t["pseudonimo"]) for t in t2] == [("PV_POT", "Potencia")]
    assert "PV_POT" in _nombres(raiz, "sag1")


def test_un_sp_se_puede_compartir_y_el_otro_sag_lo_recibe_sin_rol(dos_sags):
    """Compartir un SP = que ambos SAG lo vean. Lo sigue escribiendo uno solo."""
    c, raiz, _ = dos_sags
    r = c.post("/gestion/api/tags/asignar", json={"name": "SP_VEL", "dueno": "compartido"})
    assert r.status_code == 200, r.get_json()
    t2 = {t["name"]: t for t in json.loads((raiz / "sags" / "sag2" / "tags.json").read_text())["tags"]}
    assert t2["SP_VEL"]["rol"] == ""


def test_mover_un_sp_lo_saca_del_sag_de_origen(dos_sags):
    c, raiz, _ = dos_sags
    assert c.post("/gestion/api/tags/asignar", json={"name": "SP_VEL", "dueno": "sag2"}).status_code == 200
    assert "SP_VEL" not in _nombres(raiz, "sag1") and _nombres(raiz, "sag2") == ["SP_VEL"]


def test_no_se_quita_un_tag_que_el_sag_esta_usando(dos_sags):
    c, raiz, _ = dos_sags
    r = c.post("/gestion/api/tags/asignar", json={"name": "PV_USADO", "dueno": "sag2"})
    assert r.status_code == 409 and "rol 'potencia'" in r.get_json()["error"]
    assert "PV_USADO" in _nombres(raiz, "sag1") and _nombres(raiz, "sag2") == []


def test_un_limite_referenciado_en_el_fuzzy_tampoco_se_quita(dos_sags):
    c, raiz, _ = dos_sags
    (raiz / "sags" / "sag1" / "fuzzy.json").write_text(json.dumps({"x": {"limites": {"lmax": "PV_POT"}}}))
    r = c.post("/gestion/api/tags/asignar", json={"name": "PV_POT", "dueno": "sag2"})
    assert r.status_code == 409 and "fuzzy.json" in r.get_json()["error"]


def test_gestion_crea_y_no_deja_eliminar_el_sag_1_ni_uno_con_motor_corriendo(dos_sags):
    c, raiz, servidores = dos_sags
    assert c.delete("/gestion/api/sags/sag1").status_code == 409
    servidores[1].running = True
    r = c.delete("/gestion/api/sags/sag2")
    assert r.status_code == 409 and "corriendo" in r.get_json()["error"]
    servidores[1].running = False
    r = c.delete("/gestion/api/sags/sag2")
    assert r.status_code == 200 and r.get_json()["archivado_en"].startswith("sags_archivo")
    ids = [s["id"] for s in c.get("/gestion/api/sags").get_json()["sags"]]
    assert ids == ["sag1"]
    r = c.post("/gestion/api/sags", json={"nombre": "SAG 2", "base": "vacio"})
    assert r.status_code == 201 and r.get_json()["sag"]["id"] == "sag2"
    assert c.post("/gestion/api/sags", json={"nombre": "SAG 3"}).status_code == 201
    assert c.post("/gestion/api/sags", json={"nombre": "SAG 4"}).status_code == 201
    assert c.post("/gestion/api/sags", json={"nombre": "SAG 5"}).status_code == 400   # maximo 4


def test_renombrar_y_cambiar_color(dos_sags):
    c, _, _ = dos_sags
    r = c.patch("/gestion/api/sags/sag2", json={"nombre": "Molino Sur", "color": "#ff0000"})
    assert r.status_code == 200
    s2 = c.get("/gestion/api/sags").get_json()["sags"][1]
    assert (s2["nombre"], s2["color"]) == ("Molino Sur", "#ff0000")
    assert c.patch("/gestion/api/sags/sag9", json={"nombre": "x"}).status_code == 404


def test_la_pagina_de_gestion_se_sirve():
    raiz = os.path.join(RAIZ, "web", "templates", "gestion.html")
    assert os.path.isfile(raiz)


# ---------------------------------------------------------------------------
# Fase 4: pagina Motor y fuera el Diagrama de flujo
# ---------------------------------------------------------------------------
def _plantilla(nombre):
    return open(os.path.join(RAIZ, "web", "templates", nombre), encoding="utf-8").read()


def test_el_router_sirve_la_pagina_motor(cliente_router):
    c, _ = cliente_router
    r = c.get("/motor")
    assert r.status_code == 200 and b"Motor" in r.data
    assert b"__SE_SAG__" not in r.data, "Motor es de todos los SAG: no se fija a uno"


def test_el_diagrama_de_flujo_ya_no_existe():
    from app import app as se_app
    with se_app.test_client() as c:
        assert c.get("/espesador/diagrama").status_code == 404
    for f in ("index", "entrada", "graficos", "postgres", "export_import"):
        assert "/espesador/diagrama" not in _plantilla(f + ".html"), f


def test_los_paneles_del_motor_se_mudan_a_motor_solo_con_router():
    """Con router se ocultan (data-se-solo-local) y aparece el aviso con el
    enlace a /motor (data-se-solo-router). Sin router la pagina queda igual."""
    idx = _plantilla("index.html")
    for panel in ("system-control-box", "se-live-panel", "gen-panel", "hs-panel", "hb-panel"):
        assert f'id="{panel}" data-se-solo-local' in idx, panel
    assert 'data-se-solo-router style="display:none' in idx
    assert "[data-se-solo-local]{display:none!important}" in router.SAG_JS
    for f in ("entrada", "graficos"):
        assert '<a href="/motor" data-se-solo-router style="display:none">Motor</a>' in _plantilla(f + ".html"), f
    # Editor, PostgreSQL, Export / Import y Motor: el enlace esta en el menu lateral comun.
    assert 'href="/motor" class="external" data-sb="/motor" data-se-solo-router style="display:none"' in _plantilla("_sidebar.html")


def test_la_pagina_motor_usa_cada_sag_explicitamente():
    html = _plantilla("motor.html")
    assert "'X-SE-SAG': sag" in html
    for ruta in ("/api/se/start", "/api/se/stop", "/api/tags/handshake", "/api/tags/heartbeat",
                 "/api/tags/generator", "/api/se/piso", "/api/se/auto-aplicar"):
        assert ruta in html, ruta


# ---------------------------------------------------------------------------
# Fase 5: vistas con varios SAG
# ---------------------------------------------------------------------------
def test_el_explorador_pide_las_series_de_otro_sag_a_ese_sag():
    g = _plantilla("graficos.html")
    assert "SE_SAG.fetchDe(sid, q(nombres))" in g          # historial
    assert "fetchDe(s.id, '/api/tags/catalogo')" in g       # catalogo
    assert "fetchDe(id, '/api/tags/lectura')" in g          # su buffer tambien muestrea
    assert "const MULTI = !!(window.SE_SAG" in g, "sin router la pagina queda como antes"


def test_waits_y_tracking_muestran_los_otros_sag_en_solo_lectura():
    idx = _plantilla("index.html")
    assert '<script src="/static/otros-sag.js"></script>' in idx
    # Tracking y las demas secciones: un bloque editable por SAG (multi-seccion.js)
    assert '<script src="/static/multi-seccion.js"></script>' in idx
    # Waits: todos los SAG en la misma pagina y editables ahi (no solo lectura)
    assert "var WaitsMulti = (function" in idx
    for accion in ("'POST', '/api/waits/reset'", "'DELETE', '/api/waits/'", "esEdicion ? 'PUT' : 'POST'"):
        assert accion in idx, accion
    assert "window.SE_SAG.fetchDe(sid, url, init)" in idx
    js = open(os.path.join(RAIZ, "static", "otros-sag.js"), encoding="utf-8").read()
    assert "SE_SAG.fetchDe" in js and "Solo lectura" in js
    assert "method" not in js.replace("Error", ""), "otros-sag.js no escribe nada"


def test_motor_muestra_las_alertas_de_cada_sag():
    assert "'/api/alerts'" in _plantilla("motor.html")



# ---------------------------------------------------------------------------
# Un tag en varios SAG, una sola escritura (exclusividad.py)
# ---------------------------------------------------------------------------
import exclusividad  # noqa: E402


def _sags_dir(tmp_path, tags_por_sag):
    for sid, store in tags_por_sag.items():
        (tmp_path / sid).mkdir()
        (tmp_path / sid / "tags.json").write_text(json.dumps(store))
    return str(tmp_path)


def test_un_sp_con_rol_en_otro_sag_es_de_ese_sag(tmp_path):
    d = _sags_dir(tmp_path, {
        "sag1": {"tags": [{"name": "SP_A", "categoria": "sp", "rol": "vel"},
                          {"name": "SP_B", "categoria": "sp", "rol": ""},
                          {"name": "PV_A", "categoria": "pv", "rol": "pot"}]},
        "sag2": {"tags": [{"name": "SP_A", "categoria": "sp", "rol": ""}]}})
    assert exclusividad.escritores_sp("sag2", d) == {"SP_A": "sag1"}
    assert exclusividad.escritores_sp("sag1", d) == {}


def test_los_tags_del_dcs_de_otro_sag_quedan_reservados(tmp_path):
    d = _sags_dir(tmp_path, {
        "sag1": {"tags": [], "handshake": {"enable_fbk_tag": "FBK1", "enable_ext_tag": "EXT1"},
                 "heartbeat": {"tag_out": "HB1", "tag_in": ""}},
        "sag2": {"tags": []}})
    assert exclusividad.usos_dcs_otros("sag2", d) == {
        "FBK1": ("sag1", "handshake"), "EXT1": ("sag1", "handshake"), "HB1": ("sag1", "heartbeat")}


def test_sin_sag_id_no_hay_restricciones(tmp_path):
    d = _sags_dir(tmp_path, {"sag1": {"tags": [{"name": "SP_A", "categoria": "sp", "rol": "x"}]}})
    assert exclusividad.escritores_sp("", d) == {}


def test_tags_kepserver_marca_los_compartidos():
    idx = _plantilla("index.html")
    assert "function _marcaCompartido(tag)" in idx and "COMPARTIDO</span>" in idx
    assert "fetch('/gestion/api/tags')" in idx


def test_export_import_elige_sag_de_origen_y_destino():
    h = _plantilla("export_import.html")
    assert 'id="exp-sag"' in h and 'id="imp-destino"' in h
    assert "new Option('Todos los SAG', '*')" in h
    assert "fetchSag(id, '/api/export-import/apply'" in h



def test_todas_las_secciones_muestran_cada_sag_editable():
    js = open(os.path.join(RAIZ, "static", "multi-seccion.js"), encoding="utf-8").read()
    for sec in ("contrato", "variables", "tracking", "filtros", "fuzzy", "aceleracion",
                "estados", "permisivos", "reglas", "defuzzy"):
        assert "'" + sec + "'" in js, sec
    assert "'waits'" not in js, "Waits tiene su version nativa (WaitsMulti)"
    # el bloque de otro SAG es el mismo editor, fijado a ese SAG
    assert "'/espesador?_sag=' + encodeURIComponent(s.id) + '&_embed=1#' + sec" in js
    # dentro de un bloque embebido no se vuelve a anidar nada
    assert js.index("if (EMBED) {") < js.index("function montar(sec)")
    assert "_embed=1" in router.SAG_JS


def test_los_textos_de_ayuda_van_una_sola_vez_arriba_de_los_bloques():
    idx = _plantilla("index.html")
    # Contrato (2), Tracking (2), Aceleracion, Permisivos, Fuzzy de pendiente
    assert idx.count("data-se-ayuda") == 7
    assert '<details class="acel-ayuda" data-se-ayuda>' in idx
    js = open(os.path.join(RAIZ, "static", "multi-seccion.js"), encoding="utf-8").read()
    assert "html.se-embed [data-se-ayuda]{display:none!important}" in js
    assert "seccion.querySelectorAll('[data-se-ayuda]')" in js



def test_fuzzy_no_alerta_por_cada_pv_sin_membresia():
    idx = _plantilla("index.html")
    assert "PV sin fuzzy:</b>" not in idx, "ya no hay alerta roja por PV sin fuzzy"
    assert 'id="fz-crear-var"' in idx and "function crearFuzzyDesdeSelector()" in idx
    assert "conFuzzy.map(_fzCardHtml)" in idx


def test_tracking_explica_con_desplegable():
    idx = _plantilla("index.html")
    assert '<summary style="cursor:pointer;color:#4fb3d9;font-size:.86rem;font-weight:600">Como funciona el Tracking</summary>' in idx


def test_el_editor_no_pinta_selector_ni_franja():
    assert "function paginaDeTodos()" in router.SAG_JS
    assert router.SAG_JS.index("if (paginaDeTodos()) return;") < router.SAG_JS.index("Franja superior")



def test_la_pagina_de_postgres_siempre_es_del_sag_1(cliente_router):
    c, _ = cliente_router
    c.set_cookie(router.COOKIE, "sag2")
    # aunque la cookie y el parametro pidan SAG 2, la pagina la sirve SAG 1
    assert c.get("/espesador/postgres?_sag=sag2").get_json()["sag"] == "sag1"
    # el resto sigue la eleccion normal
    assert c.get("/api/x?_sag=sag2").get_json()["sag"] == "sag2"


def test_guardar_la_conexion_en_sag1_la_copia_a_los_demas(tmp_path, monkeypatch):
    import rutas
    import web.api.postgres as pg
    for sid in ("sag1", "sag2", "sag3"):
        (tmp_path / sid).mkdir()
    monkeypatch.setattr(rutas, "SAG_ID", "sag1")
    monkeypatch.setattr(rutas, "SAGS_DIR", str(tmp_path))
    monkeypatch.setattr(pg, "POSTGRES_JSON", str(tmp_path / "sag1" / "postgres.json"))
    pg._save_pg_config({"host": "db", "port": 5432, "database": "ReTO"})
    for sid in ("sag2", "sag3"):
        assert json.loads((tmp_path / sid / "postgres.json").read_text())["host"] == "db"
    # desde otro SAG no se propaga
    monkeypatch.setattr(rutas, "SAG_ID", "sag2")
    monkeypatch.setattr(pg, "POSTGRES_JSON", str(tmp_path / "sag2" / "postgres.json"))
    pg._save_pg_config({"host": "otro"})
    assert json.loads((tmp_path / "sag3" / "postgres.json").read_text())["host"] == "db"


def test_menu_lateral_comun():
    """Editor, PostgreSQL, Export / Import y Motor comparten el menu lateral;
    Motor esta en la seccion Conexion."""
    import menu_lateral
    editor = menu_lateral.menu(en_editor=True)
    fuera = menu_lateral.menu(en_editor=False)
    assert 'href="#reglas"' in editor and 'href="/espesador#reglas"' in fuera
    assert "__SB_BASE__" not in editor + fuera
    conexion = fuera.split('>Conexion<', 1)[1].split('section-label', 1)[0]
    assert 'href="/motor"' in conexion and 'href="/espesador/postgres"' in conexion
    plantillas = os.path.join(RAIZ, "web", "templates")
    for f in ("index", "postgres", "export_import", "motor"):
        html = menu_lateral.incrustar(open(os.path.join(plantillas, f + ".html"), encoding="utf-8").read())
        assert html.count('id="main-sidebar"') == 1, f
        assert "<!--SE_SIDEBAR" not in html, f


def test_cada_motor_se_reinicia_solo_con_los_cambios_de_su_sag():
    """La huella que dispara el auto-aplicar es de la carpeta de ESE SAG, y no
    incluye lo que otro SAG escribe en ella (postgres.json lo copia el SAG 1)."""
    from web.api import config as cfg_api
    assert "postgres.json" not in cfg_api._ARCHIVOS_VERSIONADOS
    html = _plantilla("motor.html")
    # "Aplicar cambios" reinicia SOLO el SAG de su tarjeta (encabezado explicito).
    assert "api(id, 'POST', '/api/se/restart'" in html
    assert "api(s.id, 'GET', '/api/se/auto-aplicar')" in html
