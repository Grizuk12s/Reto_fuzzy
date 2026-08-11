# -*- coding: utf-8 -*-
"""Sistema Experto Espesador — aplicación Flask.

IT-7: app.py reducido a factory delgada.
  - Estado compartido → web/state.py
  - Rutas de config → web/api/config.py  (Blueprint bp_config)
  - Rutas de tags   → web/api/tags.py    (Blueprint bp_tags)
  - Rutas de SE     → web/api/se.py      (Blueprint bp_se)
  - Vistas HTML     → web/api/views.py   (Blueprint bp_views)
"""
from __future__ import annotations

from flask import Flask

from web.state import _startup_checks
from web.api.config import bp_config
from web.api.tags import bp_tags
from web.api.se import bp_se
from web.api.postgres import bp_postgres
from web.api.kepserver import bp_kep
from web.api.views import bp_views

app = Flask(__name__)

# Nota: la pagina del Explorador de Series vive en web/templates/graficos.html
# y se sirve desde web/api/views.py (bp_views.graficos).

# Registro de blueprints
app.register_blueprint(bp_config)
app.register_blueprint(bp_tags)
app.register_blueprint(bp_se)
app.register_blueprint(bp_postgres)
app.register_blueprint(bp_kep)
app.register_blueprint(bp_views)

# Health checks al arrancar (puebla _alerts)
_startup_checks()


if __name__ == "__main__":
    import os
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print("=" * 60)
    print("  Reto Digital -- Sistema Experto (v2)")
    print(f"  http://{host}:{port}                    (bienvenida)")
    print(f"  http://{host}:{port}/espesador           (configuracion SE)")
    print(f"  http://{host}:{port}/espesador/entrada   (entrada de datos)")
    print(f"  http://{host}:{port}/espesador/graficos  (explorador de series)")
    print(f"  http://{host}:{port}/espesador/diagrama  (diagrama de flujo)")
    print(f"  http://{host}:{port}/espesador/postgres  (conexion PostgreSQL)")
    print("=" * 60)
    app.run(debug=debug, host=host, port=port)
