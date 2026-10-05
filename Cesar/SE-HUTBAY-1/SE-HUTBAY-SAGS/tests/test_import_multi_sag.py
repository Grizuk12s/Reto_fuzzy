# -*- coding: utf-8 -*-
"""Importar con varios SAG: emparejar por nombre y una sola escritura al DCS.

- Un paquete de otro SAG trae ids que aqui pueden ser de OTRO tag: antes se
  emparejaba por id y se pisaba el nombre de un tag ajeno.
- "Importar en todos": un SP que ya escribe otro SAG entra sin rol, y un
  handshake/heartbeat con tags de otro SAG no se importa.
"""
from __future__ import annotations

import json

import exclusividad
from test_export_import import cfg_export  # noqa: F401  (fixture)
from web.api import export_import as ei


def _pkg(tags, **extra):
    data = {"tags": dict({"tags": tags}, **extra)}
    return {"format": ei.FORMAT_ID, "version": 2, "exported_at": "2026-10-01",
            "selected": {"tags": True}, "data": data}


def _importar(pkg, modo="agregar"):
    return ei._aplicar_paquete(ei._normalizar_selected(pkg["selected"]), pkg["data"], modo, pkg["selected"])


def _store(cfg):
    return json.loads(cfg["tags"].read_text(encoding="utf-8"))


def test_un_id_repetido_de_otro_sag_no_pisa_un_tag_ajeno(cfg_export):  # noqa: F811
    # En destino el id 1 es A.PV1. El paquete trae id 1 = B.OTRO.
    aplicados, errores = _importar(_pkg([{"id": 1, "name": "B.OTRO", "data_type": "Float",
                                          "enabled": True, "categoria": "pv", "rol": ""}]))
    assert not errores
    st = _store(cfg_export)
    por = {t["name"]: t for t in st["tags"]}
    assert "A.PV1" in por and por["A.PV1"]["id"] == 1, "el tag existente quedo intacto"
    assert "B.OTRO" in por and por["B.OTRO"]["id"] != 1
    assert len({t["id"] for t in st["tags"]}) == len(st["tags"]), "ids unicos"


def test_un_sp_que_escribe_otro_sag_entra_sin_rol(cfg_export, monkeypatch):  # noqa: F811
    monkeypatch.setattr(exclusividad, "escritores_sp", lambda *a, **k: {"B.SP": "sag1"})
    aplicados, errores = _importar(_pkg([{"id": 50, "name": "B.SP", "data_type": "Float",
                                          "enabled": True, "categoria": "sp", "rol": "vel"}]))
    assert not errores
    assert aplicados["tags"]["sp_sin_rol"] == ["B.SP"]
    por = {t["name"]: t for t in _store(cfg_export)["tags"]}
    assert por["B.SP"]["rol"] == ""
    assert por["A.SP1"]["rol"] == "sp_a", "los SP propios no se tocan"


def test_un_handshake_con_tags_de_otro_sag_no_se_importa(cfg_export, monkeypatch):  # noqa: F811
    st = _store(cfg_export)
    st["handshake"] = {"enabled": True, "enable_fbk_tag": "", "enable_ext_tag": ""}
    cfg_export["tags"].write_text(json.dumps(st), encoding="utf-8")
    monkeypatch.setattr(exclusividad, "usos_dcs_otros", lambda *a, **k: {"HS.FBK": ("sag1", "handshake")})
    aplicados, errores = _importar(
        _pkg([], handshake={"enabled": True, "enable_fbk_tag": "HS.FBK", "enable_ext_tag": "HS.EXT"},
             heartbeat={"enabled": False, "tag_out": "HB.PROPIO", "tag_in": ""}),
        modo="reemplazar")
    assert not errores
    assert aplicados["tags"]["dcs_omitidos"] == ["handshake"]
    st = _store(cfg_export)
    assert st["handshake"] == {"enabled": True, "enable_fbk_tag": "", "enable_ext_tag": ""}
    assert st["heartbeat"]["tag_out"] == "HB.PROPIO", "lo que no choca si se importa"


def test_sin_varios_sag_el_import_es_el_de_siempre(cfg_export):  # noqa: F811
    aplicados, errores = _importar(_pkg([{"id": 60, "name": "B.SP2", "data_type": "Float",
                                          "enabled": True, "categoria": "sp", "rol": "vel2"}]))
    assert not errores and aplicados["tags"]["sp_sin_rol"] == []
    assert {t["name"]: t for t in _store(cfg_export)["tags"]}["B.SP2"]["rol"] == "vel2"


# ---------------------------------------------------------------------------
# 2026-10-02: "importar en todos" con un SAG que escribe, y respaldos por SAG
# ---------------------------------------------------------------------------

def _importar_cediendo(pkg, modo="agregar"):
    return ei._aplicar_paquete(ei._normalizar_selected(pkg["selected"]), pkg["data"], modo,
                               pkg["selected"], cede=True)


def test_el_sag_que_cede_ve_los_sp_pero_no_los_escribe(cfg_export, monkeypatch):  # noqa: F811
    monkeypatch.setattr(exclusividad, "escritores_sp", lambda *a, **k: {})
    monkeypatch.setattr(exclusividad, "usos_dcs_otros", lambda *a, **k: {})
    aplicados, errores = _importar_cediendo(
        _pkg([{"id": 70, "name": "B.SP3", "data_type": "Float", "enabled": True,
               "categoria": "sp", "rol": "vel3"}],
             handshake={"enabled": True, "enable_fbk_tag": "HS.F", "enable_ext_tag": "HS.E"}))
    assert not errores
    st = _store(cfg_export)
    por = {t["name"]: t for t in st["tags"]}
    assert por["B.SP3"]["rol"] == "", "el SP entra (se ve) sin rol (no se escribe)"
    assert (st.get("handshake") or {}).get("enable_fbk_tag") != "HS.F", "el handshake es del que escribe"
    assert por["A.SP1"]["rol"] == "sp_a", "lo que no viene en el paquete no se toca"


def test_un_handshake_previo_que_choca_se_vacia(cfg_export, monkeypatch):  # noqa: F811
    st = _store(cfg_export)
    st["handshake"] = {"enabled": True, "enable_fbk_tag": "HS.F", "enable_ext_tag": "HS.E"}
    cfg_export["tags"].write_text(json.dumps(st), encoding="utf-8")
    monkeypatch.setattr(exclusividad, "escritores_sp", lambda *a, **k: {})
    monkeypatch.setattr(exclusividad, "usos_dcs_otros", lambda *a, **k: {})
    aplicados, errores = _importar_cediendo(
        _pkg([], handshake={"enabled": True, "enable_fbk_tag": "HS.F", "enable_ext_tag": "HS.E"}))
    assert not errores
    assert aplicados["tags"]["dcs_liberados"] == ["handshake"]
    hs = _store(cfg_export)["handshake"]
    assert hs["enable_fbk_tag"] == "" and hs["enable_ext_tag"] == ""


def test_dos_imports_en_el_mismo_segundo_no_comparten_respaldo(cfg_export):  # noqa: F811
    sel = {"reglas": True}
    a = ei._crear_backup(sel, {"reglas": []}, sel)
    b = ei._crear_backup(sel, {"reglas": []}, sel)
    assert a != b


def test_el_respaldo_se_resuelve_dentro_del_sag(cfg_export):  # noqa: F811
    d = cfg_export["tmp"] / ".backup" / "import_20261001_120000"
    d.mkdir(parents=True)
    # Un historial escrito dentro del contenedor (/app/...) sigue sirviendo.
    assert ei._resolver_backup("/app/config/sags/sag2/.backup/import_20261001_120000") == str(d)
    # Cualquier otra carpeta no: no se copian JSON ajenos a esta config.
    assert ei._resolver_backup(str(cfg_export["tmp"])) is None
    assert ei._resolver_backup("/etc") is None
    res, err = ei.restaurar_backup("/tmp/no_es_un_respaldo")
    assert err


def test_deshacer_no_devuelve_un_sp_que_ahora_escribe_otro_sag(cfg_export, monkeypatch):  # noqa: F811
    d = cfg_export["tmp"] / ".backup" / "import_20261001_120001"
    d.mkdir(parents=True)
    (d / "tags.json").write_text(cfg_export["tags"].read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(exclusividad, "escritores_sp", lambda *a, **k: {"A.SP1": "sag1"})
    monkeypatch.setattr(exclusividad, "usos_dcs_otros", lambda *a, **k: {})
    res, err = ei.restaurar_backup(str(d))
    assert err is None and res["sp_sin_rol"] == ["A.SP1"]
    assert {t["name"]: t for t in _store(cfg_export)["tags"]}["A.SP1"]["rol"] == ""
