# -*- coding: utf-8 -*-
"""Verificacion del REINICIO EN CALIENTE contra la config REAL de la planta.

Responde las dos preguntas que motivaron el mecanismo, comparando el reinicio
frio (el de siempre) con el caliente, sobre el mismo escenario:

  1. Al aplicar cambios, el SP interno del SE se pega al del DCS?
  2. Cuanto tiempo queda el SE sin poder decidir, esperando a que la pendiente
     vuelva a cubrir su ventana?

No usa hilos: llama a `_run_tick()` a mano, como `_verif_bumpless.py`, asi el
resultado es determinista. El reinicio se reproduce con las mismas piezas que
usa `SEEngine.reiniciar()` — snapshot, `_init_state()`, restaurar — sin lanzar
el worker.

    python _verif_reinicio_caliente.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import web.state as st

TAG_SP_SE   = "PCS7.OS01.PU009_Exp_Speed_Exp"          # lo que el SE escribe
TAG_SP_DCS  = "PCS7.OS01.PU009_Velocidad_SP"           # el SP del DCS
TAG_FBK     = "PCS7.OS01.PU009_Exp_Enable_FBK"
TAG_EXT     = "PCS7.OS01.PU009_Exp_Enable_Ext"
TAG_HOPPER  = "PCS7.OS01.Hopper_Nivel_PV_A"
FAMILIA     = "velocidad_salida_del_se"

MUNDO = {
    TAG_HOPPER:                    63.5,
    "PCS7.OS01.Hopper_Lvl_MAX":   100.0,
    "PCS7.OS01.Hopper_Lvl_MIN":     0.0,
    "PCS7.OS01.PU009_Speed_MAX":  100.0,
    "PCS7.OS01.PU009_Speed_MIN":   50.0,
    "PCS7.OS01.PU009_Velocidad_PV": 71.0,
    "PCS7.OS01.PU009_Corriente":   12.0,
    "PCS7.OS01.AG004_Nivel_PV_A":  40.0,
    TAG_SP_SE:                     70.0,
    TAG_SP_DCS:                    72.0,
    TAG_FBK:                       True,   # el lazo esta ENTREGADO al SE
}
ESCRITURAS = []


def _fake_read(tags):
    out = {}
    for t in tags:
        if t not in MUNDO:
            out[t] = {"exists": False, "value": None, "quality": "Bad",
                      "status_code": "BadNodeIdUnknown", "connected": True}
            continue
        out[t] = {"exists": True, "value": MUNDO[t], "quality": "Good",
                  "status_code": "Good", "connected": True, "source_ts": None,
                  "estancado_s": 0.0}
    return out


def _fake_write(vals):
    ESCRITURAS.append(dict(vals))
    for t, v in vals.items():
        MUNDO[t] = float(v)
    return {"escritos": list(vals), "fallidos": []}


def _fake_write_bool(tag, valor):
    ESCRITURAS.append({tag: bool(valor)})
    MUNDO[tag] = bool(valor)
    return True


st._read_kepserver_tags_batch = _fake_read
import connectors.kepserver as _kepmod
_kepmod.write_float_batch = _fake_write
st._kep.write_float_batch = _fake_write
for _nombre in ("write_bool", "write_boolean", "write_value"):
    if hasattr(st._kep, _nombre):
        setattr(st._kep, _nombre, _fake_write_bool)


def _pendientes_vivas(motor):
    """Nombres de pendiente que el ULTIMO tick pudo producir, y las que no.

    Se deduce de `pendientes_omitidas` de la traza y no se recalcula: pedirle
    el valor al evaluador agregaria una muestra y correria la ventana, o sea
    la medicion cambiaria lo medido.
    """
    tz = st._get_trazas(1)
    tz = tz[0] if tz else {}
    faltan = list(tz.get("pendientes_omitidas") or [])
    mudas = {str(m).split(" ", 1)[0] for m in faltan}
    vivas = sorted(n for n in motor._pendientes.nombres if n not in mudas)
    return vivas, faltan


def linea(motor, etiqueta):
    vivas, faltan = _pendientes_vivas(motor)
    sp = motor._setpoints.get(FAMILIA)
    print(f"{etiqueta:38} SP_interno={sp!s:>8}  SP_DCS={MUNDO[TAG_SP_DCS]:>6}  "
          f"t_s={motor._t_s:6.1f}  pendientes_vivas={vivas}")
    if faltan:
        print(f"{'':38} sin pendiente: {faltan[0][:70]}")


PASO_SIMULADO_S = 1.0


def calentar(motor, segundos, etiqueta):
    """Corre ticks hasta acumular `segundos` de RELOJ DEL MOTOR.

    El reloj se adelanta corriendo `_t0` hacia atras en vez de dormir: cubrir
    una ventana de dos minutos en tiempo real haria que verificar un cambio
    costara dos minutos, y nadie correria el script.
    """
    objetivo = motor._t_s + segundos
    while motor._t_s < objetivo:
        motor._run_tick()
        motor._t0 -= PASO_SIMULADO_S
    linea(motor, etiqueta)


def arrancar():
    st._alerts = st.AlertCollector()
    st._reset_trazas()
    motor = st.SEEngine()
    motor._piso_s = 0.0
    motor._init_state()
    return motor


VENTANA_S = max([float(m.get("ventana_min", 1.0)) * 60.0
                 for m in (st.pendientes_definidas() or {}).values()
                 if isinstance(m, dict)] or [0.0])

print("=" * 110)
print("CONFIG DE PLANTA — ventana de pendiente mas larga declarada: "
      f"{VENTANA_S:.0f} s")
print("Es exactamente el tiempo que el SE queda mudo tras un reinicio FRIO si")
print("alguna regla nombra esa pendiente.")
print("=" * 110)

# ------------------------------------------------------------------
print("\nFASE 1 — el motor lleva rato corriendo y sus ventanas estan cubiertas")
print("-" * 110)
motor = arrancar()
motor._run_tick()
linea(motor, "  primer tick")
calentar(motor, VENTANA_S + 5.0, f"  tras {VENTANA_S + 5:.0f} s de operacion")
vivas_antes, _ = _pendientes_vivas(motor)
sp_antes = motor._setpoints.get(FAMILIA)
assert vivas_antes, ("Ninguna pendiente llego a producirse: revisa que el "
                     "escenario mueva la variable fuente.")

# ------------------------------------------------------------------
print("\nFASE 2 — el operador guarda un cambio. REINICIO FRIO (el de siempre)")
print("-" * 110)
ESCRITURAS.clear()
frio = arrancar()          # _init_state() limpio: reloj en 0 y buffers vacios
frio._run_tick()
linea(frio, "  tick 1 despues del reinicio frio")
vivas_frio, faltan_frio = _pendientes_vivas(frio)
print(f"\n  pendientes vivas: {vivas_frio or 'NINGUNA'}")
print(f"  -> el SE no puede evaluar las reglas que las nombran durante "
      f"{VENTANA_S:.0f} s")

# ------------------------------------------------------------------
print("\nFASE 3 — el mismo guardado con REINICIO EN CALIENTE")
print("-" * 110)
ESCRITURAS.clear()
snap = motor._snapshot_caliente()
motor._init_state()                      # recarga reglas, fuzzy, defuzzy...
avisos = motor._restaurar_caliente(snap)  # ...y devuelve lo que no cambio
motor._run_tick()
linea(motor, "  tick 1 despues del reinicio caliente")
vivas_cal, _ = _pendientes_vivas(motor)
sp_despues = motor._setpoints.get(FAMILIA)

print(f"\n  avisos del transplante: {avisos or 'ninguno (nada cambio de ventana)'}")
print(f"  pendientes vivas antes:   {vivas_antes}")
print(f"  pendientes vivas despues: {vivas_cal}")
print(f"  SP interno antes={sp_antes}  despues={sp_despues}")
print(f"  escrituras al DCS durante el reinicio: {ESCRITURAS or 'ninguna'}")

print("\n" + "=" * 110)
ok = True
if vivas_cal != vivas_antes:
    ok = False
    print("FALLA: el reinicio en caliente perdio pendientes que ya estaban vivas.")
if sp_despues != sp_antes:
    ok = False
    print(f"FALLA: el SP interno cambio en el reinicio ({sp_antes} -> {sp_despues}). "
          "Eso es el pegado al SP del DCS que este mecanismo existe para evitar.")
if MUNDO[TAG_FBK] is not True:
    ok = False
    print("FALLA: el reinicio toco el handshake.")
print("OK — el reinicio en caliente conserva el lazo y las ventanas."
      if ok else "REVISAR: ver las fallas de arriba.")
print("=" * 110)
sys.exit(0 if ok else 1)
