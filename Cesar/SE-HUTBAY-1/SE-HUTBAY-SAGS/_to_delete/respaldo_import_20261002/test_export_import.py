# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.api import export_import as ei


@pytest.fixture
def cfg_export(monkeypatch, tmp_path):
    tags = tmp_path / "tags.json"
    contrato = tmp_path / "contrato.json"
    reglas = tmp_path / "reglas.json"
    filtros = tmp_path / "filtros.json"
    tracking = tmp_path / "tracking.json"
    variables = tmp_path / "variables.json"
    kepserver = tmp_path / "kepserver.json"

    tags.write_text(json.dumps({
        "next_id": 3,
        "tags": [
            {"id": 1, "name": "A.PV1", "data_type": "Float", "enabled": True,
             "categoria": "pv", "rol": "pv_a", "pseudonimo": "PV A"},
            {"id": 2, "name": "A.SP1", "data_type": "Float", "enabled": True,
             "categoria": "sp", "rol": "sp_a", "pseudonimo": "SP A"},
        ],
    }), encoding="utf-8")
    contrato.write_text(json.dumps({
        "variables_proceso": ["pv_a"],
        "setpoints": ["sp_a"],
        "descripciones": {"pv_a": "PV A", "sp_a": "SP A"},
        "limites_sp": {"sp_a": [0, 100]},
    }), encoding="utf-8")
    reglas.write_text(json.dumps([
        {"id": "R1", "nombre": "Regla 1", "habilitada": True, "condicion": {}, "acciones": []},
    ]), encoding="utf-8")
    filtros.write_text("{}", encoding="utf-8")
    tracking.write_text("{}", encoding="utf-8")
    variables.write_text(json.dumps({"crudas": {}, "definiciones": []}), encoding="utf-8")
    kepserver.write_text(json.dumps({
        "host": "127.0.0.1", "port": 49320, "timeout_s": 2.0,
        "aceptar_uncertain": False, "estancado_alerta_s": 60.0, "retencion_s": 5.0,
    }), encoding="utf-8")

    import web.state as st
    import web.api.contrato as ctr
    import web.api.config as cfgmod
    from connectors import kepserver as kep_mod

    monkeypatch.setattr(st, "TAGS_JSON", str(tags))
    monkeypatch.setattr(st, "CONTRATO_JSON", str(contrato))
    monkeypatch.setattr(st, "REGLAS_JSON", str(reglas))
    monkeypatch.setattr(st, "FILTROS_JSON", str(filtros))
    monkeypatch.setattr(st, "TRACKING_JSON", str(tracking))
    monkeypatch.setattr(st, "VARIABLES_JSON", str(variables))
    monkeypatch.setattr(ei, "TAGS_JSON", str(tags))
    monkeypatch.setattr(ei, "CONTRATO_JSON", str(contrato))
    monkeypatch.setattr(ei, "REGLAS_JSON", str(reglas))
    monkeypatch.setattr(ei, "FILTROS_JSON", str(filtros))
    monkeypatch.setattr(ei, "TRACKING_JSON", str(tracking))
    monkeypatch.setattr(ei, "VARIABLES_JSON", str(variables))
    monkeypatch.setattr(ctr, "CONTRATO_JSON", str(contrato))
    monkeypatch.setattr(cfgmod, "VARIABLES_JSON", str(variables))
    monkeypatch.setattr(ei, "_CFG_DIR", str(tmp_path))
    backup_dir = tmp_path / ".backup"
    backup_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(ei, "ULTIMO_IMPORT_JSON", str(backup_dir / "ultimo_import.json"))
    monkeypatch.setattr(ei, "HISTORIAL_IMPORT_JSON", str(backup_dir / "historial_import.json"))
    monkeypatch.setattr(kep_mod, "CONFIG_JSON", str(kepserver))
    monkeypatch.setattr(ei, "_MODULO_A_RUTA", {
        **ei._MODULO_A_RUTA,
        "tags": str(tags),
        "kepserver": str(kepserver),
        "tracking": str(tracking),
        "variables": str(variables),
    })

    class _Eng:
        def status(self):
            return {"running": False}

    monkeypatch.setattr(ei, "_se_engine", _Eng())

    return {"tags": tags, "contrato": contrato, "reglas": reglas, "variables": variables, "tmp": tmp_path}


def test_export_tags_unificado(cfg_export):
    pkg = ei.build_export_package({"tags": True})
    assert pkg["selected"]["tags"] is True
    assert "tags" in pkg["data"]
    assert len(pkg["data"]["tags"]["tags"]) == 2


def test_export_incluye_contrato_y_selected(cfg_export):
    pkg = ei.build_export_package({"contrato": True, "filtros": True})
    assert pkg["selected"]["contrato"] is True
    assert "contrato" in pkg["data"]


def test_import_agrega_tag_nuevo(cfg_export):
    pkg = {
        "format": ei.FORMAT_ID,
        "version": 2,
        "exported_at": "2026-01-01",
        "selected": {"tags": True},
        "data": {
            "tags": {
                "tags": [{"id": 99, "name": "A.PV_NUEVA", "data_type": "Float",
                          "enabled": True, "categoria": "pv", "rol": "pv_nueva",
                          "pseudonimo": "Nueva"}],
                "next_id": 100,
            },
        },
    }
    aplicados, errores = ei._aplicar_paquete(
        ei._normalizar_selected(pkg["selected"]), pkg["data"], "agregar", pkg["selected"])
    assert not errores
    assert aplicados["tags"]["agregados"]
    store = json.loads(cfg_export["tags"].read_text(encoding="utf-8"))
    assert "A.PV_NUEVA" in {t["name"] for t in store["tags"]}


def test_paquete_legacy_tags_sigue_funcionando(cfg_export):
    pkg = {
        "format": ei.FORMAT_ID,
        "version": 1,
        "exported_at": "2026-01-01",
        "selected": {"tags_kepserver": True, "entrada_datos": True},
        "data": {
            "tags_kepserver": {
                "tags": [{"id": 88, "name": "A.LEG", "data_type": "Float",
                          "enabled": True, "categoria": "pv", "rol": ""}],
            },
            "entrada_datos": {"tags": [{"id": 88, "name": "A.LEG", "pseudonimo": "Leg"}]},
        },
    }
    sel = ei._normalizar_selected(pkg["selected"])
    assert ei._tags_en_paquete(pkg["selected"], pkg["data"])
    aplicados, errores = ei._aplicar_paquete(sel, pkg["data"], "agregar", pkg["selected"])
    assert not errores


def test_import_bloqueado_si_motor_corre(monkeypatch, cfg_export):
    monkeypatch.setattr(ei, "_motor_corriendo", lambda: True)
    from app import app

    client = app.test_client()
    pkg = ei.build_export_package({"filtros": True})
    r = client.post("/api/export-import/apply", json={"package": pkg, "modo": "agregar"})
    assert r.status_code == 409


def test_variables_agregar_omite_duplicado(cfg_export, monkeypatch):
    import web.api.config as cfgmod

    cfgmod.VARIABLES_JSON = str(cfg_export["variables"])
    ei.VARIABLES_JSON = str(cfg_export["variables"])
    cfg_export["variables"].write_text(json.dumps({
        "crudas": {"cruda_x": "desc", "cruda_y": "y"},
        "definiciones": [{
            "nombre": "calc_a", "descripcion": "", "tipo": "aritmetica",
            "operacion": "suma", "args": ["cruda_x", "cruda_y"],
        }],
    }), encoding="utf-8")

    bloque = {
        "crudas": {"cruda_x": "otra", "cruda_nueva": "n"},
        "definiciones": [{
            "nombre": "calc_a", "descripcion": "dup", "tipo": "aritmetica",
            "operacion": "suma", "args": ["cruda_x", "cruda_nueva"],
        }, {
            "nombre": "calc_b", "descripcion": "", "tipo": "aritmetica",
            "operacion": "suma", "args": ["cruda_x", "cruda_nueva"],
        }],
    }
    res = ei._aplicar_variables(bloque, "agregar")
    assert not res.get("error")
    assert "cruda_nueva" in res["crudas_agregadas"]
    assert "calc_b" in res["definiciones_agregadas"]
    assert "calc_a" not in res.get("definiciones_agregadas", [])

    guardado = json.loads(cfg_export["variables"].read_text(encoding="utf-8"))
    nombres = {d["nombre"] for d in guardado["definiciones"]}
    assert "calc_b" in nombres
    assert guardado["crudas"]["cruda_x"] == "desc"


def test_historial_guarda_varios_backups(cfg_export):
    b1 = cfg_export["tmp"] / "b1"
    b2 = cfg_export["tmp"] / "b2"
    b1.mkdir()
    b2.mkdir()
    (b1 / "reglas.json").write_text("[]", encoding="utf-8")
    ei._guardar_manifest_backup(
        str(b1), {"reglas": True}, {"reglas": []}, {"reglas": True}, modo="copias",
    )
    ei._guardar_manifest_backup(
        str(b2), {"filtros": True}, {"filtros": {}}, {"filtros": True}, modo="agregar",
    )
    hist = ei.listar_backups_historial(enriquecer=True)
    assert len(hist) == 2
    assert hist[0]["backup_dir"] == str(b2)
    assert hist[0]["modo"] == "agregar"
    assert hist[1]["modo"] == "copias"
    assert "Motor de reglas" in hist[1]["modulos_labels"]


def test_undo_restaura_desde_backup(cfg_export):
    reglas = cfg_export["reglas"]
    original = reglas.read_text(encoding="utf-8")
    reglas.write_text("[]", encoding="utf-8")

    backup_dir = cfg_export["tmp"] / "bak"
    backup_dir.mkdir()
    (backup_dir / "reglas.json").write_text(original, encoding="utf-8")
    ei._guardar_manifest_backup(str(backup_dir), {"reglas": True}, {"reglas": []}, {"reglas": True})

    res, err = ei.restaurar_ultimo_backup()
    assert err is None
    assert reglas.read_text(encoding="utf-8") == original


def test_orden_aplica_contrato_despues_de_tags(cfg_export, monkeypatch):
    llamadas = []

    monkeypatch.setattr(ei, "_aplicar_tags_fusionado", lambda *a, **k: (llamadas.append("tags") or {"agregados": []}))
    monkeypatch.setattr(ei, "_aplicar_modulo", lambda c, b, m: (llamadas.append(c) or {"ok": True}))

    raw = {"tags": True, "contrato": True, "filtros": True}
    sel = ei._normalizar_selected(raw)
    data = {"tags": {"tags": []}, "contrato": {}, "filtros": {}}
    ei._aplicar_paquete(sel, data, "agregar", raw)
    assert llamadas == ["tags", "contrato", "filtros"]
