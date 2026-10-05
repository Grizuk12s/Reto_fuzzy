# -*- coding: utf-8 -*-
"""El import de tracking pasa por el mismo validador que la pagina.

Antes el import escribia `tracking.json` crudo. `PUT /api/tracking` rechazaba
una familia inexistente o un `rango` no numerico; el import los aceptaba y los
dejaba en disco. No es una diferencia cosmetica: en tracking el peor caso no es
"una regla no evalua", es

  * `pv_key` que aca no existe  -> el motor RETIENE la familia (fail-closed:
    sin poder verificar el readback no empuja) y el SE deja de escribir ese SP;
  * `arranque.tag` de la otra planta -> la familia no se siembra nunca, y eso
    es todavia mas callado porque no levanta alerta.
"""
from __future__ import annotations

import copy
import json

import pytest

import web.api.config as cfgapi
import web.api.export_import as ei
from app import app


FAMILIA = "velocidad_salida_del_se"


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    """tracking.json aislado. No se toca la configuracion de la planta."""
    ruta = str(tmp_path / "tracking.json")
    monkeypatch.setattr(cfgapi, "TRACKING_JSON", ruta)
    monkeypatch.setattr(ei, "TRACKING_JSON", ruta)
    monkeypatch.setattr(ei, "_motor_corriendo", lambda: False)
    cfgapi._save_tracking({
        FAMILIA: {"pv_key": "velocidad_pv", "rango": 0.3, "habilitado": False,
                  "arranque": {"fuente": "ninguno", "tag": ""}},
    })
    with app.test_client() as c:
        yield c, ruta


def _paquete(bloque):
    return {"format": ei.FORMAT_ID, "version": 1,
            "exported_at": "2026-09-22T00:00:00+00:00",
            "selected": {"tracking": True},
            "data": {"tracking": bloque}}


def _aplicar(c, bloque, modo="reemplazar"):
    r = c.post("/api/export-import/apply",
               json={"package": _paquete(bloque), "modo": modo})
    return r.status_code, r.get_json()


def _disco(ruta):
    return json.load(open(ruta, encoding="utf-8"))


# ------------------------------------------------------------
# Lo que ya funcionaba y tiene que seguir funcionando
# ------------------------------------------------------------

def test_round_trip_exacto(entorno):
    """Exportar y reimportar deja el archivo identico, `arranque` incluido."""
    c, ruta = entorno
    antes = _disco(ruta)
    pkg = c.post("/api/export-import/export",
                 json={"selected": {"tracking": True}}).get_json()["package"]
    st, _ = _aplicar(c, pkg["data"]["tracking"])
    assert st == 200
    assert _disco(ruta) == antes


# ------------------------------------------------------------
# Lo que antes entraba sin chistar
# ------------------------------------------------------------

def test_una_familia_de_otra_planta_se_rechaza(entorno):
    c, ruta = entorno
    antes = _disco(ruta)
    st, d = _aplicar(c, {"sp_de_otra_planta": {"pv_key": "x", "rango": 0.5}})
    assert st == 409 and d["ok"] is False
    assert "no corresponde a ningun tag de categoria SP" in d["errores"][0]
    assert _disco(ruta) == antes, "se escribio igual: el import no valido nada"


def test_un_rango_no_numerico_se_rechaza(entorno):
    c, ruta = entorno
    antes = _disco(ruta)
    st, d = _aplicar(c, {FAMILIA: {"pv_key": "velocidad_pv", "rango": "muchisimo"}})
    assert st == 409 and d["ok"] is False
    assert _disco(ruta) == antes


def test_un_tag_de_arranque_inexistente_se_rechaza(entorno):
    """Es el caso callado: sin este chequeo la familia no se siembra nunca y
    no hay alerta que lo explique."""
    c, ruta = entorno
    antes = _disco(ruta)
    st, d = _aplicar(c, {FAMILIA: {"pv_key": "velocidad_pv", "rango": 0.3,
                                   "arranque": {"fuente": "tag",
                                                "tag": "TAG.QUE.NO.EXISTE"}}})
    assert st == 409 and d["ok"] is False
    assert "TAG.QUE.NO.EXISTE" in d["errores"][0]
    assert _disco(ruta) == antes


def test_el_error_dice_que_hacer(entorno):
    c, _ = entorno
    _, d = _aplicar(c, {"sp_de_otra_planta": {"pv_key": "x", "rango": 0.5}})
    assert "desmarca Tracking PV-SP" in d["errores"][0]


def test_un_bloque_que_no_es_objeto_no_rompe(entorno):
    c, ruta = entorno
    antes = _disco(ruta)
    st, d = _aplicar(c, ["esto", "no", "es", "un", "objeto"])
    assert st == 409 and d["ok"] is False
    assert _disco(ruta) == antes


# ------------------------------------------------------------
# Modos
# ------------------------------------------------------------

def test_agregar_no_pisa_lo_que_ya_estaba(entorno):
    c, ruta = entorno
    antes = _disco(ruta)
    st, d = _aplicar(c, {FAMILIA: {"pv_key": "", "rango": 0.0}}, modo="agregar")
    assert st == 200
    assert _disco(ruta) == antes
    assert d["aplicados"]["tracking"]["omitidas"] == [FAMILIA]


def test_copias_se_aplica_como_agregar_y_lo_dice(entorno):
    """Una familia ES el identificador de un SP: `velocidad_sp_2` no seria una
    copia de nada, seria una huerfana recien fabricada."""
    c, ruta = entorno
    st, d = _aplicar(c, {FAMILIA: {"pv_key": "", "rango": 0.0}}, modo="copias")
    assert st == 200
    assert list(_disco(ruta)) == [FAMILIA], "se fabrico una familia copia"
    assert "copias" in d["aplicados"]["tracking"]["nota"]


# ------------------------------------------------------------
# Preview: avisar ANTES de aplicar
# ------------------------------------------------------------

def _preview(c, bloque):
    return c.post("/api/export-import/preview", json=_paquete(bloque)).get_json()


def test_el_preview_avisa_del_readback_colgado(entorno):
    c, _ = entorno
    d = _preview(c, {FAMILIA: {"pv_key": "readback_fantasma", "rango": 0.5}})
    refs = d["conflictos"]["tracking_refs"]["huerfanos"]
    campos = {h["campo"]: h["ref"] for h in refs}
    assert campos["pv_key"] == "readback_fantasma"
    assert "RETIENE" in [h for h in refs if h["campo"] == "pv_key"][0]["efecto"]


def test_el_preview_avisa_del_tag_de_arranque_colgado(entorno):
    c, _ = entorno
    d = _preview(c, {FAMILIA: {"pv_key": "velocidad_pv", "rango": 0.3,
                               "arranque": {"fuente": "tag", "tag": "TAG.FANTASMA"}}})
    refs = d["conflictos"]["tracking_refs"]["huerfanos"]
    assert any(h["campo"] == "arranque.tag" and h["ref"] == "TAG.FANTASMA" for h in refs)


def test_el_preview_avisa_de_la_familia_huerfana(entorno):
    c, _ = entorno
    d = _preview(c, {"sp_de_otra_planta": {"pv_key": "", "rango": 0.0}})
    refs = d["conflictos"]["tracking_refs"]["huerfanos"]
    assert any(h["campo"] == "familia" for h in refs)


def test_un_paquete_de_esta_planta_no_genera_avisos(entorno):
    """El aviso tiene que significar algo: si salta siempre, no lo mira nadie."""
    c, ruta = entorno
    d = _preview(c, _disco(ruta))
    assert "tracking_refs" not in d.get("conflictos", {})


def test_sin_tracking_marcado_no_se_revisa_nada(entorno):
    c, _ = entorno
    pkg = _paquete({"sp_de_otra_planta": {"pv_key": "x", "rango": 1}})
    pkg["selected"] = {"tracking": False, "filtros": True}
    pkg["data"]["filtros"] = {}
    d = c.post("/api/export-import/preview", json=pkg).get_json()
    assert "tracking_refs" not in d.get("conflictos", {})


# ------------------------------------------------------------
# Una familia nueva nace igual por los dos caminos
# ------------------------------------------------------------

def test_la_familia_nueva_nace_con_arranque_por_los_dos_caminos(entorno):
    """El sync del import y el boton Sincronizar producian formas distintas del
    mismo archivo. El comportamiento era el mismo; la diferencia se paga al
    comparar dos tracking.json."""
    c, ruta = entorno
    cfgapi._save_tracking({})
    ei._sincronizar_tracking_post()
    por_import = _disco(ruta)

    cfgapi._save_tracking({})
    c.post("/api/tracking/sincronizar")
    por_boton = _disco(ruta)

    assert por_import == por_boton
    for spec in por_import.values():
        assert spec["arranque"] == {"fuente": "ninguno", "tag": ""}
