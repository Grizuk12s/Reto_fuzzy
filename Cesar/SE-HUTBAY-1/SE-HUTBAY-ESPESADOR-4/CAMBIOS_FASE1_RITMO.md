# Fase 1 — Ritmo del lazo configurable y visible (2026-09-22)

Cierra el punto 1 (refresco / deteccion tardia del escenario) y el 7 (reinicio
en caliente) del listado de afinacion.

## El problema

`startSystem()` mandaba `{piso_s: 0.05}` **hardcodeado en la plantilla**. El
piso no se podia cambiar sin editar `index.html`, y las metricas que dicen a
que ritmo corre el motor de verdad (`periodo_ms`, `dur_tick_ms`,
`dormido_ms`, `ticks_por_s`) ya viajaban en `/api/se/status` **sin que ninguna
pantalla las mostrara**. Resultado: ante una deteccion tardia no habia forma de
saber si el retardo venia del lazo, de OPC-UA o de las ventanas de filtro y
pendiente.

## Que es el piso (y que no)

El motor corre en **ciclo libre**: apenas termina de escribir los setpoints
vuelve a leer. El piso es un **periodo minimo**, no el periodo del lazo. Al
final de cada vuelta el worker duerme `piso - duracion_del_tick`, o **nada** si
el tick tardo mas. Entonces:

    ritmo real = max(duracion del tick, piso)

- `dur_tick_ms` — tiempo de ejecucion del **sistema completo** (OPC-UA +
  filtros + fuzzy + reglas + escritura al DCS).
- `periodo_ms` — lo que el motor logra entre dos ticks.
- `dormido_ms` — cuanto durmio por el piso. **Si vive en cero, el piso no esta
  limitando nada** y bajarlo no cambia el ritmo: manda el tick.

## Que cambio

| Archivo | Cambio |
|---|---|
| `config/espesador/motor.json` | **Nuevo.** Guarda `piso_s`. Ausente o corrupto cae al default (0,05 s), no rompe el arranque |
| `web/state.py` | `MOTOR_JSON`, `cargar_motor_cfg()`, `guardar_motor_cfg()`, `SEEngine.set_piso()`. `start()` sin piso explicito toma el de disco |
| `web/api/se.py` | `GET/PUT /api/se/piso`. `POST /api/se/start` sin `piso_s` en el body usa `motor.json` |
| `web/templates/index.html` | Campo **Piso del lazo (ms)** en la barra del motor; fila de metricas en el panel en vivo; el `0.05` hardcodeado ya no existe |
| `tests/test_piso_motor.py` | **Nuevo.** 9 tests: persistencia, default, archivo corrupto, rechazos, y que el cambio **no** pase por un reinicio |

### El piso se aplica EN VIVO

`set_piso()` asigna `self._piso_s` y nada mas. El worker lo lee al final de
cada vuelta, asi que surte efecto en el tick siguiente **sin reiniciar el motor
y sin vaciar las ventanas de pendientes y aceleraciones**. Pasarlo por
`reiniciar()` habria sido mas simple de escribir y habria tirado a la basura
los buffers cada vez que alguien mueve el numero — hay un test que lo cubre.

Por el mismo motivo `motor.json` **no** entra en `_ARCHIVOS_VERSIONADOS` de
`/api/config/version`: si entrara, cada cambio de piso dispararia un reinicio
en caliente, justo lo que este diseno evita.

### Auto-reinicio en caliente: ahora ON por defecto

El toggle ya existia pero nacia **apagado**, asi que editar filtros o
pendientes no aplicaba nada hasta pulsar *Iniciar Sistema*: el operador creia
estar afinando y miraba el motor viejo. Ahora arranca encendido. El reinicio es
en caliente (no suelta el lazo del DCS ni vacia ventanas), y si el operador lo
apaga, su eleccion se recuerda en `localStorage`.

## Como verificar en planta

1. Abrir la pagina de tags con el SE corriendo y mirar la fila de metricas.
2. Comparar `dormido_ms` contra cero:
   - `> 0` → el piso manda; bajarlo acelera el lazo.
   - `= 0` → manda `dur_tick_ms`; el retardo esta en OPC-UA o en las ventanas
     (filtro `ventana_s`, pendiente `ventana_min`) y el piso no tiene nada que
     ver.
3. Cambiar el piso y confirmar que **el tick no se reinicia** (el contador de
   ticks sigue subiendo, no vuelve a cero).

## Lo que NO entra en esta fase

`motor.json` no viaja en el paquete de export/import todavia: el piso se
reconfigura a mano en cada instalacion. Se puede agregar cuando se toque
`web/api/export_import.py`.

## Estado de los tests

`383 passed, 2 failed, 7 skipped`. Los dos que fallan son **previos a este
cambio** (`test_sp_ilegible_se_inhibe_y_se_recupera_solo` y
`test_el_historial_registra_lo_que_se_escribio_al_dcs`): fallan por el nombre
del SP del contrato de esta planta, es la reincidencia de A17/A22 anotada en
`AFINACION_PENDIENTE.md`, y no tocan el ritmo del lazo.
