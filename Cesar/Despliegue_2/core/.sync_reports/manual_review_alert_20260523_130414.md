# Alerta de revision manual core -> Prototipo_2

## Resumen

- Fecha: `2026-05-23T13:04:14`
- Directorio canonico: `C:\Users\cvall\Desktop\Reto\Reto_fuzzy\Cesar\Despliegue_2\core\core`
- Directorio objetivo: `C:\Users\cvall\Desktop\Reto\Reto_fuzzy\Cesar\Despliegue_2\core\Prototipo_2`
- Tipo de evento: `manual-review`
- Accion automatica: `sin sincronizacion`

## Archivos detectados

- `core\reglas_estrategia_correcta.py`: Solo es fallback; revisar tambien la migracion de reglas.json.

## Accion recomendada

- Revisar la divergencia entre `core/reglas_estrategia_correcta.py` y `Prototipo_2/reglas.json`.
- Decidir manualmente si el fallback Python debe copiarse, migrarse o dejarse sin cambios.