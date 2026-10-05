# -*- coding: utf-8 -*-
"""Configuración de Gunicorn para SE-HUTBAY-ESPESADOR-1.

Se usa SOLO en despliegue (contenedor Ubuntu). En Windows se sigue
ejecutando con:  python app.py  (Flask dev server).

Arranque en producción:
    gunicorn -c gunicorn.conf.py app:app
"""

import os

# ------------------------------------------------------------------
# Red
# ------------------------------------------------------------------
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")

# ------------------------------------------------------------------
# Workers
# ------------------------------------------------------------------
# IMPORTANTE: workers = 1 es OBLIGATORIO.
#
# El SEEngine y el TagGenerator (web/state.py) mantienen estado en
# memoria del proceso (setpoints, histórico, filtro Exp-Q, threads
# daemon) y comparten una única conexión OPC-UA hacia KEPserver.
#
# Con N workers tendríamos N motores independientes compitiendo por
# los mismos tags y N conexiones OPC-UA divergentes. Para escalar en
# el futuro habrá que sacar el motor a un proceso worker aparte y
# compartir estado vía Redis / DB.
#
# La concurrencia HTTP se resuelve con múltiples threads dentro del
# único worker (worker_class = "gthread").
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# ------------------------------------------------------------------
# Timeouts
# ------------------------------------------------------------------
# Un tick del SE puede tardar leyendo/escribiendo OPC-UA si KEPserver
# responde lento. 120 s da margen de sobra sin ocultar cuelgues reales.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# ------------------------------------------------------------------
# Comportamiento de arranque
# ------------------------------------------------------------------
# preload_app = False para que _startup_checks() y los threads daemon
# del SEEngine se inicialicen dentro del worker (no en el master).
# Con 1 solo worker el trade-off es irrelevante y evita problemas de
# fork() con threads pre-existentes.
preload_app = False

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
# stdout/stderr → los captura Docker (`docker logs`).
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(L)ss "%(f)s"'


# ------------------------------------------------------------------
# Hooks
# ------------------------------------------------------------------
def on_starting(server):
    """Banner de arranque equivalente al print() de app.py."""
    print("=" * 60)
    print("  Reto Digital -- Sistema Experto (v2)  [Gunicorn]")
    print(f"  bind:     {bind}")
    print(f"  workers:  {workers}  threads: {threads}")
    print(f"  timeout:  {timeout}s")
    print("=" * 60)
