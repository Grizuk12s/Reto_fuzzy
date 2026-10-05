# Menu lateral comun (2026-10-02)

PostgreSQL, Motor y Export / Import ahora usan el mismo menu lateral que el
editor (antes tenian una barra superior propia).

- El menu vive una sola vez en `web/templates/_sidebar.html`.
- `menu_lateral.py` lo pega en la marca de cada pagina:
  - `<!--SE_SIDEBAR_EDITOR-->` en el editor (`index.html`): las secciones son `#hash`.
  - `<!--SE_SIDEBAR-->` en postgres, export_import y motor: las secciones apuntan a `/espesador#...`.
- `web/state.py::_load_template` y `router.py` (pagina Motor) llaman a `menu_lateral.incrustar`.
- Estilos del menu: `static/sidebar.css` (se sacaron de index.html).
- `toggleSidebar()` y el estado plegado ahora estan en el parcial (se quitaron de index.html).
- La pagina actual se marca sola (`data-sb` = ruta).

Cambios en el menu:
- **Motor** paso de Visualizacion a **Conexion** (Tags KEPserver, Motor, PostgreSQL, Export / Import).
- **Gestion de SAG** quedo en Sistema (antes solo se llegaba desde la barra de Motor).
- Ambos solo aparecen con el router (`data-se-solo-router`).

Respaldo de los archivos anteriores: `_to_delete/respaldo_menu_20261002/`.
Requiere reconstruir el contenedor (`docker compose build` + `docker compose up -d`).

## Formato homogeneo (mismo dia)

Todas las paginas arrancan a la izquierda con el mismo margen que el editor
(20 px) y usan el ancho completo; antes cada una se centraba con su propio
ancho maximo (720 a 1520 px).

- `.container` en postgres, export_import, motor, gestion y entrada: `max-width:none;margin:0;padding:20px`.
- `main` en traza, historial, escrituras y alertas_historial: igual.
- El titulo del editor (h1) usa el mismo tamano que el resto (1.6rem, negrita).
- La portada (bienvenida) sigue centrada a proposito.

Respaldo: `_to_delete/respaldo_formato_20261002/`.
