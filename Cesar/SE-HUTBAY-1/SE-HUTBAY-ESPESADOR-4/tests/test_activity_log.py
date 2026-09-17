# -*- coding: utf-8 -*-
"""Tests del log de actividad de configuracion (solo observabilidad)."""
from __future__ import annotations

import json
import time

import pytest

from web.state import ActivityLogCollector


@pytest.fixture
def log():
    return ActivityLogCollector()


def test_unsaved_y_clear(log):
    log.mark_unsaved("variables", "Variables calc.")
    activas = log.get_active()
    assert len(activas) == 1
    assert activas[0]["kind"] == "unsaved"
    log.clear_unsaved("variables")
    assert log.get_active() == []


def test_saved_expira(log, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr("web.state.time.time", lambda: t[0])

    log.log_saved("filtros", "Filtros Exp-Q", "Guardado")
    assert len(log.get_active()) == 1
    assert log.get_active()[0]["kind"] == "saved"

    t[0] += ActivityLogCollector.TRANSIENT_S + 0.1
    assert log.get_active() == []


def test_restart_se_limpia_al_iniciar(log):
    log.log_restart_needed("defuzzy", "Defuzzificacion")
    assert len(log.get_active()) == 1
    log.clear_restart()
    assert log.get_active() == []


def test_historial_max_100(log):
    for i in range(105):
        log.log_info(f"evento {i}")
    hist = log.get_history()
    assert len(hist) == 100
    assert hist[0]["message"] == "evento 104"


def test_filtro_por_tipo(log):
    log.mark_unsaved("variables", "Variables calc.")
    log.log_error("variables", "Variables calc.", "fallo")
    assert len(log.get_history("error")) == 1
    assert log.get_history("error")[0]["kind"] == "error"


def test_persistencia_en_disco(tmp_path):
    path = tmp_path / "activity_log.json"
    log1 = ActivityLogCollector(storage_path=str(path))
    log1.log_info("evento persistido")
    log1.log_error("variables", "Variables calc.", "fallo de prueba", "detalle")
    log1.mark_unsaved("fuzzy", "Fuzzificacion")

    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    assert len(on_disk["entries"]) == 3

    log2 = ActivityLogCollector(storage_path=str(path))
    hist = log2.get_history()
    assert len(hist) == 3
    msgs = {e["message"] for e in hist}
    assert "fallo de prueba" in msgs
    assert "evento persistido" in msgs
    activas = log2.get_active()
    assert any(e["message"] == "evento persistido" for e in activas)
    assert any(e["kind"] == "error" for e in activas)
    assert any(e["kind"] == "unsaved" for e in activas)


def test_archivo_corrupto_arranca_vacio(tmp_path):
    path = tmp_path / "activity_log.json"
    path.write_text("{no es json", encoding="utf-8")
    log = ActivityLogCollector(storage_path=str(path))
    assert log.get_history() == []
    assert log.get_active() == []


def test_dismiss_persiste(tmp_path):
    path = tmp_path / "activity_log.json"
    log = ActivityLogCollector(storage_path=str(path))
    log.log_restart_needed("defuzzy", "Defuzzificacion")
    eid = log.get_active()[0]["id"]
    assert log.dismiss(eid)

    log2 = ActivityLogCollector(storage_path=str(path))
    assert log2.get_active() == []
    assert log2.get_history()[0]["dismissed"] is True
