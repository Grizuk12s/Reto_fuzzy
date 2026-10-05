# -*- coding: utf-8 -*-
"""Router del contenedor SAG: un proceso del SE por SAG, una sola puerta.

    navegador --:5001--> [router :5000] --+--> SE sag1 (127.0.0.1:5101)
                                          +--> SE sag2 (127.0.0.1:5102)

Qué hace:
1. **Supervisa** un proceso del SE por SAG del registro (``config/sags.json``),
   cada uno con ``SE_SAG_ID`` -> su propia carpeta de config, su motor, sus
   tablas. Si un proceso muere, lo vuelve a lanzar (con espera creciente).
2. **Reenvía** cada petición al SAG que corresponde:
       ?_sag=<id>  >  encabezado X-SE-SAG  >  cookie se_sag  >  primer SAG
3. **Fija cada página a su SAG**: a toda página HTML le agrega un script que
   pone ``X-SE-SAG`` en cada fetch() y ``_sag`` en cada enlace interno. Así,
   cambiar de SAG en otra pestaña no puede hacer que un Guardar de esta caiga
   en el SAG equivocado.

El código del SE no sabe nada de esto: cada proceso cree ser el único equipo.

Arranque (contenedor):   gunicorn -c router_gunicorn.conf.py router:app
Arranque (desarrollo):   python router.py      (los SAG corren con app.py)
"""
from __future__ import annotations

import atexit
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

from flask import Flask, Response, jsonify, make_response, redirect, request

import sags as sags_mod
import tags_sag
from rutas import CONFIG_RAIZ, RAIZ

COOKIE = "se_sag"
ENCABEZADO = "X-SE-SAG"
PARAM = "_sag"
TIMEOUT_PROXY_S = 130

# Encabezados que no se reenvían (hop-by-hop, RFC 7230 §6.1). Host SÍ se
# reenvía: así las redirecciones del SE apuntan al host real y no a 127.0.0.1.
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailer", "trailers", "transfer-encoding", "upgrade", "content-length"}


# ===========================================================================
# Supervisor de procesos
# ===========================================================================
def _puerto_responde(puerto: int, timeout: float = 1.0) -> bool:
    try:
        c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=timeout)
        c.request("GET", "/health")
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except OSError:
        return False


def _morir_con_el_padre() -> None:  # pragma: no cover - solo Linux
    """En Linux, el proceso del SAG recibe SIGTERM si muere el router que lo
    lanzó (incluso si al router lo matan con SIGKILL por timeout). Así nunca
    quedan motores huérfanos escribiendo al DCS sin supervisor."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


def _comando_sag(puerto: int) -> tuple[list[str], dict]:
    """Gunicorn en el contenedor; ``python app.py`` donde no hay gunicorn
    (Windows / desarrollo)."""
    env = {}
    try:
        import gunicorn  # noqa: F401
        usar_gunicorn = os.name != "nt"
    except ImportError:
        usar_gunicorn = False
    if usar_gunicorn:
        env["GUNICORN_BIND"] = f"127.0.0.1:{puerto}"
        return [sys.executable, "-m", "gunicorn", "-c", "gunicorn.conf.py", "app:app"], env
    env.update({"FLASK_HOST": "127.0.0.1", "FLASK_PORT": str(puerto), "FLASK_DEBUG": "0"})
    return [sys.executable, "app.py"], env


class Supervisor:
    """Mantiene vivo un proceso del SE por SAG."""

    INTERVALO_S = 2.0

    def __init__(self, sags: list[dict]):
        self._sags: dict[str, dict] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._estado: dict[str, dict] = {}
        self._lock = threading.Lock()
        for s in sags:
            self.agregar(s)
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None

    # -- SAG que entran y salen en caliente -----------------------------------
    def agregar(self, sag: dict) -> None:
        """Un SAG nuevo: el bucle lo lanza en su próxima vuelta (≤ 2 s)."""
        with self._lock:
            self._sags[sag["id"]] = sag
            self._estado.setdefault(sag["id"], {"vivo": False, "pid": None, "lanzamientos": 0,
                                                "ultimo_error": None, "proximo_intento": 0.0})

    def quitar(self, sid: str, timeout: float = 15.0) -> None:
        """Baja el proceso del SAG y deja de supervisarlo."""
        with self._lock:
            self._sags.pop(sid, None)
            self._estado.pop(sid, None)
            p = self._procs.pop(sid, None)
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()

    # -- ciclo de vida ----------------------------------------------------
    def iniciar(self) -> None:
        if self._hilo and self._hilo.is_alive():
            return
        self._hilo = threading.Thread(target=self._bucle, name="supervisor-sags", daemon=True)
        self._hilo.start()
        atexit.register(self.detener)

    def detener(self) -> None:
        self._parar.set()
        with self._lock:
            procs = list(self._procs.items())
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    # -- bucle --------------------------------------------------------------
    def _bucle(self) -> None:
        while not self._parar.is_set():
            for sid in list(self._sags):
                try:
                    self._revisar(sid)
                except KeyError:
                    pass                               # se quitó mientras se revisaba
                except Exception as exc:  # noqa: BLE001 - el supervisor no puede morir
                    if sid in self._estado:
                        self._estado[sid]["ultimo_error"] = f"{type(exc).__name__}: {exc}"
            self._parar.wait(self.INTERVALO_S)

    def _revisar(self, sid: str) -> None:
        sag, est = self._sags[sid], self._estado[sid]
        p = self._procs.get(sid)
        if p is not None and p.poll() is None:
            # Se pregunta por HTTP solo hasta que responde la primera vez;
            # después basta con que el proceso siga vivo. Sin esto cada SAG
            # registraría un GET /health cada 2 s en su log de acceso.
            if not est["vivo"]:
                est["vivo"] = _puerto_responde(sag["puerto"])
                if est["vivo"]:
                    est["fallos_seguidos"] = 0
                    if est["ultimo_error"]:
                        est["ultimo_reinicio"] = est["ultimo_error"]
                        est["ultimo_error"] = None
            est["pid"] = p.pid
            return
        if p is not None:                              # murió
            est["ultimo_error"] = f"el proceso termino con codigo {p.returncode}"
            est["fallos_seguidos"] = est.get("fallos_seguidos", 0) + 1
            est["proximo_intento"] = time.time() + min(60, 2 ** est["fallos_seguidos"])
            with self._lock:
                self._procs.pop(sid, None)
            est["vivo"], est["pid"] = False, None
            return
        # Sin proceso propio. Si el puerto ya responde es un SE que quedó de un
        # router anterior (p. ej. el worker murió por timeout): se adopta en
        # vez de lanzar otro que no podría abrir el puerto.
        if _puerto_responde(sag["puerto"]):
            est["vivo"], est["pid"] = True, None
            return
        if time.time() < est["proximo_intento"]:
            est["vivo"] = False
            return
        cmd, env_extra = _comando_sag(sag["puerto"])
        env = dict(os.environ, SE_SAG_ID=sid, **env_extra)
        env.pop("SE_PROCESO_BD", None)                 # cada SAG usa su id para las tablas
        with self._lock:
            if sid not in self._sags:                  # lo quitaron recién
                return
            nuevo = subprocess.Popen(cmd, cwd=RAIZ, env=env,
                                     preexec_fn=_morir_con_el_padre if os.name == "posix" else None)
            self._procs[sid] = nuevo
        est["lanzamientos"] += 1
        est["pid"], est["vivo"] = nuevo.pid, False

    def estado(self) -> dict:
        return {sid: {"vivo": e["vivo"], "pid": e["pid"],
                      "lanzamientos": e["lanzamientos"], "ultimo_error": e["ultimo_error"],
                      "ultimo_reinicio": e.get("ultimo_reinicio")}
                for sid, e in list(self._estado.items())}


# ===========================================================================
# Script que se agrega a cada página
# ===========================================================================
SAG_JS = r"""
(function () {
  var SAG = window.__SE_SAG__, SAGS = window.__SE_SAGS__ || [];
  if (!SAG) return;
  var H = 'X-SE-SAG', P = '_sag';
  var esReq = function (x) { return typeof Request !== 'undefined' && x instanceof Request; };
  function mismoOrigen(u) { try { return new URL(u, location.href).origin === location.origin; } catch (e) { return false; } }

  // 1) Toda llamada a la API de esta pagina va a SU SAG (salvo que la pagina
  //    pida otro explicitamente con SE_SAG.fetchDe).
  var f0 = window.fetch;
  window.fetch = function (input, init) {
    try {
      var url = esReq(input) ? input.url : String(input);
      if (mismoOrigen(url)) {
        init = Object.assign({}, init || {});
        var h = new Headers(init.headers || (esReq(input) ? input.headers : undefined));
        if (!h.has(H)) h.set(H, SAG.id);
        init.headers = h;
      }
    } catch (e) {}
    return f0.call(this, input, init);
  };

  // API para paginas que muestren VARIOS SAG a la vez (p. ej. los waits de
  // todos, uno debajo del otro): SE_SAG.fetchDe('sag2', '/api/waits').
  window.SE_SAG = {
    actual: SAG, todos: SAGS,
    fetchDe: function (sagId, url, init) {
      init = Object.assign({}, init || {});
      var h = new Headers(init.headers || undefined);
      h.set(H, sagId);
      init.headers = h;
      return window.fetch(url, init);
    }
  };

  // 2) Los enlaces internos y la URL de la pagina siguen en el mismo SAG.
  function fijar(ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a) return;
    var raw = a.getAttribute('href') || '';
    if (!raw || raw.charAt(0) === '#' || /^(javascript|mailto):/i.test(raw)) return;
    try {
      var u = new URL(a.href, location.href);
      if (u.origin !== location.origin || u.pathname.indexOf('/gestion') === 0 || u.pathname.indexOf('/motor') === 0) return;
      if (!u.searchParams.has(P)) { u.searchParams.set(P, SAG.id); a.href = u.toString(); }
    } catch (e) {}
  }
  document.addEventListener('click', fijar, true);
  document.addEventListener('auxclick', fijar, true);
  try {
    var aqui = new URL(location.href);
    if (!aqui.searchParams.has(P)) { aqui.searchParams.set(P, SAG.id); history.replaceState(history.state, '', aqui.toString()); }
  } catch (e) {}

  // 3) Color e identidad del SAG en la pagina.
  function rgba(hex, a) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m) return 'rgba(122,139,148,' + a + ')';
    return 'rgba(' + parseInt(m[1], 16) + ',' + parseInt(m[2], 16) + ',' + parseInt(m[3], 16) + ',' + a + ')';
  }
  function corto(s) { var n = /(\d+)$/.exec(s.id); return n ? 'S' + n[1] : s.nombre.slice(0, 3).toUpperCase(); }
  document.documentElement.style.setProperty('--se-sag-color', SAG.color);
  // Una pagina que muestra TODOS los SAG (el editor) lo declara con
  // data-se-sag="oculto": ni franja, ni selector, ni nombre en la pestana.
  function paginaDeTodos() { return !!document.querySelector('[data-se-sag="oculto"]'); }
  function ponerTitulo() {
    if (paginaDeTodos()) return;
    if (document.title.indexOf(SAG.nombre) === -1) document.title = SAG.nombre + ' · ' + document.title;
  }

  var menu = null;
  function cerrarMenu() { if (menu) { menu.remove(); menu = null; } }
  function volverAqui() {
    return location.pathname + location.search.replace(/([?&])_sag=[^&]*&?/, '$1').replace(/[?&]$/, '') + location.hash;
  }
  function abrirMenu(anchor) {
    if (menu) { cerrarMenu(); return; }
    var r = anchor.getBoundingClientRect();
    menu = document.createElement('div');
    menu.setAttribute('role', 'menu');
    menu.style.cssText = 'position:fixed;z-index:1000;min-width:200px;background:#0a1216;border:1px solid #35474f;border-radius:10px;padding:5px;box-shadow:0 10px 30px rgba(0,0,0,.45);font:600 12.5px "Segoe UI",system-ui,sans-serif';
    var titulo = document.createElement('div');
    titulo.textContent = 'Cambiar de SAG';
    titulo.style.cssText = 'padding:6px 10px 4px;color:#7a8b94;font-size:11px;text-transform:uppercase;letter-spacing:.05em';
    menu.appendChild(titulo);
    var estados = {};
    SAGS.forEach(function (s) {
      var it = document.createElement('a');
      it.href = '/gestion/seleccionar/' + encodeURIComponent(s.id) + '?volver=' + encodeURIComponent(volverAqui());
      it.style.cssText = 'display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:7px;color:#e8eef1;text-decoration:none' + (s.id === SAG.id ? ';background:' + rgba(s.color, .14) : '');
      it.onmouseenter = function () { if (s.id !== SAG.id) it.style.background = '#152128'; };
      it.onmouseleave = function () { if (s.id !== SAG.id) it.style.background = ''; };
      var dot = document.createElement('span');
      dot.style.cssText = 'width:10px;height:10px;border-radius:50%;flex-shrink:0;background:' + s.color;
      var txt = document.createElement('span');
      txt.style.flex = '1';
      txt.textContent = s.nombre;
      var est = document.createElement('span');
      est.style.cssText = 'font-size:11px;font-weight:400;color:#7a8b94';
      est.textContent = s.id === SAG.id ? 'actual' : '';
      estados[s.id] = est;
      it.append(dot, txt, est);
      menu.appendChild(it);
    });
    var sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:#23323b;margin:5px 2px';
    menu.appendChild(sep);
    var gest = document.createElement('a');
    gest.href = '/gestion';
    gest.textContent = 'Gestionar SAG…';
    gest.style.cssText = 'display:block;padding:8px 10px;border-radius:7px;color:#a2b1b9;text-decoration:none';
    menu.appendChild(gest);
    document.body.appendChild(menu);
    var w = menu.offsetWidth;
    menu.style.top = Math.min(r.bottom + 6, window.innerHeight - menu.offsetHeight - 8) + 'px';
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
    // Estado en vivo de cada SAG (el router lo sabe sin despertar a nadie).
    f0.call(window, '/gestion/api/sags').then(function (x) { return x.json(); }).then(function (d) {
      (d.sags || []).forEach(function (s) {
        var e = estados[s.id];
        if (e && s.id !== SAG.id && !s.vivo) { e.textContent = 'sin conexion'; e.style.color = '#ef4444'; }
      });
    }).catch(function () {});
  }
  document.addEventListener('click', function (ev) {
    if (menu && !menu.contains(ev.target) && !(ev.target.closest && ev.target.closest('[data-se-sag-btn]'))) cerrarMenu();
  });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') cerrarMenu(); });
  window.addEventListener('resize', cerrarMenu);
  window.addEventListener('scroll', cerrarMenu, true);

  function estilarBadge(el, modo) {
    el.textContent = modo === 'short' ? corto(SAG) : SAG.nombre;
    if (modo === 'texto') {           // texto dentro de un titulo
      // hudbay.css pinta .process-accent con un degradado rojo y !important:
      // solo un estilo en linea con !important le gana.
      ['background-image', 'background-color'].forEach(function (k) { el.style.setProperty(k, 'none', 'important'); });
      el.style.setProperty('-webkit-text-fill-color', SAG.color, 'important');
      el.style.setProperty('color', SAG.color, 'important');
      return;
    }
    // Sin tocar `display`: la barra lateral muestra/oculta la version larga y
    // la corta segun este colapsada, y un display en linea le ganaria al CSS.
    el.style.padding = modo === 'short' ? '2px 6px' : '3px 10px';
    el.style.borderRadius = '12px';
    el.style.fontSize = modo === 'short' ? '.6rem' : '.7rem';
    el.style.fontWeight = '700';
    el.style.letterSpacing = '.3px';
    el.style.background = rgba(SAG.color, .15);
    el.style.color = SAG.color;
    el.style.border = '1px solid ' + rgba(SAG.color, .5);
    el.style.cursor = 'pointer';
    el.style.whiteSpace = 'nowrap';
    if (modo !== 'short') {
      var flecha = document.createElement('span');
      flecha.textContent = '▾';
      flecha.style.cssText = 'opacity:.75;margin-left:6px';
      el.appendChild(flecha);
    }
    el.title = 'SAG de esta pagina. Clic para cambiar de SAG.';
    el.setAttribute('data-se-sag-btn', '');
    el.setAttribute('role', 'button');
    el.tabIndex = 0;
    el.onclick = function (ev) { ev.preventDefault(); ev.stopPropagation(); abrirMenu(el); };
    el.onkeydown = function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); abrirMenu(el); } };
  }

  // Con router, el arranque, handshake, heartbeat y generador viven en /motor:
  // se ocultan los paneles viejos (marcados data-se-solo-local) y aparecen los
  // enlaces y avisos que solo existen aqui (data-se-solo-router). Sin router
  // (un solo equipo) la pagina queda exactamente como antes.
  var css = document.createElement('style');
  css.textContent = '[data-se-solo-local]{display:none!important}';
  (document.head || document.documentElement).appendChild(css);

  function pintar() {
    document.querySelectorAll('[data-se-solo-router]').forEach(function (e) { e.style.removeProperty('display'); });
    // Bloque de otro SAG embebido en una seccion (static/multi-seccion.js):
    // la pagina que lo contiene ya muestra el SAG y su color.
    if (window.self !== window.top && /[?&]_embed=1(&|$)/.test(location.search)) return;
    ponerTitulo();
    if (paginaDeTodos()) return;
    // Franja superior del color del SAG: se ve en cualquier parte de la pagina.
    var franja = document.createElement('div');
    franja.style.cssText = 'position:fixed;left:0;right:0;top:0;height:3px;z-index:999;pointer-events:none;background:' + SAG.color;
    document.body.appendChild(franja);

    var lugares = document.querySelectorAll('[data-se-sag]');
    lugares.forEach(function (el) { estilarBadge(el, el.getAttribute('data-se-sag')); });
    if (lugares.length) return;
    // Pagina sin lugar reservado: pastilla flotante arriba a la derecha.
    var flot = document.createElement('span');
    flot.style.cssText = 'position:fixed;top:10px;right:22px;z-index:95;background:#0a1216';
    document.body.appendChild(flot);
    estilarBadge(flot, 'full');
    flot.style.background = '#0a1216';
    flot.style.border = '1.5px solid ' + SAG.color;
  }
  if (document.body) pintar(); else document.addEventListener('DOMContentLoaded', pintar);
})();
"""

_HEAD_RE = re.compile(rb"<head(\s[^>]*)?>", re.I)


def _inyectar(html: bytes, sag: dict, sags: list[dict]) -> bytes:
    """Agrega el script del SAG justo después de ``<head>``.

    Tiene que ir ANTES que cualquier script de la página: las páginas piden
    sus datos apenas se cargan, y esas primeras llamadas también tienen que
    salir con ``X-SE-SAG`` (si no, irían al SAG de la cookie)."""
    publico = lambda s: {"id": s["id"], "nombre": s["nombre"], "color": s["color"]}
    datos = json.dumps(publico(sag)).replace("</", "<\\/")
    lista = json.dumps([publico(s) for s in sags]).replace("</", "<\\/")
    bloque = (f'<script>window.__SE_SAG__={datos};window.__SE_SAGS__={lista};</script>'
              f'<script src="/gestion/sag.js"></script>').encode("utf-8")
    m = _HEAD_RE.search(html)
    if m:
        return html[:m.end()] + bloque + html[m.end():]
    return bloque + html


# ===========================================================================
# Aplicación
# ===========================================================================
class _Registro:
    """Vista en memoria de ``sags.json``; se recarga después de cada cambio."""

    def __init__(self, config_raiz: str):
        self.raiz = config_raiz
        self.recargar()

    def recargar(self) -> None:
        reg = sags_mod.cargar_registro(self.raiz)
        self.max_sags = reg["max_sags"]
        self.lista = reg["sags"]
        self.por_id = {s["id"]: s for s in self.lista}


def _llamar_sag(sag: dict, metodo: str, ruta: str, cuerpo=None, timeout: float = 15.0):
    """Llamada directa a la API de un SAG. Devuelve (status, json|None)."""
    conn = http.client.HTTPConnection("127.0.0.1", sag["puerto"], timeout=timeout)
    try:
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        conn.request(metodo, ruta, body=datos,
                     headers={"Content-Type": "application/json"} if datos else {})
        r = conn.getresponse()
        texto = r.read()
        try:
            return r.status, json.loads(texto or b"null")
        except ValueError:
            return r.status, None
    except OSError as exc:
        return 503, {"error": f"{sag['nombre']} no responde ({exc})."}
    finally:
        conn.close()


def crear_app(config_raiz: str = CONFIG_RAIZ, iniciar_supervisor: bool = True) -> Flask:
    if config_raiz == CONFIG_RAIZ:
        sags_mod.migrar(config_raiz)
    reg = _Registro(config_raiz)
    sup = Supervisor(reg.lista)
    if iniciar_supervisor:
        sup.iniciar()

    app = Flask(__name__)
    app.config["SUPERVISOR"] = sup
    app.config["REGISTRO"] = reg
    gestion_lock = threading.Lock()      # crear/eliminar/asignar: de a uno

    # Paginas que son de TODO el contenedor y viven en el SAG 1 (el fijo):
    # la conexion a PostgreSQL es una sola (ver web/api/postgres.py).
    PAGINAS_DEL_SAG_FIJO = {"espesador/postgres"}

    def _elegir() -> tuple[dict | None, str | None]:
        """(sag, error). Un SAG pedido explícitamente y que no existe es un
        error: no se adivina a dónde mandar un Guardar."""
        if request.path.strip("/") in PAGINAS_DEL_SAG_FIJO and sags_mod.SAG_FIJO in reg.por_id:
            return reg.por_id[sags_mod.SAG_FIJO], None
        for valor in (request.args.get(PARAM), request.headers.get(ENCABEZADO)):
            if valor:
                return (reg.por_id.get(valor), None if valor in reg.por_id else f"El SAG '{valor}' no existe.")
        return reg.por_id.get(request.cookies.get(COOKIE, ""), reg.lista[0] if reg.lista else None), None

    def _error(msg: str, status: int = 400):
        return jsonify({"ok": False, "error": msg}), status

    # ---- rutas propias del router ----------------------------------------
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "rol": "router-sag", "sags": sup.estado()})

    @app.route("/gestion")
    @app.route("/gestion/")
    def gestion_pagina():
        with open(os.path.join(RAIZ, "web", "templates", "gestion.html"), encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/html")

    @app.route("/motor")
    @app.route("/motor/")
    def motor_pagina():
        """Motor de TODOS los SAG: iniciar/detener, ritmo, handshake,
        heartbeat y generador de cada uno (Fase 4)."""
        with open(os.path.join(RAIZ, "web", "templates", "motor.html"), encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/html")

    @app.route("/gestion/api/sags", methods=["GET"])
    def api_sags():
        est = sup.estado()
        return jsonify({"max_sags": reg.max_sags, "sag_fijo": sags_mod.SAG_FIJO,
                        "sags": [dict(s, **est.get(s["id"], {})) for s in reg.lista]})

    @app.route("/gestion/api/sags", methods=["POST"])
    def api_crear_sag():
        body = request.get_json(silent=True) or {}
        with gestion_lock:
            try:
                nuevo = sags_mod.crear_sag(body.get("nombre"), body.get("color"),
                                           body.get("base") or "vacio", config_raiz=reg.raiz)
            except sags_mod.ErrorSag as exc:
                return _error(str(exc))
            reg.recargar()
            sup.agregar(nuevo)
        return jsonify({"ok": True, "sag": nuevo}), 201

    @app.route("/gestion/api/sags/<sag_id>", methods=["PATCH"])
    def api_actualizar_sag(sag_id):
        body = request.get_json(silent=True) or {}
        with gestion_lock:
            try:
                sag = sags_mod.actualizar_sag(sag_id, body.get("nombre"), body.get("color"),
                                              config_raiz=reg.raiz)
            except sags_mod.ErrorSag as exc:
                return _error(str(exc), 404 if "no existe" in str(exc) else 400)
            reg.recargar()
        return jsonify({"ok": True, "sag": sag})

    @app.route("/gestion/api/sags/<sag_id>", methods=["DELETE"])
    def api_eliminar_sag(sag_id):
        with gestion_lock:
            sag = reg.por_id.get(sag_id)
            if sag is None:
                return _error(f"El SAG '{sag_id}' no existe.", 404)
            if sag_id == sags_mod.SAG_FIJO:
                return _error("El SAG 1 es el SAG por defecto y no se puede eliminar.", 409)
            # Con el motor corriendo, bajarlo de golpe dejaría el lazo con el
            # DCS sin un Detener ordenado. Primero se detiene a mano.
            st, data = _llamar_sag(sag, "GET", "/api/se/status", timeout=5)
            if st == 200 and isinstance(data, dict) and data.get("running"):
                return _error(f"El motor de {sag['nombre']} esta corriendo. Detenlo antes de eliminar el SAG.", 409)
            sup.quitar(sag_id)
            try:
                archivo = sags_mod.eliminar_sag(sag_id, config_raiz=reg.raiz)
            except sags_mod.ErrorSag as exc:
                sup.agregar(sag)
                return _error(str(exc))
            reg.recargar()
        return jsonify({"ok": True, "archivado_en": os.path.relpath(archivo, reg.raiz)})

    # ---- Fase 2: a qué SAG pertenece cada tag ---------------------------------
    @app.route("/gestion/api/tags", methods=["GET"])
    def api_tags_union():
        publico = [{"id": s["id"], "nombre": s["nombre"], "color": s["color"]} for s in reg.lista]
        return jsonify({"sags": publico, "compartido": tags_sag.COMPARTIDO,
                        "tags": tags_sag.union(reg.lista, reg.raiz)})

    @app.route("/gestion/api/tags/asignar", methods=["POST"])
    def api_tags_asignar():
        body = request.get_json(silent=True) or {}
        nombre, dueno = str(body.get("name") or ""), str(body.get("dueno") or "")
        with gestion_lock:
            try:
                plan = tags_sag.plan_asignacion(reg.lista, nombre, dueno, reg.raiz)
            except sags_mod.ErrorSag as exc:
                return _error(str(exc), 409)
            f = plan["fuente"]
            # 1) Agregar donde falta. Si algo falla aquí no se quitó nada.
            for sid in plan["agregar"]:
                sag = reg.por_id[sid]
                st, data = _llamar_sag(sag, "POST", "/api/tags", {
                    "name": f["name"], "data_type": f.get("data_type", "Float"),
                    "categoria": f.get("categoria", "otro"), "rol": ""})
                if st != 201:
                    return _error(f"No se pudo agregar a {sag['nombre']}: "
                                  f"{(data or {}).get('error', st)}", 502 if st >= 500 else 409)
                nuevo_id = data["tag"]["id"]
                extra = {k: f.get(k) or "" for k in ("pseudonimo", "instrumento", "unidad_ing", "equipo")}
                extra["enabled"] = bool(f.get("enabled", True))
                _llamar_sag(sag, "PUT", f"/api/tags/{nuevo_id}", extra)
            # 2) Quitar donde sobra (ya se comprobó que nadie lo usa).
            for sid, tag_id in plan["quitar"]:
                sag = reg.por_id[sid]
                st, data = _llamar_sag(sag, "DELETE", f"/api/tags/{tag_id}")
                if st != 200:
                    return _error(f"Se agrego pero no se pudo quitar de {sag['nombre']}: "
                                  f"{(data or {}).get('error', st)}", 502 if st >= 500 else 409)
        return jsonify({"ok": True, "name": nombre, "dueno": dueno,
                        "agregado_en": plan["agregar"], "quitado_de": [s for s, _ in plan["quitar"]]})

    @app.route("/gestion/seleccionar/<sag_id>")
    def seleccionar(sag_id):
        if sag_id not in reg.por_id:
            return jsonify({"ok": False, "error": f"El SAG '{sag_id}' no existe."}), 404
        volver = request.args.get("volver") or "/espesador"
        if not volver.startswith("/") or volver.startswith("//"):
            volver = "/espesador"                      # nada de redirecciones a otro sitio
        resp = make_response(redirect(volver))
        resp.set_cookie(COOKIE, sag_id, max_age=30 * 86400, samesite="Lax", path="/")
        return resp

    @app.route("/gestion/sag.js")
    def sag_js():
        return Response(SAG_JS, mimetype="application/javascript",
                        headers={"Cache-Control": "no-cache"})

    # ---- todo lo demás va al SAG -------------------------------------------
    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    @app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    def proxy(path):
        sag, error = _elegir()
        if error or sag is None:
            return jsonify({"ok": False, "error": error or "No hay SAG configurados."}), 404
        qs = [(k, v) for k, v in urllib.parse.parse_qsl(request.query_string.decode("latin-1"),
                                                        keep_blank_values=True) if k != PARAM]
        destino = "/" + path + ("?" + urllib.parse.urlencode(qs) if qs else "")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        headers[ENCABEZADO] = sag["id"]
        headers["X-Forwarded-For"] = request.remote_addr or ""
        cuerpo = request.get_data() if request.method not in ("GET", "HEAD") else None
        conn = http.client.HTTPConnection("127.0.0.1", sag["puerto"], timeout=TIMEOUT_PROXY_S)
        try:
            conn.request(request.method, destino, body=cuerpo, headers=headers)
            r = conn.getresponse()
        except OSError:
            conn.close()
            msg = (f"{sag['nombre']} no esta disponible en este momento (arrancando o detenido). "
                   "Reintenta en unos segundos.")
            if "text/html" in request.headers.get("Accept", ""):
                return Response(f"<!doctype html><meta charset=utf-8><body style='background:#101a20;color:#e8eef1;"
                                f"font-family:Segoe UI,sans-serif;padding:40px'><h2>{msg}</h2>"
                                f"<p><a style='color:#4fb3d9' href='javascript:location.reload()'>Reintentar</a></p>",
                                status=503, mimetype="text/html")
            return jsonify({"ok": False, "error": msg, "sag": sag["id"]}), 503

        out = [(k, v) for k, v in r.getheaders() if k.lower() not in _HOP]
        out.append(("X-SE-SAG", sag["id"]))
        ctipo = r.getheader("Content-Type", "") or ""
        if "text/html" in ctipo and not r.getheader("Content-Encoding") and request.method != "HEAD":
            datos = r.read()
            conn.close()
            return Response(_inyectar(datos, sag, reg.lista), status=r.status, headers=out)

        def flujo():
            try:
                while True:
                    trozo = r.read(65536)
                    if not trozo:
                        break
                    yield trozo
            finally:
                conn.close()
        return Response(flujo(), status=r.status, headers=out, direct_passthrough=True)

    return app


# Bajo pytest no se arranca nada al importar: los tests crean su propia app.
if "pytest" not in sys.modules:
    app = crear_app()

if __name__ == "__main__":
    puerto = int(os.environ.get("ROUTER_PORT", "5000"))
    print(f"[router] SAG: {[s['id'] for s in app.config['REGISTRO'].lista]}  ->  http://0.0.0.0:{puerto}")
    app.run(host="0.0.0.0", port=puerto, threaded=True, debug=False)
