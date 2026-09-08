"""Verificacion en el contenedor del rate limit por SP (rampa). No toca config."""
import sys

sys.path.insert(0, "/app")
import config as cfg          # noqa: E402
import web.state as st        # noqa: E402


# El DCS falso DEVUELVE lo que se le escribio. No es un detalle: es la premisa
# de `_reconciliar_sp_con_dcs`, que decide "intervencion manual" cuando el DCS
# difiere de lo ultimo que el SE escribio. Un DCS que ignora las escrituras hace
# que el SE lea su propia escritura como si el operador la hubiera deshecho.
en_dcs = {}
# Proceso falso: el readback SIGUE al setpoint escrito. Sin esto el tracking
# retiene la familia (correctamente: el proceso nunca alcanza) y la rampa se
# queda en el primer tramo — que es lo que paso al escribir esta verificacion.
espejo = {}


def leer(nombres):
    for rb_tag, sp_tag in espejo.items():
        if sp_tag in en_dcs:
            en_dcs[rb_tag] = en_dcs[sp_tag]
    return {n: {"connected": True, "exists": True,
                "value": en_dcs.get(n, 60.0), "quality": "Good",
                "status_code": "", "source_ts": None, "estancado_s": 0.0}
            for n in nombres}


def escribir(vals):
    en_dcs.update({t: float(v) for t, v in vals.items()})
    return {"escritos": list(vals), "fallidos": {}}


st._read_kepserver_tags_batch = leer
st._license_check = lambda: {"valid": True, "reason": "", "expires_at": None}
st._kep.write_float_batch = escribir
st._record_tag_values = lambda vals: None
st._persistir_tick = lambda *a, **k: None
st._alerts = st.AlertCollector()

sp0 = cfg.SETPOINT_KEYS[0]
cfg.RATE_SP_CONTRATO[sp0] = 1.0          # 1 unidad de ingenieria por segundo
print("rate declarado:", dict(cfg.RATE_SP_CONTRATO), "| limites:",
      dict(cfg.LIMITES_SP_CONTRATO))

eng = st.SEEngine()
eng._init_state()
tag = eng._mapeo["sp_to_tag"][sp0]

# Cablea el espejo readback -> SP segun tracking.json de la planta.
pv_key = ((eng._tracking or {}).get(sp0) or {}).get("pv_key") or ""
if pv_key:
    rb_tag = next(t for t, v in eng._mapeo["tag_to_pv"].items() if v == pv_key)
    espejo[rb_tag] = tag
    print("tracking: readback %s (%s) sigue a %s" % (pv_key, rb_tag, tag))

eng._run_tick()
print("arranque bumpless -> escrito:", eng._sp_escritos[tag],
      "| rampas:", st._get_trazas(1)[0]["escritura"]["rampas"])

# El operador (o una regla) pide un salto de 5 unidades.
objetivo = eng._sp_escritos[tag] + 5.0
eng._setpoints[sp0] = objetivo
print("objetivo interno  :", objetivo)
for i in range(1, 7):
    eng._t0 -= 1.0                        # 1 s de presupuesto por vuelta
    eng._run_tick()
    tz = st._get_trazas(1)[0]
    r = tz["escritura"]["rampas"]
    w = tz["escritura"]
    print("  tramo %d -> escrito %.2f | interno %.2f | rampas %s | escritos %s "
          "| sin_cambio %s | inhibidos %s | retenidos %s | error %s" % (
              i, eng._sp_escritos[tag], eng._setpoints[sp0],
              [round(x["escrito"], 2) for x in r], w.get("escritos"),
              w.get("sin_cambio"), [x["motivo"] for x in w.get("inhibidos", [])],
              eng._sp_retenidos, w.get("error")))
