"""Gunicorn del ROUTER del contenedor SAG (ver router.py).

workers = 1 es OBLIGATORIO: el worker es quien supervisa los procesos de cada
SAG. Con 2 workers habria 2 supervisores lanzando motores duplicados.
"""
import os

bind = os.environ.get("ROUTER_BIND", "0.0.0.0:5000")
workers = 1
worker_class = "gthread"
# Cada pestaña abierta hace polling; el router solo reenvía, así que conviene
# tener hilos de sobra para no encolar al SAG 1 detrás del SAG 2.
threads = int(os.environ.get("ROUTER_THREADS", "24"))
# Mayor que el timeout de los SAG (120 s): el router no debe cortar antes.
timeout = int(os.environ.get("ROUTER_TIMEOUT", "150"))
graceful_timeout = 30
keepalive = 5
preload_app = False
accesslog = None          # el acceso ya lo registra cada SAG
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")


def worker_exit(server, worker):
    """Al bajar el router se bajan ordenadamente los procesos de cada SAG."""
    try:
        import router
        sup = router.app.config.get("SUPERVISOR")
        if sup is not None:
            sup.detener()
    except Exception as exc:  # noqa: BLE001
        server.log.warning(f"[router] no se pudo detener los SAG: {exc}")
