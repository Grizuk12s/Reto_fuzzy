# Fase 6 — El import de tracking PV-SP valida (2026-09-22)

## El problema

`PUT /api/tracking` pasaba por `_normalizar_tracking_payload`; el **import
escribia el archivo crudo**. Comprobado sobre una copia de la config de esta
planta:

| Payload | `PUT /api/tracking` | Import (antes) |
|---|---|---|
| Familia `sp_de_otra_planta` | rechaza | **acepta**, queda en disco |
| `"rango": "muchisimo"` | rechaza | **acepta**, queda en disco |
| `arranque.tag` inexistente | rechaza | **acepta**, queda en disco |

El import crudo es el diseño compartido por todos los modulos de diccionario
(filtros, fuzzy, pendientes, estados, defuzzy). Lo que hace distinto al
tracking es el **peor caso**: no es "una regla no evalua", es que el SE deja de
escribir un setpoint.

- `pv_key` que aca no existe -> `_evaluar_tracking` es fail-closed (sin poder
  verificar el readback no empuja) y **retiene esa familia** indefinidamente.
  Se ve como alerta y como motivo en la traza, pero recien despues de arrancar
  el motor.
- `arranque.tag` de la otra planta -> `_valor_de_arranque` devuelve `None`, la
  familia **no se siembra nunca** y por lo tanto no escribe. Este es el callado:
  un tag ausente no levanta alerta, solo queda el motivo de semilla pendiente.

## Que cambio

| Archivo | Cambio |
|---|---|
| `web/api/export_import.py` | `_aplicar_tracking()`: el bloque pasa por `_normalizar_tracking_payload` y se rechaza entero sin tocar el archivo. `_preview_tracking()`: avisa antes de aplicar. `_sincronizar_tracking_post()`: crea la familia con el bloque `arranque`, igual que el boton Sincronizar |
| `web/templates/export_import.html` | El preview pinta las referencias colgadas con familia, campo y efecto |
| `tests/test_import_tracking.py` | **Nuevo.** 14 tests: round-trip exacto, los tres rechazos, modos, y los avisos del preview |

### Se valida lo que trae el paquete, no el resultado del merge

Si en disco ya habia familias huerfanas de antes, no es este import quien tiene
que fallar por ellas.

### `copias` se aplica como `agregar`

Una familia **es** el identificador de un SP de esta planta: `velocidad_sp_2`
no seria una copia de nada, seria una huerfana recien fabricada. Se aplica como
`agregar` y el resultado lo dice.

### El aviso del preview tiene que significar algo

Un paquete de esta misma planta no genera ningun aviso — hay un test que lo
cubre. Un aviso que salta siempre no lo mira nadie.

## Estado de los tests

`458 passed, 2 failed, 7 skipped`. Los dos que fallan son previos a la Fase 1
(nombre del SP del contrato, reincidencia de A17/A22 en `AFINACION_PENDIENTE.md`).
