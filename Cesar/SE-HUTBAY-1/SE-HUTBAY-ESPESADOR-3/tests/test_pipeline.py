# -*- coding: utf-8 -*-
"""Red de seguridad del pipeline del SE.

No prueba la logica difusa en si (eso es el nucleo y no se toca), sino que
el cableado alrededor se comporte igual antes y despues de los cambios:
mapeo tag<->rol, lectura, aborto por PV faltante y traza.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
import time

import json as _json_fixture

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


def _valores_sinteticos():
    """Valores y limites de prueba para las PV del contrato VIGENTE.

    Antes esto era un dict fijo con las 9 PV del espesador, asi que la red de
    seguridad se caia entera en cuanto se moldeaba el contrato a otro cliente
    — justo la operacion que el producto existe para soportar. Se genera a
    escala 0-100 con la PV a media escala: el cableado no depende del valor.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    # Los SETPOINTS tambien pueden tener limites cableados a un tag, asi que
    # entran a LIMS: es lo que hace resolvible un tag `PLANTA.LIM_<sp>_lmax`.
    return ({v: 50.0 for v in VARIABLES_PROCESO},
            {v: (0.0, 100.0) for v in list(VARIABLES_PROCESO) + list(SETPOINT_KEYS)})


VALORES, LIMS = _valores_sinteticos()


@pytest.fixture
def kep(monkeypatch):
    """Sustituye la lectura OPC-UA por valores fijos. `omitir` simula tags caidos.

    `sp_en_dcs` es el valor que el DCS tiene ANTES de que el SE escriba nada
    (lo usan el arranque bumpless y los tests de saturacion). Una vez que el SE
    escribe, el DCS devuelve lo escrito: `_write` lo asienta en `sp_en_dcs_por_tag`
    y `_read` lo lee de ahi.

    Ese eco no es un detalle del fixture, es la premisa de `_reconciliar_sp_con_dcs`:
    la reconciliacion decide que hubo intervencion manual cuando el DCS difiere de
    lo ultimo que el SE escribio. Con un DCS que ignoraba las escrituras y devolvia
    siempre el mismo numero, el SE leia su propia escritura como si el operador la
    hubiera deshecho y la revertia en el tick siguiente — 6 tests caian por eso.
    """
    estado = {"omitir": set(), "escrituras": [], "sp_en_dcs": 42.0,
              "sp_en_dcs_por_tag": {},
              # B1.4: calidad y estancamiento por tag, para los tests de politica
              # de calidad. Vacio = todo Good y recien cambiado.
              "calidad": {}, "estancado_s": {},
              # Permiso del DCS para el control externo (tags PLANTA.HS_*).
              "handshake": True}

    def _read(nombres):
        out = {}
        for n in nombres:
            if n in estado["omitir"]:
                out[n] = {"connected": True, "exists": False, "value": None,
                          "quality": "Bad", "status_code": "BadNotConnected",
                          "source_ts": None, "estancado_s": 0.0}
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
            elif n.startswith("PLANTA.SP_"):
                val = estado["sp_en_dcs_por_tag"].get(n, estado["sp_en_dcs"])
            elif n.startswith("PLANTA.HS_"):
                # Handshake del DCS: booleano, y por defecto CONCEDIDO. Sin este
                # caso caia en el `val = 0.0` de arriba, que el fail-closed lee
                # como permiso denegado — o sea que el camino feliz del
                # handshake no era testeable.
                val = estado["handshake"]
            calidad = estado["calidad"].get(n, "Good")
            out[n] = {"connected": True,
                      # Igual que el conector: un Bad no cuenta como existente,
                      # tenga valor o no.
                      "exists": calidad != "Bad",
                      "value": val, "quality": calidad,
                      "status_code": "" if calidad == "Good" else calidad + "Falso",
                      "source_ts": None,
                      "estancado_s": estado["estancado_s"].get(n, 0.0)}
        return out

    estado["rechazar"] = set()      # tags que el DCS rechaza (write por tag)

    def _write(vals):
        estado["escrituras"].append(dict(vals))
        escritos = [t for t in vals if t not in estado["rechazar"]]
        fallidos = {t: "BadUserAccessDenied" for t in vals if t in estado["rechazar"]}
        # Lo que el DCS acepta, el DCS lo devuelve. Lo rechazado no: ahi el
        # valor viejo sigue en pie, que es justo lo que hace que el
        # write-on-change lo reintente.
        for t in escritos:
            estado["sp_en_dcs_por_tag"][t] = float(vals[t])
        return {"escritos": escritos, "fallidos": fallidos}

    monkeypatch.setattr(st, "_read_kepserver_tags_batch", _read)
    monkeypatch.setattr(st._kep, "write_float_batch", _write)
    monkeypatch.setattr(st, "_license_check",
                        lambda: {"valid": True, "reason": "", "expires_at": None})
    return estado


@pytest.fixture
def config_completa(monkeypatch, tmp_path):
    """filtros.json, fuzzy.json y limites de SP alineados al contrato vigente.

    El motor se niega a arrancar si a una PV le falta filtro o modelo difuso,
    o si a un SP le faltan limites; estas piezas son las que lo dejan correr.

    Tambien parte de config VACIA de tracking, reglas y defuzzy. No es
    cosmetico: esos tres se leian de la config VIVA de la planta, asi que el
    resultado del suite dependia de lo que el operador tuviera cargado. Paso de
    verdad dos veces:

      - `tracking.json` gano un `pv_key` real con rango 0.3. El readback
        sintetico del fixture (50.0) quedo lejisimo del SP (42.0), el tracking
        retuvo la familia en TODOS los tests y cuatro empezaron a fallar sin
        que nadie tocara una linea de codigo.
      - `reglas.json` gano una segunda regla de prueba con `BAJAR_SP_VEL`
        (paso -2.0), asi que el SP se movia solo en tests que asumian que
        nadie lo tocaba.

    El criterio es el mismo que ya se aplico al desatar el fixture del contrato
    del espesador: la red de seguridad prueba el CABLEADO, y no puede depender
    de la calibracion de la planta. Los tests que necesitan reglas o tracking se
    los declaran ellos (`_motor_con`, `_motor_con_tracking`) desde el cuerpo, que
    corre despues de los fixtures y por lo tanto gana.

    Los cinco JSON restantes (permisivos, pendientes, variables, estados y
    waits) se apuntan tambien a temporales. Dos motivos: el mismo de arriba —
    declarar un permisivo o una pendiente en la planta cambiaba el resultado del
    suite — y que `_load_estados` / `_load_waits` SIEMBRAN el archivo cuando
    falta, asi que sin esto los tests escribian en la config viva.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    import runner, config as cfg

    monkeypatch.setattr(st, "TRACKING_JSON", str(tmp_path / "sin-tracking.json"))
    monkeypatch.setattr(runner, "cargar_reglas_json", lambda path=None: [])
    monkeypatch.setattr(runner, "cargar_defuzzy_json", lambda path=None: {})

    # Los loaders de `runner` tienen sus propias constantes de ruta, y
    # `roles_en_uso()` lee los mismos archivos con las de `web.state`: hay que
    # apuntar las dos, o una mitad del motor mira la planta y la otra el temporal.
    for nombre, attr_state, attr_runner in (
            ("permisivos.json", "PERMISIVOS_JSON", "PERMISIVOS_JSON_PATH"),
            ("pendientes.json", "PENDIENTES_JSON", "PENDIENTES_JSON_PATH"),
            ("variables.json",  "VARIABLES_JSON",  "VARIABLES_JSON_PATH")):
        ruta = str(tmp_path / nombre)
        monkeypatch.setattr(st, attr_state, ruta)
        monkeypatch.setattr(runner, attr_runner, ruta)
    monkeypatch.setattr(st, "ESTADOS_JSON_PATH", str(tmp_path / "estados.json"))
    monkeypatch.setattr(st, "WAITS_JSON_PATH", str(tmp_path / "waits.json"))

    monkeypatch.setattr(runner, "cargar_filtros_json",
                        lambda path=None: {v: {"q": 0.15, "ventana_s": 50.0}
                                           for v in VARIABLES_PROCESO})
    monkeypatch.setattr(cfg, "LIMITES_SP_CONTRATO",
                        {k: (0.0, 100.0) for k in SETPOINT_KEYS})

    # Y el MISMO contrato en disco (A22). `_init_state()` llama a
    # `recargar_contrato()`, que relee `CONTRATO_JSON` y **vacia y repuebla**
    # `LIMITES_SP_CONTRATO` en el mismo objeto: el monkeypatch de arriba
    # sobrevivia como referencia pero con los numeros de la planta viva
    # ([50, 100] hoy). Seis tests fallaban por eso, y no por el codigo.
    # Mismo criterio de A17/A19: todo archivo del que dependa el motor tiene
    # que apuntar al temporal, no a la config de la planta.
    ruta_contrato = tmp_path / "contrato.json"
    ruta_contrato.write_text(_json_fixture.dumps({
        "variables_proceso": list(VARIABLES_PROCESO),
        "setpoints": list(SETPOINT_KEYS),
        "descripciones": {},
        "limites_sp": {k: [0.0, 100.0] for k in SETPOINT_KEYS},
        "rate_sp": {},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONTRATO_JSON", str(ruta_contrato))

    # `limites` es el cableado limite -> tag. Desde el 2026-09-03 el mapeo de
    # los LIM sale de aca y NO del campo `rol` del tag: sin esto el motor
    # arranca sin ningun limite y ninguna PV se fuzzifica.
    fuzzy_fixture = {v: {"type": "norm", "offset": [0.0, 0.5, 1.0],
                         "labels": {"HIGH": [1.0, 0.5, 0.0],
                                    "OK":   [0.0, 1.0, 0.0],
                                    "LOW":  [0.0, 0.5, 1.0]},
                         "limites": {"lmin": f"PLANTA.LIM_{v}_lmin",
                                     "lmax": f"PLANTA.LIM_{v}_lmax"}}
                     for v in VARIABLES_PROCESO}
    monkeypatch.setattr(runner, "cargar_fuzzy_json", lambda path=None: fuzzy_fixture)

    # Y el MISMO fuzzy en disco, apuntando `FUZZY_JSON` al temporal.
    #
    # Desde A27 el validador de reglas no acepta cualquier etiqueta: pregunta
    # cuales tiene ESA variable, y eso sale de `fuzzy.json`. Si aca solo se
    # parchea el loader de `runner`, el motor evalua contra el fixture y el
    # validador contra la planta — y el suite pasa o falla segun lo que el
    # operador tenga cargado ese dia. Es la leccion de A17/A19 una vez mas:
    # cada archivo nuevo del que dependa la validacion hay que traerlo aca.
    ruta_fuzzy = tmp_path / "fuzzy.json"
    ruta_fuzzy.write_text(_json_fixture.dumps(fuzzy_fixture), encoding="utf-8")
    monkeypatch.setattr(st, "FUZZY_JSON", str(ruta_fuzzy))
    monkeypatch.setattr(runner, "FUZZY_JSON_PATH", str(ruta_fuzzy), raising=False)

    # Idem `defuzzy.json`: de ahi salen el cableado de los limites de SP y su
    # respaldo numerico. Sin repuntarlo, el suite miraria las familias de la
    # planta viva — la leccion de A17 aplicada al archivo nuevo.
    #
    # Las familias van SIN acciones (`steps_por_accion` vacio) a proposito: asi
    # ninguna queda "en uso" y las tablas de los tests siguen viniendo del
    # monkeypatch de `cargar_defuzzy_json`. Lo unico que aporta el archivo es el
    # respaldo numerico, que es de donde el motor saca el tope de escritura
    # cuando el bound no tiene tag cableado.
    ruta_defuzzy = tmp_path / "defuzzy.json"

    def _escribir_defuzzy(limites_sp):
        datos = {}
        for k in SETPOINT_KEYS:
            par = limites_sp.get(k)
            datos[k] = {
                "belief_axis": [0.0, 1.0],
                "steps_por_accion": {},
                "limites_num": ({"habilitado": True,
                                 "lmin": float(par[0]), "lmax": float(par[1])}
                                if par and len(par) == 2
                                else {"habilitado": False, "lmin": None, "lmax": None}),
            }
        ruta_defuzzy.write_text(_json_fixture.dumps(datos), encoding="utf-8")

    _escribir_defuzzy({k: [0.0, 100.0] for k in SETPOINT_KEYS})
    monkeypatch.setattr(st, "DEFUZZY_JSON", str(ruta_defuzzy))
    def _ajustar(**campos):
        """Cambia el contrato temporal y, si toca, el respaldo de los SP.

        Es la unica forma correcta de mover el tope de escritura o el rate desde
        el cuerpo de un test:

        - `rate_sp` vive en el contrato, y `monkeypatch.setattr(cfg,
          "RATE_SP_CONTRATO", ...)` no sobrevive a `_init_state()`, que llama a
          `recargar_contrato()` y repuebla ese dict desde el archivo (A22).
        - `limites_sp` ya **no lo lee el motor**: desde el 2026-09-03 el tope de
          un SP sale del tag cableado o del respaldo numerico de `defuzzy.json`.
          Se sigue aceptando con ese nombre porque es como lo piensan los tests,
          y se traduce al respaldo. Un par vacio lo deja deshabilitado, que es
          "ese setpoint no tiene tope y por lo tanto no se escribe".
        """
        if "limites_sp" in campos:
            _escribir_defuzzy(campos["limites_sp"])
        datos = _json_fixture.loads(ruta_contrato.read_text(encoding="utf-8"))
        datos.update(campos)
        ruta_contrato.write_text(_json_fixture.dumps(datos), encoding="utf-8")
        cfg.recargar_contrato()
        return datos

    _ajustar()          # deja config.py alineado con el contrato temporal ya
    return _ajustar


@pytest.fixture
def store(monkeypatch):
    datos = {"tags": _tags_completos(), "next_id": 999}
    monkeypatch.setattr(st, "_load_tags", lambda: copy.deepcopy(datos))
    monkeypatch.setattr(st, "_save_tags", lambda d: None)
    return datos


# ---------------------------------------------------------------
# Mapeo
# ---------------------------------------------------------------
def test_mapeo_completo_con_nombres_de_planta(store, config_completa):
    """El mapeo funciona con nomenclatura arbitraria, no solo RETO.*

    Necesita `config_completa` porque el cableado de los LIM sale de
    `fuzzy.json`: sin apuntarlo al temporal, este test leeria el de la planta
    viva y su cobertura dependeria de como este calibrada hoy (A17).
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    m = st.construir_mapeo()
    assert m["listo"] is True
    assert m["duplicados"] == {}
    # Los tamanos salen del contrato vigente, no de un numero fijo.
    assert len(m["tag_to_pv"]) == len(VARIABLES_PROCESO)
    assert len(m["tag_to_lim"]) == 2 * len(VARIABLES_PROCESO)
    sp0 = SETPOINT_KEYS[0]
    assert m["sp_to_tag"][sp0] == f"PLANTA.SP_{sp0}"
    pv0 = VARIABLES_PROCESO[0]
    assert m["tag_to_pv"][f"PLANTA.PV_{pv0}"] == pv0


def test_mapeo_detecta_rol_faltante(store):
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    store["tags"] = [t for t in store["tags"] if t["rol"] != victima]
    m = st.construir_mapeo()
    assert m["listo"] is False
    assert victima in m["faltantes"]["pv"]


def test_mapeo_ignora_tags_suspendidos(store):
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[-1]
    for t in store["tags"]:
        if t["rol"] == victima and t["categoria"] == "pv":
            t["enabled"] = False
    m = st.construir_mapeo()
    assert victima in m["faltantes"]["pv"]
    assert m["listo"] is False


def test_cruda_faltante_no_bloquea_arranque(store):
    """Las CRUDA degradan los permisivos pero no impiden arrancar.

    El contrato de crudas es configurable y puede estar vacio; en ese caso
    no hay nada que probar aqui.
    """
    from config import VARIABLES_CRUDAS_REQUERIDAS
    if not VARIABLES_CRUDAS_REQUERIDAS:
        pytest.skip("El contrato vigente no define variables crudas.")
    victima = VARIABLES_CRUDAS_REQUERIDAS[0]
    store["tags"] = [t for t in store["tags"] if t["rol"] != victima]
    m = st.construir_mapeo()
    assert victima in m["faltantes"]["cruda"]
    assert m["listo"] is True


def test_motor_arranca_degradado_con_mapeo_incompleto(store, kep, config_completa,
                                                      monkeypatch):
    """Un rol sin tag ya no impide arrancar: degrada y avisa.

    El catalogo de roles sale del contrato, asi que declarar una variable que
    todavia no esta instrumentada dejaba al SE ENTERO sin arrancar. Ahora el
    pipeline corre con lo que si llego y la falta se reporta como aviso.
    """
    from config import VARIABLES_PROCESO
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    victima = VARIABLES_PROCESO[0]
    store["tags"] = [t for t in store["tags"] if t["rol"] != victima]
    eng = st.SEEngine()
    try:
        res = eng.start(intervalo_s=1)
        assert res["ok"] is True
        assert eng._running is True
        assert any(victima in a for a in eng._advertencias_arranque)
    finally:
        eng.stop()


def test_solo_se_avisa_por_los_roles_que_el_pipeline_usa(store, kep, config_completa,
                                                         monkeypatch):
    """El criterio de alerta es 'se usa aguas abajo', no 'esta en el contrato'."""
    from config import VARIABLES_PROCESO
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("El contrato vigente tiene una sola PV.")
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    usada, ociosa = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    # `usada` la nombra una regla; `ociosa` no la nombra ni la fuzzifica nadie.
    monkeypatch.setattr(st, "roles_en_uso",
                        lambda fuzzy_cfg=None: {"pv": {usada}, "lim": set(),
                                                "sp": set(), "cruda": set(),
                                                "otro": set()})
    store["tags"] = [t for t in store["tags"] if t["rol"] not in (usada, ociosa)]
    m = st.construir_mapeo()
    assert usada in m["faltantes"]["pv"] and ociosa in m["faltantes"]["pv"]
    assert m["faltantes_en_uso"]["pv"] == [usada]
    assert m["listo_en_uso"] is False


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


def test_tick_completo_produce_setpoints(store, kep, config_completa, monkeypatch):
    from config import SETPOINT_KEYS
    st._reset_trazas()
    eng = _tick(monkeypatch)
    assert eng._last_error is None
    assert set(eng._setpoints) == set(SETPOINT_KEYS), (
        "los setpoints del motor deben salir del contrato, no de una lista fija")
    assert kep["escrituras"], "debio escribir los SP al KEPserver"
    assert set(kep["escrituras"][-1]) == {f"PLANTA.SP_{k}" for k in SETPOINT_KEYS}


def test_setpoints_respetan_limites(store, kep, config_completa, monkeypatch):
    eng = _tick(monkeypatch)
    for k, v in eng._setpoints.items():
        lo, hi = eng._limites_sp[k]
        assert lo <= v <= hi, f"{k}={v} fuera de [{lo},{hi}]"


def test_pv_caida_no_aborta_el_tick_pero_no_se_fuzzifica(store, kep, config_completa,
                                                         monkeypatch):
    """Una PV caida sale del fuzzy; el resto del pipeline sigue corriendo.

    Antes se tiraba el tick completo: una sola senal con calidad mala dejaba
    a la planta sin experto. Lo que NO puede pasar es que se fuzzifique con
    un valor inventado.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    kep["omitir"].add(f"PLANTA.PV_{victima}")
    eng = _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] is None, "una PV caida no puede tumbar el tick"
    assert any(f["rol"] == victima for f in tz["lectura"] if not f["ok"])
    assert victima not in {f["var"] for f in tz["fuzzy"]}
    assert any(victima in x for x in tz["fuzzy_omitidas"])
    assert tz["reglas"] is not None, "el motor debe haber evaluado igual"


# ---------------------------------------------------------------
# Politica de calidad de dato (B1.4)
# ---------------------------------------------------------------
def test_una_pv_uncertain_no_se_fuzzifica(store, kep, config_completa, monkeypatch):
    """El caso que B1.4 vuelve detectable.

    Antes el conector rellenaba "Good" sin mirar el StatusCode, asi que un
    instrumento que el servidor marcaba `Uncertain` entraba al fuzzy como si
    estuviera sano. `UncertainLastUsableValue` es literalmente "la fuente se
    cayo y esto es lo ultimo que hubo": decidir sobre eso es decidir sobre un
    dato viejo creyendolo fresco.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    kep["calidad"][f"PLANTA.PV_{victima}"] = "Uncertain"
    eng = _tick(monkeypatch)

    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] is None, "una calidad dudosa no puede tumbar el tick"
    assert victima not in {f["var"] for f in tz["fuzzy"]}
    fila = next(f for f in tz["lectura"] if f["rol"] == victima)
    assert fila["ok"] is False
    assert fila["quality"] == "Uncertain"


def test_aceptar_uncertain_deja_pasar_la_pv(store, kep, config_completa, monkeypatch):
    """La escotilla de escape: hay codigos Uncertain benignos.

    `UncertainEngineeringUnitsExceeded` es solo "fuera del rango de ingenieria".
    En una planta que los emita seguido, rechazarlos deja al experto mudo — por
    eso la politica es configurable en vez de estar hundida en el codigo.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    kep["calidad"][f"PLANTA.PV_{victima}"] = "Uncertain"
    monkeypatch.setattr(st._kep, "get_aceptar_uncertain", lambda: True)
    _tick(monkeypatch)

    tz = st._get_trazas(1)[0]
    fila = next(f for f in tz["lectura"] if f["rol"] == victima)
    assert fila["ok"] is True


def test_el_motivo_de_la_falla_llega_a_la_traza(store, kep, config_completa,
                                                monkeypatch):
    """"Bad" no manda a nadie a ningun lado; el nombre del status si.

    `BadNotConnected` es un cable y `BadUserAccessDenied` son permisos en el
    KEPserver: dos viajes distintos. Antes la traza decia "Bad" en los dos casos.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    kep["omitir"].add(f"PLANTA.PV_{victima}")
    _tick(monkeypatch)

    tz = st._get_trazas(1)[0]
    fila = next(f for f in tz["lectura"] if f["rol"] == victima)
    assert fila["status_code"] == "BadNotConnected"


# ---------------------------------------------------------------
# Retencion del ultimo valor bueno (#3 de CLAUDE.md)
# ---------------------------------------------------------------
def test_un_parpadeo_de_calidad_no_saca_la_pv_del_pipeline(store, kep, config_completa,
                                                           monkeypatch):
    """El motivo por el que existe la retencion.

    Un tick con calidad mala duraba lo que dura un tick (~0.16 s) y ya dejaba
    muda a la regla que nombra esa PV. Un parpadeo del KEPserver no puede sacar
    al experto de servicio: se sigue usando el ultimo valor bueno.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    tag = f"PLANTA.PV_{victima}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()

    eng._run_tick()                                  # tick sano: deja la marca
    bueno = next(f for f in st._get_trazas(1)[0]["lectura"] if f["tag"] == tag)["valor"]

    kep["calidad"][tag] = "Bad"                      # parpadeo
    eng._run_tick()

    tz = st._get_trazas(1)[0]
    assert victima in {f["var"] for f in tz["fuzzy"]}, (
        "la PV tiene que seguir fuzzificandose con el ultimo valor bueno")
    fila = next(f for f in tz["lectura"] if f["tag"] == tag)
    assert fila["ok"] is True
    assert fila["retenido"] is True
    assert fila["valor"] == bueno, "debe mostrar el valor con el que DECIDIO"
    assert fila["quality"] == "Bad", "la calidad mostrada es la de esta lectura"
    assert [r["rol"] for r in tz["retenidos"]] == [victima]


def test_al_vencer_la_retencion_la_pv_desaparece(store, kep, config_completa,
                                                 monkeypatch):
    """La retencion es un puente, no una tapadera.

    Pasada la ventana el dato viejo deja de valer y la variable sale del
    pipeline — que es el comportamiento que habia antes de la retencion.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    tag = f"PLANTA.PV_{victima}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    monkeypatch.setattr(st._kep, "get_retencion_s", lambda: 5.0)
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()

    kep["calidad"][tag] = "Bad"
    # El reloj del motor es `_t_s = monotonic() - _t0`; se envejece corriendo
    # el origen hacia atras, no esperando 6 s de reloj real.
    eng._t0 -= 6.0
    eng._run_tick()

    tz = st._get_trazas(1)[0]
    assert victima not in {f["var"] for f in tz["fuzzy"]}
    fila = next(f for f in tz["lectura"] if f["tag"] == tag)
    assert fila["ok"] is False
    assert fila["retenido"] is False
    assert tz["retenidos"] == []


def test_retencion_en_cero_desactiva_la_retencion(store, kep, config_completa,
                                                  monkeypatch):
    """Comportamiento previo a la retencion, disponible para quien lo quiera."""
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    tag = f"PLANTA.PV_{victima}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    monkeypatch.setattr(st._kep, "get_retencion_s", lambda: 0.0)
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()

    kep["calidad"][tag] = "Bad"
    eng._run_tick()

    tz = st._get_trazas(1)[0]
    assert victima not in {f["var"] for f in tz["fuzzy"]}
    assert tz["retenidos"] == []


def test_una_pv_que_nunca_estuvo_buena_no_se_retiene(store, kep, config_completa,
                                                     monkeypatch):
    """No hay nada que retener, y el SE no inventa un valor de arranque."""
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    kep["calidad"][f"PLANTA.PV_{victima}"] = "Bad"
    _tick(monkeypatch)

    tz = st._get_trazas(1)[0]
    assert victima not in {f["var"] for f in tz["fuzzy"]}
    assert tz["retenidos"] == []


def test_arrancar_el_motor_olvida_lo_retenido(store, kep, config_completa, monkeypatch):
    """`_ultimo_bueno` fecha con `_t_s`, que vuelve a 0 en cada arranque.

    Sin limpiarlo, la marca de la corrida anterior daria una edad NEGATIVA — o
    sea, dentro de la ventana para siempre — y el motor arrancaria decidiendo
    sobre valores de la corrida pasada.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    tag = f"PLANTA.PV_{victima}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    assert eng._ultimo_bueno, "el tick sano deja marcas"

    eng._init_state()                                # re-arranque
    assert eng._ultimo_bueno == {}
    kep["calidad"][tag] = "Bad"
    eng._run_tick()

    tz = st._get_trazas(1)[0]
    assert tz["retenidos"] == [], "no puede retener nada de la corrida anterior"
    assert victima not in {f["var"] for f in tz["fuzzy"]}


def test_un_limite_retenido_sostiene_su_pv(store, kep, config_completa, monkeypatch):
    """La retencion vale para los LIM igual que para las PV.

    Un limite es justamente lo que menos cambia, y sin el la PV no se puede
    fuzzificar contra ninguna escala.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()

    lims = [t for t, pares in eng._mapeo["tag_to_lim"].items()
            if any(v == victima for v, _b in pares)]
    if not lims:
        pytest.skip("La primera PV del contrato no tiene tags LIM mapeados.")
    kep["calidad"][lims[0]] = "Bad"
    eng._run_tick()

    tz = st._get_trazas(1)[0]
    assert victima in {f["var"] for f in tz["fuzzy"]}, (
        "con el limite retenido la PV sigue teniendo escala")
    assert lims[0] in {r["tag"] for r in tz["retenidos"]}


def test_el_handshake_no_retiene_nada(store, kep, config_completa, monkeypatch):
    """Un permiso que no se puede verificar es un permiso DENEGADO.

    La retencion es para las mediciones: sirve porque un proceso no salta en
    5 s, asi que el dato viejo sigue siendo casi cierto. Un permiso no funciona
    asi — el DCS puede haberlo quitado justo ahora, y eso es precisamente
    cuando importa. El handshake se resuelve en `_chequear_handshake_dcs`, que
    lee `_last_read` y hace su propio chequeo de calidad; la retencion no lo
    toca porque NO muta `_last_read`.
    """
    from config import SETPOINT_KEYS
    hs_tag = "PLANTA.HS_ENABLE_FBK"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._handshake_enabled = True
    eng._handshake_fbk_tag = hs_tag
    eng._handshake_ext_tag = ""

    eng._run_tick()                          # el handshake llega en Good
    assert eng._handshake_ultimo["ok"] is True
    tz = st._get_trazas(1)[0]
    assert not tz["escritura"].get("inhibidos"), "con permiso se escribe"

    kep["calidad"][hs_tag] = "Bad"           # el DCS retira el permiso
    eng._run_tick()

    assert eng._handshake_ultimo["ok"] is False
    tz = st._get_trazas(1)[0]
    motivos = {i["motivo"] for i in tz["escritura"].get("inhibidos", [])}
    assert any("handshake DCS" in m for m in motivos), (
        "el permiso no se retiene: sin poder verificarlo, no se escribe")
    assert {i["sp"] for i in tz["escritura"]["inhibidos"]} == set(SETPOINT_KEYS)


def test_el_aviso_de_retencion_llega_a_las_alertas(store, kep, config_completa,
                                                   monkeypatch):
    """Retener en silencio seria peor que no retener.

    El mensaje NO lleva la edad en segundos: `AlertCollector.add` deduplica por
    texto exacto y un numero que cambia cada tick generaria una alerta nueva por
    vuelta (ver A18).
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    tag = f"PLANTA.PV_{victima}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()

    kep["calidad"][tag] = "Bad"
    eng._run_tick()
    eng._run_tick()

    avisos = [a for a in st._alerts.get_active() if a["category"] == "calidad"
              and "ultimo valor bueno" in a["message"]]
    assert len(avisos) == 1, "dos ticks del mismo problema son UNA alerta"
    assert avisos[0]["count"] >= 2
    assert victima in avisos[0]["message"]


def test_una_pv_estancada_avisa_pero_sigue_usandose(store, kep, config_completa,
                                                    monkeypatch):
    """La decision central del detector de congelado: avisa, no inhibe.

    No puede distinguir un scan congelado de un proceso genuinamente quieto —
    un nivel en un tanque lleno o un readback clavado en su setpoint no se
    mueven y estan perfectos. Inhibir por esto dejaria al experto mudo justo en
    regimen estacionario, que es cuando mas se lo necesita.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    alertas = st.AlertCollector()
    monkeypatch.setattr(st, "_alerts", alertas)
    kep["estancado_s"][f"PLANTA.PV_{victima}"] = 300.0

    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()

    tz = st._get_trazas(1)[0]
    assert victima in {e["rol"] for e in tz["estancadas"]}
    # Lo importante: sigue en el fuzzy.
    fila = next(f for f in tz["lectura"] if f["rol"] == victima)
    assert fila["ok"] is True


def test_el_aviso_de_calidad_sobrevive_a_un_tick_sano(store, kep, config_completa,
                                                     monkeypatch):
    """Defecto que B1.4 dejo a la vista: el aviso se borraba solo.

    `_run_tick` cierra la categoria "kep" al final de cada tick que termina bien
    — correcto para un error de CONEXION, porque un tick sano prueba que la
    conexion anda. Pero un tick sano no prueba nada sobre los instrumentos: una
    PV en Bad no impide que el tick termine bien. Con las dos cosas en la misma
    categoria, el aviso de calidad se auto-resolvia en el mismo tick que lo
    creaba y no llegaba nunca a la pantalla.
    """
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    alertas = st.AlertCollector()
    monkeypatch.setattr(st, "_alerts", alertas)
    kep["calidad"][f"PLANTA.PV_{victima}"] = "Bad"

    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    assert eng._last_error is None, "una PV en Bad no debe hacer fallar el tick"

    vivas = [a for a in alertas.get_active() if a["category"] == "calidad"]
    assert vivas, "el aviso de calidad tiene que seguir vivo despues del tick"
    assert victima in vivas[0]["message"]


def test_el_aviso_de_calidad_se_limpia_cuando_la_senal_vuelve(store, kep,
                                                              config_completa,
                                                              monkeypatch):
    """La otra mitad: no puede quedar un aviso pegado para siempre."""
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    alertas = st.AlertCollector()
    monkeypatch.setattr(st, "_alerts", alertas)
    tag = f"PLANTA.PV_{victima}"
    kep["calidad"][tag] = "Bad"

    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    assert [a for a in alertas.get_active() if a["category"] == "calidad"]

    del kep["calidad"][tag]
    eng._run_tick()
    assert [a for a in alertas.get_active() if a["category"] == "calidad"] == []


def test_el_umbral_cero_desactiva_la_deteccion_de_estancamiento(store, kep,
                                                                config_completa,
                                                                monkeypatch):
    from config import VARIABLES_PROCESO
    st._reset_trazas()
    kep["estancado_s"][f"PLANTA.PV_{VARIABLES_PROCESO[0]}"] = 9999.0
    monkeypatch.setattr(st._kep, "get_estancado_alerta_s", lambda: 0.0)
    _tick(monkeypatch)
    assert st._get_trazas(1)[0]["estancadas"] == []


def test_un_lim_uncertain_saca_del_fuzzy_a_su_pv(store, kep, config_completa,
                                                 monkeypatch):
    """Un limite dudoso es una escala dudosa, y la escala define el dominio."""
    from config import VARIABLES_PROCESO
    victima = VARIABLES_PROCESO[0]
    st._reset_trazas()
    kep["calidad"][f"PLANTA.LIM_{victima}_lmax"] = "Uncertain"
    _tick(monkeypatch)

    tz = st._get_trazas(1)[0]
    assert victima not in {f["var"] for f in tz["fuzzy"]}


def test_no_se_adopta_un_sp_del_dcs_con_calidad_dudosa(store, kep, config_completa,
                                                       monkeypatch):
    """B1.4 x B1.3: reconciliar contra un readback dudoso es peor que no reconciliar.

    El SE se llevaria como "lo que quiso el operador" un valor que el servidor
    mismo no sostiene, y despues lo defenderia contra las reglas.
    """
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    tag_sp = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()                                  # alinea el DCS
    escrito = eng._setpoints[sp0]

    kep["sp_en_dcs_por_tag"][tag_sp] = 77.0
    kep["calidad"][tag_sp] = "Uncertain"
    eng._run_tick()

    assert eng._setpoints[sp0] == escrito, "no se adopta un readback dudoso"
    assert st._get_trazas(1)[0]["resync_sp"] == []


# ---------------------------------------------------------------
# Traza
# ---------------------------------------------------------------
def test_traza_registra_todas_las_etapas(store, kep, config_completa, monkeypatch):
    st._reset_trazas()
    _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] is None
    # El tamano depende del contrato vigente, no de un numero fijo.
    # La traza lista lo que el mapeo resolvio, no el catalogo entero: desde que
    # los SP tienen limites cableables, el catalogo LIM ofrece bounds de SP que
    # esta planta cubre con numeros y que no se leen de ningun tag.
    cat = st.catalogo_roles()
    m = st.construir_mapeo()
    esperado = (len(m["tag_to_pv"]) + len(m["tag_to_cruda"])
                + len(m["tag_to_lim"]) + len(m["sp_to_tag"]))
    assert len(tz["lectura"]) == esperado
    assert len(tz["filtro"]) == len(cat["pv"])
    assert tz["fuzzy"], "debe haber estado fuzzy"
    # El set de reglas es configurable y puede estar vacio; si hay reglas,
    # la traza debe reportar el desenlace de todas.
    from runner import cargar_reglas_json
    assert len(tz["reglas"]) == len(cargar_reglas_json())
    assert len(tz["defuzzy"]) == len(cat["sp"])
    assert tz["cobertura"]["listo"] is True
    sp0 = cat["sp"][0]
    assert any(f["categoria"] == "sp" and f["rol"] == sp0 for f in tz["lectura"])


def test_traza_explica_por_que_no_disparo_cada_regla(store, kep, config_completa, monkeypatch):
    st._reset_trazas()
    _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    estados = {r["estado"] for r in tz["reglas"]}
    assert estados <= {"disparo", "descartada", "no_evaluable", "no_evaluada"}
    for r in tz["reglas"]:
        assert r["motivo"], f"la regla {r['id']} no explica su desenlace"


def test_traza_defuzzy_setpoints_helper():
    live = {"SP.TAG": {"exists": True, "value": 73.5, "quality": "Good"}}
    mapeo = {"sp_to_tag": {"velocidad_sp": "SP.TAG"}}
    filas = st._traza_defuzzy_setpoints(
        {"velocidad_sp": 89.0},
        {"velocidad_sp": 88.0},
        mapeo, live, {"SP.TAG": 50.0}, {"velocidad_sp": (50, 100)},
    )
    assert len(filas) == 1
    f = filas[0]
    assert f["planta"] == 73.5
    assert f["planta_ok"] is True
    assert f["escrito"] == 50.0
    assert f["interno"] == {"antes": 88.0, "despues": 89.0, "delta": 1.0}


def test_traza_defuzzy_en_vivo_expone_planta_escrito_e_interno(store, kep, config_completa, monkeypatch):
    st._reset_trazas()
    _tick(monkeypatch)
    for row in st._get_trazas(1)[0]["defuzzy"]:
        assert "planta" in row
        assert "escrito" in row
        assert "interno" in row
        assert row["interno"]["antes"] == row["antes"]
        assert row["interno"]["despues"] == row["despues"]


def test_traza_setpoints_disparo_planta_vs_interno():
    live = {"SP.TAG": {"exists": True, "value": 73.0, "quality": "Good"}}
    mapeo = {"sp_to_tag": {"vel_sp": "SP.TAG"}}
    eff = st._traza_setpoints_disparo(
        {"vel_sp": 88.0}, {"vel_sp": 89.0}, mapeo, live,
        {"SP.TAG": 50.0}, {"SP.TAG": 51.0}, {"SP.TAG"},
    )
    assert eff["vel_sp"]["interno"] == {"antes": 88.0, "despues": 89.0, "delta": 1.0}
    assert eff["vel_sp"]["planta"]["antes"] == 73.0
    assert eff["vel_sp"]["planta"]["despues"] == 51.0
    assert eff["vel_sp"]["planta"]["delta"] == -22.0
    assert st._movio_planta(eff) is True


def test_traza_setpoints_disparo_sin_escritura_planta_queda_igual():
    live = {"SP.TAG": {"exists": True, "value": 73.0, "quality": "Good"}}
    mapeo = {"sp_to_tag": {"vel_sp": "SP.TAG"}}
    eff = st._traza_setpoints_disparo(
        {"vel_sp": 88.0}, {"vel_sp": 89.0}, mapeo, live,
        {"SP.TAG": 50.0}, {"SP.TAG": 50.0}, set(),
    )
    assert eff["vel_sp"]["interno"]["delta"] == 1.0
    assert eff["vel_sp"]["planta"]["antes"] == 73.0
    assert eff["vel_sp"]["planta"]["despues"] == 73.0
    assert eff["vel_sp"]["planta"]["delta"] == 0.0
    assert st._movio_planta(eff) is False


def test_disparo_en_vivo_expone_planta_e_interno(store, kep, config_completa, monkeypatch):
    from config import SETPOINT_KEYS
    st._reset_trazas()
    _tick(monkeypatch)
    disparos = st._get_trazas(1)[0].get("disparadas") or []
    if not disparos:
        pytest.skip("Sin reglas que disparen en esta config.")
    sp0 = SETPOINT_KEYS[0]
    det = disparos[0]["setpoints"][sp0]
    assert "planta" in det and "interno" in det
    assert "movio_planta" in disparos[0]


def test_sin_escritura_el_interno_no_acumula_con_handshake(store, kep, config_completa,
                                                          monkeypatch):
    """Fase 2: con handshake denegado el objetivo no sube en memoria."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    hs_tag = "PLANTA.HS_ENABLE_FBK"
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tag_sp = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)
    motor._handshake_enabled = True
    motor._handshake_fbk_tag = hs_tag
    motor._handshake_ext_tag = ""

    motor._run_tick()
    escrito_tras_ok = motor._sp_escritos[tag_sp]
    assert motor._setpoints[sp0] == pytest.approx(escrito_tras_ok)

    kep["calidad"][hs_tag] = "Bad"
    motor._run_tick()
    assert motor._setpoints[sp0] == pytest.approx(escrito_tras_ok), (
        "handshake denegado: el interno no acumula otro paso")
    tz = st._get_trazas(1)[0]
    assert tz["escritura"].get("inhibidos"), "sin handshake no se escribe"


def test_sin_escritura_por_tracking_el_interno_no_acumula(store, kep, config_completa,
                                                         monkeypatch, tmp_path):
    """Fase 2: retenido por tracking, el defuzzy no empuja el objetivo interno."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV para readback de tracking.")
    pv, rb = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    sp0 = SETPOINT_KEYS[0]
    tag_sp = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 3.0]}}}
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": rb, "rango": 0.1, "habilitado": True}},
        [_regla_simple(pv)], tabla)

    motor._run_tick()
    escrito = motor._sp_escritos[tag_sp]
    motor._last_action_time.clear()
    motor._run_tick()
    assert motor._sp_retenidos
    assert motor._setpoints[sp0] == pytest.approx(escrito), (
        "tracking retenido: el interno no acumula pasos")


def test_buffer_de_traza_es_circular(store, kep, config_completa, monkeypatch):
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
    from core.engine.motor import variables_de_regla
    from runner import cargar_reglas_json
    reglas = cargar_reglas_json()
    if not any("bed_level" in variables_de_regla(r) for r in reglas):
        pytest.skip("El set de reglas vigente no usa bed_level.")
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
    # Un nombre que no puede existir en ningun contrato real, para que el test
    # no dependa de cual sea el contrato vigente.
    nueva = "pv_inexistente_de_prueba"
    nuevo = {
        "variables_proceso": actual["variables_proceso"] + [nueva],
        "setpoints": list(actual["setpoints"]),
    }
    imp = analizar_impacto(nuevo)
    falta = next(f for f in imp["faltantes_nuevas"] if f["variable"] == nueva)
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


# ---------------------------------------------------------------
# Fugas del espesador en el motor en vivo
# ---------------------------------------------------------------
def test_filtro_sale_de_filtros_json_no_del_default(store, kep, config_completa,
                                                    monkeypatch):
    """El filtro Exp-Q debe configurarse con las PV del contrato.

    Con el default hardcodeado del espesador, ExpQFilter.actualizar() lanzaba
    KeyError en todos los ticks de cualquier contrato distinto.
    """
    from config import VARIABLES_PROCESO
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    assert eng._filtro is not None
    assert set(eng._filtro._config) == set(VARIABLES_PROCESO)


def test_pv_sin_filtro_recibe_el_default_y_arranca(store, kep, config_completa,
                                                   monkeypatch):
    """Una PV sin filtro se siembra con el default en vez de frenar el arranque.

    No se la puede dejar sin filtro: ExpQFilter lanza KeyError con una
    variable sin config y se caeria cada tick.
    """
    import runner
    from config import VARIABLES_PROCESO
    huerfana = VARIABLES_PROCESO[0]
    monkeypatch.setattr(runner, "cargar_filtros_json",
                        lambda path=None: {v: {"q": 0.15, "ventana_s": 50.0}
                                           for v in VARIABLES_PROCESO
                                           if v != huerfana})
    guardado = {}
    monkeypatch.setattr(st, "_guardar_filtros_sembrados", lambda cfg: guardado.update(cfg))
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    assert huerfana in eng._filtro._config
    cfg_pv = eng._filtro._config[huerfana]   # ExpQFilter agrega tau_s/bucket_s
    assert (cfg_pv["q"], cfg_pv["ventana_s"]) == (st.FILTRO_NUEVO_DEFAULT["q"],
                                                  st.FILTRO_NUEVO_DEFAULT["ventana_s"])
    assert guardado.get(huerfana) == st.FILTRO_NUEVO_DEFAULT, "debe persistirse"
    assert any(huerfana in a for a in eng._advertencias_arranque)
    assert eng._problemas_arranque == []


def test_sp_sin_limites_se_inhibe_y_el_resto_corre(store, kep, config_completa,
                                                   monkeypatch):
    """Un SP sin limites no se clipea, asi que NO se escribe. El SE igual corre.

    Antes esto impedia arrancar el motor entero. La propiedad de seguridad
    que hay que conservar es una sola: que no llegue al DCS un valor sin tope.
    """
    from config import SETPOINT_KEYS
    st._reset_trazas()
    config_completa(limites_sp={})
    eng = _tick(monkeypatch)
    # Desde el 2026-09-03 la falta de limites NO es un veredicto de arranque
    # (`_sp_inhibidos`) sino un bloqueo POR TICK: con los limites leidos de un
    # tag, el veredicto de arranque quedaria pegado toda la corrida aunque el
    # tag conteste en el tick siguiente.
    assert set(eng._sp_sin_limite) == set(SETPOINT_KEYS)
    assert set(eng._sp_inhibidos) == set()
    assert kep["escrituras"] == [], "un SP sin limites no puede escribirse"
    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] is None
    assert {d["sp"] for d in tz["escritura"]["inhibidos"]} == set(SETPOINT_KEYS)


def test_sp_ilegible_se_inhibe_y_se_recupera_solo(store, kep, config_completa,
                                                  monkeypatch):
    """Sin poder leer el SP no hay arranque bumpless: se inhibe y se reintenta.

    El reintento evita que un corte momentaneo del KEPserver deje la familia
    muerta hasta el proximo stop/start manual.
    """
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    tag_sp = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    kep["omitir"].add(tag_sp)
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    assert sp0 in eng._sp_inhibidos
    eng._run_tick()
    assert kep["escrituras"] == []

    # Vuelve el tag: el motor se realinea con el DCS y habilita la escritura.
    kep["omitir"].discard(tag_sp)
    eng._t_ultimo_reintento_sp = -1e18
    eng._run_tick()
    assert sp0 not in eng._sp_inhibidos
    assert eng._sp_escritos.get(tag_sp) == kep["sp_en_dcs"], (
        "el reenganche tiene que ser bumpless: la referencia es el valor del DCS")


def test_arranca_con_aviso_si_una_pv_no_tiene_modelo_difuso(store, kep, config_completa,
                                                            monkeypatch):
    """Una PV sin modelo degrada: no se fuzzifica, pero el SE corre igual.

    No toda PV del contrato es entrada difusa — algunas se leen de readback o
    para graficar. Antes esto bloqueaba el arranque y obligaba a inventar una
    membresia falsa. Ahora se avisa y se sigue.
    """
    import runner
    monkeypatch.setattr(runner, "cargar_fuzzy_json", lambda path=None: {})
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    motor = st.SEEngine()
    res = motor.start(intervalo_s=1)
    try:
        assert res["ok"] is True
        avisos = " | ".join(motor.status().get("advertencias", []))
        assert "modelo difuso" in avisos
    finally:
        motor.stop()


def test_arranque_bumpless_parte_del_sp_del_dcs(store, kep, config_completa,
                                                monkeypatch):
    """No debe pisar el setpoint que el operador tiene puesto."""
    from config import SETPOINT_KEYS
    kep["sp_en_dcs"] = 37.5
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    for k in SETPOINT_KEYS:
        assert eng._setpoints[k] == 37.5


def test_start_devuelve_dict_al_arrancar_bien(store, kep, config_completa,
                                              monkeypatch):
    """start() no devolvia nada en el camino feliz."""
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    try:
        res = eng.start(intervalo_s=1)
        assert res == {"ok": True, "error": None}
        assert eng._running is True
    finally:
        eng.stop()


# ===============================================================
# Ciclo libre, reloj real y write-on-change
# ===============================================================

def test_t_s_es_reloj_real_y_no_un_contador_de_ticks(store, kep, config_completa,
                                                     monkeypatch):
    """`t_s` debe medir segundos, no ticks.

    Antes era `self._t_s += self._intervalo_s`: avanzaba 5.0 por vuelta sin
    importar cuanto habia tardado. Eso hacia que un wait de 900 s no durara
    900 s y que la ventana de pendientes no midiera 60 s.
    """
    import time
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()

    eng._run_tick()
    t1 = eng._t_s
    time.sleep(0.25)
    eng._run_tick()
    t2 = eng._t_s

    # Dos ticks seguidos: el reloj avanza lo que paso de verdad (~0.25 s),
    # no un intervalo nominal.
    assert 0.2 < (t2 - t1) < 0.6
    assert t1 >= 0.0


def test_el_lazo_no_espera_entre_ticks(store, kep, config_completa, monkeypatch):
    """Con piso 0 el motor debe iterar muchas veces en poco tiempo."""
    import time
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    try:
        assert eng.start(piso_s=0.0)["ok"] is True
        time.sleep(0.5)
        ticks = eng._tick
    finally:
        eng.stop()
    # Con el wait fijo de 5 s esto daba 1 tick. En ciclo libre son cientos.
    assert ticks > 20, f"solo {ticks} ticks en 0,5 s: el lazo sigue esperando"


def test_el_piso_acota_la_velocidad_del_lazo(store, kep, config_completa, monkeypatch):
    """El piso es un periodo MINIMO: limita los ticks, no los elimina."""
    import time
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    try:
        assert eng.start(piso_s=0.1)["ok"] is True
        time.sleep(0.6)
        ticks = eng._tick
    finally:
        eng.stop()
    assert 2 <= ticks <= 9, f"{ticks} ticks en 0,6 s con piso de 0,1 s"


def test_setpoint_se_escribe_solo_cuando_cambia(store, kep, config_completa,
                                                monkeypatch):
    """Write-on-change: sin reglas el SP no se mueve y no hay que reescribirlo."""
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()

    eng._run_tick()
    assert len(kep["escrituras"]) == 1, "el primer tick debe alinear el DCS"

    for _ in range(5):
        eng._run_tick()
    assert len(kep["escrituras"]) == 1, "sin cambio de SP no se debe reescribir"
    assert eng._sp_omitidos >= 5


def test_setpoint_cambiado_vuelve_a_escribirse(store, kep, config_completa,
                                               monkeypatch):
    """El deadband no debe bloquear un cambio real."""
    from config import SETPOINT_KEYS
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    n = len(kep["escrituras"])

    eng._setpoints[SETPOINT_KEYS[0]] += 3.0
    eng._run_tick()
    assert len(kep["escrituras"]) == n + 1


def test_el_se_adopta_el_sp_que_el_operador_movio_en_el_dcs(store, kep,
                                                            config_completa,
                                                            monkeypatch):
    """B1.3: sin esto el SE revierte la intervencion manual de un salto.

    El operador mueve el SP en el HMI. El SE tiene su propia copia con el valor
    viejo, asi que al siguiente write-on-change ve "cambio" en la direccion
    contraria y devuelve el setpoint a donde el estaba. Tiene que adoptarlo.
    """
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    tag_sp = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()                                  # alinea el DCS

    kep["sp_en_dcs_por_tag"][tag_sp] = 77.0           # el operador, a mano
    eng._run_tick()

    assert eng._setpoints[sp0] == 77.0, "el valor del operador manda"
    assert eng._sp_escritos[tag_sp] == 77.0, (
        "y pasa a ser la referencia, o el proximo tick lo revierte")
    ev = st._get_trazas(1)[0]["resync_sp"]
    assert [e["sp"] for e in ev] == [sp0]
    assert ev[0]["dcs"] == 77.0


def test_no_se_reconcilia_un_sp_que_el_se_nunca_escribio(store, kep,
                                                         config_completa,
                                                         monkeypatch):
    """El primer tick no es una intervencion manual.

    Sin la condicion `tag in _sp_escritos`, el arranque veria "todo cambio" y
    reportaria como intervencion lo que el arranque bumpless ya trato.
    """
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    st._reset_trazas()
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    assert st._get_trazas(1)[0]["resync_sp"] == []


def test_escritura_fallida_se_reintenta(store, kep, config_completa, monkeypatch):
    """Si el write falla, el SP NO debe quedar marcado como escrito."""
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()

    def _explota(vals):
        raise RuntimeError("KEP caido")

    monkeypatch.setattr(st._kep, "write_float_batch", _explota)
    eng._run_tick()
    assert eng._sp_escritos == {}, "un write fallido no debe marcar el SP como escrito"

    monkeypatch.setattr(st._kep, "write_float_batch",
                        lambda vals: kep["escrituras"].append(dict(vals)))
    eng._run_tick()
    assert len(kep["escrituras"]) == 1, "el tick siguiente debe reintentar"


def test_traza_distingue_sin_cambio_de_error(store, kep, config_completa, monkeypatch):
    """Un tick sin escrituras es sano, no un fallo: la traza debe decirlo."""
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    st._reset_trazas()
    eng = st.SEEngine()
    eng._init_state()
    eng._run_tick()
    eng._run_tick()

    tz = st._get_trazas(1)[0]
    assert tz["escritura"]["error"] is None
    assert tz["escritura"]["escritos"] == []
    assert tz["escritura"]["sin_cambio"]


def test_historial_de_tags_se_decima_por_tiempo(monkeypatch):
    """El ring buffer es de observabilidad: debe cubrir tiempo, no ticks."""
    st._tag_history.clear()
    for _ in range(500):                      # 500 vueltas en microsegundos
        st._record_tag_values({"PLANTA.SP_x": 1.0})
    guardadas = len(st._tag_history["PLANTA.SP_x"])
    assert guardadas == 1, (
        f"{guardadas} muestras guardadas: sin decimacion el Explorador de "
        "Series pierde su ventana de horas en minutos")


# ===============================================================
# Decimacion de la persistencia
# ===============================================================

@pytest.fixture
def pg(monkeypatch, tmp_path):
    """PostgreSQL falso: cuenta INSERTs sin tocar una base real."""
    import web.api.postgres as pgmod

    estado = {"inserts": 0, "filas": 0}

    class _Cur:
        def execute(self, *a, **k): pass
        def close(self): pass

    class _Conn:
        def cursor(self): return _Cur()
        def close(self): pass

    def _execute_values(cur, sql, records):
        estado["inserts"] += 1
        estado["filas"] += len(records)

    # psycopg2 esta en requirements.txt, pero un entorno de desarrollo minimo
    # puede no tenerlo: se salta en vez de fallar.
    _ex = pytest.importorskip("psycopg2.extras")
    monkeypatch.setattr(_ex, "execute_values", _execute_values)
    monkeypatch.setattr(pgmod, "_connect", lambda: _Conn())

    cfg = tmp_path / "postgres.json"
    cfg.write_text('{"database":"d","user":"u","persist_enabled":true,'
                   '"persist_periodo_ms":1000}', encoding="utf-8")
    monkeypatch.setattr(pgmod, "POSTGRES_JSON", str(cfg))
    monkeypatch.setattr(pgmod, "_cfg_cache", None, raising=False)
    monkeypatch.setattr(pgmod, "_cfg_cache_mtime", -1.0, raising=False)
    monkeypatch.setattr(pgmod, "_persist_last_t", 0.0, raising=False)
    for k in pgmod._persist_stats:
        pgmod._persist_stats[k] = 0 if isinstance(pgmod._persist_stats[k], int) else None
    return estado


def test_persistencia_se_decima_por_periodo(pg):
    """El motor llama en cada tick; la BD debe recibir un fotograma por periodo."""
    import web.api.postgres as pgmod
    entrada = {f"t{i}": 1.0 for i in range(27)}

    escritos = sum(pgmod.persist_tick(entrada, {"sp": 1.0}, {}, "espesadores")
                   for _ in range(300))

    assert escritos == 1, (
        f"{escritos} escrituras en 300 ticks: sin decimacion la base recibe "
        "una insercion por vuelta del ciclo libre")
    assert pgmod._persist_stats["ticks_omitidos"] == 299


def test_persistencia_forzada_ignora_el_periodo(pg):
    """La validacion de la pagina necesita escribir en el momento."""
    import web.api.postgres as pgmod
    assert pgmod.persist_tick({"t": 1.0}, {}, {}, "espesadores", forzar=True) is True
    assert pgmod.persist_tick({"t": 1.0}, {}, {}, "espesadores", forzar=True) is True


def test_persistencia_deshabilitada_no_escribe(pg, monkeypatch):
    import web.api.postgres as pgmod
    import json, os
    cfg = json.load(open(pgmod.POSTGRES_JSON, encoding="utf-8"))
    cfg["persist_enabled"] = False
    pgmod._save_pg_config(cfg)
    assert pgmod.persist_tick({"t": 1.0}, {}, {}, "espesadores") is False
    assert pg["inserts"] == 0


def test_estado_persistencia_reporta_sin_datos_al_inicio(pg):
    import web.api.postgres as pgmod
    est = pgmod.estado_persistencia()
    assert est["salud"] == "sin_datos"
    assert est["periodo_ms"] == 1000
    assert est["periodos_disponibles"] == [500, 1000]


# ===============================================================
# Fuzzy con etiquetas libres (filas creables / renombrables / borrables)
# ===============================================================

@pytest.fixture
def cfg_dir(monkeypatch, tmp_path):
    """Apunta fuzzy.json y reglas.json a un directorio temporal."""
    import web.state as _st
    import web.api.config as cfgmod
    fz = tmp_path / "fuzzy.json"
    rg = tmp_path / "reglas.json"
    rg.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(_st, "FUZZY_JSON", str(fz))
    monkeypatch.setattr(cfgmod, "FUZZY_JSON", str(fz))
    monkeypatch.setattr(_st, "REGLAS_JSON", str(rg))
    monkeypatch.setattr(cfgmod, "REGLAS_JSON", str(rg))
    return {"fuzzy": fz, "reglas": rg}


def test_registry_acepta_etiquetas_arbitrarias():
    """El motor debe construirse con las filas que el operador defina."""
    from core.fuzzy.templates import construir_registry_fuzzy
    reg = construir_registry_fuzzy({
        "nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                  "labels": {"CRITICO_ALTO": [1.0, 0.2, 0.0],
                             "NORMAL":       [0.0, 1.0, 0.0],
                             "BAJO":         [0.0, 0.2, 1.0],
                             "MUY_BAJO":     [0.0, 0.0, 1.0]}}})
    assert "nivel" in reg
    modelo = reg["nivel"]["model"]
    assert set(modelo.conjuntos) == {"CRITICO_ALTO", "NORMAL", "BAJO", "MUY_BAJO"}
    # norm: off = (pv - lmin)/(lmax - lmin). Con pv=0 el offset es 0, donde
    # CRITICO_ALTO vale 1.0 y es la dominante.
    dominante, val, off, pert = modelo.evaluar(0.0, 0.0, 1.0)
    assert dominante == "CRITICO_ALTO"
    assert set(pert) == {"CRITICO_ALTO", "NORMAL", "BAJO", "MUY_BAJO"}


def test_registry_acepta_dos_etiquetas():
    """Nada obliga a que sean tres filas."""
    from core.fuzzy.templates import construir_registry_fuzzy
    reg = construir_registry_fuzzy({
        "v": {"type": "high", "offset": [0.0, 1.0, 2.0],
              "labels": {"ALTO": [1.0, 0.5, 0.0], "BAJO": [0.0, 0.5, 1.0]}}})
    assert set(reg["v"]["model"].conjuntos) == {"ALTO", "BAJO"}


def test_validador_acepta_etiquetas_libres():
    import web.api.config as cfgmod
    spec, err = cfgmod._validar_fuzzy_spec("v", {
        "type": "norm", "offset": [0.0, 0.5, 1.0],
        "labels": {"critico_alto": [1.0, 0.0, 0.0], "NORMAL": [0.0, 1.0, 0.0]}})
    assert err is None
    # Se normalizan a mayusculas: las reglas las nombran asi.
    assert set(spec["labels"]) == {"CRITICO_ALTO", "NORMAL"}


@pytest.mark.parametrize("etiqueta,motivo", [
    ("NO-ALTO",    "NO-"),
    ("CERCA_ALTO", "reservada"),
    ("ON",         "reservada"),
    ("2ALTO",      "invalido"),
    ("con espacio", "invalido"),
])
def test_validador_rechaza_etiquetas_que_pisan_al_nucleo(etiqueta, motivo):
    import web.api.config as cfgmod
    _, err = cfgmod._validar_fuzzy_spec("v", {
        "type": "norm", "offset": [0.0, 0.5, 1.0],
        "labels": {etiqueta: [1.0, 0.0, 0.0]}})
    assert err is not None, f"'{etiqueta}' deberia rechazarse"


def test_validador_rechaza_fuzzy_sin_etiquetas():
    import web.api.config as cfgmod
    _, err = cfgmod._validar_fuzzy_spec("v", {
        "type": "norm", "offset": [0.0, 0.5, 1.0], "labels": {}})
    assert err is not None and "al menos una" in err


def test_no_se_puede_borrar_una_etiqueta_que_una_regla_usa(cfg_dir):
    """Fallar ruidoso: borrar la fila deja la regla muerta."""
    import json
    import web.api.config as cfgmod

    cfg_dir["fuzzy"].write_text(json.dumps({
        "nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                  "labels": {"HIGH": [1.0, 0.5, 0.0], "OK": [0.0, 1.0, 0.0],
                             "LOW": [0.0, 0.5, 1.0]}}}), encoding="utf-8")
    cfg_dir["reglas"].write_text(json.dumps([
        {"id": "R1", "bloque": "critico", "if": [["nivel", "OK"]], "then": []}]),
        encoding="utf-8")

    nuevo = {"nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                       "labels": {"HIGH": [1.0, 0.5, 0.0], "LOW": [0.0, 0.5, 1.0]}}}
    problemas = cfgmod._huerfanas_por_guardar(cfgmod._load_fuzzy(), nuevo)
    assert problemas and "nivel.OK" in problemas[0] and "R1" in problemas[0]


def test_renombrar_una_etiqueta_usada_tambien_se_bloquea(cfg_dir):
    """Desde el JSON, renombrar es borrar + dar de alta."""
    import json
    import web.api.config as cfgmod

    cfg_dir["fuzzy"].write_text(json.dumps({
        "nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                  "labels": {"OK": [0.0, 1.0, 0.0]}}}), encoding="utf-8")
    cfg_dir["reglas"].write_text(json.dumps([
        {"id": "R7", "bloque": "critico", "if": [["nivel", "OK"]], "then": []}]),
        encoding="utf-8")

    nuevo = {"nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                       "labels": {"NORMAL": [0.0, 1.0, 0.0]}}}
    problemas = cfgmod._huerfanas_por_guardar(cfgmod._load_fuzzy(), nuevo)
    assert problemas and "R7" in problemas[0]


def test_una_etiqueta_sin_reglas_se_puede_borrar(cfg_dir):
    import json
    import web.api.config as cfgmod
    cfg_dir["fuzzy"].write_text(json.dumps({
        "nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                  "labels": {"OK": [0.0, 1.0, 0.0], "ALTO": [1.0, 0.0, 0.0]}}}),
        encoding="utf-8")
    nuevo = {"nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                       "labels": {"OK": [0.0, 1.0, 0.0]}}}
    assert cfgmod._huerfanas_por_guardar(cfgmod._load_fuzzy(), nuevo) == []


def test_el_catalogo_de_etiquetas_incluye_las_filas_del_fuzzy(cfg_dir):
    """Sin esto, el editor de reglas no ofrece la etiqueta recien creada."""
    import json
    import web.state as _st
    cfg_dir["fuzzy"].write_text(json.dumps({
        "nivel": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                  "labels": {"CRITICO_ALTO": [1.0, 0.0, 0.0]}}}), encoding="utf-8")
    disponibles = _st.etiquetas_disponibles()
    assert "CRITICO_ALTO" in disponibles
    assert "NO-CRITICO_ALTO" in disponibles      # derivada, la genera el nucleo
    assert "OK" in disponibles                   # las base no se pierden


def test_pares_de_regla_conserva_la_etiqueta():
    from core.engine.motor import pares_de_regla
    pares = pares_de_regla({"if": [{"AND": [["nivel", "OK"], ["torque", "HIGH"]]}],
                            "fuerza": ["nivel", "LOW"]})
    assert pares == {("nivel", "OK"), ("torque", "HIGH"), ("nivel", "LOW")}


# ===============================================================
# Defuzzy: eje del belief acotado y ordenado, pasos sin tope
# ===============================================================

def _familia_sp_valida() -> str:
    """Una familia que el validador acepte en esta instalacion.

    Las familias validas ya no incluyen las del espesador antiguo
    (sp_vel_bomba y companhia): salen de los tags de categoria SP del
    cliente. Los tests de abajo prueban el eje del belief, no el nombre de
    la familia, asi que toman la primera disponible.
    """
    from web.api.config import salidas_sp_disponibles
    salidas = salidas_sp_disponibles()
    return salidas[0]["identificador"] if salidas else "sp_vel_bomba"

def test_belief_axis_debe_ir_de_menor_a_mayor():
    import web.api.config as cfgmod
    fam = _familia_sp_valida()
    _, err = cfgmod._normalizar_defuzzy_payload({
        fam: {"belief_axis": [0.0, 0.8, 0.5],
              "steps_por_accion": {"SUBIR": [0.0, 1.0, 2.0]}}})
    assert err is not None and "menor a mayor" in err


@pytest.mark.parametrize("axis", [[0.0, 1.5], [-0.2, 1.0]])
def test_belief_axis_esta_acotado_a_cero_uno(axis):
    import web.api.config as cfgmod
    fam = _familia_sp_valida()
    _, err = cfgmod._normalizar_defuzzy_payload({
        fam: {"belief_axis": axis,
              "steps_por_accion": {"SUBIR": [0.0, 1.0]}}})
    assert err is not None and "0.0 a 1.0" in err


def test_los_pasos_de_una_accion_no_tienen_tope():
    """Estan en unidades del SP: pueden ser negativos o mucho mayores que 1."""
    import web.api.config as cfgmod
    fam = _familia_sp_valida()
    norm, err = cfgmod._normalizar_defuzzy_payload({
        fam: {"belief_axis": [0.0, 0.5, 1.0],
              "steps_por_accion": {"BAJAR": [-0.5, -12.0, -250.0],
                                   "SUBIR": [0.0, 40.0, 1500.0]}}})
    assert err is None, err
    assert norm[fam]["steps_por_accion"]["BAJAR"] == [-0.5, -12.0, -250.0]
    assert norm[fam]["steps_por_accion"]["SUBIR"][-1] == 1500.0


def test_enlaces_defuzzy_mapea_fuzzy_regla_accion_sp(cfg_dir, monkeypatch, tmp_path):
    """La pagina tiene que poder mostrar que fuzzys terminan moviendo el SP."""
    import json
    import web.state as _st
    import web.api.config as cfgmod

    dfz = tmp_path / "defuzzy.json"
    monkeypatch.setattr(_st, "DEFUZZY_JSON", str(dfz))
    monkeypatch.setattr(cfgmod, "DEFUZZY_JSON", str(dfz))
    dfz.write_text(json.dumps({
        "sp_vel_bomba": {"belief_axis": [0.0, 1.0],
                         "steps_por_accion": {"SUBIR_VEL": [0.0, 5.0]}}}), encoding="utf-8")
    cfg_dir["reglas"].write_text(json.dumps([
        {"id": "R1", "bloque": "critico",
         "if": [["nivel_hopper", "HIGH"], ["torque", "OK"]],
         "then": ["SUBIR_VEL"]},
        {"id": "R2", "bloque": "critico",
         "if": [["densidad", "LOW"]], "then": ["OTRA_ACCION"]}]),
        encoding="utf-8")

    enl = cfgmod.enlaces_defuzzy()
    assert [r["id"] for r in enl["sp_vel_bomba"]["reglas"]] == ["R1"]
    assert enl["sp_vel_bomba"]["fuzzys"] == ["nivel_hopper", "torque"]
    # R2 no toca esta familia: su accion no esta en la tabla.
    assert all(r["id"] != "R2" for r in enl["sp_vel_bomba"]["reglas"])


# ===============================================================
# Acciones: unica fuente de verdad = defuzzy.json
# ===============================================================

def test_acciones_disponibles_sale_de_defuzzy_json(cfg_dir, monkeypatch, tmp_path):
    """El catalogo de acciones son las columnas del defuzzy, no una lista en codigo."""
    import json as _json
    import web.state as _st

    dfz = tmp_path / "defuzzy.json"
    dfz.write_text(_json.dumps({
        "velocidad_sp": {"belief_axis": [0.0, 1.0],
                         "steps_por_accion": {"AUMENTAR_SP_VEL": [0.0, 1.0]}}}),
        encoding="utf-8")
    monkeypatch.setattr(_st, "DEFUZZY_JSON", str(dfz))

    disponibles = _st.acciones_disponibles()
    assert disponibles == ["AUMENTAR_SP_VEL"]
    # Las del espesador antiguo ya no se ofrecen: pertenecen a otra operacion.
    assert "AUMENTAR_VEL_BOMBA_FUERTE" not in disponibles
    assert "AUMENTAR_FLOCULANTE" not in disponibles


def test_acciones_disponibles_vacio_sin_defuzzy(monkeypatch, tmp_path):
    """Sin archivo el catalogo es vacio, no la plantilla del espesador."""
    import web.state as _st
    monkeypatch.setattr(_st, "DEFUZZY_JSON", str(tmp_path / "no_existe.json"))
    assert _st.acciones_disponibles() == []


def _fuzzy_en_disco(monkeypatch, tmp_path, variables=("hopper_nvl_pv_a",)):
    """Escribe un fuzzy.json temporal y apunta `web.state.FUZZY_JSON` ahi.

    Hace falta en cualquier test que valide una regla: desde A27 el validador
    pregunta que etiquetas tiene ESA variable, y la respuesta sale de
    `fuzzy.json`. Sin esto el test lee el archivo de la planta, y pasa o falla
    segun lo que el operador tenga cargado ese dia (leccion A17/A19).
    """
    import json as _j
    import web.state as _st
    ruta = tmp_path / "fuzzy.json"
    ruta.write_text(_j.dumps({
        v: {"type": "norm", "offset": [0.0, 0.5, 1.0],
            "labels": {"HIGH": [1.0, 0.5, 0.0], "OK": [0.0, 1.0, 0.0],
                       "LOW": [0.0, 0.5, 1.0]}}
        for v in variables}), encoding="utf-8")
    monkeypatch.setattr(_st, "FUZZY_JSON", str(ruta))
    monkeypatch.setattr(_st, "PENDIENTES_JSON", str(tmp_path / "sin-pendientes.json"))
    return ruta


def test_regla_con_accion_sin_asignar_es_rechazada(monkeypatch, tmp_path):
    """Guardar con la accion en '(sin asignar)' no puede pasar la validacion."""
    import json as _json
    import web.state as _st
    import web.api.config as cfgmod

    dfz = tmp_path / "defuzzy.json"
    dfz.write_text(_json.dumps({
        "velocidad_sp": {"belief_axis": [0.0, 1.0],
                         "steps_por_accion": {"AUMENTAR_SP_VEL": [0.0, 1.0]}}}),
        encoding="utf-8")
    monkeypatch.setattr(_st, "DEFUZZY_JSON", str(dfz))
    _fuzzy_en_disco(monkeypatch, tmp_path)

    base = {"id": "R_test", "bloque": "estabilidad",
            "if": [["hopper_nvl_pv_a", "HIGH"]]}

    _, err = cfgmod._normalizar_regla_payload({**base, "then": [{"accion": "", "waits": []}]})
    assert err is not None and "sin asignar" in err

    _, err = cfgmod._normalizar_regla_payload(
        {**base, "then": [{"accion": "AUMENTAR_VEL_BOMBA_FUERTE", "waits": []}]})
    assert err is not None and "no existe" in err.lower()

    regla, err = cfgmod._normalizar_regla_payload(
        {**base, "then": [{"accion": "aumentar_sp_vel", "waits": []}]})
    assert err is None, err
    assert regla["then"][0]["accion"] == "AUMENTAR_SP_VEL"


# ===============================================================
# Contrato: disco vs nucleo, y roles que quedan huerfanos
# ===============================================================

def test_estado_contrato_detecta_desincronizacion(monkeypatch, tmp_path):
    """Guardar el contrato no recarga config.py: hay que poder decirlo."""
    import json as _json
    import config as _cfg
    import web.state as _st

    contrato = tmp_path / "contrato.json"
    contrato.write_text(_json.dumps({
        "variables_proceso": ["hopper_nvl_pv_a", "velocidad_pv"],
        "setpoints": ["velocidad_sp"]}), encoding="utf-8")
    monkeypatch.setattr(_st, "CONTRATO_JSON", str(contrato))

    # El nucleo quedo con la lista vieja (la que tenia al importar).
    monkeypatch.setattr(_cfg, "VARIABLES_PROCESO",
                        ["hopper_nvl_pv_a", "corriente", "presion"])
    monkeypatch.setattr(_cfg, "SETPOINT_KEYS", ["velocidad_salida_del_se"])

    est = _st.estado_contrato()
    assert est["desincronizado"] is True
    assert est["agregadas"]["variables_proceso"] == ["velocidad_pv"]
    assert est["quitadas"]["variables_proceso"] == ["corriente", "presion"]
    assert est["agregadas"]["setpoints"] == ["velocidad_sp"]

    # Mismo contenido en ambos lados: no hay aviso que dar.
    monkeypatch.setattr(_cfg, "VARIABLES_PROCESO", ["hopper_nvl_pv_a", "velocidad_pv"])
    monkeypatch.setattr(_cfg, "SETPOINT_KEYS", ["velocidad_sp"])
    assert _st.estado_contrato()["desincronizado"] is False


def test_roles_huerfanos_marca_los_que_el_contrato_ya_no_tiene(monkeypatch):
    """Un rol renombrado por el contrato deja de existir: no es 'sin asignar'."""
    import config as _cfg
    import web.state as _st

    monkeypatch.setattr(_cfg, "VARIABLES_PROCESO", ["velocidad_pv"])
    monkeypatch.setattr(_cfg, "SETPOINT_KEYS", ["velocidad_sp"])
    monkeypatch.setattr(_cfg, "VARIABLES_CRUDAS_REQUERIDAS", [])

    tags = [
        {"id": 1, "name": "T.PV",  "categoria": "pv",  "rol": "velocidad"},      # viejo
        {"id": 2, "name": "T.PV2", "categoria": "pv",  "rol": "velocidad_pv"},   # vigente
        {"id": 3, "name": "T.SP",  "categoria": "sp",  "rol": "velocidad_salida_del_se"},
        {"id": 4, "name": "T.LIM", "categoria": "lim", "rol": "velocidad_pv_lmax"},
        {"id": 5, "name": "T.X",   "categoria": "otro", "rol": "cualquiera"},    # otro no aplica
        {"id": 6, "name": "T.Y",   "categoria": "pv",  "rol": ""},               # sin asignar
    ]
    huerfanos = {h["tag"] for h in _st.roles_huerfanos(tags)}
    assert huerfanos == {"T.PV", "T.SP"}


# ===============================================================
# Defuzzy aplicado: la accion tiene que mover el setpoint
# ===============================================================

def test_apply_actions_no_muta_los_setpoints_recibidos():
    """Contrato del helper: devuelve copia. Quien lo llama DEBE reasignar."""
    from defuzzy_actions import apply_actions_tabla

    tabla = {"velocidad_sp": {"belief_axis": [0.0, 1.0],
                              "steps_por_accion": {"AUMENTAR_SP_VEL": [0.0, 5.0]}}}
    sp = {"velocidad_sp": 70.0}
    nuevos = apply_actions_tabla([("AUMENTAR_SP_VEL", 1.0)], sp, {}, tabla)

    assert sp["velocidad_sp"] == 70.0          # el original queda intacto
    assert nuevos["velocidad_sp"] == 75.0      # el resultado trae el paso


def test_el_tick_aplica_el_paso_al_setpoint(monkeypatch):
    """Regresion: el tick descartaba el retorno y el SP no se movia nunca.

    Se ejercita el mismo patron que usa _run_tick: apply_actions + update.
    """
    from defuzzy_actions import apply_actions_tabla

    tabla = {"velocidad_sp": {"belief_axis": [0.0, 1.0],
                              "steps_por_accion": {"AUMENTAR_SP_VEL": [0.0, 5.0]}}}
    setpoints = {"velocidad_sp": 70.0}
    limites = {"velocidad_sp": (50.0, 100.0)}

    setpoints.update(apply_actions_tabla([("AUMENTAR_SP_VEL", 1.0)], setpoints, limites, tabla))
    assert setpoints["velocidad_sp"] == 75.0

    # Y el clipeo sigue vigente: no se pasa del limite superior del contrato.
    for _ in range(10):
        setpoints.update(apply_actions_tabla([("AUMENTAR_SP_VEL", 1.0)], setpoints, limites, tabla))
    assert setpoints["velocidad_sp"] == 100.0


def test_pv_sin_fuzzy_no_bloquea_el_arranque():
    """Una PV sin membresia degrada (no se fuzzifica) pero deja correr el SE."""
    import inspect
    import web.state as _st

    src = inspect.getsource(_st.SEEngine._init_state)
    assert "advertencias.append(\"PV sin modelo difuso" in src
    assert "problemas.append(\"PV sin modelo difuso" not in src


def test_evaluar_fuzzys_omite_la_variable_sin_modelo():
    """La PV sin modelo no aparece en fuzzy_out: la traza no la lista."""
    from core.fuzzy.evaluator import evaluar_fuzzys
    from core.fuzzy.templates import construir_registry_fuzzy

    registry = construir_registry_fuzzy({
        "hopper_nvl_pv_a": {"type": "high", "offset": [0.0, 0.5, 1.0],
                            "labels": {"HIGH": [1.0, 0.5, 0.0]}}})
    out = evaluar_fuzzys({"hopper_nvl_pv_a": 5.0, "velocidad_pv": 60.0},
                         {"hopper_nvl_pv_a": {"lmin": 0.0, "lmax": 10.0},
                          "velocidad_pv": {"lmin": 50.0, "lmax": 100.0}},
                         registry)
    assert "hopper_nvl_pv_a" in out
    assert "velocidad_pv" not in out


def test_un_tick_con_regla_que_dispara_mueve_el_sp_y_lo_escribe(store, kep, config_completa,
                                                                monkeypatch):
    """Regresion end-to-end del bug: disparar sin mover el setpoint.

    El tick llamaba a apply_actions y tiraba el retorno, asi que la regla
    disparaba, armaba su wait, y el SP quedaba en el valor leido del DCS para
    siempre. Aca se corre un tick real: la regla dispara y el SP tiene que
    cambiar Y llegar al KEPserver.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    import runner

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]

    # OR de las tres etiquetas: sea cual sea el dominio del valor simulado,
    # alguna pertenencia supera el minimo de belief y la regla dispara.
    regla = {
        "id": "R_sube", "bloque": "estabilidad", "priority": 100.0, "weight": 1.0,
        # Las hojas van como tupla: es el formato que deja cargar_reglas_json
        # al normalizar el JSON (una lista de dos strings no es una hoja).
        "if": [{"OR": [(pv0, "HIGH"), (pv0, "OK"), (pv0, "LOW")]}],
        "then": [{"accion": "SUBIR_SP", "waits": [], "reiniciar_waits": []}],
    }
    monkeypatch.setattr(runner, "cargar_reglas_json", lambda path=None: [regla])
    monkeypatch.setattr(runner, "cargar_defuzzy_json",
                        lambda path=None: {sp0: {"belief_axis": [0.0, 1.0],
                                                 "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}})
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())

    motor = st.SEEngine()
    motor._piso_s = 1.0
    motor._init_state()
    sp_inicial = motor._setpoints[sp0]
    motor._run_tick()

    assert motor._last_events, "la regla no disparo: el test no prueba nada"
    assert motor._setpoints[sp0] > sp_inicial, "el SP no se movio pese al disparo"
    escrito = [w for w in kep["escrituras"] if any(v > sp_inicial for v in w.values())]
    assert escrito, "el SP nuevo no se escribio al KEPserver"


# ===============================================================
# Historial de escrituras al DCS (auditoria)
# ===============================================================

def test_el_historial_registra_lo_que_se_escribio_al_dcs(store, kep, config_completa,
                                                         monkeypatch):
    """Lo unico que sale del SE hacia la planta tiene que quedar registrado.

    La traza es un anillo de 60 ticks y el write-on-change hace que la mayoria
    no escriba nada: sin este historial no habia forma de responder "que le
    mando el experto al DCS en la ultima hora".
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    import runner

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    regla = {"id": "R_sube", "bloque": "estabilidad", "priority": 100.0, "weight": 1.0,
             "if": [{"OR": [(pv0, "HIGH"), (pv0, "OK"), (pv0, "LOW")]}],
             "then": [{"accion": "SUBIR_SP", "waits": [], "reiniciar_waits": []}]}
    monkeypatch.setattr(runner, "cargar_reglas_json", lambda path=None: [regla])
    monkeypatch.setattr(runner, "cargar_defuzzy_json",
                        lambda path=None: {sp0: {"belief_axis": [0.0, 1.0],
                                                 "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}})
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())

    motor = st.SEEngine()
    motor._piso_s = 1.0
    motor._init_state()
    assert list(motor._historial_escrituras) == [], "arranca vacio"
    motor._run_tick()

    hist = list(motor._historial_escrituras)
    assert hist, "se escribio al DCS pero no quedo registrado"
    ult = hist[-1]
    assert ult["sp"] == sp0
    assert ult["tag"] == motor._mapeo["sp_to_tag"][sp0]
    assert ult["ok"] is True
    assert ult["valor"] == pytest.approx(motor._sp_escritos[ult["tag"]])
    assert ult["ts_wall"] > 0

    # Y sale en el estado, que es lo que consume la pagina de traza.
    assert motor.status()["historial_escrituras"][0]["tag"] == ult["tag"]


def test_el_historial_registra_tambien_las_escrituras_RECHAZADAS(store, kep, config_completa,
                                                                 monkeypatch):
    """"El experto quiso mover el SP y el DCS no lo acepto" es justo el evento
    que hay que poder reconstruir. Si solo se guardaran los exitosos no
    quedaria ni rastro."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    import runner

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    regla = {"id": "R_sube", "bloque": "estabilidad", "priority": 100.0, "weight": 1.0,
             "if": [{"OR": [(pv0, "HIGH"), (pv0, "OK"), (pv0, "LOW")]}],
             "then": [{"accion": "SUBIR_SP", "waits": [], "reiniciar_waits": []}]}
    monkeypatch.setattr(runner, "cargar_reglas_json", lambda path=None: [regla])
    monkeypatch.setattr(runner, "cargar_defuzzy_json",
                        lambda path=None: {sp0: {"belief_axis": [0.0, 1.0],
                                                 "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}})
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())

    motor = st.SEEngine()
    motor._piso_s = 1.0
    motor._init_state()
    tag_sp = motor._mapeo["sp_to_tag"][sp0]
    kep["rechazar"].add(tag_sp)          # el DCS rechaza la escritura
    motor._run_tick()

    rechazadas = [h for h in motor._historial_escrituras if not h["ok"]]
    assert rechazadas, "una escritura rechazada no quedo en el historial"
    assert rechazadas[-1]["tag"] == tag_sp
    assert rechazadas[-1]["error"]


def test_el_historial_de_escrituras_esta_acotado():
    """No crece sin limite: es un anillo."""
    assert st.HISTORIAL_ESCRITURAS_MAX == 200
    motor = st.SEEngine()
    motor._historial_escrituras.clear()
    for i in range(500):
        motor._historial_escrituras.append({"i": i})
    assert len(motor._historial_escrituras) == 200
    assert motor._historial_escrituras[-1]["i"] == 499


# ===============================================================
# Generador de tags: la configuracion de rangos no se pierde
# ===============================================================

def test_suspender_un_tag_conserva_sus_rangos(monkeypatch):
    """Regresion: suspender un tag borraba su min/max/ruido.

    Suspender es temporal (la señal la da la planta un rato); al reactivar,
    el operador tenia que volver a tipear los rangos. Ahora queda dormido.
    """
    datos = {"tags": [{"name": "PLANTA.PV_x", "categoria": "pv", "rol": "x", "enabled": True}],
             "next_id": 2}
    monkeypatch.setattr(st, "_load_tags", lambda: copy.deepcopy(datos))
    monkeypatch.setattr(st, "_save_tags", lambda d: datos.update(d))

    gen = st.TagGenerator()
    gen.sincronizar_con_tags()
    gen._ranges["PLANTA.PV_x"].update({"min": 62.0, "max": 78.0, "noise": 1.5, "enabled": True})

    datos["tags"][0]["enabled"] = False          # el operador lo suspende
    gen.sincronizar_con_tags()
    assert "PLANTA.PV_x" in gen._ranges          # no se borro
    assert gen._ranges["PLANTA.PV_x"]["vigente"] is False
    assert "PLANTA.PV_x" not in gen.status()["ranges"]   # pero no se muestra
    assert "PLANTA.PV_x" in gen.status()["dormidos"]

    datos["tags"][0]["enabled"] = True           # y lo reactiva
    gen.sincronizar_con_tags()
    cfg = gen._ranges["PLANTA.PV_x"]
    assert (cfg["min"], cfg["max"], cfg["noise"]) == (62.0, 78.0, 1.5)
    assert cfg["vigente"] is True


def test_un_tag_borrado_si_sale_de_los_rangos(monkeypatch):
    """Borrar el tag si lo saca: dormido es solo para los suspendidos."""
    datos = {"tags": [{"name": "PLANTA.PV_x", "categoria": "pv", "rol": "x", "enabled": True}],
             "next_id": 2}
    monkeypatch.setattr(st, "_load_tags", lambda: copy.deepcopy(datos))
    monkeypatch.setattr(st, "_save_tags", lambda d: datos.update(d))

    gen = st.TagGenerator()
    gen.sincronizar_con_tags()
    assert "PLANTA.PV_x" in gen._ranges

    datos["tags"] = []
    gen.sincronizar_con_tags()
    assert "PLANTA.PV_x" not in gen._ranges


def test_el_generador_no_arranca_con_la_plantilla_de_otra_planta(monkeypatch):
    """Los RETO.* del espesador viejo ya no se siembran solos."""
    datos = {"tags": [], "next_id": 1}
    monkeypatch.setattr(st, "_load_tags", lambda: copy.deepcopy(datos))
    monkeypatch.setattr(st, "_save_tags", lambda d: None)
    gen = st.TagGenerator()
    assert gen._ranges == {}


# ===============================================================
# Simulacion offline: datos del contrato, no del espesador de fabrica
# ===============================================================

def test_la_simulacion_genera_las_columnas_del_contrato(store, kep, config_completa,
                                                        monkeypatch):
    """Regresion: la simulacion moria con KeyError de la primera PV.

    generar_datos_proceso() traia las columnas del espesador original
    (torque, bed_level, densidad...). Con otro contrato no habia forma de
    correr una prueba offline.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    from web.api.se import _datos_desde_generador

    df, avisos, sp_base = _datos_desde_generador(n_muestras=20, dt_s=10.0, seed=1)

    assert len(df) == 20
    for var in VARIABLES_PROCESO:
        assert var in df.columns
        assert f"{var}_lmin" in df.columns and f"{var}_lmax" in df.columns
    for sp in SETPOINT_KEYS:
        assert sp in df.columns and sp in sp_base
    assert "t_s" in df.columns
    assert isinstance(avisos, list)


def test_la_simulacion_es_reproducible_con_la_misma_semilla(store, kep, config_completa):
    """Dos corridas con la misma semilla dan lo mismo; con otra, no."""
    from config import VARIABLES_PROCESO
    from web.api.se import _datos_desde_generador

    pv = VARIABLES_PROCESO[0]
    a, _, _ = _datos_desde_generador(30, 10.0, seed=5)
    b, _, _ = _datos_desde_generador(30, 10.0, seed=5)
    c, _, _ = _datos_desde_generador(30, 10.0, seed=6)
    assert list(a[pv]) == list(b[pv])
    if a[pv].std() > 0:                      # con ruido 0 dos semillas coinciden
        assert list(a[pv]) != list(c[pv])


# ===============================================================
# Observabilidad: como saber que la regla si esta actuando
# ===============================================================

def test_el_historial_de_disparos_sobrevive_al_anillo_de_trazas(store, kep, config_completa,
                                                                monkeypatch):
    """La traza guarda 60 ticks; en ciclo libre eso son segundos.

    Con un wait de minutos, el disparo nunca cae dentro de esa ventana y la
    pagina parece decir que la regla no actua. El historial es aparte.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    import runner

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    regla = {
        "id": "R_sube", "bloque": "estabilidad", "priority": 100.0, "weight": 1.0,
        "if": [{"OR": [(pv0, "HIGH"), (pv0, "OK"), (pv0, "LOW")]}],
        "then": [{"accion": "SUBIR_SP", "waits": [], "reiniciar_waits": []}],
    }
    monkeypatch.setattr(runner, "cargar_reglas_json", lambda path=None: [regla])
    monkeypatch.setattr(runner, "cargar_defuzzy_json",
                        lambda path=None: {sp0: {"belief_axis": [0.0, 1.0],
                                                 "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}})
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())

    motor = st.SEEngine()
    motor._piso_s = 1.0
    motor._init_state()
    motor._run_tick()

    disparos = motor.status()["ultimos_disparos"]
    assert disparos, "el disparo no quedo registrado en el historial"
    d = disparos[-1]
    assert d["regla_id"] == "R_sube"
    assert d["ts_wall"] > 0                       # hora de reloj para el 'hace X s'
    assert d["setpoints"][sp0]["delta"] > 0       # y su efecto real sobre el SP


def test_marca_el_setpoint_pegado_a_su_limite():
    """Un SP saturado absorbe cada paso: hay que decirlo, no dejarlo mudo."""
    assert st._sp_en_limite(100.0, (50.0, 100.0)) == "max"
    assert st._sp_en_limite(50.0, (50.0, 100.0)) == "min"
    assert st._sp_en_limite(75.0, (50.0, 100.0)) is None
    assert st._sp_en_limite(75.0, None) is None


# ===============================================================
# Lote de correcciones criticas del pipeline en vivo
# ===============================================================

def _regla_simple(pv, accion="SUBIR_SP", waits=None):
    return {"id": "R", "bloque": "estabilidad", "priority": 100.0, "weight": 1.0,
            "if": [{"OR": [(pv, "HIGH"), (pv, "OK"), (pv, "LOW")]}],
            "then": [{"accion": accion, "waits": waits or [], "reiniciar_waits": []}]}


def _motor_con(monkeypatch, reglas, defuzzy):
    import runner
    monkeypatch.setattr(runner, "cargar_reglas_json", lambda path=None: reglas)
    monkeypatch.setattr(runner, "cargar_defuzzy_json", lambda path=None: defuzzy)
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    motor = st.SEEngine()
    motor._piso_s = 1.0
    motor._init_state()
    return motor


def test_el_motor_en_vivo_usa_defuzzy_json_sin_pasar_por_la_simulacion(store, kep,
                                                                      config_completa,
                                                                      monkeypatch):
    """Regresion: el tick usaba el dict global con las tablas del espesador.

    La tabla configurada solo llegaba al motor si alguien corria una
    simulacion desde la web (que hacia clear()+update() sobre ese global).
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    import defuzzy_actions as _dfz

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)

    # El global sigue teniendo las tablas de fabrica: el motor NO depende de el.
    assert "SUBIR_SP" not in {a for t in _dfz.DEFUZZY_POR_FAMILIA.values()
                              for a in t.get("steps_por_accion", {})}
    sp_inicial = motor._setpoints[sp0]
    motor._run_tick()
    assert motor._setpoints[sp0] > sp_inicial


def test_una_accion_sin_tabla_se_avisa_al_arrancar_y_deja_error(store, kep,
                                                                config_completa,
                                                                monkeypatch):
    """Un disparo que no puede aplicarse tiene que verse en /api/se/status."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"OTRA": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0, accion="SUBIR_SP")], tabla)

    avisos = " | ".join(motor.status()["advertencias"])
    assert "SUBIR_SP" in avisos                      # se avisa antes de correr
    motor._run_tick()
    assert motor.status()["last_error"]              # y no queda en silencio


def test_un_setpoint_rechazado_por_el_dcs_se_reintenta(store, kep, config_completa,
                                                       monkeypatch):
    """Regresion: el error por tag se tragaba y el SP quedaba marcado escrito."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)
    tag_sp = motor._mapeo["sp_to_tag"][sp0]
    kep["rechazar"].add(tag_sp)

    motor._run_tick()
    assert tag_sp not in motor._sp_escritos, "un write rechazado no puede darse por escrito"
    assert motor.status()["last_error"], "el rechazo del DCS no puede quedar mudo"

    n_antes = len(kep["escrituras"])
    motor._run_tick()
    assert len(kep["escrituras"]) > n_antes, "el tick siguiente tiene que reintentar"


def test_un_limite_no_legible_saca_la_pv_del_fuzzy_sin_abortar(store, kep,
                                                               config_completa,
                                                               monkeypatch):
    """Regresion: se rellenaba con 0.0 y se fuzzificaba sobre una escala falsa.

    Hoy no se inventa la escala NI se tira el tick: la variable queda fuera
    del fuzzy y la regla que la nombra no puede disparar.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS

    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)
    kep["omitir"].add(f"PLANTA.LIM_{pv0}_lmax")

    sp_inicial = motor._setpoints[sp0]
    motor._run_tick()
    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] is None
    assert pv0 not in {f["var"] for f in tz["fuzzy"]}
    assert any(f"{pv0} (sin lmax)" == x for x in tz["fuzzy_omitidas"])
    assert motor._setpoints[sp0] == sp_inicial, "no se decide con limites inventados"


def test_negar_una_variable_ausente_no_dispara_la_regla():
    """Regresion (fail-open): NOT de un dato que falta valia 1.0."""
    from core.engine.motor import evaluar_condicion

    fuzzy_out = {"nivel": {"pert": {"HIGH": 1.0}}}
    # La variable existe: NOT se comporta como siempre.
    assert evaluar_condicion({"NOT": ("nivel", "HIGH")}, fuzzy_out) == 0.0
    assert evaluar_condicion({"NOT": ("nivel", "LOW")}, fuzzy_out) == 1.0
    # La variable NO existe (pendiente sin modelo, permisivo borrado): 0.0.
    assert evaluar_condicion({"NOT": ("pend_nivel", "INC")}, fuzzy_out) == 0.0
    assert evaluar_condicion({"NOT": {"OR": [("x", "ON"), ("nivel", "LOW")]}}, fuzzy_out) == 0.0


def test_una_regla_con_wait_mal_formado_no_mata_el_tick():
    """Regresion: reventaba evaluar_reglas entero, incluidas las criticas."""
    from core.engine.motor import evaluar_reglas

    rota = {"id": "ROTA", "bloque": "estabilidad", "priority": 200.0, "weight": 1.0,
            "if": [("nivel", "HIGH")],
            "then": [{"accion": "A", "waits": [{"wait_id": "w1"}]}]}   # sin duracion_s
    sana = {"id": "SANA", "bloque": "estabilidad", "priority": 10.0, "weight": 1.0,
            "if": [("nivel", "HIGH")], "then": [{"accion": "B", "waits": []}]}

    from config import BLOQUES
    out = evaluar_reglas([rota, sana], {"nivel": {"pert": {"HIGH": 1.0}}}, 0.0,
                         BLOQUES, estado_waits={}, min_belief=0.05)
    ids = {e["id"] for e in out["fired"]}
    assert "SANA" in ids, "una regla rota no puede llevarse puesto el resto del tick"
    estados = {r["id"]: r["estado"] for r in out["evaluadas"]}
    assert estados["ROTA"] == "invalida"


def test_no_se_puede_guardar_una_regla_con_wait_sin_duracion(monkeypatch, tmp_path):
    import json as _json
    import web.state as _st
    import web.api.config as cfgmod

    dfz = tmp_path / "defuzzy.json"
    dfz.write_text(_json.dumps({"velocidad_sp": {"belief_axis": [0.0, 1.0],
                                                 "steps_por_accion": {"A": [0.0, 1.0]}}}),
                   encoding="utf-8")
    monkeypatch.setattr(_st, "DEFUZZY_JSON", str(dfz))
    _fuzzy_en_disco(monkeypatch, tmp_path)

    base = {"id": "R", "bloque": "estabilidad", "if": [["hopper_nvl_pv_a", "HIGH"]]}
    _, err = cfgmod._normalizar_regla_payload(
        {**base, "then": [{"accion": "A", "waits": [{"wait_id": "w1"}]}]})
    assert err is not None and "duracion_s" in err

    _, err = cfgmod._normalizar_regla_payload(
        {**base, "then": [{"accion": "A", "waits": [{"wait_id": "w1", "duracion_s": 900}]}]})
    assert err is None, err


# ===============================================================
# Grabadores por regla
# ===============================================================

def _ev(estado="descartada", motivo="Bloqueada por wait activo", rid="R1"):
    return [{"id": rid, "bloque": "estabilidad", "priority": 100, "estado": estado,
             "motivo": motivo, "belief": 1.0, "variables_faltantes": []}]


def test_el_grabador_comprime_repeticiones_y_cuenta_disparos():
    """Repetir 900 veces 'bloqueada por wait' llenaria el buffer sin decir nada.

    Se guarda por transicion, con contador; cada disparo es entrada propia
    porque es el evento que hay que poder contar.
    """
    st.grabador_start("R1")
    try:
        for i in range(40):
            st._grabar_evaluaciones(_ev(), {}, i, i * 0.1)
        st._grabar_evaluaciones(
            _ev("disparo", "Disparo"),
            {"R1": {"acciones": ["SUBIR"], "ok": True,
                    "setpoints": {"sp": {"antes": 70.0, "despues": 75.0, "delta": 5.0}}}},
            40, 4.0)
        for i in range(10):
            st._grabar_evaluaciones(_ev(), {}, 41 + i, 4.1)

        h = st.grabador_historial("R1")
        assert h["resumen"]["eventos"] == 3          # 40 + 1 + 10 -> tres transiciones
        assert h["resumen"]["evaluaciones"] == 51    # pero se cuentan todas
        assert h["resumen"]["disparos"] == 1
        assert h["entradas"][0]["repeticiones"] == 40
        assert h["entradas"][1]["setpoints"]["sp"]["delta"] == 5.0
    finally:
        st.grabador_stop("R1")


def test_el_grabador_solo_sigue_las_reglas_pedidas_y_se_puede_detener():
    st.grabador_start("R1")
    try:
        st._grabar_evaluaciones(_ev(rid="R1") + _ev(rid="R2"), {}, 1, 0.1)
        assert st.grabador_historial("R1")["resumen"]["eventos"] == 1
        assert st.grabador_historial("R2")["resumen"]["eventos"] == 0   # no se pidio

        st.grabador_stop("R1")
        st._grabar_evaluaciones(_ev(rid="R1"), {}, 2, 0.2)
        h = st.grabador_historial("R1")
        assert h["grabando"] is False
        assert h["resumen"]["eventos"] == 1, "detenido no graba, pero conserva lo grabado"
    finally:
        st.grabador_stop("R1")


def test_grabar_de_nuevo_arranca_de_cero():
    """'Desde que se pulsa el boton': start descarta lo anterior."""
    st.grabador_start("R1")
    st._grabar_evaluaciones(_ev(), {}, 1, 0.1)
    assert st.grabador_historial("R1")["resumen"]["eventos"] == 1
    st.grabador_start("R1")
    try:
        assert st.grabador_historial("R1")["resumen"]["eventos"] == 0
    finally:
        st.grabador_stop("R1")


def test_el_buffer_de_una_regla_se_topa_en_500():
    st.grabador_start("R1")
    try:
        for i in range(600):
            # motivo distinto cada vez: fuerza una entrada nueva por tick
            st._grabar_evaluaciones(_ev(motivo="motivo %d" % i), {}, i, i * 0.1)
        h = st.grabador_historial("R1")
        assert h["resumen"]["eventos"] == 500
        assert h["resumen"]["truncado"] is True
        assert h["entradas"][-1]["motivo"] == "motivo 599"
    finally:
        st.grabador_stop("R1")


def test_varias_reglas_a_la_vez():
    """Se pueden grabar varias; la pagina muestra una por ventana."""
    st.grabador_start("R1")
    st.grabador_start("R2")
    try:
        st._grabar_evaluaciones(_ev(rid="R1") + _ev(rid="R2"), {}, 1, 0.1)
        assert set(st.grabador_estado()["activos"]) >= {"R1", "R2"}
        assert st.grabador_historial("R1")["resumen"]["eventos"] == 1
        assert st.grabador_historial("R2")["resumen"]["eventos"] == 1
    finally:
        st.grabador_stop("R1")
        st.grabador_stop("R2")


def test_grabador_limite_reglas_activas():
    """Como maximo 5 reglas grabando en paralelo."""
    ids = ["R%d" % i for i in range(6)]
    try:
        for rid in ids[:5]:
            st.grabador_start(rid)
        assert len(st.grabador_estado()["activos"]) == 5
        assert st.grabador_estado()["max_activos"] == 5
        with pytest.raises(st.GrabadorLimiteActivosError):
            st.grabador_start(ids[5])
        # Reiniciar una ya activa sigue permitido (no cuenta como sexta).
        st.grabador_start(ids[0])
        assert len(st.grabador_estado()["activos"]) == 5
    finally:
        for rid in ids[:5]:
            st.grabador_stop(rid)


# ---------------------------------------------------------------
# Roles "en uso": el criterio para avisar por un rol sin tag
# ---------------------------------------------------------------
def _cfg_en_uso(monkeypatch, tmp_path, **archivos):
    """Apunta los JSON de configuracion a un directorio temporal."""
    import json as _json
    nombres = {"fuzzy": "FUZZY_JSON", "reglas": "REGLAS_JSON",
               "permisivos": "PERMISIVOS_JSON", "variables": "VARIABLES_JSON",
               "defuzzy": "DEFUZZY_JSON", "tracking": "TRACKING_JSON"}
    for clave, const in nombres.items():
        ruta = tmp_path / f"{clave}.json"
        ruta.write_text(_json.dumps(archivos.get(clave, {} if clave != "reglas" else [])),
                        encoding="utf-8")
        monkeypatch.setattr(st, const, str(ruta))


def test_en_uso_ignora_las_pv_que_nadie_consume(monkeypatch, tmp_path):
    """Una PV declarada en el contrato que nada usa no es un faltante real."""
    from config import VARIABLES_PROCESO
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("El contrato vigente tiene una sola PV.")
    usada, ociosa = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    _cfg_en_uso(monkeypatch, tmp_path,
                fuzzy={usada: {"type": "high", "offset": [0.0, 1.0],
                               "labels": {"HIGH": [0.0, 1.0]}}})
    uso = st.roles_en_uso()
    assert usada in uso["pv"]
    assert ociosa not in uso["pv"], "una PV que nadie fuzzifica ni nombra no esta en uso"


def test_en_uso_solo_pide_el_limite_que_el_tipo_de_fuzzy_necesita(monkeypatch, tmp_path):
    """Un fuzzy `high` normaliza contra lmax: exigirle lmin era pedir un tag inutil."""
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    _cfg_en_uso(monkeypatch, tmp_path,
                fuzzy={pv: {"type": "high", "offset": [0.0, 1.0],
                            "labels": {"HIGH": [0.0, 1.0]}}})
    uso = st.roles_en_uso()
    assert f"{pv}_lmax" in uso["lim"]
    assert f"{pv}_lmin" not in uso["lim"]

    _cfg_en_uso(monkeypatch, tmp_path,
                fuzzy={pv: {"type": "norm", "offset": [0.0, 0.5, 1.0],
                            "labels": {"OK": [0.0, 1.0, 0.0]}}})
    uso = st.roles_en_uso()
    assert {f"{pv}_lmin", f"{pv}_lmax"} <= uso["lim"]


def test_en_uso_reconoce_una_pv_nombrada_solo_por_una_regla(monkeypatch, tmp_path):
    """Sin fuzzy pero nombrada en una regla: se sigue necesitando el tag."""
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    regla = {"id": "R", "bloque": "estabilidad", "if": [[pv, "HIGH"]],
             "then": ["SUBIR"], "weight": 1.0, "priority": 1, "enabled": True}
    _cfg_en_uso(monkeypatch, tmp_path, reglas=[regla])
    assert pv in st.roles_en_uso()["pv"]


def test_en_uso_ignora_una_regla_deshabilitada(monkeypatch, tmp_path):
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    regla = {"id": "R", "bloque": "estabilidad", "if": [[pv, "HIGH"]],
             "then": ["SUBIR"], "weight": 1.0, "priority": 1, "enabled": False}
    _cfg_en_uso(monkeypatch, tmp_path, reglas=[regla])
    assert pv not in st.roles_en_uso()["pv"]


def test_en_uso_no_siembra_variables_del_espesador(monkeypatch, tmp_path):
    """Con todo vacio, nadie esta en uso.

    Los `cargar_*_json()` de runner caen a la plantilla del espesador cuando
    el archivo falta; aqui eso produciria avisos por variables de otra planta.
    """
    _cfg_en_uso(monkeypatch, tmp_path)
    uso = st.roles_en_uso()
    assert uso["pv"] == set() and uso["lim"] == set() and uso["sp"] == set()


def test_en_uso_marca_el_sp_con_tabla_defuzzy(monkeypatch, tmp_path):
    from config import SETPOINT_KEYS
    sp = SETPOINT_KEYS[0]
    _cfg_en_uso(monkeypatch, tmp_path,
                defuzzy={sp: {"belief_axis": [0.0, 1.0],
                              "steps_por_accion": {"SUBIR": [0.0, 1.0]}}})
    assert sp in st.roles_en_uso()["sp"]


# ---------------------------------------------------------------
# Waits: el temporizador solo cuenta si la regla ACTUO
# ---------------------------------------------------------------
def _wait_spec(wait_id="wait::sp", dur=600.0, accion="SUBIR_SP"):
    return {"wait_id": wait_id, "tipo": "custom", "accion": accion,
            "variable_controlada": "sp", "descripcion": "", "duracion_s": dur}


def test_el_wait_arranca_cuando_la_regla_mueve_el_sp(store, kep, config_completa,
                                                     monkeypatch):
    """Camino feliz: si el SP se movio, el wait tiene que quedar corriendo."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    regla = _regla_simple(pv0, waits=[_wait_spec()])
    motor = _motor_con(monkeypatch, [regla], tabla)

    sp_inicial = motor._setpoints[sp0]
    motor._run_tick()
    assert motor._setpoints[sp0] > sp_inicial
    assert "wait::sp" in motor._last_action_time, "el wait debe quedar armado"
    tz = st._get_trazas(1)[0]
    assert tz["disparadas"][0]["movio_sp"] is True
    assert tz["disparadas"][0]["waits_revertidos"] == []
    assert any(w["wait_id"] == "wait::sp" for w in tz["waits_activos"])


def test_el_wait_no_arranca_si_el_sp_esta_saturado_en_su_limite(store, kep,
                                                                config_completa,
                                                                monkeypatch):
    """El caso del estandar: SP pegado al limite, el clipeo se come el paso.

    La regla dispara, el defuzzy calcula, el valor no se mueve. Dejar el wait
    armado la silenciaba minutos por una accion que nunca ocurrio.
    """
    import config as cfg
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    # El SP del DCS (42.0) ya es el tope: cualquier paso hacia arriba se clipea.
    config_completa(limites_sp={k: [0.0, kep["sp_en_dcs"]] for k in SETPOINT_KEYS})
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    regla = _regla_simple(pv0, waits=[_wait_spec()])
    motor = _motor_con(monkeypatch, [regla], tabla)

    sp_inicial = motor._setpoints[sp0]
    motor._run_tick()
    assert motor._setpoints[sp0] == sp_inicial, "el clipeo tiene que dejarlo igual"
    assert "wait::sp" not in motor._last_action_time, (
        "un disparo sin efecto no puede reiniciar el wait")
    tz = st._get_trazas(1)[0]
    assert tz["disparadas"][0]["movio_sp"] is False
    assert tz["disparadas"][0]["waits_revertidos"] == ["wait::sp"]
    assert tz["waits_activos"] == [], "la foto de waits debe ser posterior al revert"


def test_la_regla_saturada_puede_reintentar_al_tick_siguiente(store, kep,
                                                              config_completa,
                                                              monkeypatch):
    """Consecuencia practica: apenas el SP deja de estar en el tope, actua.

    Con el wait armado por un disparo sin efecto, la regla quedaba muda
    aunque el limite se ampliara.
    """
    import config as cfg
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tope = kep["sp_en_dcs"]
    config_completa(limites_sp={k: [0.0, tope] for k in SETPOINT_KEYS})
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0, waits=[_wait_spec()])], tabla)

    motor._run_tick()                       # saturado: no mueve, no arma wait
    assert motor._setpoints[sp0] == tope
    # El operador amplia el rango. No se pisa `motor._limites_sp`: ese dict se
    # recalcula en cada tick, asi que escribirlo a mano no sobrevive la vuelta
    # siguiente. Se toca el RESPALDO, que es de donde sale — y se refresca su
    # foto a mano porque la configuracion la toma el motor en su proximo
    # `start()`, igual que todo lo demas. Lo que se lee en vivo son los tags.
    config_completa(limites_sp={k: [0.0, tope + 50.0] for k in SETPOINT_KEYS})
    motor._limites_num_sp = st.limites_num(st.DEFUZZY_JSON)
    motor._run_tick()
    assert motor._setpoints[sp0] > tope, "sin el wait espurio, la regla actua ya"


def test_una_accion_que_falla_no_reinicia_el_wait(store, kep, config_completa,
                                                  monkeypatch):
    """Si apply_actions revienta, el wait no puede quedar armado igual."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    # Tabla sin la accion de la regla: apply_actions_tabla lanza.
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"OTRA": [0.0, 5.0]}}}
    regla = _regla_simple(pv0, accion="SUBIR_SP", waits=[_wait_spec()])
    motor = _motor_con(monkeypatch, [regla], tabla)

    motor._run_tick()
    assert motor.status()["last_error"], "el fallo tiene que verse"
    assert "wait::sp" not in motor._last_action_time, (
        "una accion que fallo no actuo: su wait no se reinicia")


def test_revertir_conserva_la_cuenta_de_un_wait_que_ya_venia_corriendo():
    """Revertir restaura el estado exacto, no borra a ciegas.

    Si el wait ya lo habia armado otra regla, deshacer el armado espurio no
    puede regalarle a nadie un wait limpio.
    """
    from core.engine.motor import _activar_waits, revertir_waits

    estado = {}
    spec = [{"wait_id": "W", "duracion_s": 600.0, "variable_controlada": "sp"}]
    _activar_waits(spec, estado, t_s=100.0, regla_id="A")
    original = dict(estado["W"])

    evento = {"waits_previos": _activar_waits(spec, estado, t_s=500.0, regla_id="B")}
    assert estado["W"]["t_ultima_activacion_s"] == 500.0
    assert revertir_waits(evento, estado) == ["W"]
    assert estado["W"] == original, "tiene que volver el wait de A, con su hora"


def test_revertir_borra_el_wait_que_no_existia_antes():
    from core.engine.motor import _activar_waits, revertir_waits

    estado = {}
    spec = [{"wait_id": "W", "duracion_s": 600.0, "variable_controlada": "sp"}]
    evento = {"waits_previos": _activar_waits(spec, estado, t_s=10.0, regla_id="A")}
    assert "W" in estado
    revertir_waits(evento, estado)
    assert "W" not in estado


def test_el_wait_sigue_bloqueando_dentro_del_mismo_barrido(store, kep, config_completa,
                                                           monkeypatch):
    """Alcance del cambio: el armado sigue siendo en el disparo.

    Dos reglas que comparten wait no pueden disparar las dos en el mismo tick
    solo porque el revert ocurra despues.
    """
    from core.engine.motor import evaluar_reglas
    from config import BLOQUES

    wait = _wait_spec(dur=600.0)
    reglas = [
        {"id": "A", "bloque": "optimizacion", "priority": 200.0, "weight": 1.0,
         "if": [("v", "HIGH")],
         "then": [{"accion": "SUBIR_SP", "waits": [wait], "reiniciar_waits": []}]},
        {"id": "B", "bloque": "optimizacion", "priority": 100.0, "weight": 1.0,
         "if": [("v", "HIGH")],
         "then": [{"accion": "SUBIR_SP", "waits": [wait], "reiniciar_waits": []}]},
    ]
    out = evaluar_reglas(reglas, {"v": {"pert": {"HIGH": 1.0}}}, t_s=0.0,
                         bloques=BLOQUES, estado_waits={})
    assert [e["id"] for e in out["fired"]] == ["A"]


# ---------------------------------------------------------------
# Variables calculadas EN VIVO
# ---------------------------------------------------------------
def test_online_aritmetica_y_encadenado():
    """Una calculada puede alimentar a la siguiente, en el orden declarado."""
    from core.variables.online import VariablesOnline

    vc = VariablesOnline([
        {"nombre": "suma", "tipo": "aritmetica", "operacion": "suma", "args": ["a", "b"]},
        {"nombre": "doble", "tipo": "aritmetica", "operacion": "multiplicacion",
         "args": ["suma", "dos"]},
    ])
    calc, omit = vc.actualizar({"a": 3.0, "b": 4.0, "dos": 2.0}, t_s=0.0)
    assert calc == {"suma": 7.0, "doble": 14.0}
    assert omit == []


def test_online_una_fuente_ausente_no_tumba_las_demas():
    """Regresion del modo batch: alli un argumento faltante lanza KeyError.

    En vivo eso se llevaria el tick entero por una sola PV con calidad mala.
    """
    from core.variables.online import VariablesOnline

    vc = VariablesOnline([
        {"nombre": "buena", "tipo": "aritmetica", "operacion": "suma", "args": ["a", "b"]},
        {"nombre": "rota", "tipo": "aritmetica", "operacion": "suma", "args": ["a", "no_existe"]},
    ])
    calc, omit = vc.actualizar({"a": 1.0, "b": 2.0}, t_s=0.0)
    assert calc == {"buena": 3.0}
    assert any("rota" in x and "no_existe" in x for x in omit)


def test_online_division_por_cero_no_produce_la_variable():
    """Un NaN fuzzificado da pertenencias sin sentido: mejor no producirla."""
    from core.variables.online import VariablesOnline

    vc = VariablesOnline([{"nombre": "r", "tipo": "aritmetica",
                           "operacion": "division", "args": ["a", "b"]}])
    calc, omit = vc.actualizar({"a": 1.0, "b": 0.0}, t_s=0.0)
    assert calc == {}
    assert omit and "r" in omit[0]


def test_online_la_ventana_se_mide_en_segundos_no_en_muestras():
    """En ciclo libre el periodo cambia tick a tick: contar muestras no sirve.

    Se alimenta la misma serie a dos cadencias muy distintas y el delta de la
    ventana tiene que dar lo mismo.
    """
    from core.variables.online import VariablesOnline

    defn = [{"nombre": "d", "tipo": "rolling_delta", "arg": "x", "ventana_min": 1.0}]

    def _correr(paso_s):
        vc = VariablesOnline(defn)
        ultimo = 0.0
        t = 0.0
        while t <= 60.0:
            calc, _ = vc.actualizar({"x": t}, t_s=t)   # rampa de 1 unidad/s
            ultimo = calc["d"]
            t += paso_s
        return ultimo

    lento, rapido = _correr(5.0), _correr(0.1)
    # 60 s de rampa a 1 u/s ~ 60 de delta, con la tolerancia de la decimacion.
    assert abs(lento - 60.0) < 6.0
    assert abs(rapido - 60.0) < 6.0
    assert abs(lento - rapido) < 6.0, "la ventana no puede depender de la cadencia"


def test_online_el_buffer_no_crece_con_la_velocidad_del_lazo():
    """Una ventana de 30 min a 10 tick/s serian 18.000 muestras por variable."""
    from core.variables.online import VariablesOnline, MAX_MUESTRAS

    vc = VariablesOnline([{"nombre": "s", "tipo": "rolling_std", "arg": "x",
                           "ventana_min": 30.0}], max_muestras=50)
    t = 0.0
    while t < 1800.0:
        vc.actualizar({"x": t % 7}, t_s=t)
        t += 0.05
    assert len(vc._hist["s"]) <= 51


def _con_calculadas(monkeypatch, tmp_path, defs, fuzzy=None, reglas=None):
    """Apunta variables.json (y opcionalmente fuzzy/reglas) a un temporal."""
    import json as _json
    for clave, const, datos in (("variables", "VARIABLES_JSON",
                                 {"crudas": {}, "definiciones": defs}),
                                ("fuzzy", "FUZZY_JSON", fuzzy or {}),
                                ("reglas", "REGLAS_JSON", reglas or [])):
        ruta = tmp_path / f"{clave}.json"
        ruta.write_text(_json.dumps(datos), encoding="utf-8")
        monkeypatch.setattr(st, const, str(ruta))


def test_el_motor_en_vivo_calcula_las_variables_de_variables_json(store, kep,
                                                                  config_completa,
                                                                  monkeypatch, tmp_path):
    """Regresion: `calcular_variables_df` solo se llamaba en el runner de CSV.

    Las definiciones de variables.json estaban muertas en el camino online:
    se podian crear en la interfaz y el motor nunca las producia.
    """
    from config import VARIABLES_PROCESO
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV para probar una aritmetica.")
    a, b = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    st._reset_trazas()
    _con_calculadas(monkeypatch, tmp_path, [
        {"nombre": "suma_pv", "tipo": "aritmetica", "operacion": "suma", "args": [a, b]},
    ])
    eng = _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert eng._vars_calc.nombres == ["suma_pv"]
    assert tz["derivadas"]["suma_pv"] == pytest.approx(VALORES[a] + VALORES[b])
    assert tz["derivadas_omitidas"] == []


def test_una_calculada_se_fuzzifica_con_sus_limites_de_respaldo(store, kep,
                                                                config_completa,
                                                                monkeypatch, tmp_path):
    """Una calculada no tiene tag propio: sus limites salen de variables.json.

    Es lo que la hace usable en una regla sin obligar a crear dos tags en el
    DCS por una variable puramente interna.
    """
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    st._reset_trazas()
    _con_calculadas(
        monkeypatch, tmp_path,
        [{"nombre": "doble_pv", "tipo": "aritmetica", "operacion": "suma",
          "args": [pv, pv], "lmin": 0.0, "lmax": 200.0}],
        fuzzy={"doble_pv": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                            "labels": {"HIGH": [1.0, 0.5, 0.0],
                                       "OK": [0.0, 1.0, 0.0],
                                       "LOW": [0.0, 0.5, 1.0]}}},
    )
    import runner
    monkeypatch.setattr(runner, "cargar_fuzzy_json",
                        lambda path=None: {
                            "doble_pv": {"type": "norm", "offset": [0.0, 0.5, 1.0],
                                         "labels": {"HIGH": [1.0, 0.5, 0.0],
                                                    "OK": [0.0, 1.0, 0.0],
                                                    "LOW": [0.0, 0.5, 1.0]}}})
    eng = _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    fila = next((f for f in tz["fuzzy"] if f["var"] == "doble_pv"), None)
    assert fila is not None, "la calculada tiene que entrar al fuzzy"
    assert (fila["lmin"], fila["lmax"]) == (0.0, 200.0)
    assert fila["pert"], "debe producir pertenencias"


def test_una_calculada_es_variable_valida_en_una_regla(monkeypatch, tmp_path):
    """Antes el catalogo ofrecia VARIABLES_EXTERNAS, la lista del espesador.

    Una calculada creada en la interfaz no existia para el validador: se podia
    definir y no se podia usar.
    """
    _con_calculadas(monkeypatch, tmp_path,
                    [{"nombre": "mi_calculada", "tipo": "aritmetica",
                      "operacion": "suma", "args": ["x", "y"]}])
    assert "mi_calculada" in st.variables_validas()
    assert "mi_calculada" in st.variables_disponibles()


def test_una_calculada_en_uso_arrastra_a_sus_pv_de_origen(monkeypatch, tmp_path):
    """Nadie nombra la PV, pero sin ella la calculada no se puede calcular."""
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    _cfg_en_uso(monkeypatch, tmp_path,
                variables={"crudas": {}, "definiciones": [
                    {"nombre": "derivada", "tipo": "aritmetica", "operacion": "suma",
                     "args": [pv, pv]}]},
                reglas=[{"id": "R", "bloque": "estabilidad", "enabled": True,
                         "if": [["derivada", "HIGH"]], "then": ["SUBIR"]}])
    uso = st.roles_en_uso()
    assert "derivada" in uso["calculada"]
    assert pv in uso["pv"], "la PV de origen queda en uso aunque nadie la nombre"


def test_los_roles_lim_incluyen_a_las_calculadas(monkeypatch, tmp_path):
    """Para poder cablear sus limites al DCS si viven alla."""
    _con_calculadas(monkeypatch, tmp_path,
                    [{"nombre": "calc_x", "tipo": "aritmetica",
                      "operacion": "suma", "args": ["a", "b"]}])
    lims = st.catalogo_roles()["lim"]
    assert "calc_x_lmin" in lims and "calc_x_lmax" in lims


def test_una_calculada_sin_su_fuente_no_se_calcula_y_el_tick_sigue(store, kep,
                                                                   config_completa,
                                                                   monkeypatch, tmp_path):
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    st._reset_trazas()
    kep["omitir"].add(f"PLANTA.PV_{pv}")
    _con_calculadas(monkeypatch, tmp_path,
                    [{"nombre": "depende", "tipo": "aritmetica", "operacion": "suma",
                      "args": [pv, pv]}])
    _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert tz["abortado_en"] is None
    assert "depende" not in tz["derivadas"]
    assert any("depende" in x for x in tz["derivadas_omitidas"])


# ---------------------------------------------------------------
# Fuzzy de PENDIENTE configurable
# ---------------------------------------------------------------
_EJE_PEND = [-6.0, -2.0, 0.0, 2.0, 6.0]
_FILAS_PEND = {"DEC": [1.0, 0.5, 0.0, 0.0, 0.0],
               "STABLE": [0.0, 0.5, 1.0, 0.5, 0.0],
               "INC": [0.0, 0.0, 0.0, 0.5, 1.0]}


def _pend_cfg(variable, ventana_min):
    return {"variable": variable, "ventana_min": ventana_min,
            "x": list(_EJE_PEND), "labels": {k: list(v) for k, v in _FILAS_PEND.items()}}


def _alimentar(p, var, pendiente_por_min, hasta_s, paso_s=0.5):
    """Corre una rampa de `pendiente_por_min` u/min y devuelve la ultima salida."""
    out, omit, t = {}, [], 0.0
    while t <= hasta_s:
        out, omit = p.actualizar({var: pendiente_por_min * (t / 60.0)}, t_s=t)
        t += paso_s
    return out, omit


def test_la_ventana_de_la_pendiente_es_configurable():
    """Regresion: `evaluar_pendiente_var` tenia ventana_s=60.0 y nadie se la pasaba.

    Toda pendiente medía exactamente un minuto, dijera lo que dijera la config.
    """
    from core.fuzzy.pendientes import PendientesOnline

    p = PendientesOnline({"corta": _pend_cfg("x", 0.5), "larga": _pend_cfg("x", 5.0)})
    out, _ = _alimentar(p, "x", 4.0, hasta_s=400.0)
    assert out["corta"]["ventana_s"] == 30.0
    assert out["larga"]["ventana_s"] == 300.0


def test_varias_pendientes_sobre_la_misma_variable():
    """El requisito literal del estandar: la clave era `pend_<var>`, una sola."""
    from core.fuzzy.pendientes import PendientesOnline

    p = PendientesOnline({"arranque": _pend_cfg("torque", 1.0),
                          "deriva": _pend_cfg("torque", 10.0)})
    assert p.variables_fuente() == {"torque"}
    out, _ = _alimentar(p, "torque", 3.0, hasta_s=700.0, paso_s=1.0)
    assert set(out) == {"arranque", "deriva"}
    assert out["arranque"]["ventana_s"] != out["deriva"]["ventana_s"]


def test_la_pendiente_no_se_afirma_hasta_cubrir_la_ventana():
    """Reportar STABLE sin historia es afirmar algo que el motor no sabe."""
    from core.fuzzy.pendientes import PendientesOnline

    p = PendientesOnline({"tend": _pend_cfg("x", 1.0)})     # 60 s
    out, omit = p.actualizar({"x": 0.0}, t_s=0.0)
    assert out == {}
    assert omit and "ventana incompleta" in omit[0]

    out, omit = _alimentar(p, "x", 5.0, hasta_s=30.0)
    assert out == {}, "a mitad de ventana todavia no se puede afirmar"

    out, omit = _alimentar(p, "x", 5.0, hasta_s=70.0)
    assert "tend" in out and omit == []


def test_la_pendiente_mide_en_unidades_por_minuto():
    from core.fuzzy.pendientes import PendientesOnline

    p = PendientesOnline({"tend": _pend_cfg("x", 1.0)})
    out, _ = _alimentar(p, "x", 4.0, hasta_s=120.0)
    assert out["tend"]["slope_per_min"] == pytest.approx(4.0, abs=0.2)
    assert out["tend"]["dom"] == "INC"

    p2 = PendientesOnline({"tend": _pend_cfg("x", 1.0)})
    out2, _ = _alimentar(p2, "x", -4.0, hasta_s=120.0)
    assert out2["tend"]["dom"] == "DEC"


def test_la_pendiente_sin_su_variable_no_se_produce():
    from core.fuzzy.pendientes import PendientesOnline

    p = PendientesOnline({"tend": _pend_cfg("no_existe", 0.1)})
    out, omit = p.actualizar({"otra": 1.0}, t_s=0.0)
    assert out == {}
    assert omit and "no_existe" in omit[0]


def test_una_pendiente_mal_formada_no_rompe_el_registry():
    """Una config a medio escribir no puede dejar a la planta sin experto."""
    from core.fuzzy.pendientes import construir_registry_pendientes

    reg = construir_registry_pendientes({
        "buena": _pend_cfg("x", 1.0),
        "sin_variable": {"ventana_min": 1.0, "x": _EJE_PEND, "labels": _FILAS_PEND},
        "fila_corta": {"variable": "x", "ventana_min": 1.0, "x": _EJE_PEND,
                       "labels": {"INC": [1.0, 0.0]}},
        "ventana_cero": {"variable": "x", "ventana_min": 0.0, "x": _EJE_PEND,
                         "labels": _FILAS_PEND},
    })
    assert set(reg) == {"buena"}


def _con_pendientes(monkeypatch, tmp_path, cfg, reglas=None):
    import json as _json
    for clave, const, datos in (("pendientes", "PENDIENTES_JSON", cfg),
                                ("reglas", "REGLAS_JSON", reglas or [])):
        ruta = tmp_path / f"{clave}.json"
        ruta.write_text(_json.dumps(datos), encoding="utf-8")
        monkeypatch.setattr(st, const, str(ruta))


def test_una_pendiente_es_variable_valida_en_una_regla(monkeypatch, tmp_path):
    """Y los `pend_<var>` automaticos dejan de ofrecerse.

    Ofrecer `pend_<pv>` para toda PV era ofrecer una variable que el motor no
    podia producir: la regla se guardaba y quedaba `no_evaluable` para siempre.
    """
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    _con_pendientes(monkeypatch, tmp_path, {"mi_tendencia": _pend_cfg(pv, 5.0)})
    validas = st.variables_validas()
    assert "mi_tendencia" in validas
    assert f"pend_{pv}" not in validas, "ya no se ofrecen pendientes que nadie declaro"


def test_las_etiquetas_de_una_pendiente_entran_al_catalogo(monkeypatch, tmp_path):
    """Las filas son libres, igual que en un fuzzy de PV."""
    from config import VARIABLES_PROCESO
    cfg = _pend_cfg(VARIABLES_PROCESO[0], 5.0)
    cfg["labels"] = {"SUBIENDO_RAPIDO": [0.0, 0.0, 0.0, 0.5, 1.0]}
    _con_pendientes(monkeypatch, tmp_path, {"t": cfg})
    etiquetas = st.etiquetas_disponibles()
    assert "SUBIENDO_RAPIDO" in etiquetas
    assert "NO-SUBIENDO_RAPIDO" in etiquetas


def test_una_pendiente_en_uso_arrastra_a_su_variable(monkeypatch, tmp_path):
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    _cfg_en_uso(monkeypatch, tmp_path,
                reglas=[{"id": "R", "bloque": "estabilidad", "enabled": True,
                         "if": [["tend", "INC"]], "then": ["SUBIR"]}])
    ruta = tmp_path / "pendientes.json"
    import json as _json
    ruta.write_text(_json.dumps({"tend": _pend_cfg(pv, 5.0)}), encoding="utf-8")
    monkeypatch.setattr(st, "PENDIENTES_JSON", str(ruta))
    uso = st.roles_en_uso()
    assert "tend" in uso["pendiente"]
    assert pv in uso["pv"], "sin la PV la tendencia no se puede calcular"


def test_el_motor_en_vivo_evalua_las_pendientes_de_pendientes_json(store, kep,
                                                                   config_completa,
                                                                   monkeypatch, tmp_path):
    """Regresion: PEND_MODELOS estaba hardcodeado con las variables del espesador."""
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    st._reset_trazas()
    # Ventana de 0,6 s: se cubre en un segundo de ticks. Tiene que ser mayor
    # que el periodo del lazo, si no la ventana nunca junta min_puntos.
    _con_pendientes(monkeypatch, tmp_path, {"tend_pv": _pend_cfg(pv, 0.01)})
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    assert eng._pendientes.nombres == ["tend_pv"]
    for _ in range(12):
        eng._run_tick()
        time.sleep(0.1)
    tz = st._get_trazas(1)[0]
    fila = next((f for f in tz["fuzzy"] if f["var"] == "tend_pv"), None)
    assert fila is not None, "la pendiente configurada tiene que entrar al fuzzy_out"
    assert fila["es_pendiente"] is True
    assert fila["fuente"] == pv
    assert fila["pert"], "debe producir pertenencias"


def test_la_pendiente_recibe_sus_etiquetas_compuestas(store, kep, config_completa,
                                                      monkeypatch, tmp_path):
    """Una regla tiene que poder escribir ("tendencia", "NO-INC").

    Por eso las pendientes se mezclan ANTES de expandir_etiquetas_compuestas.
    """
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    st._reset_trazas()
    _con_pendientes(monkeypatch, tmp_path, {"tend_pv": _pend_cfg(pv, 0.01)})
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    eng = st.SEEngine()
    eng._init_state()
    for _ in range(12):
        eng._run_tick()
        time.sleep(0.1)
    tz = st._get_trazas(1)[0]
    fila = next(f for f in tz["fuzzy"] if f["var"] == "tend_pv")
    assert any(k.startswith("NO-") for k in fila["pert"]) or fila["pert"]


# ---------------------------------------------------------------
# Tracking SP -> readback
# ---------------------------------------------------------------
def _motor_con_tracking(monkeypatch, tmp_path, tracking, reglas, defuzzy):
    import json as _json
    ruta = tmp_path / "tracking.json"
    ruta.write_text(_json.dumps(tracking), encoding="utf-8")
    monkeypatch.setattr(st, "TRACKING_JSON", str(ruta))
    return _motor_con(monkeypatch, reglas, defuzzy)


def test_el_tracking_retiene_la_familia_hasta_que_el_proceso_alcanza(store, kep,
                                                                     config_completa,
                                                                     monkeypatch, tmp_path):
    """Regresion B3.6: `tracking.json` se guardaba y nadie lo leia en runtime.

    Con el readback lejos del setpoint escrito, seguir empujando acumula un
    salto que llega de golpe cuando el proceso se pone al dia.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV: una para la regla y otra de readback.")
    pv, rb = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    sp0 = SETPOINT_KEYS[0]
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    # rango 0.1 con el readback en 50 y el SP del DCS en 42: siempre desviado.
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": rb, "rango": 0.1, "habilitado": True}},
        [_regla_simple(pv, waits=[_wait_spec()])], tabla)

    motor._run_tick()                       # actua y escribe
    sp_tras_primero = motor._setpoints[sp0]
    motor._last_action_time.clear()         # aislar el efecto del wait
    motor._run_tick()                       # ahora el tracking retiene

    tz = st._get_trazas(1)[0]
    assert motor._sp_retenidos, "el readback esta lejos: hay que retener"
    assert motor._setpoints[sp0] == sp_tras_primero, "la accion no puede acumularse"
    assert [d["sp"] for d in tz["tracking"]] == [sp0]
    assert tz["disparadas"][0]["movio_sp"] is False


def test_el_tracking_impide_que_el_wait_se_reinicie(store, kep, config_completa,
                                                    monkeypatch, tmp_path):
    """La mitad que faltaba de la etapa 7 del estandar."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV.")
    pv, rb = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    sp0 = SETPOINT_KEYS[0]
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": rb, "rango": 0.1, "habilitado": True}},
        [_regla_simple(pv, waits=[_wait_spec()])], tabla)

    motor._run_tick()
    motor._last_action_time.clear()
    motor._run_tick()
    assert "wait::sp" not in motor._last_action_time, (
        "retenida por tracking la regla no actuo: su wait no se reinicia")


def test_sin_readback_legible_el_tracking_es_fail_closed(store, kep, config_completa,
                                                         monkeypatch, tmp_path):
    """Sin poder verificar que el proceso responde, no se empuja.

    El readback se cae en el primer tick, asi que no hay ultimo valor bueno que
    retener y el fail-closed se ejecuta de verdad. El caso del readback
    que se cae DESPUES de haber estado bien lo cubre el test siguiente.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV.")
    pv, rb = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    sp0 = SETPOINT_KEYS[0]
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": rb, "rango": 999.0, "habilitado": True}},
        [_regla_simple(pv)], tabla)

    sp_previo = motor._setpoints[sp0]
    kep["omitir"].add(f"PLANTA.PV_{rb}")    # nunca se pudo leer
    motor._run_tick()
    assert motor._sp_retenidos, "sin readback no se puede verificar: no se empuja"
    assert "no se pudo leer" in motor._sp_retenidos[sp0]
    assert motor._setpoints[sp0] == sp_previo


def test_un_parpadeo_del_readback_no_congela_el_tracking(store, kep, config_completa,
                                                         monkeypatch, tmp_path):
    """Interaccion de la retencion con el tracking, y es la que se busca.

    El fail-closed del tracking existe para el caso "no puedo verificar". Un
    parpadeo de un tick no es ese caso: la retencion sostiene el readback, el
    tracking lo verifica igual y el lazo no se congela por un hipo del
    KEPserver. Recien cuando vence la retencion no hay nada que verificar.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV.")
    pv, rb = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    sp0 = SETPOINT_KEYS[0]
    st._reset_trazas()
    monkeypatch.setattr(st._kep, "get_retencion_s", lambda: 5.0)
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": rb, "rango": 999.0, "habilitado": True}},
        [_regla_simple(pv)], tabla)

    motor._run_tick()                       # rango enorme: no retiene
    assert not motor._sp_retenidos

    kep["omitir"].add(f"PLANTA.PV_{rb}")    # parpadeo del readback
    motor._run_tick()
    assert not motor._sp_retenidos, "la retencion sostiene el readback"

    motor._t0 -= 6.0                        # vence la retencion
    motor._run_tick()
    assert motor._sp_retenidos, "sin nada que retener, vuelve el fail-closed"
    assert "no se pudo leer" in motor._sp_retenidos[sp0]


# ---------------------------------------------------------------
# Rate limit por SP (#5 de CLAUDE.md)
# ---------------------------------------------------------------
def _motor_con_rate(monkeypatch, ajustar, rate, reglas, defuzzy):
    """Motor con un limite de velocidad por familia de SP.

    El rate va por el contrato temporal y no por `monkeypatch.setitem`: el
    `recargar_contrato()` de `_init_state()` vacia `RATE_SP_CONTRATO` y lo
    repuebla desde el archivo, asi que un setitem no sobrevive al arranque.
    """
    ajustar(rate_sp={k: float(v) for k, v in rate.items()})
    return _motor_con(monkeypatch, reglas, defuzzy)


def test_el_rate_limit_rampea_en_vez_de_descartar(store, kep, config_completa,
                                                  monkeypatch):
    """La decision central: el paso que no cabe se ENTREGA despues.

    Descartarlo (lo que hace el tracking, a proposito) perderia la decision del
    experto. Un rate limit no discute el objetivo, solo la velocidad: el SP
    interno avanza completo y al DCS le llega en varios tramos.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tag = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 10.0]}}}
    motor = _motor_con_rate(monkeypatch, config_completa, {sp0: 1.0}, [_regla_simple(pv)], tabla)

    motor._run_tick()                       # arranque bumpless: no rampea
    escrito_1 = motor._sp_escritos[tag]
    objetivo_1 = motor._setpoints[sp0]

    motor._t0 -= 0.5                        # 0.5 s de presupuesto a 1 u/s
    motor._run_tick()

    objetivo_2 = motor._setpoints[sp0]
    escrito_2 = motor._sp_escritos[tag]
    assert objetivo_2 > objetivo_1, "el setpoint INTERNO avanza completo"
    assert escrito_2 < objetivo_2, "al DCS le llega recortado"
    assert escrito_2 - escrito_1 == pytest.approx(0.5, abs=0.15), (
        "el paso escrito es rate x dt, no el paso entero")

    tz = st._get_trazas(1)[0]
    rampa = next(r for r in tz["escritura"]["rampas"] if r["tag"] == tag)
    assert rampa["falta"] > 0, "la traza tiene que decir cuanto queda por entregar"


def test_la_rampa_termina_de_entregar_el_objetivo(store, kep, config_completa,
                                                  monkeypatch):
    """Lo que falta no se pierde: el write-on-change lo reintenta solo.

    Mientras quede diferencia entre el objetivo y lo escrito, el tag sigue
    apareciendo como cambiado, asi que la rampa avanza sin codigo extra.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tag = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    # Sin regla: el objetivo se fija a mano y no se mueve mas.
    motor = _motor_con_rate(monkeypatch, config_completa, {sp0: 1.0}, [], {})
    motor._run_tick()

    objetivo = motor._sp_escritos[tag] + 3.0
    motor._setpoints[sp0] = objetivo
    for _ in range(4):                      # 4 tramos de 1 s a 1 u/s
        motor._t0 -= 1.0
        motor._run_tick()

    assert motor._sp_escritos[tag] == pytest.approx(objetivo, abs=0.3), (
        "la rampa tiene que llegar al objetivo, no quedarse a mitad")


def test_sin_rate_declarado_el_sp_viaja_de_un_salto(store, kep, config_completa,
                                                    monkeypatch):
    """Vacio = sin limite de velocidad, que es el comportamiento previo."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tag = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 10.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv)], tabla)

    motor._run_tick()
    motor._t0 -= 0.05
    motor._run_tick()

    assert motor._sp_escritos[tag] == pytest.approx(motor._setpoints[sp0]), (
        "sin rate declarado, lo escrito iguala al objetivo")
    assert st._get_trazas(1)[0]["escritura"]["rampas"] == []


def test_una_rampa_no_revierte_el_wait_de_la_regla(store, kep, config_completa,
                                                   monkeypatch):
    """Interaccion con *el wait cuenta solo si la regla actuo*.

    Va al revES que el tracking, y es el motivo por el que el rate limit se
    aplica al ESCRIBIR y no al calcular. El tracking descarta el paso, el SP
    interno no se mueve y el wait se revierte — correcto, la regla no actuo.
    Una rampa si actuo: el objetivo cambio y el efecto esta en camino. Si el
    rate limit tocara `_setpoints`, el wait se revertiria y la regla volveria a
    disparar en cada tick de la rampa.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 10.0]}}}
    motor = _motor_con_rate(monkeypatch, config_completa, {sp0: 0.1}, [_regla_simple(pv)], tabla)

    motor._run_tick()
    motor._t0 -= 1.0
    motor._run_tick()

    disparo = motor._last_events[-1]
    assert disparo["movio_sp"] is True, "la regla actuo: su efecto va en rampa"
    assert disparo["waits_revertidos"] == []


def test_el_primer_tick_no_rampea(store, kep, config_completa, monkeypatch):
    """El arranque bumpless parte del valor vigente en el DCS.

    Rampear hacia un valor que el DCS ya tiene no tiene sentido, y ademas
    dejaria al SE escribiendo tramos durante el arranque.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    tag = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    motor = _motor_con_rate(monkeypatch, config_completa, {sp0: 0.01}, [], {})
    motor._run_tick()

    assert motor._sp_escritos[tag] == pytest.approx(motor._setpoints[sp0])
    assert st._get_trazas(1)[0]["escritura"]["rampas"] == []


def test_una_pausa_larga_no_compra_un_salto_grande(store, kep, config_completa,
                                                   monkeypatch):
    """El presupuesto de rampa esta TOPEADO: una pausa no autoriza un salto.

    Es el caso que se descubrio verificando en el contenedor. Mientras el SP
    esta retenido (tracking, handshake, write rechazado) el reloj de la rampa
    no avanza, asi que al liberarse el presupuesto acumulado seria enorme y el
    DCS recibiria de un golpe todo lo que no se mando. Con el tope, el paso de
    un solo write no supera `rate` x RATE_DT_MAX_S.
    """
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    tag = f"PLANTA.SP_{sp0}"
    st._reset_trazas()
    motor = _motor_con_rate(monkeypatch, config_completa, {sp0: 1.0}, [], {})
    motor._run_tick()
    base = motor._sp_escritos[tag]

    kep["rechazar"].add(tag)
    motor._setpoints[sp0] = base + 30.0
    motor._t0 -= 1.0
    motor._run_tick()
    assert motor._sp_escritos[tag] == base, "rechazado: la referencia no se mueve"

    kep["rechazar"].discard(tag)
    motor._t0 -= 20.0                       # 20 s de pausa
    motor._run_tick()
    avance = motor._sp_escritos[tag] - base
    tope = 1.0 * st.RATE_DT_MAX_S
    assert avance <= tope + 1e-6, (
        f"tras 20 s de pausa el SP avanzo {avance}, mas que el tope {tope}")
    assert avance == pytest.approx(tope, rel=0.05), "y avanza el tope completo"


def test_una_familia_con_tracking_deshabilitado_no_retiene(store, kep, config_completa,
                                                           monkeypatch, tmp_path):
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV.")
    pv, rb = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    sp0 = SETPOINT_KEYS[0]
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": rb, "rango": 0.1, "habilitado": False}},
        [_regla_simple(pv)], tabla)
    assert motor._tracking == {}
    motor._run_tick()
    motor._run_tick()
    assert motor._sp_retenidos == {}


def test_tracking_sin_readback_declarado_no_hace_nada(store, kep, config_completa,
                                                      monkeypatch, tmp_path):
    """`pv_key` vacio = "sin readback": esa familia no se verifica."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": "", "rango": 0.3, "habilitado": True}},
        [_regla_simple(pv)], tabla)
    assert motor._tracking == {}
    motor._run_tick()
    assert motor._sp_retenidos == {}


# ---------------------------------------------------------------
# Recarga en caliente del contrato
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# Escritura atomica y lock de tags.json (B3.4)
# ---------------------------------------------------------------
def test_un_fallo_a_mitad_de_escritura_no_corrompe_el_archivo(tmp_path):
    """El modo de falla que esto evita no es perder la ultima edicion.

    `open(ruta, "w")` trunca ANTES de escribir. Si el dump falla a mitad, en
    disco queda un JSON incompleto, el loader lo da por corrupto y cae a la
    plantilla de Python: la planta arranca con la configuracion de OTRA planta.
    """
    import json as _json
    from core.jsonio import escribir_json_atomico

    ruta = tmp_path / "cfg.json"
    escribir_json_atomico(str(ruta), {"bueno": 1})

    class Explota:
        """No serializable: `json.dump` revienta a mitad de camino."""

    with pytest.raises(TypeError):
        escribir_json_atomico(str(ruta), {"a": "x" * 5000, "b": Explota()})

    assert _json.loads(ruta.read_text(encoding="utf-8")) == {"bueno": 1}, (
        "el destino tiene que conservar la version anterior COMPLETA")
    assert [p.name for p in tmp_path.iterdir()] == ["cfg.json"], (
        "el temporal no puede quedar de recuerdo en el volumen de config")


def test_el_temporal_vive_en_el_directorio_del_destino(tmp_path, monkeypatch):
    """`os.replace` solo es atomico dentro del mismo sistema de archivos.

    `./config` es un volumen de Docker: un temporal en /tmp degradaria el
    rename a copiar+borrar, que es justo lo que se quiere evitar.
    """
    import os as _os
    from core import jsonio

    vistos = []
    real = jsonio.os.replace
    monkeypatch.setattr(jsonio.os, "replace",
                        lambda src, dst: (vistos.append(src), real(src, dst))[1])
    destino = tmp_path / "sub" / "cfg.json"
    jsonio.escribir_json_atomico(str(destino), {"x": 1})

    assert _os.path.dirname(vistos[0]) == _os.path.dirname(_os.path.abspath(destino))


def test_el_generador_y_el_heartbeat_no_se_pisan_al_guardar(monkeypatch, tmp_path):
    """La carrera del read-modify-write, que ya corrompio datos una vez.

    `escribir_json_atomico` garantiza que el archivo no quede a medias, pero no
    alcanza: los dos hilos leen la misma version, cada uno escribe SU clave
    sobre esa copia y el segundo guardado publica un archivo entero y
    consistente al que le falta la clave del primero.

    Se ejercitan los `_save_config` de produccion contra un tags.json REAL: con
    el `_save_tags` de la fixture `store` (un no-op) no habria nada que perder,
    y un `with st._tags_lock` escrito aca probaria el lock del test en vez del
    de los sitios de llamada.
    """
    import json as _json
    import threading as _th

    ruta = tmp_path / "tags.json"
    ruta.write_text(_json.dumps({"tags": [], "next_id": 1}), encoding="utf-8")
    monkeypatch.setattr(st, "TAGS_JSON", str(ruta))

    gen = st.TagGenerator()
    hb = st.HeartbeatManager()
    gen._ranges = {"PLANTA.X": {"min": 0.0, "max": 1.0}}
    hb._cfg = {**hb._cfg, "tag_out": "MARCA.HB"}

    arrancar = _th.Barrier(2)
    errores = []

    def guardar(obj):
        try:
            arrancar.wait(timeout=5)
            for _ in range(40):                 # varias vueltas: la ventana es corta
                obj._save_config()
        except Exception as e:                  # pragma: no cover
            errores.append(e)

    hilos = [_th.Thread(target=guardar, args=(o,)) for o in (gen, hb)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=20)

    assert not errores
    final = _json.loads(ruta.read_text(encoding="utf-8"))
    assert final.get("generator", {}).get("ranges") == gen._ranges, (
        "el guardado del heartbeat no puede borrar la config del generador")
    assert final.get("heartbeat", {}).get("tag_out") == "MARCA.HB", (
        "ni al reves")


def test_recargar_contrato_muta_en_el_mismo_objeto(monkeypatch, tmp_path):
    """Medio proyecto hace `from config import VARIABLES_PROCESO`.

    Reasignar el nombre en config.py no cambiaria nada para quien ya lo
    importo: seguiria mirando la lista vieja. Por eso se muta en sitio.
    """
    import json as _json
    import config as cfg

    original_pv = list(cfg.VARIABLES_PROCESO)
    original_sp = list(cfg.SETPOINT_KEYS)
    original_lim = dict(cfg.LIMITES_SP_CONTRATO)
    original_rate = dict(cfg.RATE_SP_CONTRATO)
    ref_pv = cfg.VARIABLES_PROCESO          # la referencia que ya tiene todo el mundo
    ref_cols = cfg.COLUMNAS_ENTRADA
    ref_rate = cfg.RATE_SP_CONTRATO
    ruta = tmp_path / "contrato.json"
    ruta.write_text(_json.dumps({
        "variables_proceso": ["uno", "dos"],
        "setpoints": ["sp_x"],
        "limites_sp": {"sp_x": [0.0, 10.0]},
        "rate_sp": {"sp_x": 0.5},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONTRATO_JSON", str(ruta))
    try:
        vigente = cfg.recargar_contrato()
        assert vigente["variables_proceso"] == ["uno", "dos"]
        assert ref_pv is cfg.VARIABLES_PROCESO, "no puede reasignarse el objeto"
        assert list(ref_pv) == ["uno", "dos"]
        assert ref_cols is cfg.COLUMNAS_ENTRADA
        assert "uno_lmax" in ref_cols and "sp_x" in ref_cols
        assert cfg.LIMITES_SP_CONTRATO == {"sp_x": (0.0, 10.0)}
        assert cfg.LIMITES_FUZZY_POR_VARIABLE["dos"]["lmin"] == "dos_lmin"
        assert ref_rate is cfg.RATE_SP_CONTRATO, "el rate se muta en sitio igual"
        assert cfg.RATE_SP_CONTRATO == {"sp_x": 0.5}
    finally:
        cfg.VARIABLES_PROCESO[:] = original_pv
        cfg.SETPOINT_KEYS[:] = original_sp
        cfg.LIMITES_SP_CONTRATO.clear()
        cfg.LIMITES_SP_CONTRATO.update(original_lim)
        cfg.RATE_SP_CONTRATO.clear()
        cfg.RATE_SP_CONTRATO.update(original_rate)
        cfg.LIMITES_FUZZY_POR_VARIABLE.clear()
        cfg.LIMITES_FUZZY_POR_VARIABLE.update({
            v: {"lmin": f"{v}_lmin", "lmax": f"{v}_lmax"} for v in original_pv})


def test_el_catalogo_de_roles_sigue_al_contrato_recargado(monkeypatch, tmp_path):
    """El síntoma que se veía: el Contrato mostraba 2 PV y el Mapeo exigía 8."""
    import json as _json
    import config as cfg

    original_pv = list(cfg.VARIABLES_PROCESO)
    original_sp = list(cfg.SETPOINT_KEYS)
    ruta = tmp_path / "contrato.json"
    ruta.write_text(_json.dumps({"variables_proceso": ["alfa"],
                                 "setpoints": ["sp_alfa"]}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONTRATO_JSON", str(ruta))
    # `estado_contrato()` compara el nucleo contra el archivo EN DISCO y tiene
    # su propia constante de ruta: hay que apuntar las dos al mismo temporal.
    monkeypatch.setattr(st, "CONTRATO_JSON", str(ruta))
    try:
        cfg.recargar_contrato()
        cat = st.catalogo_roles()
        assert cat["pv"] == ["alfa"]
        assert cat["sp"] == ["sp_alfa"]
        assert set(cat["lim"]) >= {"alfa_lmin", "alfa_lmax"}
        assert "alfa" in st.variables_validas()
        assert st.estado_contrato()["desincronizado"] is False
    finally:
        cfg.VARIABLES_PROCESO[:] = original_pv
        cfg.SETPOINT_KEYS[:] = original_sp
        cfg.LIMITES_FUZZY_POR_VARIABLE.clear()
        cfg.LIMITES_FUZZY_POR_VARIABLE.update({
            v: {"lmin": f"{v}_lmin", "lmax": f"{v}_lmax"} for v in original_pv})


def test_los_nombres_reservados_siguen_al_contrato(monkeypatch, tmp_path):
    """Una foto al importar reservaría las variables del contrato viejo."""
    import json as _json
    import config as cfg
    from web.api.config import var_nombres_reservados

    original_pv = list(cfg.VARIABLES_PROCESO)
    ruta = tmp_path / "contrato.json"
    ruta.write_text(_json.dumps({"variables_proceso": ["beta"],
                                 "setpoints": ["sp_beta"]}), encoding="utf-8")
    monkeypatch.setattr(cfg, "CONTRATO_JSON", str(ruta))
    try:
        cfg.recargar_contrato()
        reservados = var_nombres_reservados()
        assert {"beta", "beta_lmin", "beta_lmax", "pend_beta", "sp_beta"} <= reservados
        for viejo in original_pv:
            assert viejo not in reservados
    finally:
        cfg.VARIABLES_PROCESO[:] = original_pv
        cfg.SETPOINT_KEYS[:] = ["velocidad_sp"]


# ---------------------------------------------------------------
# Limites cableados a un tag, declarados donde se usan (2026-09-03)
# ---------------------------------------------------------------
# Antes el limite de una variable era el campo `rol` de un tag LIM. Ahora lo
# declara quien lo consume: `fuzzy.json` para una PV, `defuzzy.json` para un
# SP. Lo que se prueba aca es el cableado nuevo, no la logica difusa.

def _tag_lim(store, nombre_rol):
    """Agrega un tag LIM al store con el nombre que entiende el KEP falso."""
    nombre = f"PLANTA.LIM_{nombre_rol}"
    if not any(t["name"] == nombre for t in store["tags"]):
        store["tags"].append({"id": 900 + len(store["tags"]), "name": nombre,
                              "data_type": "Float", "enabled": True,
                              "categoria": "lim", "rol": ""})
    return nombre


def _cablear_sp(sp, **bounds):
    """Escribe `limites` de una familia en el defuzzy.json TEMPORAL."""
    cfg = _json_fixture.loads(open(st.DEFUZZY_JSON, encoding="utf-8").read())
    fam = cfg.setdefault(sp, {"belief_axis": [0.0, 1.0], "steps_por_accion": {}})
    fam.setdefault("limites", {}).update({k: v for k, v in bounds.items() if v})
    with open(st.DEFUZZY_JSON, "w", encoding="utf-8") as f:
        f.write(_json_fixture.dumps(cfg))


def test_un_tag_puede_acotar_dos_variables(store, config_completa):
    """El motivo por el que el cableado dejo de ser el `rol` del tag.

    `PU009_Speed_MAX` es a la vez la escala de la PV de velocidad y el tope de
    escritura de su setpoint. Con un rol por tag habia que elegir uno de los
    dos o duplicar el tag en KEPserver.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    compartido = f"PLANTA.LIM_{pv0}_lmax"       # ya lo usa la PV en el fixture
    _cablear_sp(sp0, lmax=compartido, lmin=f"PLANTA.LIM_{pv0}_lmin")

    m = st.construir_mapeo()
    pares = m["tag_to_lim"][compartido]
    assert (pv0, "lmax") in pares and (sp0, "lmax") in pares, pares
    assert m["duplicados"] == {}, "compartir un tag NO es una asignacion duplicada"


def test_los_limites_de_un_sp_salen_del_tag_y_se_siguen_en_vivo(store, kep,
                                                                config_completa,
                                                                monkeypatch):
    """Cableado el tag, el clipeo usa lo que dice el DCS, no el contrato."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    lmax = _tag_lim(store, f"{sp0}_lmax")
    lmin = _tag_lim(store, f"{sp0}_lmin")
    _cablear_sp(sp0, lmin=lmin, lmax=lmax)
    # El contrato dice [0, 100]; el tag dice [10, 60]. Tiene que mandar el tag.
    LIMS[sp0] = (10.0, 60.0)
    try:
        eng = _tick(monkeypatch)
        assert eng._limites_sp[sp0] == (10.0, 60.0)
        assert eng._sp_sin_limite == {}
    finally:
        LIMS[sp0] = (0.0, 100.0)


def test_un_limite_de_sp_no_legible_bloquea_la_familia_y_se_libera_solo(
        store, kep, config_completa, monkeypatch):
    """Fail-closed: con el tag cableado NO se cae al numero del contrato.

    El servidor acaba de decir que ese limite no es confiable; clipear contra
    un numero viejo seria fijar el tope con un dato que el DCS no sostiene.
    Y el bloqueo es POR TICK: en cuanto el tag vuelve, la familia se libera
    sola, sin reiniciar el motor.
    """
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    lmax = _tag_lim(store, f"{sp0}_lmax")
    _cablear_sp(sp0, lmax=lmax)
    monkeypatch.setattr(st._kep, "get_retencion_s", lambda: 0.0)

    kep["omitir"].add(lmax)
    eng = _tick(monkeypatch)
    assert sp0 in eng._sp_sin_limite
    assert sp0 not in eng._limites_sp, "sin limites no se le aplica ni el paso"
    assert sp0 in eng._familias_sin_escritura()

    kep["omitir"].discard(lmax)
    eng._run_tick()
    assert eng._sp_sin_limite == {}, "el bloqueo tiene que soltarse solo"
    assert eng._limites_sp[sp0] == (0.0, 100.0)


def test_sin_tag_cableado_el_sp_usa_el_numero_del_contrato(store, kep,
                                                           config_completa,
                                                           monkeypatch):
    """El respaldo numerico sigue valiendo: no obliga a cablear nada."""
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    config_completa(limites_sp={sp0: [5.0, 55.0]})
    eng = _tick(monkeypatch)
    assert eng._limites_sp[sp0] == (5.0, 55.0)
    assert eng._sp_sin_limite == {}


def test_limites_de_sp_invertidos_bloquean_la_familia(store, kep, config_completa,
                                                      monkeypatch):
    """lmin >= lmax es una escala inventada: no se escribe contra eso."""
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    lmin = _tag_lim(store, f"{sp0}_lmin")
    lmax = _tag_lim(store, f"{sp0}_lmax")
    _cablear_sp(sp0, lmin=lmin, lmax=lmax)
    LIMS[sp0] = (80.0, 20.0)                 # el DCS los da al reves
    try:
        eng = _tick(monkeypatch)
        assert "invertidos" in eng._sp_sin_limite[sp0]
        assert sp0 not in eng._limites_sp
    finally:
        LIMS[sp0] = (0.0, 100.0)


def test_un_binding_a_un_tag_deshabilitado_no_mapea_y_se_reporta(store,
                                                                 config_completa):
    """No se reemplaza solo: se reporta para que la pagina lo pinte en rojo."""
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    tag = f"PLANTA.LIM_{pv0}_lmax"
    for t in store["tags"]:
        if t["name"] == tag:
            t["enabled"] = False

    assert tag not in st.construir_mapeo()["tag_to_lim"]
    huerfanos = st.limites_huerfanos()
    assert any(h["tag"] == tag and h["bound"] == "lmax" for h in huerfanos), huerfanos


def test_la_migracion_de_roles_lim_es_idempotente(store, config_completa,
                                                  monkeypatch):
    """El rol viejo se adopta una vez y despues no queda nada que migrar.

    La migracion es ADITIVA: escribe el binding y deja el rol donde esta. Corre
    en cada `import app`, tambien contra la config viva de la planta, asi que no
    puede permitirse destruir nada. Un rol cuya variable todavia no tiene fuzzy
    queda pendiente hasta que la membresia exista.
    """
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    # Se limpia el binding de fuzzy.json y se restaura el rol viejo en el tag.
    cfg = _json_fixture.loads(open(st.FUZZY_JSON, encoding="utf-8").read())
    cfg[pv0]["limites"] = {}
    cfg.pop("_sin_fuzzy", None)
    with open(st.FUZZY_JSON, "w", encoding="utf-8") as f:
        f.write(_json_fixture.dumps(cfg))
    tag = f"PLANTA.LIM_{pv0}_lmax"
    huerfano = _tag_lim(store, "variable_sin_fuzzy_lmax")
    for t in store["tags"]:
        if t["name"] == tag:
            t["rol"] = f"{pv0}_lmax"
        if t["name"] == huerfano:
            t["rol"] = "variable_sin_fuzzy_lmax"

    # El fixture deja el rol viejo en TODOS los LIM, asi que se migran todos;
    # lo que importa es que el binding vacio se llene y que el huerfano quede
    # esperando.
    r1 = st.migrar_roles_lim_a_bindings()
    assert tag in {m["tag"] for m in r1["migrados"]}
    assert {m["tag"] for m in r1["pendientes"]} == {huerfano}
    assert st.bindings_limites()[(pv0, "lmax")] == tag

    r2 = st.migrar_roles_lim_a_bindings()
    assert r2["migrados"] == [], "la segunda pasada no debe volver a adoptar nada"
    assert {m["tag"] for m in r2["pendientes"]} == {huerfano}
    # Y el rol sigue ahi: aditivo, no destructivo.
    assert any(t["name"] == tag and t["rol"] == f"{pv0}_lmax" for t in store["tags"])


def test_el_binding_del_bound_que_el_tipo_no_usa_se_conserva(store, config_completa):
    """Cambiar un fuzzy a `high` y volver a `norm` no puede perder el lmin."""
    from web.api.config import _validar_fuzzy_spec
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    spec = {"type": "high", "offset": [0.0, 0.5, 1.0],
            "labels": {"ALTO": [0.0, 0.5, 1.0]},
            "limites": {"lmin": f"PLANTA.LIM_{pv0}_lmin",
                        "lmax": f"PLANTA.LIM_{pv0}_lmax"}}
    norm, error = _validar_fuzzy_spec(pv0, spec)
    assert error is None, error
    assert norm["limites"]["lmin"] == f"PLANTA.LIM_{pv0}_lmin"


def test_un_limite_no_asignable_se_rechaza_al_guardar(store, config_completa):
    """Tag inexistente, tag que no es LIM, y el mismo tag en los dos extremos."""
    from web.api.config import _validar_limites
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]

    _, err = _validar_limites(pv0, {"limites": {"lmax": "PLANTA.NO_EXISTE"}})
    assert err and "no existe" in err

    _, err = _validar_limites(pv0, {"limites": {"lmax": f"PLANTA.PV_{pv0}"}})
    assert err, "un tag PV no puede ser el limite de nadie"

    mismo = f"PLANTA.LIM_{pv0}_lmax"
    _, err = _validar_limites(pv0, {"limites": {"lmin": mismo, "lmax": mismo}})
    assert err and "ancho cero" in err

    ok, err = _validar_limites(pv0, {"limites": {"lmax": mismo}}, requeridos=("lmax",))
    assert err is None and ok == {"lmax": mismo}


def _cablear_pv_sin_tags(var):
    """Deja el fuzzy de `var` SIN tags de limite, para probar el respaldo."""
    cfg = _json_fixture.loads(open(st.FUZZY_JSON, encoding="utf-8").read())
    cfg[var]["limites"] = {}
    with open(st.FUZZY_JSON, "w", encoding="utf-8") as f:
        f.write(_json_fixture.dumps(cfg))


def _respaldo_pv(var, habilitado=True, lmin=None, lmax=None):
    """Escribe `limites_num` de un fuzzy en el fuzzy.json TEMPORAL."""
    cfg = _json_fixture.loads(open(st.FUZZY_JSON, encoding="utf-8").read())
    cfg[var]["limites_num"] = {"habilitado": habilitado, "lmin": lmin, "lmax": lmax}
    with open(st.FUZZY_JSON, "w", encoding="utf-8") as f:
        f.write(_json_fixture.dumps(cfg))


def test_el_respaldo_apagado_no_cubre_nada(store, kep, config_completa, monkeypatch):
    """Nace apagado: un numero que valiera solo por existir es lo que hacia
    invisible al viejo `limites_sp` del contrato."""
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    _cablear_pv_sin_tags(pv0)
    _respaldo_pv(pv0, habilitado=False, lmin=1.0, lmax=9.0)
    eng = _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert any(pv0 in x for x in tz["fuzzy_omitidas"]), tz["fuzzy_omitidas"]


def test_el_respaldo_encendido_cubre_el_bound_sin_tag(store, kep, config_completa,
                                                      monkeypatch):
    """Sin tag cableado, el numero deja fuzzificar la variable igual."""
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    _cablear_pv_sin_tags(pv0)
    _respaldo_pv(pv0, habilitado=True, lmin=0.0, lmax=100.0)
    eng = _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert not any(pv0 in x for x in tz["fuzzy_omitidas"]), tz["fuzzy_omitidas"]


def test_el_tag_le_gana_al_respaldo(store, kep, config_completa, monkeypatch):
    """"Si hay tags asignados, siempre priorizan el tag." El numero es la red
    de abajo, no una alternativa que compita."""
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    _respaldo_pv(pv0, habilitado=True, lmin=-999.0, lmax=999.0)
    eng = _tick(monkeypatch)
    lims = {f["rol"]: f for f in st._get_trazas(1)[0]["lectura"]}
    assert lims[f"{pv0}_lmax"]["valor"] == 100.0, "manda el tag, no el -999/999"


def test_un_bound_con_tag_ilegible_no_cae_al_respaldo(store, kep, config_completa,
                                                      monkeypatch):
    """Fail-closed, igual que en el SP: si el DCS dice que ese limite no es
    confiable, medir contra un numero viejo seria inventar la escala."""
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    _respaldo_pv(pv0, habilitado=True, lmin=0.0, lmax=100.0)
    monkeypatch.setattr(st._kep, "get_retencion_s", lambda: 0.0)
    kep["omitir"].add(f"PLANTA.LIM_{pv0}_lmax")
    _tick(monkeypatch)
    tz = st._get_trazas(1)[0]
    assert any(pv0 in x and "lmax" in x for x in tz["fuzzy_omitidas"]), tz["fuzzy_omitidas"]


def test_un_respaldo_habilitado_saca_el_limite_de_faltantes(store, config_completa):
    """Con el numero puesto, el tag es opcional: pedirlo seria un aviso que no
    lleva a ninguna accion."""
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    _cablear_pv_sin_tags(pv0)
    assert f"{pv0}_lmax" in st.construir_mapeo()["faltantes"]["lim"]
    _respaldo_pv(pv0, habilitado=True, lmin=0.0, lmax=100.0)
    assert f"{pv0}_lmax" not in st.construir_mapeo()["faltantes"]["lim"]


def test_el_respaldo_del_sp_migra_del_contrato_habilitado(store, config_completa,
                                                          tmp_path, monkeypatch):
    """Se migra ENCENDIDO: esos numeros estan clipeando hoy, y apagarlos al
    actualizar dejaria el setpoint sin tope, o sea sin escritura."""
    import config as cfg
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    ruta = tmp_path / "defuzzy-migracion.json"
    ruta.write_text(_json_fixture.dumps(
        {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {}}}), encoding="utf-8")
    monkeypatch.setattr(st, "DEFUZZY_JSON", str(ruta))
    monkeypatch.setattr(cfg, "LIMITES_SP_CONTRATO", {sp0: (7.0, 77.0)})

    assert st.migrar_limites_sp_del_contrato() == [
        {"familia": sp0, "limites": [7.0, 77.0]}]
    assert st.limites_num(str(ruta))[sp0] == {"lmin": 7.0, "lmax": 77.0}
    assert st.migrar_limites_sp_del_contrato() == [], "idempotente"


def test_un_respaldo_habilitado_sin_numeros_se_rechaza(store, config_completa):
    """Un interruptor que promete un respaldo que no existe."""
    from web.api.config import _validar_limites_num
    _, err = _validar_limites_num("x", {"limites_num": {"habilitado": True}})
    assert err and "no cubre ningun limite" in err

    _, err = _validar_limites_num("x", {"limites_num": {"habilitado": True,
                                                        "lmin": 9.0, "lmax": 1.0}})
    assert err and "dada vuelta" in err

    ok, err = _validar_limites_num("x", {"limites_num": {"habilitado": False}})
    assert err is None and ok["habilitado"] is False


# ---------------------------------------------------------------
# Handshake con el DCS, configurable desde la pagina de Tags
# ---------------------------------------------------------------
def _client_tags(monkeypatch, store):
    """Cliente Flask con tags.json en memoria.

    Se parchea `web.api.tags`, NO `web.state`: ese modulo hace
    `from web.state import _load_tags`, asi que el nombre ya esta ligado y
    parchear el origen no lo alcanza. Sin esto el PUT escribiria el tags.json
    **de la planta**.
    """
    import web.api.tags as tags_api
    from app import app
    monkeypatch.setattr(tags_api, "_load_tags", lambda: store)
    monkeypatch.setattr(tags_api, "_save_tags", lambda d: store.update(d))
    # `_autosync_contrato` escribe contrato.json DE LA PLANTA cuando cambia la
    # categoria, el rol, el nombre o el enabled de un tag. Con los tags
    # sinteticos del fixture eso agregaba setpoints inventados a la config viva
    # y rompia otros tests en la corrida siguiente. Se neutraliza: lo que estos
    # tests verifican es la validacion del PUT, no la sincronizacion.
    monkeypatch.setattr(tags_api, "_autosync_contrato", lambda *a, **k: None)
    return app.test_client()


def test_el_handshake_no_se_puede_exigir_sin_tag_fbk(store, monkeypatch):
    """Exigirlo sin FBK seria fail-closed permanente: el SE no escribiria nunca."""
    c = _client_tags(monkeypatch, store)

    r = c.put("/api/tags/handshake", json={"enabled": True, "enable_fbk_tag": ""})
    assert r.status_code == 400 and "FBK" in r.get_json()["error"]

    # Un tag que no existe tampoco sirve: seria un permiso que nadie contesta.
    r = c.put("/api/tags/handshake", json={"enabled": True,
                                           "enable_fbk_tag": "PLANTA.NO_EXISTE"})
    assert r.status_code == 400

    tag = store["tags"][0]["name"]
    r = c.put("/api/tags/handshake", json={"enabled": True, "enable_fbk_tag": tag,
                                           "enable_ext_tag": tag})
    assert r.status_code == 400 and "distintos" in r.get_json()["error"]


def test_el_handshake_se_guarda_en_tags_json(store, monkeypatch):
    c = _client_tags(monkeypatch, store)
    fbk, ext = store["tags"][0]["name"], store["tags"][1]["name"]
    r = c.put("/api/tags/handshake", json={"enabled": True, "enable_fbk_tag": fbk,
                                           "enable_ext_tag": ext})
    assert r.status_code == 200, r.get_json()
    assert store["handshake"] == {"enabled": True, "enable_fbk_tag": fbk,
                                  "enable_ext_tag": ext}


def test_el_handshake_viaja_en_el_paquete_de_export(store, monkeypatch):
    """No viajaba hasta el 2026-09-03: una planta clonada arrancaba sin el
    permiso configurado y no escribia nada, sin decir por que."""
    from web.api.export_import import _export_tags_completo
    store["handshake"] = {"enabled": True, "enable_fbk_tag": "T.FBK",
                          "enable_ext_tag": "T.EXT"}
    bloque = _export_tags_completo(store)
    assert bloque["handshake"]["enable_fbk_tag"] == "T.FBK"


# ---------------------------------------------------------------
# Siembra al recibir el lazo: el SP arranca en su PV
# ---------------------------------------------------------------
HS_FBK = "PLANTA.HS_ENABLE_FBK"


def _motor_con_enable(monkeypatch, tmp_path, tracking, reglas, defuzzy, kep,
                      permiso_inicial=True):
    """Motor con handshake exigido y el permiso del DCS bajo control del test."""
    kep["handshake"] = permiso_inicial
    motor = _motor_con_tracking(monkeypatch, tmp_path, tracking, reglas, defuzzy)
    motor._handshake_enabled = True
    motor._handshake_fbk_tag = HS_FBK
    motor._handshake_ext_tag = ""
    return motor


def test_el_enable_del_dcs_siembra_el_sp_con_la_pv(store, kep, config_completa,
                                                   monkeypatch, tmp_path):
    """Flanco denegado -> concedido: el SP se lleva al valor actual del proceso.

    Retomar el lazo con el SP viejo es un salto para la planta, y ademas deja
    al tracking comparando contra un objetivo que nadie siguio.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 0.3, "habilitado": True}},
        [], {}, kep, permiso_inicial=False)

    motor._run_tick()                       # DENEGADO: no escribe ni siembra
    assert kep["escrituras"] == []
    assert motor._setpoints[sp0] == kep["sp_en_dcs"]

    kep["handshake"] = True                 # el DCS entrega el lazo
    motor._run_tick()

    assert motor._setpoints[sp0] == VALORES[pv0]
    assert kep["escrituras"], "con el lazo entregado tiene que escribir"
    assert kep["escrituras"][-1][f"PLANTA.SP_{sp0}"] == VALORES[pv0]
    tz = st._get_trazas(1)[0]
    assert [e["sp"] for e in tz.get("semilla_sp", [])] == [sp0]


def test_la_siembra_se_repite_en_cada_flanco_del_enable(store, kep, config_completa,
                                                        monkeypatch, tmp_path):
    """No es "solo la primera vez": cada entrega de lazo vuelve a sembrar."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 999.0, "habilitado": True}},
        [_regla_simple(pv0)], tabla, kep)

    motor._run_tick()                       # arranca con permiso: siembra
    motor._run_tick()                       # las reglas alejan el SP de la PV
    lejos = motor._setpoints[sp0]
    assert lejos > VALORES[pv0]

    kep["handshake"] = False                # el DCS retira el lazo
    motor._run_tick()
    assert motor._setpoints[sp0] == lejos, "sin permiso no se mueve ni se siembra"

    kep["handshake"] = True                 # y lo vuelve a entregar
    motor._run_tick()
    tz = st._get_trazas(1)[0]
    assert [e["sp"] for e in tz.get("semilla_sp", [])] == [sp0]
    # Sembro con la PV y despues el tick aplico su accion: queda PV + paso.
    assert motor._setpoints[sp0] < lejos


def test_sin_flanco_el_sp_sigue_su_curso(store, kep, config_completa,
                                         monkeypatch, tmp_path):
    """Mientras el permiso se mantiene, la PV no vuelve a pisar el SP."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 999.0, "habilitado": True}},
        [_regla_simple(pv0)], tabla, kep)

    motor._run_tick()
    primera = motor._setpoints[sp0]
    motor._run_tick()
    assert motor._setpoints[sp0] > primera, "sin flanco manda el defuzzy"
    assert motor._sp_semilla_pendiente == set()


def test_arrancar_el_motor_ya_no_siembra_por_si_solo(store, kep, config_completa,
                                                     monkeypatch, tmp_path):
    """Sin handshake exigido no hay entrega de lazo, y no se siembra."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 0.3, "habilitado": True}},
        [], {})                              # handshake apagado

    motor._run_tick()
    assert motor._setpoints[sp0] == kep["sp_en_dcs"], (
        "el arranque bumpless clasico parte del SP del DCS")
    assert st._get_trazas(1)[0].get("semilla_sp") is None


def test_el_tracking_no_puede_anular_la_semilla_del_flanco(store, kep, config_completa,
                                                           monkeypatch, tmp_path):
    """El deadlock que motivo todo esto: PV lejos del SP viejo.

    Si el tracking retuviera la familia en el tick del flanco, la semilla no se
    escribiria nunca y el readback jamas alcanzaria al objetivo.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 0.1, "habilitado": True}},
        [], {}, kep, permiso_inicial=False)

    motor._run_tick()
    kep["handshake"] = True
    motor._run_tick()

    assert sp0 not in motor._sp_retenidos, "la familia recien sembrada pasa una vez"
    assert kep["escrituras"][-1][f"PLANTA.SP_{sp0}"] == VALORES[pv0]
    motor._run_tick()                        # ya converge: SP sembrado == PV
    assert sp0 not in motor._sp_retenidos


def test_sin_pv_legible_no_se_escribe_tras_el_enable(store, kep, config_completa,
                                                     monkeypatch, tmp_path):
    """Fail-closed: el SP tiene que salir en el valor del proceso.

    Si esa PV no se puede leer, escribir mandaria justo el valor que la siembra
    evita. Se espera, y se dice por que.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    kep["omitir"].add(f"PLANTA.PV_{pv0}")
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 0.3, "habilitado": True}},
        [], {}, kep)

    motor._run_tick()
    assert kep["escrituras"] == [], "sin PV no se escribe nada"
    assert sp0 in motor._sp_semilla_pendiente
    tz = st._get_trazas(1)[0]
    motivos = {d["sp"]: d["motivo"] for d in tz["escritura"]["inhibidos"]}
    assert "siembra pendiente" in motivos[sp0]

    kep["omitir"].clear()                   # vuelve la PV: se libera sola
    motor._run_tick()
    assert motor._setpoints[sp0] == VALORES[pv0]
    assert kep["escrituras"], "con la PV legible ya se puede escribir"


# ---------------------------------------------------------------
# Disparo sin efecto: el motivo REAL, no la suposicion
# ---------------------------------------------------------------
def test_el_disparo_sin_efecto_por_limite_lo_dice(store, kep, config_completa,
                                                  monkeypatch):
    """El texto viejo era fijo: "limite alcanzado o paso 0". Aca si es el limite."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    config_completa(limites_sp={k: [0.0, kep["sp_en_dcs"]] for k in SETPOINT_KEYS})
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)

    motor._run_tick()
    tz = st._get_trazas(1)[0]
    motivo = tz["disparadas"][0]["motivo_sin_efecto"]
    assert "limite superior" in motivo and sp0 in motivo


def test_el_disparo_sin_efecto_por_tracking_no_culpa_al_limite(store, kep,
                                                               config_completa,
                                                               monkeypatch, tmp_path):
    """El error silencioso que costo una tarde: decia "limite" y era tracking."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    if len(VARIABLES_PROCESO) < 2:
        pytest.skip("Se necesitan 2 PV.")
    pv, rb = VARIABLES_PROCESO[0], VARIABLES_PROCESO[1]
    sp0 = SETPOINT_KEYS[0]
    st._reset_trazas()
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con_tracking(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": rb, "rango": 0.1, "habilitado": True}},
        [_regla_simple(pv, waits=[_wait_spec()])], tabla)

    motor._run_tick()                       # siembra, actua y escribe
    motor._last_action_time.clear()
    motor._run_tick()                       # el readback quedo lejos: retiene

    tz = st._get_trazas(1)[0]
    motivo = tz["disparadas"][0]["motivo_sin_efecto"]
    assert "tracking" in motivo
    assert "limite" not in motivo, "el motivo no puede mandar a mirar los limites"


def test_el_disparo_sin_efecto_por_paso_cero_lo_dice(store, kep, config_completa,
                                                     monkeypatch):
    """La tabla da 0 para ese belief: no es el limite y no hay que confundirlo."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 0.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)

    motor._run_tick()
    tz = st._get_trazas(1)[0]
    motivo = tz["disparadas"][0]["motivo_sin_efecto"]
    assert "tabla defuzzy" in motivo and "limite" not in motivo


def test_el_disparo_que_si_mueve_no_trae_motivo(store, kep, config_completa, monkeypatch):
    """Simetria: el campo existe para explicar lo que NO paso."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)

    motor._run_tick()
    tz = st._get_trazas(1)[0]
    assert tz["disparadas"][0]["movio_sp"] is True
    assert tz["disparadas"][0]["motivo_sin_efecto"] is None


# ---------------------------------------------------------------
# Escalas del Explorador de Series (/api/entrada/escalas)
# ---------------------------------------------------------------
def _client_escalas(monkeypatch):
    """Cliente Flask con la lectura del DCS del fixture `kep`.

    Se parchea `web.api.se`, NO `web.state`: ese modulo importa el lector con
    `from web.state import _read_kepserver_tags_batch`, asi que el nombre ya
    esta ligado y parchear el origen no lo alcanza (mismo motivo que
    `_client_tags`). Sin esto el endpoint intentaria hablar con el KEPserver
    de verdad y ninguna variable traeria escala.
    """
    import web.api.se as se_api
    from app import app
    monkeypatch.setattr(se_api, "_read_kepserver_tags_batch",
                        st._read_kepserver_tags_batch)
    return app.test_client()



def test_las_escalas_del_explorador_salen_del_cableado_del_fuzzy(store, kep,
                                                                 config_completa,
                                                                 monkeypatch):
    """El grafico normaliza contra la MISMA banda que usa el fuzzy.

    Si el explorador tuviera su propia fuente de limites, el operador podria
    estar mirando un 80% en pantalla mientras el motor razona contra otra
    escala. Es la clase de divergencia que nadie nota hasta que discuten por
    que la regla no disparo.
    """
    from config import VARIABLES_PROCESO
    c = _client_escalas(monkeypatch)

    r = c.get("/api/entrada/escalas")
    assert r.status_code == 200
    escalas = r.get_json()

    pv0 = VARIABLES_PROCESO[0]
    tag_pv = f"PLANTA.PV_{pv0}"
    assert tag_pv in escalas, "una PV con limites cableados tiene que traer escala"
    assert escalas[tag_pv]["var"] == pv0
    assert (escalas[tag_pv]["lmin"], escalas[tag_pv]["lmax"]) == LIMS[pv0]
    assert escalas[tag_pv]["origen"]["lmax"] == f"PLANTA.LIM_{pv0}_lmax"


def test_una_variable_sin_limite_legible_no_trae_escala(store, kep, config_completa,
                                                        monkeypatch):
    """Fail-closed, igual que el motor: sin escala confiable no se inventa una.

    El front lo resuelve cayendo a min/max de la ventana y diciendolo; lo que
    no puede pasar es que el grafico normalice contra un numero viejo.
    """
    from config import VARIABLES_PROCESO
    pv0 = VARIABLES_PROCESO[0]
    kep["omitir"].add(f"PLANTA.LIM_{pv0}_lmax")
    c = _client_escalas(monkeypatch)

    escalas = c.get("/api/entrada/escalas").get_json()
    assert f"PLANTA.PV_{pv0}" not in escalas


# ---------------------------------------------------------------
# Roles duplicados: dos tags peleando por el mismo rol
# ---------------------------------------------------------------
def _duplicar_rol(store, categoria, rol, nombre):
    """Agrega un segundo tag HABILITADO con un rol que ya existe.

    Es el estado que dejan los datos viejos: la validacion de la API impide
    ASIGNAR un rol tomado, pero un tags.json importado (o anterior a esa
    validacion) puede traer el duplicado ya hecho.
    """
    store["tags"].append({"id": 9001, "name": nombre, "data_type": "Float",
                          "enabled": True, "categoria": categoria, "rol": rol})
    return nombre


def test_el_mapeo_con_rol_duplicado_es_determinista(store, config_completa):
    """Gana el PRIMERO, no el ultimo recorrido.

    Antes se asignaba igual y ganaba el ultimo: a que tag le escribia el SE
    dependia del orden del archivo, asi que un tag agregado despues podia
    robarle la escritura al de produccion sin que fallara nada.
    """
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    original = f"PLANTA.SP_{sp0}"
    intruso = _duplicar_rol(store, "sp", sp0, "PLANTA.SP_INTRUSO")

    m = st.construir_mapeo()
    assert m["sp_to_tag"][sp0] == original, "el tag de produccion no puede perder el rol"
    assert m["duplicados"][f"sp::{sp0}"] == [original, intruso]
    assert m["listo"] is False


def test_un_sp_con_rol_duplicado_no_se_escribe(store, kep, config_completa, monkeypatch):
    """Fail-closed: escribir el SP en el tag equivocado mueve otro equipo.

    Para una PV, elegir mal es leer el sensor equivocado; para un SP es mandar
    una consigna a una maquina que nadie pidio tocar. Por eso aca no alcanza
    con una advertencia.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    _duplicar_rol(store, "sp", sp0, "PLANTA.SP_INTRUSO")
    tabla = {sp0: {"belief_axis": [0.0, 1.0], "steps_por_accion": {"SUBIR_SP": [0.0, 5.0]}}}
    motor = _motor_con(monkeypatch, [_regla_simple(pv0)], tabla)

    assert sp0 in motor._sp_inhibidos
    assert "ambiguo" in motor._sp_inhibidos[sp0]
    motor._run_tick()
    assert kep["escrituras"] == [], "un setpoint ambiguo no sale al DCS"
    tz = st._get_trazas(1)[0]
    motivos = {d["sp"]: d["motivo"] for d in tz["escritura"]["inhibidos"]}
    assert "PLANTA.SP_INTRUSO" in motivos[sp0], "el motivo nombra a los dos tags"


def test_no_se_puede_habilitar_un_tag_que_duplica_un_rol(store, monkeypatch):
    """El click peligroso: habilitar un duplicado dormido.

    La validacion vieja solo miraba al ASIGNAR el rol, asi que un tag viejo con
    el rol ya puesto se volvia ambiguo con un solo click.
    """
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    store["tags"].append({"id": 9002, "name": "PLANTA.SP_DORMIDO", "data_type": "Float",
                          "enabled": False, "categoria": "sp", "rol": sp0})
    c = _client_tags(monkeypatch, store)

    r = c.put("/api/tags/9002", json={"enabled": True})
    assert r.status_code == 409
    assert sp0 in r.get_json()["error"]
    assert next(t for t in store["tags"] if t["id"] == 9002)["enabled"] is False

    # Sin rol no hay ambiguedad: habilitarlo tiene que poder hacerse.
    r = c.put("/api/tags/9002", json={"rol": ""})
    assert r.status_code == 200
    r = c.put("/api/tags/9002", json={"enabled": True})
    assert r.status_code == 200


# ---------------------------------------------------------------
# Arranque bumpless configurable por familia
# ---------------------------------------------------------------
# El tag de referencia NO tiene por que ser una entrada del SE: es el SP con
# el que el DCS venia operando el equipo. El fixture devuelve 0.0 para un
# nombre que no reconoce, y eso sirve justo para distinguirlo de la PV (50.0)
# y del SP que el DCS tenia (42.0).
TAG_REF_DCS = "PLANTA.REF_SP_DEL_DCS"


def test_el_arranque_por_tag_siembra_con_el_sp_de_referencia(store, kep, config_completa,
                                                             monkeypatch, tmp_path):
    """La planta pidio arrancar SIEMPRE por el SP definido, no por la PV."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 0.3, "habilitado": True,
               "arranque": {"fuente": "tag", "tag": TAG_REF_DCS}}},
        [], {}, kep, permiso_inicial=False)

    motor._run_tick()                       # sin permiso: no siembra
    kep["handshake"] = True
    motor._run_tick()                       # el DCS entrega el lazo

    assert motor._setpoints[sp0] == 0.0, "tiene que salir del tag de referencia"
    assert motor._setpoints[sp0] != VALORES[pv0], "no es la PV"
    tz = st._get_trazas(1)[0]
    ev = tz["semilla_sp"][0]
    assert ev["fuente"] == "tag" and ev["origen"] == TAG_REF_DCS


def test_sin_referencia_legible_no_se_escribe(store, kep, config_completa,
                                              monkeypatch, tmp_path):
    """Fail-closed: retomar con un valor inventado es el salto que esto evita."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    kep["omitir"].add(TAG_REF_DCS)
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 0.3, "habilitado": True,
               "arranque": {"fuente": "tag", "tag": TAG_REF_DCS}}},
        [], {}, kep)

    motor._run_tick()
    assert kep["escrituras"] == []
    assert sp0 in motor._sp_semilla_pendiente
    tz = st._get_trazas(1)[0]
    motivos = {d["sp"]: d["motivo"] for d in tz["escritura"]["inhibidos"]}
    assert TAG_REF_DCS in motivos[sp0], "el motivo nombra la referencia que falta"

    kep["omitir"].discard(TAG_REF_DCS)
    motor._run_tick()
    assert kep["escrituras"], "con la referencia legible ya se puede escribir"


def test_arranque_ninguno_deja_el_sp_como_estaba(store, kep, config_completa,
                                                 monkeypatch, tmp_path):
    """Una planta puede no querer bumpless: entonces no se toca el SP."""
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    st._reset_trazas()
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    motor = _motor_con_enable(
        monkeypatch, tmp_path,
        {sp0: {"pv_key": pv0, "rango": 0.3, "habilitado": True,
               "arranque": {"fuente": "ninguno", "tag": ""}}},
        [], {}, kep)

    motor._run_tick()
    assert motor._setpoints[sp0] == kep["sp_en_dcs"]
    assert st._get_trazas(1)[0].get("semilla_sp") is None


def test_una_config_vieja_conserva_su_conducta(store, config_completa, monkeypatch, tmp_path):
    """Sin bloque `arranque`, una familia con readback sigue sembrando por PV.

    Actualizar el SE no puede cambiar como arranca una planta que ya estaba
    andando: eso se decide en la pantalla, no en un deploy.
    """
    import json as _json
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv0, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    ruta = tmp_path / "tracking.json"
    ruta.write_text(_json.dumps({
        sp0: {"pv_key": pv0, "rango": 0.3, "habilitado": True},
        SETPOINT_KEYS[-1] + "_sin_rb": {"pv_key": "", "rango": 0.0, "habilitado": False},
    }), encoding="utf-8")
    monkeypatch.setattr(st, "TRACKING_JSON", str(ruta))

    arr = st.arranque_definido()
    assert arr[sp0] == {"fuente": "pv", "tag": "", "pv_key": pv0}
    assert arr[SETPOINT_KEYS[-1] + "_sin_rb"]["fuente"] == "ninguno"


def test_el_arranque_por_tag_exige_un_tag_habilitado(store, monkeypatch, tmp_path):
    """La validacion vive en la API: un tag muerto no se guarda como referencia."""
    import web.api.config as cfg_api
    from app import app
    from config import SETPOINT_KEYS
    sp0 = SETPOINT_KEYS[0]
    ruta = tmp_path / "tracking.json"
    monkeypatch.setattr(cfg_api, "TRACKING_JSON", str(ruta))
    monkeypatch.setattr(st, "_load_tags", lambda: store)
    c = app.test_client()

    base = {"pv_key": "", "rango": 0.0, "habilitado": False}
    r = c.put("/api/tracking", json={sp0: {**base, "arranque": {"fuente": "tag", "tag": ""}}})
    assert r.status_code == 400 and "tag de referencia" in r.get_json()["error"]

    r = c.put("/api/tracking", json={sp0: {**base,
                                          "arranque": {"fuente": "tag",
                                                       "tag": "PLANTA.NO_EXISTE"}}})
    assert r.status_code == 400 and "no existe" in r.get_json()["error"]

    vivo = store["tags"][0]["name"]
    r = c.put("/api/tracking", json={sp0: {**base,
                                          "arranque": {"fuente": "tag", "tag": vivo}}})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["actual"][sp0]["arranque"] == {"fuente": "tag", "tag": vivo}
