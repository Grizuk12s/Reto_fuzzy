# -*- coding: utf-8 -*-
"""Red de seguridad del pipeline del SE.

No prueba la logica difusa en si (eso es el nucleo y no se toca), sino que
el cableado alrededor se comporte igual antes y despues de los cambios:
mapeo tag<->rol, lectura, aborto por PV faltante y traza.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
import pytest

import web.state as st


# ---------------------------------------------------------------
# Fixtures: KEPserver falso, sin red
# ---------------------------------------------------------------
def _tags_completos():
    """Un tags.json valido con los 36 roles del contrato mapeados."""
    from config import VARIABLES_PROCESO, VARIABLES_CRUDAS_REQUERIDAS, SETPOINT_KEYS
    tags, i = [], 1
    for v in VARIABLES_PROCESO:
        tags.append({"id": i, "name": f"PLANTA.PV_{v}", "data_type": "Float",
                     "enabled": True, "categoria": "pv", "rol": v}); i += 1
        for b in ("lmin", "lmax"):
            tags.append({"id": i, "name": f"PLANTA.LIM_{v}_{b}", "data_type": "Float",
                         "enabled": True, "categoria": "lim", "rol": f"{v}_{b}"}); i += 1
    for v in VARIABLES_CRUDAS_REQUERIDAS:
        tags.append({"id": i, "name": f"PLANTA.CR_{v}", "data_type": "Float",
                     "enabled": True, "categoria": "cruda", "rol": v}); i += 1
    for k in SETPOINT_KEYS:
        tags.append({"id": i, "name": f"PLANTA.SP_{k}", "data_type": "Float",
                     "enabled": True, "categoria": "sp", "rol": k}); i += 1
    return tags


VALORES = {"torque": 65.0, "bed_mass": 500.0, "bed_level": 2.5, "densidad": 62.0,
           "torque_bomba": 55.0, "potencia_bomba": 400.0, "presion_descarga": 12.0,
           "presion_diferencial": 6.0, "nivel_rastra": 10.0}
LIMS = {"torque": (40, 90), "bed_mass": (200, 900), "bed_level": (0.8, 4.0),
        "densidad": (55, 78), "torque_bomba": (30, 90), "potencia_bomba": (150, 750),
        "presion_descarga": (5, 22), "presion_diferencial": (1, 12), "nivel_rastra": (0, 20)}


@pytest.fixture
def kep(monkeypatch):
    """Sustituye la lectura OPC-UA por valores fijos. `omitir` simula tags caidos."""
    estado = {"omitir": set(), "escrituras": []}

    def _read(nombres):
        out = {}
        for n in nombres:
            if n in estado["omitir"]:
                out[n] = {"connected": True, "exists": False, "value": None, "quality": "Bad"}
                continue
            val = 0.0
            if n.startswith("PLANTA.PV_"):
                val = VALORES[n[len("PLANTA.PV_"):]]
            elif n.startswith("PLANTA.LIM_"):
                rol = n[len("PLANTA.LIM_"):]
                var, bound = rol.rsplit("_", 1)
                val = LIMS[var][0 if bound == "lmin" else 1]
            elif n.startswith("PLANTA.CR_"):
                val = 100.0
            out[n] = {"connected": True, "exists": True, "value": val, "quality": "Good"}
        return out

    monkeypatch.setattr(st, "_read_kepserver_tags_batch", _read)
    monkeypatch.setattr(st._kep, "write_float_batch",
                        lambda vals: estado["escrituras"].append(dict(vals)))
    monkeypatch.setattr(st, "_license_check",
                        lambda: {"valid": True, "reason": "", "expires_at": None})
    return estado


@pytest.fixture
def store(monkeypatch):
    datos = {"tags": _tags_completos(), "next_id": 999}
    monkeypatch.setattr(st, "_load_tags", lambda: copy.deepcopy(datos))
    monkeypatch.setattr(st, "_save_tags", lambda d: None)
    return datos


# ---------------------------------------------------------------
# Mapeo
# ---------------------------------------------------------------
def test_mapeo_completo_con_nombres_de_planta(store):
    """El mapeo funciona con nomenclatura arbitraria, no solo RETO.*"""
    m = st.construir_mapeo()
    assert m["listo"] is True
    assert m["duplicados"] == {}
    assert len(m["tag_to_pv"]) == 9
    assert len(m["tag_to_lim"]) == 18
    assert m["sp_to_tag"]["sp_tonelaje"] == "PLANTA.SP_sp_tonelaje"
    assert m["tag_to_pv"]["PLANTA.PV_torque"] == "torque"


def test_mapeo_detecta_rol_faltante(store):
    store["tags"] = [t for t in store["tags"] if t["rol"] != "bed_level"]
    m = st.construir_mapeo()
    assert m["listo"] is False
    assert "bed_level" in m["faltantes"]["pv"]


def test_mapeo_ignora_tags_suspendidos(store):
    for t in store["tags"]:
        if t["rol"] == "densidad" and t["categoria"] == "pv":
            t["enabled"] = False
    m = st.construir_mapeo()
    assert "densidad" in m["faltantes"]["pv"]
    assert m["listo"] is False


def test_cruda_faltante_no_bloquea_arranque(store):
    store["tags"] = [t for t in store["tags"] if t["rol"] != "turbiedad_agua"]
    m = st.construir_mapeo()
    assert "turbiedad_agua" in m["faltantes"]["cruda"]
    assert m["listo"] is True     # las CRUDA degradan, no bloquean


def test_motor_no_arranca_con_mapeo_incompleto(store, kep):
    store["tags"] = [t for t in store["tags"] if t["rol"] != "torque"]
    eng = st.SEEngine()
    res = eng.start(intervalo_s=1)
    assert res["ok"] is False
    assert "torque" in res["error"]
    assert eng._running is False


# ---------------------------------------------------------------
# Pipeline completo
# ---------------------------------------------------------------
def _tick(monkeypatch):
    """Corre un tick sin arrancar el hilo ni tocar Postgres."""
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._intervalo_s = 5.0
    eng._init_state()
    eng._run_tick()
    return eng


def test_tick_completo_produce_setpoints(store, kep, monkeypatch):
    st._reset_trazas()
    eng = _tick(monkeypatch)
    assert eng._last_error is None
    assert set(eng._setpoints) == {"sp_tonelaje", "sp_floculante", "sp_vel_bomba"}
    assert kep["escrituras"], "debio escribir los SP al KEPserver"
    assert set(kep["escrituras"][-1]) == {
        "PLANTA.SP_sp_tonelaje", "PLANTA.SP_sp_floculante", "PLANTA.SP_sp_vel_bomba"}


def test_setpoints_respetan_limites(store, kep, monkeypatch):
    eng = _tick(monkeypatch)
    for k, v in eng._setpoints.items():
        lo, hi = eng._limites_sp[k]
        assert lo <= v <= hi, f"{k}={v} fuera de [{lo},{hi}]"


def test_pv_caido_aborta_el_tick_sin_escribir(store, kep, monkeypatch):
    st._reset_trazas()
    kep["omitir"].add("PLANTA.PV_bed_level")
    eng = _tick(monkeypatch)
    assert kep["escrituras"] == []
    assert "bed_level" in (eng._last_error or "")
    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] == "lectura"
    assert any(f["rol"] == "bed_level" for f in tz["lectura"] if not f["ok"])


# ---------------------------------------------------------------
# Traza
# ---------------------------------------------------------------
def test_traza_registra_todas_las_etapas(store, kep, monkeypatch):
    st._reset_trazas()
    _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] is None
    assert len(tz["lectura"]) == 33          # 9 PV + 18 LIM + 6 CRUDA
    assert len(tz["filtro"]) == 9
    assert tz["fuzzy"], "debe haber estado fuzzy"
    assert tz["reglas"], "debe reportar el desenlace de cada regla"
    assert len(tz["defuzzy"]) == 3
    assert tz["cobertura"]["listo"] is True


def test_traza_explica_por_que_no_disparo_cada_regla(store, kep, monkeypatch):
    st._reset_trazas()
    _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    estados = {r["estado"] for r in tz["reglas"]}
    assert estados <= {"disparo", "descartada", "no_evaluable", "no_evaluada"}
    for r in tz["reglas"]:
        assert r["motivo"], f"la regla {r['id']} no explica su desenlace"


def test_buffer_de_traza_es_circular(store, kep, monkeypatch):
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    for _ in range(st._TRAZA_SIZE + 10):
        eng._run_tick()
    assert len(st._get_trazas(0)) == st._TRAZA_SIZE


# ---------------------------------------------------------------
# El reporte del motor no altera decisiones (invariante critica)
# ---------------------------------------------------------------
def test_reporte_no_cambia_el_resultado_del_motor():
    from core.engine.motor import _evaluar_set_reglas
    from runner import cargar_reglas_json
    reglas = cargar_reglas_json()
    fuzzy = {"torque": {"pert": {"HIGH": 0.9, "NORMAL": 0.1}},
             "bed_level": {"pert": {"LOW": 0.8}},
             "densidad": {"pert": {"LOW": 0.7}}}

    sin = _evaluar_set_reglas(reglas, fuzzy, 0.0, {}, 0.05, reporte=None)
    con = _evaluar_set_reglas(reglas, fuzzy, 0.0, {}, 0.05, reporte=[])
    assert [f["id"] for f in sin] == [f["id"] for f in con]


# ---------------------------------------------------------------
# Contrato de variables (editable por cliente)
# ---------------------------------------------------------------
def test_contrato_cae_a_defaults_si_no_existe(tmp_path, monkeypatch):
    """Sin contrato.json el sistema arranca con la plantilla estandar."""
    import config as cfg
    monkeypatch.setattr(cfg, "CONTRATO_JSON", str(tmp_path / "no_existe.json"))
    pv, sp = cfg._cargar_contrato()
    assert pv == cfg.VARIABLES_PROCESO_DEFAULT
    assert sp == cfg.SETPOINT_KEYS_DEFAULT


def test_contrato_ignora_json_corrupto(tmp_path, monkeypatch):
    import config as cfg
    malo = tmp_path / "contrato.json"
    malo.write_text("{ esto no es json", encoding="utf-8")
    monkeypatch.setattr(cfg, "CONTRATO_JSON", str(malo))
    pv, sp = cfg._cargar_contrato()
    assert pv == cfg.VARIABLES_PROCESO_DEFAULT


def test_contrato_deduplica_y_limpia(tmp_path, monkeypatch):
    import config as cfg, json as _j
    p = tmp_path / "contrato.json"
    p.write_text(_j.dumps({
        "variables_proceso": ["torque", "torque", "  bed_level  ", "", None, 5],
        "setpoints": ["sp_vel_bomba"],
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONTRATO_JSON", str(p))
    pv, sp = cfg._cargar_contrato()
    assert pv == ["torque", "bed_level"]
    assert sp == ["sp_vel_bomba"]


def test_impacto_detecta_reglas_afectadas():
    """Quitar una variable debe reportar las reglas que la nombran."""
    from web.api.contrato import analizar_impacto, contrato_vigente
    actual = contrato_vigente()
    nuevo = {
        "variables_proceso": [v for v in actual["variables_proceso"] if v != "bed_level"],
        "setpoints": list(actual["setpoints"]),
    }
    imp = analizar_impacto(nuevo)
    assert imp["requiere_confirmacion"] is True
    quitada = next(q for q in imp["quitadas"] if q["variable"] == "bed_level")
    assert quitada["reglas"], "bed_level aparece en reglas; debe reportarlas"


def test_impacto_avisa_piezas_faltantes_al_agregar():
    """Una PV nueva sin modelo difuso ni filtro no se puede evaluar."""
    from web.api.contrato import analizar_impacto, contrato_vigente
    actual = contrato_vigente()
    nuevo = {
        "variables_proceso": actual["variables_proceso"] + ["nivel_hopper"],
        "setpoints": list(actual["setpoints"]),
    }
    imp = analizar_impacto(nuevo)
    falta = next(f for f in imp["faltantes_nuevas"] if f["variable"] == "nivel_hopper")
    assert any("difuso" in x for x in falta["falta"])
    assert any("filtro" in x for x in falta["falta"])


def test_contrato_rechaza_nombres_invalidos():
    from web.api.contrato import _normalizar
    for malo in ["Bed Level", "9var", "t_s", ""]:
        norm, err = _normalizar({"variables_proceso": [malo], "setpoints": ["sp_x"]})
        assert err is not None, f"'{malo}' deberia rechazarse"


def test_contrato_rechaza_pv_que_tambien_es_sp():
    from web.api.contrato import _normalizar
    norm, err = _normalizar({"variables_proceso": ["torque"], "setpoints": ["torque"]})
    assert err is not None and "PV y SP" in err
