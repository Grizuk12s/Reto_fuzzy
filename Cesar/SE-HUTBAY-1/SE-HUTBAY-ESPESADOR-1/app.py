# -*- coding: utf-8 -*-
"""Sistema Experto Espesador — aplicación Flask.

IT-7: app.py reducido a factory delgada.
  - Estado compartido → web/state.py
  - Rutas de config → web/api/config.py  (Blueprint bp_config)
  - Rutas de tags   → web/api/tags.py    (Blueprint bp_tags)
  - Rutas de SE     → web/api/se.py      (Blueprint bp_se)
  - Vistas HTML     → web/api/views.py   (Blueprint bp_views)
"""
from __future__ import annotations

from flask import Flask

from web.state import _startup_checks
from web.api.config import bp_config
from web.api.tags import bp_tags
from web.api.se import bp_se
from web.api.views import bp_views

app = Flask(__name__)

# CHARTS_PAGE se mantiene aquí (inline, como IT-6) hasta que se extraiga a
# web/templates/ en una iteración futura. Se expone en app para que
# bp_views pueda acceder via current_app.charts_page.

CHARTS_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espesador -- Graficos en Tiempo Real</title>
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
.btn-sm{padding:4px 10px;font-size:.75rem}
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
.events-panel{background:#1e293b;border-radius:10px;padding:14px;max-height:260px;overflow-y:auto}
.events-panel h3{font-size:.8rem;color:#94a3b8;margin-bottom:8px;text-transform:uppercase}
.ev-item{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid #334155;font-size:.8rem}
.ev-item .t{color:#38bdf8;min-width:55px}
.ev-item .r{color:#a78bfa;min-width:60px}
.ev-item .b{color:#22c55e;min-width:80px;font-size:.7rem;text-transform:uppercase}
.ev-item .a{color:#fb923c;flex:1}
@media(max-width:900px){.charts-grid{grid-template-columns:1fr}}

/* Tabs */
.tab-bar{display:flex;gap:0;margin-bottom:20px;border-bottom:2px solid #1e293b}
.tab-btn{padding:10px 20px;font-size:.85rem;font-weight:600;color:#64748b;background:none;border:none;border-bottom:3px solid transparent;cursor:pointer;transition:.15s}
.tab-btn:hover{color:#e2e8f0}
.tab-btn.active{color:#38bdf8;border-bottom-color:#38bdf8}
.tab-panel{display:none}
.tab-panel.active{display:block}

/* Custom chart builder */
.var-picker{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;max-height:200px;overflow-y:auto;padding:12px;background:#1e293b;border-radius:8px;border:1px solid #334155}
.var-chip{padding:4px 10px;border-radius:14px;font-size:.75rem;cursor:pointer;border:1px solid #475569;color:#94a3b8;transition:.15s;user-select:none}
.var-chip:hover{border-color:#38bdf8;color:#e2e8f0}
.var-chip.selected{background:#3b82f6;border-color:#3b82f6;color:#fff}
.custom-chart-wrap{background:#1e293b;border-radius:10px;padding:16px;min-height:300px}
.custom-chart-wrap canvas{width:100%!important;height:350px!important}
</style>
</head>
<body>
<nav>
  <a href="/" style="color:#38bdf8;font-weight:700;margin-right:4px">Reto Digital</a>
  <span style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:12px;font-size:.68rem;font-weight:700;background:rgba(56,189,248,.12);color:#38bdf8;border:1px solid rgba(56,189,248,.25);letter-spacing:.3px;margin-right:8px">ESPESADORES</span>
  <a href="/espesador">Configuracion SE</a>
  <a href="/espesador/entrada">Entrada de Datos</a>
  <a href="/espesador/graficos" class="active">Graficos en Vivo</a>
  <a href="/espesador/diagrama">Diagrama de Flujo</a>
</nav>
<h1>Espesador -- Graficos en Tiempo Real</h1>
<h2>Monitoreo en vivo de variables de proceso, tags KEPserver y simulacion del sistema experto.</h2>

<!-- Tab bar -->
<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('sim')">Simulacion SE</button>
  <button class="tab-btn" onclick="switchTab('tags')">Tags en Vivo</button>
  <button class="tab-btn" onclick="switchTab('custom')">Grafico Personalizado</button>
</div>

<!-- TAB 1: Simulacion (original) -->
<div class="tab-panel active" id="tab-sim">
<div class="controls">
  <button class="btn-success" id="btn-start" onclick="startStream()">&#9654; Iniciar Simulacion</button>
  <button class="btn-danger"  id="btn-stop"  onclick="stopStream()" disabled>&#9632; Detener</button>
  <button class="btn-primary" id="btn-reset" onclick="resetStream()">&#8634; Reiniciar</button>
  <label>Velocidad:
    <select id="sel-speed" onchange="changeSpeed()">
      <option value="5000">5s (real)</option>
      <option value="2000" selected>2s (rapido)</option>
      <option value="1000">1s (muy rapido)</option>
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
<div class="status-bar" id="status-bar"></div>

<div class="charts-grid" id="charts-grid"></div>

<div class="events-panel">
  <h3>Eventos en Vivo</h3>
  <div id="ev-list"><span style="color:#64748b;font-size:.8rem">Sin eventos aun...</span></div>
</div>
</div>

<!-- TAB 2: Tags en Vivo (KEPserver real-time) -->
<div class="tab-panel" id="tab-tags">
<div class="controls">
  <button class="btn-success" id="btn-tags-start" onclick="startTagsPolling()">&#9654; Iniciar Monitoreo</button>
  <button class="btn-danger" id="btn-tags-stop" onclick="stopTagsPolling()" disabled>&#9632; Detener</button>
  <label>Intervalo:
    <select id="sel-tags-interval" onchange="changeTagsInterval()">
      <option value="1000">1s</option>
      <option value="2000" selected>2s</option>
      <option value="5000">5s</option>
      <option value="10000">10s</option>
    </select>
  </label>
  <span id="tags-live-status" style="font-size:.8rem;color:#94a3b8">Detenido</span>
</div>
<div class="charts-grid" id="tags-charts-grid"></div>
<div style="margin-top:8px;font-size:.78rem;color:#64748b">
  Muestra los tags RETO.PV.* del KEPserver en tiempo real. Si el generador esta activo, veras los valores moverse.
</div>
</div>

<!-- TAB 3: Grafico Personalizado -->
<div class="tab-panel" id="tab-custom">
<p style="font-size:.85rem;color:#94a3b8;margin-bottom:12px">Selecciona los tags o variables que quieres graficar juntos. Haz clic para agregar/quitar.</p>
<div class="controls">
  <button class="btn-success" id="btn-custom-start" onclick="startCustomPolling()">&#9654; Iniciar</button>
  <button class="btn-danger" id="btn-custom-stop" onclick="stopCustomPolling()" disabled>&#9632; Detener</button>
  <label>Intervalo:
    <select id="sel-custom-interval">
      <option value="1000">1s</option>
      <option value="2000" selected>2s</option>
      <option value="5000">5s</option>
    </select>
  </label>
  <button class="btn-primary btn-sm" onclick="clearCustomChart()">Limpiar grafico</button>
</div>
<div class="var-picker" id="var-picker"></div>
<div class="custom-chart-wrap">
  <canvas id="custom-chart"></canvas>
</div>
</div>

<script>
const CHART_VARS = CHART_VARS_JSON;
const SP_KEYS    = SP_KEYS_JSON;
const MAX_PTS    = 300;
let timer = null;
let totalEventos = 0;
const charts = {};
let spChart = null;

// ============================================================
// Tab switching
// ============================================================
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
  if (name === 'tags' && !_tagsChartsBuilt) buildTagsCharts();
  if (name === 'custom' && !_customChartBuilt) buildCustomChart();
}

// ============================================================
// TAB 1: Simulacion (original logic preserved)
// ============================================================
(function buildStatBar() {
  const bar = document.getElementById('status-bar');
  const base = [
    {id:'st-cursor',  lbl:'Muestra'},
    {id:'st-total',   lbl:'Total'},
    {id:'st-time',    lbl:'t (s)'},
    {id:'st-eventos', lbl:'Eventos'},
  ];
  const sp = SP_KEYS.map(k => ({id:'st-' + k, lbl:k}));
  const status = [{id:'st-status', lbl:'Estado', color:'#94a3b8'}];
  const items = [...base, ...sp, ...status];
  bar.innerHTML = items.map(it =>
    `<div class="item"><div class="val" id="${it.id}" ${it.color?`style="color:${it.color}"`:''}>-</div>
      <div class="lbl">${it.lbl}</div></div>`
  ).join('');
})();

(function buildCharts() {
  const grid = document.getElementById('charts-grid');
  for (const cv of CHART_VARS) {
    const card = document.createElement('div');
    card.className = 'chart-card';
    card.innerHTML = `<h3>${cv.label}</h3><canvas id="ch-${cv.key}"></canvas>`;
    grid.appendChild(card);
    charts[cv.key] = new Chart(document.getElementById('ch-' + cv.key), {
      type:'line',
      data:{labels:[], datasets:[{label:cv.label, data:[], borderColor:cv.color,
            backgroundColor:cv.color+'22', borderWidth:2, pointRadius:0, fill:true, tension:.3}]},
      options:{animation:false, responsive:true, maintainAspectRatio:false,
        scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8},grid:{color:'#1e293b'}},
                y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
        plugins:{legend:{display:false}}}
    });
  }
  const card = document.createElement('div');
  card.className = 'chart-card';
  card.innerHTML = `<h3>Setpoints</h3><canvas id="ch-sp"></canvas>`;
  document.getElementById('charts-grid').appendChild(card);
  const palette = ['#38bdf8','#a78bfa','#fb923c','#f472b6','#22c55e','#facc15'];
  spChart = new Chart(document.getElementById('ch-sp'), {
    type:'line',
    data:{labels:[], datasets: SP_KEYS.map((k,i) => ({
      label:k, data:[], borderColor:palette[i%palette.length],
      borderWidth:2, pointRadius:0, tension:.3
    }))},
    options:{animation:false, responsive:true, maintainAspectRatio:false,
      scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8},grid:{color:'#1e293b'}},
              y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
      plugins:{legend:{display:true,labels:{color:'#94a3b8',font:{size:10}}}}}
  });
})();

function pushPt(chart, label, value) {
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (chart.data.labels.length > MAX_PTS) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
}

function pushSp(label, values) {
  spChart.data.labels.push(label);
  SP_KEYS.forEach((k,i) => spChart.data.datasets[i].data.push(values[k]));
  if (spChart.data.labels.length > MAX_PTS) {
    spChart.data.labels.shift();
    spChart.data.datasets.forEach(ds => ds.data.shift());
  }
}

function clearCharts() {
  Object.values(charts).forEach(ch => {
    ch.data.labels = []; ch.data.datasets[0].data = []; ch.update();
  });
  spChart.data.labels = [];
  spChart.data.datasets.forEach(ds => ds.data = []);
  spChart.update();
  document.getElementById('ev-list').innerHTML =
    '<span style="color:#64748b;font-size:.8rem">Sin eventos aun...</span>';
  totalEventos = 0;
  document.getElementById('st-eventos').textContent = '0';
}

async function startStream() {
  if (timer) return;
  document.getElementById('btn-start').disabled = true;
  document.getElementById('btn-stop').disabled = false;
  document.getElementById('st-status').textContent = 'Iniciando...';
  document.getElementById('st-status').style.color = '#fbbf24';

  clearCharts();
  const batch = parseInt(document.getElementById('sel-batch').value) || 5;
  const res = await fetch('/api/simulacion/start', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({batch_size: batch})
  });
  const data = await res.json();
  if (!data.ok) { alert(data.error || 'Error iniciando'); stopStream(); return; }
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
  ['st-total', ...SP_KEYS.map(k=>'st-'+k)].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '-';
  });
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
    if (data.done) {
      stopStream();
      document.getElementById('st-status').textContent = 'Completado';
      document.getElementById('st-status').style.color = '#38bdf8';
      return;
    }

    for (const p of data.points) {
      const lbl = p.t_min.toFixed(1);
      for (const cv of CHART_VARS) {
        if (p[cv.key] !== undefined) pushPt(charts[cv.key], lbl, p[cv.key]);
      }
      const spVals = {};
      SP_KEYS.forEach(k => spVals[k] = p[k]);
      pushSp(lbl, spVals);
    }
    Object.values(charts).forEach(ch => ch.update());
    spChart.update();

    const last = data.points[data.points.length - 1];
    document.getElementById('st-cursor').textContent = data.cursor;
    document.getElementById('st-time').textContent = last.t_s;
    SP_KEYS.forEach(k => {
      const el = document.getElementById('st-' + k);
      if (el && last[k] !== undefined) el.textContent = last[k];
    });
    document.getElementById('progress-fill').style.width =
      (data.cursor / data.total * 100).toFixed(1) + '%';

    if (data.eventos && data.eventos.length > 0) {
      const el = document.getElementById('ev-list');
      if (totalEventos === 0) el.innerHTML = '';
      for (const ev of data.eventos) {
        totalEventos++;
        const div = document.createElement('div');
        div.className = 'ev-item';
        div.innerHTML = `<span class="t">${ev.t_s}s</span>
          <span class="r">${ev.regla_id}</span>
          <span class="b">${ev.bloque||''}</span>
          <span class="a">${ev.acciones}</span>`;
        el.prepend(div);
      }
      document.getElementById('st-eventos').textContent = totalEventos;
    }
  } catch (e) {
    console.error(e);
  }
}

// ============================================================
// TAB 2: Tags en Vivo (KEPserver polling)
// ============================================================
let _tagsChartsBuilt = false;
let _tagsTimer = null;
const _tagsCharts = {};
const TAGS_PV = [
  {key:'RETO.PV.torque',             label:'Torque (%)',           color:'#38bdf8'},
  {key:'RETO.PV.bed_mass',           label:'Bed Mass',            color:'#a78bfa'},
  {key:'RETO.PV.bed_level',          label:'Bed Level (m)',       color:'#22c55e'},
  {key:'RETO.PV.densidad',           label:'Densidad (%)',        color:'#fb923c'},
  {key:'RETO.PV.torque_bomba',       label:'Torque Bomba (%)',    color:'#f472b6'},
  {key:'RETO.PV.potencia_bomba',     label:'Potencia Bomba (kW)', color:'#facc15'},
  {key:'RETO.PV.presion_descarga',   label:'Presion Descarga',    color:'#34d399'},
  {key:'RETO.PV.presion_diferencial',label:'Presion Diferencial', color:'#f87171'},
  {key:'RETO.PV.nivel_rastra',       label:'Nivel Rastra (%)',    color:'#c084fc'},
];

function buildTagsCharts() {
  const grid = document.getElementById('tags-charts-grid');
  grid.innerHTML = '';
  for (const tv of TAGS_PV) {
    const card = document.createElement('div');
    card.className = 'chart-card';
    card.innerHTML = `<h3>${tv.label}</h3><canvas id="tch-${tv.key.replace(/\./g,'_')}"></canvas>`;
    grid.appendChild(card);
    _tagsCharts[tv.key] = new Chart(document.getElementById('tch-' + tv.key.replace(/\./g,'_')), {
      type:'line',
      data:{labels:[], datasets:[{label:tv.label, data:[], borderColor:tv.color,
            backgroundColor:tv.color+'22', borderWidth:2, pointRadius:1, fill:true, tension:.3}]},
      options:{animation:false, responsive:true, maintainAspectRatio:false,
        scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:8},grid:{color:'#1e293b'}},
                y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
        plugins:{legend:{display:false}}}
    });
  }
  _tagsChartsBuilt = true;
}

async function _fetchTagsLive() {
  try {
    const r = await fetch('/api/tags');
    const d = await r.json();
    const now = new Date().toLocaleTimeString('es',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    for (const tv of TAGS_PV) {
      const tag = d.tags.find(t => t.name === tv.key);
      const ch = _tagsCharts[tv.key];
      if (!ch) continue;
      const val = tag && tag.value !== null ? tag.value : null;
      ch.data.labels.push(now);
      ch.data.datasets[0].data.push(val);
      if (ch.data.labels.length > MAX_PTS) {
        ch.data.labels.shift();
        ch.data.datasets[0].data.shift();
      }
      ch.update();
    }
  } catch(e) { console.error(e); }
}

function startTagsPolling() {
  if (_tagsTimer) return;
  const interval = parseInt(document.getElementById('sel-tags-interval').value) || 2000;
  _tagsTimer = setInterval(_fetchTagsLive, interval);
  _fetchTagsLive();
  document.getElementById('btn-tags-start').disabled = true;
  document.getElementById('btn-tags-stop').disabled = false;
  document.getElementById('tags-live-status').textContent = 'Monitoreando...';
  document.getElementById('tags-live-status').style.color = '#22c55e';
}

function stopTagsPolling() {
  if (_tagsTimer) { clearInterval(_tagsTimer); _tagsTimer = null; }
  document.getElementById('btn-tags-start').disabled = false;
  document.getElementById('btn-tags-stop').disabled = true;
  document.getElementById('tags-live-status').textContent = 'Detenido';
  document.getElementById('tags-live-status').style.color = '#94a3b8';
}

function changeTagsInterval() {
  if (!_tagsTimer) return;
  stopTagsPolling();
  startTagsPolling();
}

// ============================================================
// TAB 3: Grafico Personalizado
// ============================================================
let _customChartBuilt = false;
let _customChart = null;
let _customTimer = null;
let _selectedVars = [];
const PALETTE = ['#38bdf8','#a78bfa','#22c55e','#fb923c','#f472b6','#facc15','#34d399','#f87171','#c084fc','#67e8f9','#fca5a5','#a3e635','#e879f9','#fcd34d','#6ee7b7'];

const ALL_AVAILABLE_TAGS = [
  'RETO.PV.torque','RETO.PV.bed_mass','RETO.PV.bed_level','RETO.PV.densidad',
  'RETO.PV.torque_bomba','RETO.PV.potencia_bomba','RETO.PV.presion_descarga',
  'RETO.PV.presion_diferencial','RETO.PV.nivel_rastra',
  'RETO.CRUDA.tonelaje_sag_1','RETO.CRUDA.tonelaje_sag_2','RETO.CRUDA.tonelaje_relave',
  'RETO.CRUDA.presion_bomba_1','RETO.CRUDA.presion_bomba_2','RETO.CRUDA.turbiedad_agua',
  'RETO.SP.sp_tonelaje','RETO.SP.sp_floculante','RETO.SP.sp_vel_bomba',
  'RETO.IN.Potencia_SAG','RETO.IN.Potencia_Bolas','RETO.IN.Nivel_Molino'
];

function buildCustomChart() {
  const picker = document.getElementById('var-picker');
  picker.innerHTML = '';
  for (const tag of ALL_AVAILABLE_TAGS) {
    const chip = document.createElement('span');
    chip.className = 'var-chip';
    chip.textContent = tag.replace('RETO.','');
    chip.dataset.tag = tag;
    chip.onclick = function() { toggleVarSelection(this); };
    picker.appendChild(chip);
  }

  _customChart = new Chart(document.getElementById('custom-chart'), {
    type:'line',
    data:{labels:[], datasets:[]},
    options:{animation:false, responsive:true, maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      scales:{x:{ticks:{color:'#64748b',font:{size:9},maxTicksLimit:10},grid:{color:'#1e293b'}},
              y:{ticks:{color:'#64748b',font:{size:9}},grid:{color:'#334155'}}},
      plugins:{legend:{display:true,labels:{color:'#94a3b8',font:{size:10}}}}}
  });
  _customChartBuilt = true;
}

function toggleVarSelection(chip) {
  const tag = chip.dataset.tag;
  const idx = _selectedVars.indexOf(tag);
  if (idx >= 0) {
    _selectedVars.splice(idx, 1);
    chip.classList.remove('selected');
    _rebuildCustomDatasets();
  } else {
    if (_selectedVars.length >= 8) return;
    _selectedVars.push(tag);
    chip.classList.add('selected');
    _rebuildCustomDatasets();
  }
}

function _rebuildCustomDatasets() {
  if (!_customChart) return;
  const existingLabels = _customChart.data.labels;
  _customChart.data.datasets = _selectedVars.map((tag, i) => ({
    label: tag.replace('RETO.',''),
    data: new Array(existingLabels.length).fill(null),
    borderColor: PALETTE[i % PALETTE.length],
    borderWidth: 2, pointRadius: 1, tension: .3
  }));
  _customChart.update();
}

async function _fetchCustomLive() {
  if (_selectedVars.length === 0) return;
  try {
    const r = await fetch('/api/tags');
    const d = await r.json();
    const now = new Date().toLocaleTimeString('es',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    _customChart.data.labels.push(now);
    if (_customChart.data.labels.length > MAX_PTS) _customChart.data.labels.shift();

    for (let i = 0; i < _selectedVars.length; i++) {
      const tag = d.tags.find(t => t.name === _selectedVars[i]);
      const val = tag && tag.value !== null ? tag.value : null;
      _customChart.data.datasets[i].data.push(val);
      if (_customChart.data.datasets[i].data.length > MAX_PTS)
        _customChart.data.datasets[i].data.shift();
    }
    _customChart.update();
  } catch(e) { console.error(e); }
}

function startCustomPolling() {
  if (_customTimer) return;
  if (_selectedVars.length === 0) { alert('Selecciona al menos un tag para graficar.'); return; }
  const interval = parseInt(document.getElementById('sel-custom-interval').value) || 2000;
  _customTimer = setInterval(_fetchCustomLive, interval);
  _fetchCustomLive();
  document.getElementById('btn-custom-start').disabled = true;
  document.getElementById('btn-custom-stop').disabled = false;
}

function stopCustomPolling() {
  if (_customTimer) { clearInterval(_customTimer); _customTimer = null; }
  document.getElementById('btn-custom-start').disabled = false;
  document.getElementById('btn-custom-stop').disabled = true;
}

function clearCustomChart() {
  if (!_customChart) return;
  _customChart.data.labels = [];
  _customChart.data.datasets.forEach(ds => ds.data = []);
  _customChart.update();
}
</script>
</body>
</html>"""


app.charts_page = CHARTS_PAGE  # expuesto para bp_views.graficos()

# Registro de blueprints
app.register_blueprint(bp_config)
app.register_blueprint(bp_tags)
app.register_blueprint(bp_se)
app.register_blueprint(bp_views)

# Health checks al arrancar (puebla _alerts)
_startup_checks()


if __name__ == "__main__":
    print("=" * 60)
    print("  Reto Digital -- Sistema Experto (v2)")
    print("  http://127.0.0.1:5000                    (bienvenida)")
    print("  http://127.0.0.1:5000/espesador           (configuracion SE)")
    print("  http://127.0.0.1:5000/espesador/entrada   (entrada de datos)")
    print("  http://127.0.0.1:5000/espesador/graficos  (graficos en vivo)")
    print("  http://127.0.0.1:5000/espesador/diagrama  (diagrama de flujo)")
    print("=" * 60)
    app.run(debug=True, host="127.0.0.1", port=5000)
