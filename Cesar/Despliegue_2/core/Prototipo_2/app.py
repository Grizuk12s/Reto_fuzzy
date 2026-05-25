# -*- coding: utf-8 -*-
"""Interfaz web Flask para edición en vivo de reglas y ejecución de simulaciones.

Endpoints API:
  GET    /api/reglas              — Listar todas las reglas
  GET    /api/reglas/<id>         — Obtener una regla
  PUT    /api/reglas/<id>         — Actualizar una regla
  POST   /api/reglas              — Crear una regla nueva
  DELETE /api/reglas/<id>         — Eliminar una regla
  POST   /api/simulacion          — Ejecutar simulación con reglas actuales

Interfaz web:
  GET    /                        — Panel de gestión de reglas + simulación
"""

from __future__ import annotations

import json
import os
import traceback

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, Response

from config import ETIQUETAS_REGLAS_DISPONIBLES, VARIABLES_REGLAS_DISPONIBLES
from defuzzy_actions import ACCIONES_DISPONIBLES

app = Flask(__name__)

REGLAS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reglas.json")

# ============================================================
# Helpers para reglas.json
# ============================================================

def _load_reglas() -> list[dict]:
    if not os.path.exists(REGLAS_JSON):
        return []
    with open(REGLAS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_reglas(reglas: list[dict]) -> None:
    with open(REGLAS_JSON, "w", encoding="utf-8") as f:
        json.dump(reglas, f, indent=2, ensure_ascii=False)


def _find_regla(reglas: list[dict], regla_id: str):
    for i, r in enumerate(reglas):
        if str(r.get("id")) == str(regla_id):
            return i, r
    return None, None


VARIABLES_DISPONIBLES = list(VARIABLES_REGLAS_DISPONIBLES)
ETIQUETAS_DISPONIBLES = list(ETIQUETAS_REGLAS_DISPONIBLES)

VARIABLES_VALIDAS = set(VARIABLES_DISPONIBLES)
ETIQUETAS_VALIDAS = set(ETIQUETAS_DISPONIBLES)
ACCIONES_VALIDAS = set(ACCIONES_DISPONIBLES)


def _normalizar_regla_payload(data: dict, require_id: bool = True) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, "El payload debe ser un objeto JSON."

    regla = dict(data)
    if require_id:
        regla_id = str(regla.get("id", "")).strip()
        if not regla_id:
            return None, "Campo 'id' requerido."
        regla["id"] = regla_id

    condiciones = regla.get("if")
    if not isinstance(condiciones, list) or not condiciones:
        return None, "Campo 'if' debe ser una lista no vacia."

    condiciones_norm = []
    for idx, condicion in enumerate(condiciones, start=1):
        if not isinstance(condicion, (list, tuple)) or len(condicion) != 2:
            return None, f"La condicion #{idx} debe tener el formato [variable, etiqueta]."
        variable = str(condicion[0]).strip()
        etiqueta = str(condicion[1]).strip().upper()
        if variable not in VARIABLES_VALIDAS:
            return None, f"Variable invalida en condicion #{idx}: '{variable}'."
        if etiqueta not in ETIQUETAS_VALIDAS:
            return None, f"Etiqueta invalida en condicion #{idx}: '{etiqueta}'."
        condiciones_norm.append([variable, etiqueta])
    regla["if"] = condiciones_norm

    acciones = regla.get("then")
    if not isinstance(acciones, list) or not acciones:
        return None, "Campo 'then' debe ser una lista no vacia."

    acciones_norm = []
    for idx, accion in enumerate(acciones, start=1):
        accion_norm = str(accion).strip().upper()
        if accion_norm not in ACCIONES_VALIDAS:
            return None, f"Accion invalida en then #{idx}: '{accion_norm}'."
        acciones_norm.append(accion_norm)
    regla["then"] = acciones_norm

    for key in ("weight", "priority"):
        if key in regla:
            try:
                regla[key] = float(regla[key])
            except (TypeError, ValueError):
                return None, f"Campo '{key}' debe ser numerico."

    return regla, None


# ============================================================
# API REST — Reglas
# ============================================================

@app.route("/api/reglas", methods=["GET"])
def api_get_reglas():
    return jsonify(_load_reglas())


@app.route("/api/reglas/<regla_id>", methods=["GET"])
def api_get_regla(regla_id: str):
    reglas = _load_reglas()
    _, regla = _find_regla(reglas, regla_id)
    if regla is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada"}), 404
    return jsonify(regla)


@app.route("/api/reglas/<regla_id>", methods=["PUT"])
def api_update_regla(regla_id: str):
    reglas = _load_reglas()
    idx, _ = _find_regla(reglas, regla_id)
    if idx is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada"}), 404
    data = request.get_json(force=True)
    data["id"] = regla_id
    regla_norm, error = _normalizar_regla_payload(data, require_id=True)
    if error is not None:
        return jsonify({"error": error}), 400
    reglas[idx] = regla_norm
    _save_reglas(reglas)
    return jsonify({"ok": True, "regla": regla_norm})


@app.route("/api/reglas", methods=["POST"])
def api_create_regla():
    data = request.get_json(force=True)
    regla_norm, error = _normalizar_regla_payload(data, require_id=True)
    if error is not None:
        return jsonify({"error": error}), 400
    reglas = _load_reglas()
    _, existing = _find_regla(reglas, regla_norm["id"])
    if existing is not None:
        return jsonify({"error": f"Regla '{regla_norm['id']}' ya existe"}), 409
    reglas.append(regla_norm)
    _save_reglas(reglas)
    return jsonify({"ok": True, "regla": regla_norm}), 201


@app.route("/api/reglas/<regla_id>", methods=["DELETE"])
def api_delete_regla(regla_id: str):
    reglas = _load_reglas()
    idx, _ = _find_regla(reglas, regla_id)
    if idx is None:
        return jsonify({"error": f"Regla '{regla_id}' no encontrada"}), 404
    removed = reglas.pop(idx)
    _save_reglas(reglas)
    return jsonify({"ok": True, "eliminada": removed})


@app.route("/api/meta", methods=["GET"])
def api_meta():
    return jsonify({
        "variables": VARIABLES_DISPONIBLES,
        "etiquetas": ETIQUETAS_DISPONIBLES,
        "acciones": ACCIONES_DISPONIBLES,
    })


# ============================================================
# API — Simulación
# ============================================================

@app.route("/api/simulacion", methods=["POST"])
def api_simulacion():
    """Ejecuta la simulación con las reglas actuales de reglas.json."""
    try:
        from runner import correr_prueba_general
        from simulacion import generar_datos_proceso, SETPOINTS_BASE, LIMITES_SP, META_FLAGS

        params = request.get_json(silent=True) or {}
        n_muestras = int(params.get("n_muestras", 200))
        dt_s = float(params.get("dt_s", 5.0))
        seed = int(params.get("seed", 42))

        df_data = generar_datos_proceso(n_muestras=n_muestras, dt_s=dt_s, seed=seed)

        resultados = correr_prueba_general(
            df_data=df_data,
            setpoints_base=SETPOINTS_BASE,
            limites_sp=LIMITES_SP,
            meta_flags=META_FLAGS,
            min_belief=0.05,
            verbose=False,
            usar_reglas_json=True,
        )

        df_res = resultados["resultados"]
        df_ev = resultados["eventos"]

        sp_final = {}
        if not df_res.empty:
            ultima = df_res.iloc[-1]
            sp_final = {
                "sp_ton": round(float(ultima["sp_ton"]), 2),
                "sp_am": round(float(ultima["sp_am"]), 2),
                "sp_ac": round(float(ultima["sp_ac"]), 2),
                "sp_rpm": round(float(ultima["sp_rpm"]), 2),
            }

        eventos_list = []
        if not df_ev.empty:
            for _, row in df_ev.iterrows():
                eventos_list.append({
                    "t_s": round(float(row["t_s"]), 1),
                    "regla_id": str(row["regla_id"]),
                    "acciones": str(row["acciones"]),
                    "belief": round(float(row["belief"]), 4),
                })

        activaciones_por_regla = {}
        if not df_ev.empty:
            counts = df_ev["regla_id"].value_counts().to_dict()
            activaciones_por_regla = {str(k): int(v) for k, v in counts.items()}

        return jsonify({
            "ok": True,
            "muestras": len(df_res),
            "total_eventos": len(df_ev),
            "setpoints_finales": sp_final,
            "activaciones_por_regla": activaciones_por_regla,
            "eventos": eventos_list[:50],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ============================================================
# API — Simulación streaming (para gráficos en tiempo real)
# ============================================================

# Estado global de la simulación en streaming
_sim_state = {
    "running": False,
    "cursor": 0,
    "df_resultados": None,
    "df_eventos": None,
    "batch_size": 5,
}


@app.route("/api/simulacion/start", methods=["POST"])
def api_sim_start():
    """Inicia una nueva simulación y prepara los datos para streaming."""
    try:
        from runner import correr_prueba_general
        from simulacion import generar_datos_proceso, SETPOINTS_BASE, LIMITES_SP, META_FLAGS

        params = request.get_json(silent=True) or {}
        n_muestras = int(params.get("n_muestras", 200))
        dt_s = float(params.get("dt_s", 5.0))
        seed = int(params.get("seed", 42))

        df_data = generar_datos_proceso(n_muestras=n_muestras, dt_s=dt_s, seed=seed)
        resultados = correr_prueba_general(
            df_data=df_data,
            setpoints_base=SETPOINTS_BASE,
            limites_sp=LIMITES_SP,
            meta_flags=META_FLAGS,
            min_belief=0.05,
            verbose=False,
            usar_reglas_json=True,
        )

        _sim_state["df_resultados"] = resultados["resultados"]
        _sim_state["df_eventos"] = resultados["eventos"]
        _sim_state["cursor"] = 0
        _sim_state["running"] = True
        _sim_state["batch_size"] = int(params.get("batch_size", 5))

        return jsonify({"ok": True, "total": len(resultados["resultados"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/simulacion/next", methods=["GET"])
def api_sim_next():
    """Devuelve el siguiente lote de puntos. Simula streaming en tiempo real."""
    if not _sim_state["running"] or _sim_state["df_resultados"] is None:
        return jsonify({"ok": False, "error": "No hay simulación activa. Llama a /api/simulacion/start primero."}), 400

    df = _sim_state["df_resultados"]
    cursor = _sim_state["cursor"]
    batch = _sim_state["batch_size"]
    total = len(df)

    if cursor >= total:
        _sim_state["running"] = False
        return jsonify({"ok": True, "done": True, "points": [], "eventos": [], "cursor": cursor, "total": total})

    end = min(cursor + batch, total)
    chunk = df.iloc[cursor:end]

    points = []
    for _, row in chunk.iterrows():
        points.append({
            "t_s": round(float(row["t_s"]), 1),
            "t_min": round(float(row["t_min"]), 2),
            "potencia": round(float(row["potencia"]), 1),
            "nivel": round(float(row["nivel"]), 2),
            "presion": round(float(row["presion"]), 2),
            "p80": round(float(row["p80"]), 1),
            "densidad": round(float(row["densidad"]), 3),
            "sp_ton": round(float(row["sp_ton"]), 2),
            "sp_am": round(float(row["sp_am"]), 2),
            "sp_ac": round(float(row["sp_ac"]), 2),
            "sp_rpm": round(float(row["sp_rpm"]), 2),
            "n_reglas": int(row["n_reglas_activadas"]),
            "reglas": str(row.get("reglas_activadas", "")),
        })

    # Eventos en este rango temporal
    df_ev = _sim_state["df_eventos"]
    ev_list = []
    if not df_ev.empty:
        t_start = float(chunk.iloc[0]["t_s"])
        t_end = float(chunk.iloc[-1]["t_s"])
        mask = (df_ev["t_s"] >= t_start) & (df_ev["t_s"] <= t_end)
        for _, row in df_ev[mask].iterrows():
            ev_list.append({
                "t_s": round(float(row["t_s"]), 1),
                "regla_id": str(row["regla_id"]),
                "acciones": str(row["acciones"]),
                "belief": round(float(row["belief"]), 4),
            })

    _sim_state["cursor"] = end

    return jsonify({
        "ok": True,
        "done": False,
        "points": points,
        "eventos": ev_list,
        "cursor": end,
        "total": total,
    })


@app.route("/api/simulacion/reset", methods=["POST"])
def api_sim_reset():
    """Reinicia el cursor al inicio para repetir la simulación."""
    _sim_state["cursor"] = 0
    _sim_state["running"] = True
    return jsonify({"ok": True})


# ============================================================
# Interfaz web
# ============================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sistema Experto — Editor de Reglas</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}
h1{color:#38bdf8;margin-bottom:8px}
h2{color:#94a3b8;font-size:1rem;margin-bottom:20px;font-weight:400}
.top-bar{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:20px}
button{cursor:pointer;border:none;border-radius:6px;padding:8px 16px;font-size:.875rem;font-weight:600;transition:.15s}
.btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}
.btn-success{background:#22c55e;color:#fff}.btn-success:hover{background:#16a34a}
.btn-danger{background:#ef4444;color:#fff}.btn-danger:hover{background:#dc2626}
.btn-sm{padding:4px 10px;font-size:.75rem}
table{width:100%;border-collapse:collapse;background:#1e293b;border-radius:8px;overflow:hidden;margin-bottom:20px}
th{background:#334155;color:#94a3b8;text-align:left;padding:10px 12px;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em}
td{padding:8px 12px;border-top:1px solid #334155;font-size:.85rem;vertical-align:top}
tr:hover{background:#2d3a4f}
.tag{display:inline-block;background:#334155;border-radius:4px;padding:2px 6px;margin:1px;font-size:.75rem}
.tag-var{border-left:3px solid #38bdf8}
.tag-label{border-left:3px solid #a78bfa}
.tag-action{border-left:3px solid #fb923c}
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.6);z-index:100;justify-content:center;align-items:center}
.modal-overlay.active{display:flex}
.modal{background:#1e293b;border-radius:12px;padding:24px;width:680px;max-width:95vw;max-height:90vh;overflow-y:auto;box-shadow:0 20px 40px rgba(0,0,0,.5)}
.modal h3{color:#38bdf8;margin-bottom:16px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group.full{grid-column:1/-1}
label{font-size:.75rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.03em}
input,select,textarea{background:#0f172a;border:1px solid #475569;border-radius:6px;padding:8px 10px;color:#e2e8f0;font-size:.85rem}
input:focus,select:focus,textarea:focus{outline:none;border-color:#3b82f6}
textarea{resize:vertical;min-height:60px;font-family:monospace}
.cond-row{display:flex;gap:6px;align-items:center;margin-bottom:4px}
.cond-row select{flex:1}
.cond-row button{flex-shrink:0}
#sim-results{background:#1e293b;border-radius:8px;padding:16px;margin-top:20px;display:none}
#sim-results h3{color:#22c55e;margin-bottom:12px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}
.stat-card{background:#334155;border-radius:8px;padding:12px;text-align:center}
.stat-card .val{font-size:1.5rem;font-weight:700;color:#38bdf8}
.stat-card .lbl{font-size:.7rem;color:#94a3b8;text-transform:uppercase;margin-top:4px}
.ev-table{max-height:300px;overflow-y:auto}
.loading{color:#94a3b8;font-style:italic}
</style>
</head>
<body>
<nav style="display:flex;gap:16px;margin-bottom:16px">
  <a href="/" style="color:#38bdf8;font-weight:600;text-decoration:none;border-bottom:2px solid #38bdf8;padding-bottom:2px">Reglas</a>
  <a href="/graficos" style="color:#94a3b8;text-decoration:none">Gráficos en Vivo</a>
</nav>
<div class="top-bar">
  <div><h1>Sistema Experto Difuso</h1><h2>Editor de reglas en vivo — Las reglas se aplican al ejecutar la simulación</h2></div>
  <div style="display:flex;gap:8px">
    <button class="btn-primary" onclick="openModal()">+ Nueva Regla</button>
    <button class="btn-success" onclick="runSim()">&#9654; Ejecutar Simulación</button>
  </div>
</div>

<table id="rules-table">
<thead><tr>
  <th>ID</th><th>Prioridad</th><th>Condiciones (IF)</th><th>Acciones (THEN)</th>
  <th>Weight</th><th>Cooldown</th><th style="width:100px">Opciones</th>
</tr></thead>
<tbody id="rules-body"></tbody>
</table>

<div id="sim-results">
  <h3>Resultados de la Simulación</h3>
  <div class="stat-grid" id="stat-grid"></div>
  <h4 style="color:#94a3b8;margin-bottom:8px">Eventos (máx. 50)</h4>
  <div class="ev-table">
    <table><thead><tr><th>t(s)</th><th>Regla</th><th>Acciones</th><th>Belief</th></tr></thead>
    <tbody id="ev-body"></tbody></table>
  </div>
</div>

<!-- Modal edición -->
<div class="modal-overlay" id="modal-overlay">
<div class="modal">
  <h3 id="modal-title">Nueva Regla</h3>
  <div class="form-grid">
    <div class="form-group"><label>ID</label><input id="f-id" placeholder="ej: 21.0"></div>
    <div class="form-group"><label>Prioridad</label><input id="f-priority" type="number" step="0.1" value="50"></div>
    <div class="form-group"><label>Weight</label><input id="f-weight" type="number" step="0.1" value="1.0"></div>
    <div class="form-group"><label>Cooldown (s o JSON)</label><input id="f-cooldown" value="60"></div>
    <div class="form-group full">
      <label>Condiciones (IF) — AND entre todas</label>
      <div id="conds-container"></div>
      <button class="btn-primary btn-sm" onclick="addCond()" style="margin-top:4px">+ Condición</button>
    </div>
    <div class="form-group full">
      <label>Acciones (THEN)</label>
      <div id="actions-container"></div>
      <button class="btn-primary btn-sm" onclick="addAction()" style="margin-top:4px">+ Acción</button>
    </div>
  </div>
  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:20px">
    <button class="btn-danger" onclick="closeModal()">Cancelar</button>
    <button class="btn-success" id="btn-save" onclick="saveRule()">Guardar</button>
  </div>
</div>
</div>

<script>
const VARS = VARIABLES_JSON;
const LABELS = LABELS_JSON;
const ACTIONS = ACTIONS_JSON;

let editingId = null;

async function loadRules() {
  const res = await fetch('/api/reglas');
  const rules = await res.json();
  const tbody = document.getElementById('rules-body');
  tbody.innerHTML = '';
  rules.sort((a,b) => (b.priority||0) - (a.priority||0));
  for (const r of rules) {
    const tr = document.createElement('tr');
    const conds = (r['if']||[]).map(c => `<span class="tag tag-var">${c[0]}</span> <span class="tag tag-label">${c[1]}</span>`).join('<br>');
    const acts = (r['then']||[]).map(a => `<span class="tag tag-action">${a}</span>`).join('<br>');
    const cd = typeof r.cooldown_s === 'object' ? JSON.stringify(r.cooldown_s) : r.cooldown_s;
    tr.innerHTML = `<td><b>${r.id}</b></td><td>${r.priority}</td><td>${conds}</td><td>${acts}</td>
      <td>${r.weight}</td><td style="font-size:.75rem">${cd}</td>
      <td><button class="btn-primary btn-sm" onclick="editRule('${r.id}')">Editar</button>
      <button class="btn-danger btn-sm" onclick="deleteRule('${r.id}')">Borrar</button></td>`;
    tbody.appendChild(tr);
  }
}

function addCond(v='potencia', l='OK') {
  const c = document.getElementById('conds-container');
  const row = document.createElement('div');
  row.className = 'cond-row';
  row.innerHTML = `<select class="cv">${VARS.map(x=>`<option ${x===v?'selected':''}>${x}</option>`).join('')}</select>
    <select class="cl">${LABELS.map(x=>`<option ${x===l?'selected':''}>${x}</option>`).join('')}</select>
    <button class="btn-danger btn-sm" onclick="this.parentElement.remove()">✕</button>`;
  c.appendChild(row);
}

function addAction(a='DISMINUIR_TONELAJE') {
  const c = document.getElementById('actions-container');
  const row = document.createElement('div');
  row.className = 'cond-row';
  row.innerHTML = `<select class="ca">${ACTIONS.map(x=>`<option ${x===a?'selected':''}>${x}</option>`).join('')}</select>
    <button class="btn-danger btn-sm" onclick="this.parentElement.remove()">✕</button>`;
  c.appendChild(row);
}

function openModal(rule=null) {
  editingId = null;
  document.getElementById('modal-title').textContent = 'Nueva Regla';
  document.getElementById('f-id').value = '';
  document.getElementById('f-id').disabled = false;
  document.getElementById('f-priority').value = '50';
  document.getElementById('f-weight').value = '1.0';
  document.getElementById('f-cooldown').value = '60';
  document.getElementById('conds-container').innerHTML = '';
  document.getElementById('actions-container').innerHTML = '';
  if (rule) {
    editingId = rule.id;
    document.getElementById('modal-title').textContent = 'Editar Regla ' + rule.id;
    document.getElementById('f-id').value = rule.id;
    document.getElementById('f-id').disabled = true;
    document.getElementById('f-priority').value = rule.priority;
    document.getElementById('f-weight').value = rule.weight;
    document.getElementById('f-cooldown').value = typeof rule.cooldown_s === 'object' ? JSON.stringify(rule.cooldown_s) : rule.cooldown_s;
    for (const c of (rule['if']||[])) addCond(c[0], c[1]);
    for (const a of (rule['then']||[])) addAction(a);
  } else {
    addCond(); addAction();
  }
  document.getElementById('modal-overlay').classList.add('active');
}

function closeModal() { document.getElementById('modal-overlay').classList.remove('active'); }

async function editRule(id) {
  const res = await fetch('/api/reglas/' + id);
  if (!res.ok) return alert('Error cargando regla');
  const rule = await res.json();
  openModal(rule);
}

async function deleteRule(id) {
  if (!confirm('¿Eliminar regla ' + id + '?')) return;
  await fetch('/api/reglas/' + id, {method: 'DELETE'});
  loadRules();
}

async function saveRule() {
  const id = document.getElementById('f-id').value.trim();
  if (!id) return alert('ID requerido');
  const conds = [...document.querySelectorAll('#conds-container .cond-row')].map(r => [
    r.querySelector('.cv').value, r.querySelector('.cl').value
  ]);
  const acts = [...document.querySelectorAll('#actions-container .cond-row')].map(r => r.querySelector('.ca').value);
  let cd = document.getElementById('f-cooldown').value.trim();
  try { cd = JSON.parse(cd); } catch(e) { cd = parseFloat(cd) || 0; }

  const rule = {
    id: id,
    'if': conds,
    'then': acts,
    weight: parseFloat(document.getElementById('f-weight').value) || 1.0,
    priority: parseFloat(document.getElementById('f-priority').value) || 0,
    cooldown_s: cd,
  };

  let res;
  if (editingId) {
    res = await fetch('/api/reglas/' + editingId, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(rule)});
  } else {
    res = await fetch('/api/reglas', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(rule)});
  }
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Error');
  closeModal();
  loadRules();
}

async function runSim() {
  const panel = document.getElementById('sim-results');
  panel.style.display = 'block';
  document.getElementById('stat-grid').innerHTML = '<p class="loading">Ejecutando simulación...</p>';
  document.getElementById('ev-body').innerHTML = '';

  const res = await fetch('/api/simulacion', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})});
  const data = await res.json();
  if (!data.ok) { document.getElementById('stat-grid').innerHTML = '<p style="color:#ef4444">Error: '+data.error+'</p>'; return; }

  const sp = data.setpoints_finales || {};
  document.getElementById('stat-grid').innerHTML = `
    <div class="stat-card"><div class="val">${data.muestras}</div><div class="lbl">Muestras</div></div>
    <div class="stat-card"><div class="val">${data.total_eventos}</div><div class="lbl">Eventos</div></div>
    <div class="stat-card"><div class="val">${sp.sp_ton??'-'}</div><div class="lbl">SP Tonelaje</div></div>
    <div class="stat-card"><div class="val">${sp.sp_am??'-'}</div><div class="lbl">SP Agua Molino</div></div>
    <div class="stat-card"><div class="val">${sp.sp_ac??'-'}</div><div class="lbl">SP Agua Cajón</div></div>
    <div class="stat-card"><div class="val">${sp.sp_rpm??'-'}</div><div class="lbl">SP RPM Bomba</div></div>`;

  const evBody = document.getElementById('ev-body');
  evBody.innerHTML = '';
  for (const ev of (data.eventos||[])) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${ev.t_s}</td><td>${ev.regla_id}</td><td><span class="tag tag-action">${ev.acciones}</span></td><td>${ev.belief}</td>`;
    evBody.appendChild(tr);
  }
}

loadRules();
</script>
</body>
</html>"""


@app.route("/")
def index():
    page = HTML_PAGE
    page = page.replace("VARIABLES_JSON", json.dumps(VARIABLES_DISPONIBLES))
    page = page.replace("LABELS_JSON", json.dumps(ETIQUETAS_DISPONIBLES))
    page = page.replace("ACTIONS_JSON", json.dumps(ACCIONES_DISPONIBLES))
    return Response(page, mimetype="text/html")


# ============================================================
# Página de gráficos en tiempo real
# ============================================================

CHARTS_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sistema Experto — Gráficos en Tiempo Real</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}
h1{color:#38bdf8;margin-bottom:4px}
h2{color:#94a3b8;font-size:.9rem;margin-bottom:16px;font-weight:400}
nav{display:flex;gap:16px;margin-bottom:16px}
nav a{color:#94a3b8;text-decoration:none;padding-bottom:2px}
nav a.active{color:#38bdf8;font-weight:600;border-bottom:2px solid #38bdf8}
button{cursor:pointer;border:none;border-radius:6px;padding:8px 16px;font-size:.875rem;font-weight:600;transition:.15s}
.btn-success{background:#22c55e;color:#fff}.btn-success:hover{background:#16a34a}
.btn-danger{background:#ef4444;color:#fff}.btn-danger:hover{background:#dc2626}
.btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}
.controls{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.controls label{font-size:.8rem;color:#94a3b8}
.controls select,.controls input{background:#1e293b;border:1px solid #475569;color:#e2e8f0;border-radius:6px;padding:4px 8px;font-size:.8rem}
.status-bar{background:#1e293b;border-radius:8px;padding:10px 16px;margin-bottom:16px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
.status-bar .item{text-align:center}
.status-bar .val{font-size:1.1rem;font-weight:700;color:#38bdf8}
.status-bar .lbl{font-size:.65rem;color:#94a3b8;text-transform:uppercase}
.progress-bar{width:100%;height:6px;background:#334155;border-radius:3px;overflow:hidden;margin-bottom:4px}
.progress-bar .fill{height:100%;background:#22c55e;transition:width .3s}
.charts-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
.chart-card{background:#1e293b;border-radius:10px;padding:14px;position:relative}
.chart-card h3{font-size:.8rem;color:#94a3b8;margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em}
.chart-card canvas{width:100%!important;height:220px!important}
.events-panel{background:#1e293b;border-radius:10px;padding:14px;max-height:250px;overflow-y:auto}
.events-panel h3{font-size:.8rem;color:#94a3b8;margin-bottom:8px;text-transform:uppercase}
.ev-item{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #334155;font-size:.8rem}
.ev-item .t{color:#38bdf8;min-width:50px}
.ev-item .r{color:#a78bfa;min-width:40px}
.ev-item .a{color:#fb923c;flex:1}
@media(max-width:900px){.charts-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<nav>
  <a href="/">Reglas</a>
  <a href="/graficos" class="active">Gráficos en Vivo</a>
</nav>
<h1>Gráficos en Tiempo Real</h1>
<h2>Los datos se actualizan automáticamente cada 5 segundos simulando un proceso en vivo</h2>

<div class="controls">
  <button class="btn-success" id="btn-start" onclick="startStream()">&#9654; Iniciar Simulación</button>
  <button class="btn-danger" id="btn-stop" onclick="stopStream()" disabled>&#9632; Detener</button>
  <button class="btn-primary" id="btn-reset" onclick="resetStream()">&#8634; Reiniciar</button>
  <label>Velocidad:
    <select id="sel-speed" onchange="changeSpeed()">
      <option value="5000">5s (real)</option>
      <option value="2000" selected>2s (rápido)</option>
      <option value="1000">1s (muy rápido)</option>
      <option value="500">0.5s (turbo)</option>
    </select>
  </label>
  <label>Puntos por tick:
    <select id="sel-batch">
      <option value="3">3</option>
      <option value="5" selected>5</option>
      <option value="10">10</option>
      <option value="20">20</option>
    </select>
  </label>
</div>

<div class="progress-bar"><div class="fill" id="progress-fill" style="width:0%"></div></div>
<div class="status-bar" id="status-bar">
  <div class="item"><div class="val" id="st-cursor">0</div><div class="lbl">Muestra</div></div>
  <div class="item"><div class="val" id="st-total">—</div><div class="lbl">Total</div></div>
  <div class="item"><div class="val" id="st-time">0.0</div><div class="lbl">t (s)</div></div>
  <div class="item"><div class="val" id="st-eventos">0</div><div class="lbl">Eventos</div></div>
  <div class="item"><div class="val" id="st-sp-ton">—</div><div class="lbl">SP Ton</div></div>
  <div class="item"><div class="val" id="st-sp-am">—</div><div class="lbl">SP Agua M</div></div>
  <div class="item"><div class="val" id="st-sp-ac">—</div><div class="lbl">SP Agua C</div></div>
  <div class="item"><div class="val" id="st-sp-rpm">—</div><div class="lbl">SP RPM</div></div>
  <div class="item"><div class="val" id="st-status" style="color:#94a3b8">Detenido</div><div class="lbl">Estado</div></div>
</div>

<div class="charts-grid">
  <div class="chart-card"><h3>Potencia (kW)</h3><canvas id="ch-potencia"></canvas></div>
  <div class="chart-card"><h3>Nivel (%)</h3><canvas id="ch-nivel"></canvas></div>
  <div class="chart-card"><h3>Presión</h3><canvas id="ch-presion"></canvas></div>
  <div class="chart-card"><h3>P80 (μm)</h3><canvas id="ch-p80"></canvas></div>
  <div class="chart-card"><h3>Densidad</h3><canvas id="ch-densidad"></canvas></div>
  <div class="chart-card"><h3>Setpoints</h3><canvas id="ch-sp"></canvas></div>
</div>

<div class="events-panel">
  <h3>Eventos en Vivo</h3>
  <div id="ev-list"><span style="color:#64748b;font-size:.8rem">Sin eventos aún…</span></div>
</div>

<script>
const MAX_PTS = 300;
let timer = null;
let totalEventos = 0;

const chartOpts = (label, color, yMin, yMax) => ({
  type:'line',
  data:{labels:[], datasets:[{label, data:[], borderColor:color, backgroundColor:color+'22', borderWidth:2, pointRadius:0, fill:true, tension:.3}]},
  options:{animation:false, responsive:true, maintainAspectRatio:false,
    scales:{x:{display:true, ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8}, grid:{color:'#1e293b'}},
            y:{min:yMin,max:yMax, ticks:{color:'#64748b',font:{size:9}}, grid:{color:'#334155'}}},
    plugins:{legend:{display:false}}}
});

const chPot = new Chart(document.getElementById('ch-potencia'), chartOpts('Potencia','#38bdf8',2500,5500));
const chNiv = new Chart(document.getElementById('ch-nivel'), chartOpts('Nivel','#a78bfa',30,100));
const chPre = new Chart(document.getElementById('ch-presion'), chartOpts('Presión','#fb923c',5,25));
const chP80 = new Chart(document.getElementById('ch-p80'), chartOpts('P80','#f472b6',130,270));
const chDen = new Chart(document.getElementById('ch-densidad'), chartOpts('Densidad','#34d399',1.2,2.0));

// Setpoints chart (4 datasets)
const chSp = new Chart(document.getElementById('ch-sp'), {
  type:'line',
  data:{labels:[], datasets:[
    {label:'Ton',data:[],borderColor:'#38bdf8',borderWidth:2,pointRadius:0,tension:.3},
    {label:'Agua M',data:[],borderColor:'#a78bfa',borderWidth:2,pointRadius:0,tension:.3},
    {label:'Agua C',data:[],borderColor:'#fb923c',borderWidth:2,pointRadius:0,tension:.3},
    {label:'RPM',data:[],borderColor:'#f472b6',borderWidth:2,pointRadius:0,tension:.3},
  ]},
  options:{animation:false, responsive:true, maintainAspectRatio:false,
    scales:{x:{display:true,ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8},grid:{color:'#1e293b'}},
            y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
    plugins:{legend:{display:true,labels:{color:'#94a3b8',font:{size:10}}}}}
});

const allCharts = [chPot, chNiv, chPre, chP80, chDen, chSp];

function pushPt(chart, label, value) {
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (chart.data.labels.length > MAX_PTS) { chart.data.labels.shift(); chart.data.datasets[0].data.shift(); }
}

function pushSp(label, ton, am, ac, rpm) {
  chSp.data.labels.push(label);
  chSp.data.datasets[0].data.push(ton);
  chSp.data.datasets[1].data.push(am);
  chSp.data.datasets[2].data.push(ac);
  chSp.data.datasets[3].data.push(rpm);
  if (chSp.data.labels.length > MAX_PTS) {
    chSp.data.labels.shift();
    chSp.data.datasets.forEach(ds => ds.data.shift());
  }
}

function clearCharts() {
  allCharts.forEach(ch => { ch.data.labels = []; ch.data.datasets.forEach(ds => ds.data = []); ch.update(); });
  document.getElementById('ev-list').innerHTML = '<span style="color:#64748b;font-size:.8rem">Sin eventos aún…</span>';
  totalEventos = 0;
  document.getElementById('st-eventos').textContent = '0';
}

async function startStream() {
  if (timer) return;
  document.getElementById('btn-start').disabled = true;
  document.getElementById('btn-stop').disabled = false;
  document.getElementById('st-status').textContent = 'Iniciando…';
  document.getElementById('st-status').style.color = '#fbbf24';

  clearCharts();
  const batch = parseInt(document.getElementById('sel-batch').value) || 5;
  const res = await fetch('/api/simulacion/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({batch_size: batch})});
  const data = await res.json();
  if (!data.ok) { alert(data.error); stopStream(); return; }
  document.getElementById('st-total').textContent = data.total;
  document.getElementById('st-status').textContent = 'En vivo';
  document.getElementById('st-status').style.color = '#22c55e';

  const speed = parseInt(document.getElementById('sel-speed').value) || 2000;
  timer = setInterval(fetchNext, speed);
  fetchNext();
}

function stopStream() {
  if (timer) { clearInterval(timer); timer = null; }
  document.getElementById('btn-start').disabled = false;
  document.getElementById('btn-stop').disabled = true;
  document.getElementById('st-status').textContent = 'Detenido';
  document.getElementById('st-status').style.color = '#94a3b8';
}

async function resetStream() {
  stopStream();
  clearCharts();
  document.getElementById('st-cursor').textContent = '0';
  document.getElementById('st-time').textContent = '0.0';
  document.getElementById('progress-fill').style.width = '0%';
  ['st-sp-ton','st-sp-am','st-sp-ac','st-sp-rpm','st-total'].forEach(id => document.getElementById(id).textContent = '—');
  await fetch('/api/simulacion/reset', {method:'POST'});
}

function changeSpeed() {
  if (!timer) return;
  clearInterval(timer);
  const speed = parseInt(document.getElementById('sel-speed').value) || 2000;
  timer = setInterval(fetchNext, speed);
}

async function fetchNext() {
  try {
    const res = await fetch('/api/simulacion/next');
    const data = await res.json();
    if (!data.ok) return;
    if (data.done) { stopStream(); document.getElementById('st-status').textContent = 'Completado'; document.getElementById('st-status').style.color = '#38bdf8'; return; }

    for (const p of data.points) {
      const lbl = p.t_min.toFixed(1);
      pushPt(chPot, lbl, p.potencia);
      pushPt(chNiv, lbl, p.nivel);
      pushPt(chPre, lbl, p.presion);
      pushPt(chP80, lbl, p.p80);
      pushPt(chDen, lbl, p.densidad);
      pushSp(lbl, p.sp_ton, p.sp_am, p.sp_ac, p.sp_rpm);
    }
    allCharts.forEach(ch => ch.update());

    // Status
    const last = data.points[data.points.length - 1];
    document.getElementById('st-cursor').textContent = data.cursor;
    document.getElementById('st-time').textContent = last.t_s;
    document.getElementById('st-sp-ton').textContent = last.sp_ton;
    document.getElementById('st-sp-am').textContent = last.sp_am;
    document.getElementById('st-sp-ac').textContent = last.sp_ac;
    document.getElementById('st-sp-rpm').textContent = last.sp_rpm;
    document.getElementById('progress-fill').style.width = (data.cursor / data.total * 100).toFixed(1) + '%';

    // Eventos
    if (data.eventos && data.eventos.length > 0) {
      const el = document.getElementById('ev-list');
      if (totalEventos === 0) el.innerHTML = '';
      for (const ev of data.eventos) {
        totalEventos++;
        const div = document.createElement('div');
        div.className = 'ev-item';
        div.innerHTML = `<span class="t">${ev.t_s}s</span><span class="r">${ev.regla_id}</span><span class="a">${ev.acciones}</span>`;
        el.prepend(div);
      }
      document.getElementById('st-eventos').textContent = totalEventos;
    }
  } catch(e) { console.error(e); }
}
</script>
</body>
</html>"""  # noqa: E501


@app.route("/graficos")
def graficos():
    return Response(CHARTS_PAGE, mimetype="text/html")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Sistema Experto — Editor de Reglas en Vivo")
    print("  http://127.0.0.1:5000")
    print("  http://127.0.0.1:5000/graficos  (gráficos en tiempo real)")
    print("=" * 60)
    app.run(debug=True, host="127.0.0.1", port=5000)
