# -*- coding: utf-8 -*-
"""Verificacion del bumpless contra la config REAL de la planta.

Falsea el KEPserver y observa que hace el SP interno del SE con el FBK del
handshake apagado y encendido. No toca nada del codigo del motor.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import web.state as st

TAG_SP_SE   = "PCS7.OS01.PU009_Exp_Speed_Exp"          # lo que el SE escribe
TAG_SP_DCS  = "PCS7.OS01.PU009_Velocidad_SP"           # el SP del DCS (referencia)
TAG_FBK     = "PCS7.OS01.PU009_Exp_Enable_FBK"
TAG_PV_VEL  = "PCS7.OS01.PU009_Velocidad_PV"

MUNDO = {
    "PCS7.OS01.Hopper_Nivel_PV_A": 63.5,
    "PCS7.OS01.Hopper_Lvl_MAX":   100.0,
    "PCS7.OS01.Hopper_Lvl_MIN":     0.0,
    "PCS7.OS01.PU009_Speed_MAX":  100.0,
    "PCS7.OS01.PU009_Speed_MIN":   50.0,
    TAG_PV_VEL:                    71.0,
    "PCS7.OS01.PU009_Corriente":   12.0,
    "PCS7.OS01.AG004_Nivel_PV_A":  40.0,
    TAG_SP_SE:                     55.0,   # lo que el tag del SE tiene hoy
    TAG_SP_DCS:                    72.0,   # el SP con el que opera el DCS
    TAG_FBK:                       False,  # permiso: arranca DENEGADO
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

st._read_kepserver_tags_batch = _fake_read
import connectors.kepserver as _kepmod
_kepmod.write_float_batch = _fake_write
st._kep.write_float_batch = _fake_write

def linea(motor, etiqueta):
    tz = st._get_trazas(1)
    tz = tz[0] if tz else {}
    peg = tz.get("setpoints_pegados") or []
    sem = tz.get("semilla_sp") or []
    sp = motor._setpoints.get("velocidad_salida_del_se")
    esc = motor._sp_escritos.get(TAG_SP_SE)
    bloq = (motor._sp_sin_escritura or {}).get("velocidad_salida_del_se", "")
    print(f"{etiqueta:34} SP_interno={sp!s:>8}  escrito={esc!s:>8}  "
          f"SP_DCS={MUNDO[TAG_SP_DCS]:>6}  pegado={'si' if peg else 'no'}  "
          f"semilla={'si' if sem else 'no'}")
    if bloq:
        print(f"{'':34} bloqueada: {bloq[:78]}")

print("=" * 108)
print("ESCENARIO A — el motor ARRANCA con el FBK APAGADO (el DCS no entrego el lazo)")
print("=" * 108)
st._alerts = st.AlertCollector()
st._reset_trazas()
motor = st.SEEngine()
motor._piso_s = 1.0
motor._init_state()
print(f"tras _init_state(): SP interno = {motor._setpoints.get('velocidad_salida_del_se')}  "
      f"(el tag del SE tenia {55.0}, el SP del DCS tiene {MUNDO[TAG_SP_DCS]})")
for i in range(3):
    motor._run_tick()
    linea(motor, f"  tick {i+1} (FBK OFF)")

print("\n  --- el operador mueve el SP del DCS de 72 a 88 con el SE apagado ---")
MUNDO[TAG_SP_DCS] = 88.0
for i in range(3):
    motor._run_tick()
    linea(motor, f"  tick {i+4} (FBK OFF, DCS=88)")

print(f"\n  escrituras al DCS con el FBK apagado: {ESCRITURAS}")

print("\n  --- el DCS ENTREGA el lazo: FBK pasa a True ---")
MUNDO[TAG_FBK] = True
for i in range(3):
    motor._run_tick()
    linea(motor, f"  tick {i+7} (FBK ON)")
print(f"\n  escrituras al DCS tras entregar el lazo: {ESCRITURAS}")

print("\n  --- el operador mueve el SP del DCS a 60 CON el lazo entregado ---")
MUNDO[TAG_SP_DCS] = 60.0
for i in range(3):
    motor._run_tick()
    linea(motor, f"  tick {i+10} (FBK ON, DCS=60)")

print("\n" + "=" * 108)
print("ESCENARIO B — el motor ARRANCA con el FBK ENCENDIDO (el lazo ya estaba entregado)")
print("=" * 108)
ESCRITURAS.clear()
MUNDO[TAG_FBK] = True
MUNDO[TAG_SP_DCS] = 66.0
MUNDO[TAG_SP_SE] = 55.0
st._alerts = st.AlertCollector()
st._reset_trazas()
motor2 = st.SEEngine()
motor2._piso_s = 1.0
motor2._init_state()
print(f"tras _init_state(): SP interno = {motor2._setpoints.get('velocidad_salida_del_se')}")
for i in range(3):
    motor2._run_tick()
    linea(motor2, f"  tick {i+1} (FBK ON)")

print("\n  --- ahora el DCS RETIRA el lazo: FBK pasa a False ---")
MUNDO[TAG_FBK] = False
MUNDO[TAG_SP_DCS] = 91.0
for i in range(3):
    motor2._run_tick()
    linea(motor2, f"  tick {i+4} (FBK OFF, DCS=91)")

print("\n  --- y lo vuelve a entregar ---")
MUNDO[TAG_FBK] = True
for i in range(2):
    motor2._run_tick()
    linea(motor2, f"  tick {i+7} (FBK ON otra vez)")
