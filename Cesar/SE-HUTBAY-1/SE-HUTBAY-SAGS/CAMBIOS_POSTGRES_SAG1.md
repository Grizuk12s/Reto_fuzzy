# PostgreSQL: una conexión para todos los SAG, en el SAG 1 (2026-10-02)

- La página **PostgreSQL** siempre es del **SAG 1** (el SAG por defecto, que no se puede
  eliminar): el router la sirve desde el SAG 1 aunque la cookie o `?_sag=` pidan otro.
- **Sin selector de SAG** arriba a la izquierda (al lado del logo de Hudbay), ni franja de color.
- Guardar la conexión (host, puerto, base, usuario, contraseña, persistencia) en el SAG 1 la
  **copia al `postgres.json` de cada otro SAG**. Cada SAG la relee en su siguiente escritura; no
  hace falta reiniciar. Guardar desde otro SAG no propaga nada.
- La lista de tablas y "Crear / Verificar tablas" son las del **SAG 1** (`sag1_entrada` /
  `sag1_salida`). Cada SAG sigue grabando en sus propias tablas y las crea al iniciar su motor.
- La página lo explica en un párrafo bajo el título (solo con varios SAG).

Archivos: `router.py` (`PAGINAS_DEL_SAG_FIJO`), `web/api/postgres.py`
(`_propagar_a_otros_sags`), `web/templates/postgres.html`.

Verificación: `pytest tests/` → 556 pasan, 2 fallan (los de siempre). Navegador con 2 SAG y la
cookie en SAG 2: la página carga del SAG 1, sin pastilla; guardar `host = 10.0.0.50` quedó en el
`postgres.json` de SAG 1 y de SAG 2.
