# Hopper — Fase 2: menu lateral comun y paginas alineadas (2026-10-02)

Igual que en el proyecto SAG.

- **Menu lateral comun**: vive una sola vez en `web/templates/_sidebar.html`; `menu_lateral.py` lo pega en
  el editor (`<!--SE_SIDEBAR_EDITOR-->`) y en PostgreSQL y Export / Import (`<!--SE_SIDEBAR-->`).
  `web/state.py::_load_template` llama a `menu_lateral.incrustar`. Estilos en `static/sidebar.css`.
  Se conserva la insignia PRUEBA DEMO y el enlace a Diagrama.
- **PostgreSQL y Export / Import**: ya no tienen barra superior; usan el menu lateral y marcan su enlace.
  Contraer el menu se recuerda al pasar de una pagina a otra.
- **Alineadas a la izquierda** (mismo margen que el editor, ancho completo): PostgreSQL, Export / Import,
  Entrada de Datos, Diagrama, Traza, Historial, Escrituras e Historial de alertas. La portada sigue centrada.
- Titulo del editor del mismo tamano que el resto de las paginas.
- Test: `tests/test_diagrama_hopper.py` (el enlace a Diagrama ahora esta en el menu lateral) + `test_menu_lateral_comun`.

Respaldo: `_to_delete/respaldo_fase2_20261002/`.
