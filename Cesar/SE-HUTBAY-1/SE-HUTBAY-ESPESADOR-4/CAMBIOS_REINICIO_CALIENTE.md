# Reinicio en caliente + observabilidad del corte (2026-09-17)

Mapa del cambio. El razonamiento de las decisiones está en `CLAUDE.md`, sección
*Aplicar cambios no puede costar el lazo ni las ventanas*.

---

## El problema

Con el auto-reinicio activo, guardar cualquier cambio disparaba `stop()` + `start()`
desde el navegador. Eso costaba dos cosas que no tenían nada que ver con el cambio:

1. **El SP se pegaba al del DCS.** `stop()` suelta `ENABLE_EXT`, el DCS retira el FBK y
   la familia queda *sin lazo*. El bumpless hacía lo correcto; el operador veía un salto.
2. **El SE quedaba mudo ~2 minutos.** `_init_state()` vacía los buffers, y
   `pend_hopper_nvl_pv_a_5min` (ventana 2 min) no se produce hasta cubrir su ventana. Las
   dos reglas de la planta la nombran, así que ninguna podía evaluarse.

Además, **`aceleraciones.json` no estaba en el versionado**: guardar una aceleración no
aplicaba nada aunque la página dijera "se aplica al reiniciar el motor".

---

## Qué se tocó

| Archivo | Qué |
|---|---|
| `web/state.py` | `_ciclo_vida_lock` (RLock) sobre `start` / `stop` / `reiniciar`; `_reinicios` (anillo de cortes) + `_se_generacion`; `_abrir_corte` / `_cerrar_corte` / `cortes_motor()` / `generacion_motor()`; `stop()` partido en `stop()` + `_parar_hilo(soltar_control)`; `_snapshot_caliente()`, `_restaurar_caliente()`, `reiniciar()`; `generacion` en `status()` |
| `web/api/se.py` | `POST /api/se/restart`, `GET /api/se/reinicios`, `generacion` en `/api/se/trace` |
| `web/api/config.py` | `aceleraciones.json` en `_ARCHIVOS_VERSIONADOS`; `_huella_tags()` versiona el subconjunto de `tags.json` que el motor lee (mapeo de roles + handshake) |
| `web/templates/index.html` | Debounce 3 s → 1 s, sondeo 1,5 s → 0,8 s, `_doAutoRestart` usa `/api/se/restart` y muestra los avisos del transplante |
| `web/templates/graficos.html` | `reinicioBandsPlugin` (franjas celestes), botón *SE:*, `fetchCortes()`, refresco en sitio al cambiar la generación |
| `web/templates/traza.html` | Detecta el cambio de generación y lo avisa en el subtítulo |
| `tests/test_reinicio_caliente.py` | 12 tests nuevos |
| `_verif_reinicio_caliente.py` | Verificación contra la config de planta, frío vs caliente |

---

## Reglas que hay que respetar al tocar esta zona

- **El reloj no se reinicia.** `_t0` / `_t_s` se conservan. Todo lo fechado (buffers,
  waits, retención, rate limit) vive en esa escala; con el reloj en cero esas marcas
  quedan en el futuro y se leen como edad negativa.
- **Un buffer solo se transplanta si la config de ESA variable es idéntica.** Si cambió
  la ventana, el buffer viejo está decimado con otro paso y la cobertura sería falsa:
  arranca vacío y se avisa.
- **`_sp_escritos` y `_sp_rate_t` van por TAG, no por familia.** Filtrarlos contra
  `_setpoints` los vacía y el write-on-change reescribe todos los SP.
- **Los tres caminos de error sueltan `ENABLE_EXT`.** No soltarlo es una excepción
  deliberada al fail-closed, y sólo es aceptable mientras el rearranque salga bien.
- **Un corte sin cerrar es un corte abierto.** `fin_ms: null` = el motor sigue abajo; la
  franja llega al borde derecho. Abrir uno nuevo cierra el anterior.
- **Franja con ancho mínimo.** Un reinicio en caliente dura ~5 ms: sin mínimo es
  invisible, y el dato que importa es *cuándo*, no *cuánto*.
- **Refresco en sitio, nunca `location.reload()`** en Explorador y Traza: recargar borra
  el zoom y las series que el operador estaba usando para diagnosticar.
- **Toda configuración nueva que el motor lea en `_init_state()` va a
  `_ARCHIVOS_VERSIONADOS`**, o su botón Guardar no aplica nada.
- **`start` / `stop` / `reiniciar` van bajo `_ciclo_vida_lock`.** Con `threads = 4` y dos
  pestañas del panel, dos reinicios simultáneos lanzaban un segundo motor. Reproducido, y
  fijado en `test_reinicios_simultaneos_no_dejan_dos_motores`.

---

## Verificación

```bash
python -m pytest tests/ -q                 # 351 pasan, 2 fallan (los de siempre), 7 skip
python _verif_reinicio_caliente.py         # frio vs caliente sobre la config de planta
```

Medido con el motor corriendo contra un KEPserver falso:

- corte del reinicio en caliente: **~5 ms** (antes: segundos, más lo que tardara el DCS
  en devolver el FBK)
- el contador de ticks **no se reinicia** (13 → 25 a través del reinicio)
- **cero escrituras al DCS** durante el reinicio
- la pendiente de 2 min **sigue viva**; en frío queda muda 120 s

---

## Lo que NO cambió

- El reinicio lo sigue disparando la **página principal**, en el navegador. Si esa
  pestaña está cerrada, no se aplica nada solo. Las otras dos pestañas se enteran, pero
  no disparan.
- Detener el sistema a mano sigue soltando `ENABLE_EXT`: ahí el SE se va de verdad.
- El botón *Iniciar Sistema* sigue haciendo un arranque **frío**, que es lo correcto: no
  hay nada caliente que conservar.
