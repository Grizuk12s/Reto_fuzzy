import sys, os, json, tempfile, math, time
sys.path.insert(0,"/tmp/proj"); os.chdir("/tmp/proj")
import web.state as st
SE="PCS7.OS01.PU009_Exp_Speed_Exp"; DCS="PCS7.OS01.PU009_Velocidad_SP"
FBK="PCS7.OS01.PU009_Exp_Enable_FBK"
MUNDO={"PCS7.OS01.Hopper_Nivel_PV_A":65.0,"PCS7.OS01.Hopper_Lvl_MAX":90.0,
 "PCS7.OS01.Hopper_Lvl_MIN":60.0,"PCS7.OS01.PU009_Speed_MAX":100.0,
 "PCS7.OS01.PU009_Speed_MIN":50.0,"PCS7.OS01.PU009_Velocidad_PV":65.3,
 "PCS7.OS01.PU009_Corriente":12.0,"PCS7.OS01.AG004_Nivel_PV_A":40.0,
 SE:50.0, DCS:72.0, FBK:False}
ESC=[]
rd=lambda tags:{t:({"exists":True,"value":MUNDO[t],"quality":"Good","status_code":"Good",
    "connected":True,"source_ts":None,"estancado_s":0.0} if t in MUNDO else
    {"exists":False,"value":None,"quality":"Bad","status_code":"BadNodeIdUnknown","connected":True})
    for t in tags}
def wr(v):
    ESC.append((round(time.time()%1000,2), dict(v)))
    for t,x in v.items(): MUNDO[t]=float(x)
    return {"escritos":list(v),"fallidos":[]}
st._read_kepserver_tags_batch=rd
import connectors.kepserver as k; k.write_float_batch=wr; st._kep.write_float_batch=wr
tmp=tempfile.mkdtemp(); ruta=os.path.join(tmp,"tracking.json")
open(ruta,"w",encoding="utf-8").write(json.dumps({
  "velocidad_salida_del_se":{"pv_key":"velocidad_pv","rango":0.3,"habilitado":True,
    "arranque":{"fuente":"tag","tag":DCS}}}))
st.TRACKING_JSON=ruta
st._alerts=st.AlertCollector(); st._reset_trazas()
m=st.SEEngine(); m._piso_s=1.0; m._init_state()
SPKEY="velocidad_salida_del_se"

print("FBK = 0  ->  el TAG del SE debe igualarse al SP del DCS (espejo)")
print(f"{'seg':>5} {'SP_DCS':>9} {'TAG_SE':>9} {'interno':>9}  {'espejo?':>8}")
t0=time.time()
i=0
while time.time()-t0 < 6.0:
    MUNDO[DCS]=round(72.0+1.2*math.sin((time.time()-t0)*1.1), 3)
    m._run_tick()
    tz=st._get_trazas(1)[0]
    esc=tz.get("escritura",{}) or {}
    hubo = "SI" if esc.get("espejo") else ""
    if hubo or i%12==0:
        print(f"{time.time()-t0:>5.1f} {MUNDO[DCS]:>9} {MUNDO[SE]:>9} "
              f"{round(m._setpoints[SPKEY],3):>9}  {hubo:>8}")
    i+=1
    time.sleep(0.05)
print(f"   escrituras al DCS en 6 s: {len(ESC)}  (todas espejo)")
print(f"   ultimo valor escrito: {list(ESC[-1][1].values())[0]}  |  SP del DCS: {MUNDO[DCS]}")

print("\nFBK = 1  ->  manda el SE, el espejo se apaga")
MUNDO[FBK]=True
n0=len(ESC)
t1=time.time()
while time.time()-t1 < 3.0:
    MUNDO[DCS]=round(60.0+2.0*math.sin((time.time()-t1)*1.1), 3)
    m._run_tick(); time.sleep(0.05)
tz=st._get_trazas(1)[0]
print(f"   SP del DCS ahora: {MUNDO[DCS]}   TAG del SE: {MUNDO[SE]}   interno: {round(m._setpoints[SPKEY],3)}")
print(f"   espejo en el ultimo tick: {(tz.get('escritura') or {}).get('espejo')}")
print(f"   escrituras nuevas en 3 s: {len(ESC)-n0}  (el SE no sigue al DCS)")
