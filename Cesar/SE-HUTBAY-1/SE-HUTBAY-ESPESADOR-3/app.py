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
from web.api.contrato import bp_contrato
from web.api.export_import import bp_export_import
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
app.register_blueprint(bp_contrato)
app.register_blueprint(bp_export_import)
app.register_blueprint(bp_views)

# Health checks al arrancar (puebla _alerts)
_startup_checks()

# Contrato al levantar el contenedor: se REVISA, no se sincroniza. Arrancar
# es justo cuando no hay nadie mirando, asi que un tags.json a medio editar no
# puede cambiar solo la lista de variables con la que el SE va a operar. Si
# hay tags PV/SP fuera del contrato, o configuracion apuntando a variables que
# ya no existen, queda una alerta roja en la pagina donde se ocupa.
def _revisar_contrato_al_arrancar():
    from web.state import _activity_log
    try:
        from web.api.contrato import (
            _sugerencia_desde_tags, contrato_vigente, revisar_desfases,
        )
        revisar_desfases()
        sug = _sugerencia_desde_tags()
        actual = contrato_vigente()
        fuera = ([v for v in sug["variables_proceso"]
                  if v not in actual["variables_proceso"]]
                 + [v for v in sug["setpoints"] if v not in actual["setpoints"]])
        if fuera:
            _activity_log.log_issue(
                "contrato:tags_fuera", "contrato", "Contrato de Variables",
                f"{len(fuera)} tag(s) PV/SP no estan en el contrato: el SE no "
                "los ve.",
                ", ".join(sorted(fuera))
                + ". Pulsa 'Sincronizar con tags' para incorporarlos.",
            )
        else:
            _activity_log.clear_issue("contrato:tags_fuera")
    except Exception as exc:   # noqa: BLE001 - la app tiene que levantar igual
        _activity_log.log_error(
            "contrato", "Contrato de Variables",
            "No se pudo revisar el contrato al arrancar",
            f"{type(exc).__name__}: {exc}",
        )


_revisar_contrato_al_arrancar()


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
