# Hopper — Fase 1: textos plegables y Fuzzy sin rojo (2026-10-02)

Traido del proyecto SAG (mismo cambio, sin la parte multi-SAG). Solo toca `web/templates/index.html`.

1. **Tracking PV-SP**: "Como funciona el Tracking" pasa a ser un desplegable (cerrado al entrar).
   "Ejemplo de evaluacion" (Tracking) y "Formato de condiciones" (Permisivos) usan el mismo estilo de titulo.
2. **Fuzzificacion**: ya no aparece una tarjeta roja por cada PV sin fuzzy ("Sin membresia...") ni el aviso
   rojo "N PV sin fuzzy" en el resumen. Las PV sin fuzzy se crean desde una sola fila:
   "+ Crear fuzzy para [PV] [tipo] + Crear fuzzy". Solo se muestran tarjetas de las PV que tienen fuzzy.
   El texto de la cabecera se actualizo.

Respaldo: `_to_delete/respaldo_fase1_20261002/index.html`.
Siguiente: Fase 2 (menu lateral comun + PostgreSQL / Export alineados a la izquierda),
Fase 3 (pagina Motor separada de Tags KEPserver).
