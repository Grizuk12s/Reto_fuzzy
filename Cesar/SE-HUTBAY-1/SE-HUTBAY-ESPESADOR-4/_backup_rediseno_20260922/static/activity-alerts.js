/**
 * Panel de alertas + log de actividad de configuracion.
 * Solo observabilidad — no altera el pipeline.
 */
(function (global) {
  'use strict';

  let _alertCats = {};
  let _activityKinds = {};
  let _timer = null;
  let _cfg = {};
  let _dirtySent = false;
  let _dirtyReady = false;
  let _dirtySentMap = {};
  let _pageReady = {};
  let _suppress = false;

  function isMultiPage() {
    return _cfg.pages && typeof _cfg.pages === 'object';
  }

  function pageLabel(page) {
    if (isMultiPage() && _cfg.pages[page]) {
      return _cfg.pages[page].label || page;
    }
    return _cfg.pageLabel || page;
  }

  function pageRestart(page) {
    if (isMultiPage() && _cfg.pages[page]) {
      return _cfg.pages[page].restart;
    }
    return _cfg.restartOnSave;
  }

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function injectRail() {
    if (document.getElementById('alertRail')) return;
    const fixed = _cfg.fixed !== false;
    const rail = document.createElement('div');
    rail.id = 'alertRail';
    rail.className = 'alert-rail' + (fixed ? ' alert-rail--fixed' : '');
    rail.innerHTML =
      '<div class="rail-dots" id="alertDots"></div>'
      + '<div class="alert-panel" onclick="event.stopPropagation()">'
      + '<div class="alert-panel-header">'
      + '<h4>Alertas <span class="alert-count-badge" id="alertBadge">0</span></h4>'
      + '<button type="button" class="close-btn" id="alertRailClose">&times;</button>'
      + '</div>'
      + '<div id="alertPanelContent"></div>'
      + '</div>';
    document.body.appendChild(rail);
    wireRail(rail);
  }

  function wireRail(rail) {
    if (!rail || rail.dataset.aaWired) return;
    rail.dataset.aaWired = '1';
    if (_cfg.sticky) rail.classList.add('alert-rail--sticky');
    rail.addEventListener('click', function () {
      if (!rail.classList.contains('expanded')) {
        rail.classList.add('expanded');
        fetchAlerts();
      }
    });
    const closeBtn = rail.querySelector('#alertRailClose, .close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        rail.classList.remove('expanded');
      });
    }
  }

  function wireExistingRail() {
    wireRail(document.getElementById('alertRail'));
  }

  function toggleRail() {
    const rail = document.getElementById('alertRail');
    if (!rail) return;
    rail.classList.toggle('expanded');
    if (rail.classList.contains('expanded')) fetchAlerts();
  }

  function closeRail() {
    const rail = document.getElementById('alertRail');
    if (rail) rail.classList.remove('expanded');
  }

  async function post(payload) {
    try {
      await fetch('/api/activity-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      fetchAlerts();
    } catch (e) { /* ignore */ }
  }

  function currentPageFromDom() {
    const sec = document.querySelector('.seccion.active');
    if (sec && sec.id && sec.id.startsWith('seccion-')) {
      return sec.id.replace('seccion-', '');
    }
    const modal = document.getElementById('modal-overlay');
    if (modal && modal.classList.contains('active')) return 'reglas';
    if (document.getElementById('estado-modal-overlay')) return 'estados';
    if (document.getElementById('wait-modal-overlay')) return 'waits';
    return null;
  }

  function markDirty(page) {
    if (_suppress) return;
    if (isMultiPage()) {
      const p = page || currentPageFromDom();
      if (!p || !_pageReady[p] || _dirtySentMap[p]) return;
      _dirtySentMap[p] = true;
      post({
        action: 'unsaved',
        page: p,
        page_label: pageLabel(p),
      });
      return;
    }
    const p = page || _cfg.page;
    if (!_dirtyReady || _dirtySent || !p) return;
    _dirtySent = true;
    post({
      action: 'unsaved',
      page: p,
      page_label: pageLabel(p),
    });
  }

  function clearDirtyForPage(page) {
    if (isMultiPage()) {
      delete _dirtySentMap[page];
    } else {
      _dirtySent = false;
    }
  }

  async function onSave(a, b, c, d) {
    let page;
    let ok;
    let message;
    let detail;
    let opts;

    if (typeof a === 'string') {
      page = a;
      ok = b;
      message = c;
      detail = d || '';
      opts = {};
    } else {
      ok = a;
      message = b;
      detail = c || '';
      opts = d || {};
      page = opts.page || _cfg.page;
    }

    clearDirtyForPage(page);
    const label = (opts && opts.pageLabel) || pageLabel(page);
    if (!ok) {
      await post({
        action: 'error',
        page: page,
        page_label: label,
        message: message || 'Error al guardar',
        detail: detail || '',
      });
      return;
    }
    await post({
      action: 'saved',
      page: page,
      page_label: label,
      message: message || ('Guardado en ' + label),
      detail: detail || '',
    });
    let needsRestart = opts.restart;
    if (needsRestart === undefined) needsRestart = pageRestart(page);
    if (needsRestart === 'motor') {
      try {
        const st = await (await fetch('/api/se/status')).json();
        needsRestart = !!st.running;
      } catch (e) {
        needsRestart = false;
      }
    }
    if (needsRestart) {
      await post({
        action: 'restart',
        page: page,
        page_label: label,
        message: 'Cambios guardados en ' + label + ': reinicia el motor para aplicarlos',
        detail: detail || '',
      });
    }
  }

  function renderDots(sysAlerts, actEntries) {
    const wrap = document.getElementById('alertDots');
    if (!wrap) return;
    wrap.innerHTML = '';
    const all = actEntries.length + sysAlerts.length;
    if (all === 0) {
      wrap.innerHTML = '<div class="rail-ok" title="Sin alertas"></div>';
      return;
    }
    actEntries.forEach(function (a) {
      const cat = _activityKinds[a.kind] || { color: '#64748b' };
      const dot = document.createElement('div');
      dot.className = 'rail-dot';
      dot.style.background = cat.color;
      dot.title = a.message;
      wrap.appendChild(dot);
    });
    sysAlerts.forEach(function (a) {
      const cat = _alertCats[a.category] || _alertCats.general || { color: '#64748b' };
      const dot = document.createElement('div');
      dot.className = 'rail-dot';
      dot.style.background = cat.color;
      dot.title = a.message;
      wrap.appendChild(dot);
    });
  }

  function renderActivityCard(a) {
    const cat = _activityKinds[a.kind] || { label: a.kind, color: '#64748b', icon: '?' };
    const ago = Math.round(Date.now() / 1000 - a.last_seen);
    const agoStr = ago < 60 ? ago + 's' : Math.round(ago / 60) + 'm';
    const card = document.createElement('div');
    card.className = 'alert-card activity';
    card.style.borderLeftColor = cat.color;
    card.onclick = function (e) {
      if (!e.target.classList.contains('resolve-btn')) card.classList.toggle('open');
    };
    const canDismiss = a.kind === 'unsaved' || a.kind === 'restart' || a.kind === 'error';
    card.innerHTML =
      '<div class="alert-cat" style="color:' + cat.color + '">' + cat.icon + ' ' + esc(cat.label) + '</div>'
      + '<div class="alert-msg">' + esc(a.message) + '</div>'
      + '<div class="alert-meta"><span>' + esc(a.page_label || '') + ' · hace ' + agoStr + '</div>'
      + (a.detail ? '<div class="alert-detail">' + esc(a.detail) + '</div>' : '')
      + (canDismiss
        ? '<button type="button" class="resolve-btn">Descartar</button>' : '');
    if (canDismiss) {
      card.querySelector('.resolve-btn').onclick = function (e) {
        e.stopPropagation();
        dismissActivity(a.id);
      };
    }
    return card;
  }

  function renderPanel(sysAlerts, actEntries, totalCount) {
    const panel = document.getElementById('alertPanelContent');
    const badge = document.getElementById('alertBadge');
    if (!panel || !badge) return;
    badge.textContent = totalCount;
    badge.className = 'alert-count-badge' + (totalCount === 0 ? ' ok' : '');
    panel.innerHTML = '';
    if (totalCount === 0) {
      panel.innerHTML = '<div class="alert-ok-msg">Sin alertas activas.<br>El sistema esta funcionando correctamente.</div>';
    } else {
      if (actEntries.length) {
        const h = document.createElement('div');
        h.className = 'alert-section-title';
        h.textContent = 'Actividad de configuracion';
        panel.appendChild(h);
        actEntries.forEach(function (a) { panel.appendChild(renderActivityCard(a)); });
      }
      if (sysAlerts.length) {
        const h = document.createElement('div');
        h.className = 'alert-section-title';
        h.textContent = 'Alertas del sistema';
        panel.appendChild(h);
        sysAlerts.forEach(function (a) {
          const cat = _alertCats[a.category] || _alertCats.general || { color: '#64748b', icon: '?', label: '?' };
          const ago = Math.round(Date.now() / 1000 - a.last_seen);
          const agoStr = ago < 60 ? ago + 's' : Math.round(ago / 60) + 'm';
          const card = document.createElement('div');
          card.className = 'alert-card';
          card.style.borderLeftColor = cat.color;
          card.onclick = function (e) {
            if (!e.target.classList.contains('resolve-btn')) card.classList.toggle('open');
          };
          card.innerHTML =
            '<div class="alert-cat" style="color:' + cat.color + '">' + cat.icon + ' ' + esc(cat.label) + '</div>'
            + '<div class="alert-msg">' + esc(a.message) + '</div>'
            + '<div class="alert-meta"><span>hace ' + agoStr
            + (a.count > 1 ? ' (x' + a.count + ')' : '') + '</span></div>'
            + (a.detail ? '<div class="alert-detail">' + esc(a.detail) + '</div>' : '')
            + '<button type="button" class="resolve-btn">Marcar resuelto</button>';
          card.querySelector('.resolve-btn').onclick = function (e) {
            e.stopPropagation();
            resolveAlert(a.id);
          };
          panel.appendChild(card);
        });
      }
    }
    const histBtn = document.createElement('a');
    histBtn.className = 'alert-history-btn';
    histBtn.href = '/espesador/alertas-historial';
    histBtn.target = '_blank';
    histBtn.textContent = 'Ver historial (ultimas 100)';
    panel.appendChild(histBtn);
  }

  async function fetchAlerts() {
    try {
      const res = await Promise.all([
        fetch('/api/alerts'),
        fetch('/api/activity-log'),
      ]);
      const data = await res[0].json();
      const act = await res[1].json();
      if (data.categories) _alertCats = data.categories;
      _activityKinds = act.kinds || {};
      const sys = data.alerts || [];
      const entries = act.entries || [];
      renderDots(sys, entries);
      renderPanel(sys, entries, sys.length + entries.length);
    } catch (e) { /* ignore */ }
  }

  async function resolveAlert(id) {
    await fetch('/api/alerts/' + id + '/resolve', { method: 'POST' });
    fetchAlerts();
  }

  async function dismissActivity(id) {
    await fetch('/api/activity-log/' + id + '/dismiss', { method: 'POST' });
    fetchAlerts();
  }

  function setupDirtyListener() {
    const root = _cfg.dirtyRoot ? document.querySelector(_cfg.dirtyRoot) : document.body;
    if (!root) return;
    function onInput(e) {
      if (e.target.closest('[data-activity-ignore]')) return;
      if (_cfg.dirtySelector && !e.target.matches(_cfg.dirtySelector)
          && !e.target.closest(_cfg.dirtySelector)) return;
      markDirty();
    }
    root.addEventListener('input', onInput, true);
    root.addEventListener('change', onInput, true);
  }

  function setupMultiPageListeners() {
    const root = document.querySelector(_cfg.dirtyRoot || '.main');
    if (!root) return;
    function onInput(e) {
      if (_suppress) return;
      if (e.target.closest('[data-activity-ignore]')) return;
      const sec = e.target.closest('.seccion');
      if (sec && sec.id && sec.id.startsWith('seccion-')) {
        const page = sec.id.replace('seccion-', '');
        if (_cfg.pages[page]) markDirty(page);
        return;
      }
      if (e.target.closest('#modal-overlay')) markDirty('reglas');
      else if (e.target.closest('#estado-modal-overlay')) markDirty('estados');
      else if (e.target.closest('#wait-modal-overlay')) markDirty('waits');
    }
    root.addEventListener('input', onInput, true);
    root.addEventListener('change', onInput, true);
    document.body.addEventListener('click', function (e) {
      if (_suppress) return;
      const btn = e.target.closest('button');
      if (!btn) return;
      const txt = (btn.textContent || '').trim().toLowerCase();
      if (txt !== 'x' && txt !== '×' && txt !== 'eliminar grupo') return;
      const zone = btn.closest('#modal-overlay, #estado-modal-overlay, #wait-modal-overlay, .seccion.active');
      if (!zone) return;
      markDirty(currentPageFromDom());
    }, true);
  }

  function enablePage(page) {
    if (!isMultiPage()) return;
    setTimeout(function () { _pageReady[page] = true; }, _cfg.readyDelay || 350);
  }

  function disablePage(page) {
    if (!isMultiPage()) return;
    _pageReady[page] = false;
  }

  function setSuppress(flag) {
    _suppress = !!flag;
  }

  function init(cfg) {
    _cfg = cfg || {};
    if (global.ALERT_CATEGORIES_JSON) _alertCats = global.ALERT_CATEGORIES_JSON;
    if (_cfg.inject !== false) {
      injectRail();
    } else {
      wireExistingRail();
    }
    if (isMultiPage()) {
      setupMultiPageListeners();
    } else {
      setupDirtyListener();
      setTimeout(function () { _dirtyReady = true; }, _cfg.readyDelay || 400);
    }
    fetchAlerts();
    if (_timer) clearInterval(_timer);
    _timer = setInterval(fetchAlerts, _cfg.pollMs || 5000);
  }

  function clearDirtyState(page) {
    if (isMultiPage()) {
      const p = page || _cfg.page;
      if (p) {
        delete _dirtySentMap[p];
        post({ action: 'clear_unsaved', page: p });
      }
    } else {
      _dirtySent = false;
      if (_cfg.page) {
        post({ action: 'clear_unsaved', page: _cfg.page });
      }
    }
  }

  global.ActivityAlerts = {
    init: init,
    markDirty: markDirty,
    clearDirtyState: clearDirtyState,
    onSave: onSave,
    post: post,
    refresh: fetchAlerts,
    enablePage: enablePage,
    disablePage: disablePage,
    setSuppress: setSuppress,
    toggleRail: toggleRail,
    closeRail: closeRail,
  };
})(typeof window !== 'undefined' ? window : this);
