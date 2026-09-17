"""Verificacion en el contenedor de la retencion del ultimo valor bueno.

Aisla TODA la I/O: lectura, escritura y persistencia. No toca ninguna config.
"""
import sys

sys.path.insert(0, "/app")
import web.state as st  # noqa: E402

m = st.construir_mapeo()
estado = {"malos": set()}


def leer(nombres):
    out = {}
    for n in nombres:
        mal = n in estado["malos"]
        out[n] = {"connected": True, "exists": not mal,
                  "value": None if mal else 60.0,
                  "quality": "Bad" if mal else "Good",
                  "status_code": "BadNotConnected" if mal else "",
                  "source_ts": None, "estancado_s": 0.0}
    return out


st._read_kepserver_tags_batch = leer
st._license_check = lambda: {"valid": True, "reason": "", "expires_at": None}
st._kep.write_float_batch = lambda vals: {"escritos": list(vals), "fallidos": {}}
st._record_tag_values = lambda vals: None
st._persistir_tick = lambda *a, **k: None
st._alerts = st.AlertCollector()

eng = st.SEEngine()
eng._init_state()
eng._run_tick()
tz = st._get_trazas(1)[0]
print("tick sano   -> fuzzy:", [f["var"] for f in tz["fuzzy"]],
      "| retenidos:", tz["retenidos"])

pv0 = list(m["tag_to_pv"])[0]
estado["malos"].add(pv0)
eng._run_tick()
tz = st._get_trazas(1)[0]
print("cae la PV   -> fuzzy:", [f["var"] for f in tz["fuzzy"]])
print("               retenidos:",
      [(x["rol"], x["valor"], x["edad_s"], x["status_code"]) for x in tz["retenidos"]])
fila = [f for f in tz["lectura"] if f["tag"] == pv0][0]
print("               fila traza: ok=%s retenido=%s valor=%s quality=%s"
      % (fila["ok"], fila["retenido"], fila["valor"], fila["quality"]))

eng._t0 -= 10.0
eng._run_tick()
tz = st._get_trazas(1)[0]
print("vence (10 s)-> fuzzy:", [f["var"] for f in tz["fuzzy"]],
      "| retenidos:", tz["retenidos"])

al = [a for a in st._alerts.get_active() if a["category"] == "calidad"]
print("alertas calidad:", [(a["message"][:70], a["count"]) for a in al])
