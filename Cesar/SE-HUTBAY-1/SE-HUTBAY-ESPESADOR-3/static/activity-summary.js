/**
 * Resumenes legibles para el log de actividad (fase 4).
 * Compara snapshots tomados al cargar cada seccion con el estado al guardar.
 */
(function (global) {
  'use strict';

  const _snapshots = {};

  function clone(obj) {
    try {
      return JSON.parse(JSON.stringify(obj ?? null));
    } catch (e) {
      return null;
    }
  }

  function snap(page, data) {
    _snapshots[page] = clone(data);
  }

  function getSnap(page) {
    return _snapshots[page];
  }

  function count(n, singular, plural) {
    n = Number(n) || 0;
    if (!n) return '';
    return n === 1 ? ('1 ' + singular) : (n + ' ' + plural);
  }

  function join(parts) {
    return (parts || []).filter(Boolean).join(' · ');
  }

  function fmtList(items, max) {
    max = max === undefined ? 4 : max;
    if (!items || !items.length) return '';
    const shown = items.slice(0, max);
    let s = shown.join(', ');
    const rest = items.length - shown.length;
    if (rest > 0) s += ' (+' + rest + ' mas)';
    return s;
  }

  function keyDiff(before, after) {
    const b = new Set(Object.keys(before || {}));
    const a = new Set(Object.keys(after || {}));
    return {
      added: [...a].filter(function (k) { return !b.has(k); }),
      removed: [...b].filter(function (k) { return !a.has(k); }),
    };
  }

  function arrayDiff(before, after) {
    const b = new Set(before || []);
    const a = new Set(after || []);
    return {
      added: [...a].filter(function (k) { return !b.has(k); }),
      removed: [...b].filter(function (k) { return !a.has(k); }),
    };
  }

  function keysChanged(before, after) {
    const keys = new Set([
      ...Object.keys(before || {}),
      ...Object.keys(after || {}),
    ]);
    const changed = [];
    keys.forEach(function (k) {
      if (JSON.stringify(before && before[k]) !== JSON.stringify(after && after[k])) {
        changed.push(k);
      }
    });
    return changed;
  }

  function variables(before, after) {
    const cr = keyDiff(before && before.crudas, after && after.crudas);
    const bd = ((before && before.definiciones) || []).map(function (d) { return d.nombre; });
    const ad = ((after && after.definiciones) || []).map(function (d) { return d.nombre; });
    const dd = arrayDiff(bd, ad);
    return join([
      cr.added.length
        ? count(cr.added.length, 'PV agregada', 'PV agregadas') + ': ' + fmtList(cr.added) : '',
      cr.removed.length
        ? count(cr.removed.length, 'PV quitada', 'PV quitadas') + ': ' + fmtList(cr.removed) : '',
      dd.added.length
        ? count(dd.added.length, 'definicion nueva', 'definiciones nuevas') + ': ' + fmtList(dd.added) : '',
      dd.removed.length
        ? count(dd.removed.length, 'definicion quitada', 'definiciones quitadas') + ': ' + fmtList(dd.removed) : '',
      (!cr.added.length && !cr.removed.length && !dd.added.length && !dd.removed.length)
        ? count((ad || []).length, 'definicion', 'definiciones') + ', '
          + count(Object.keys((after && after.crudas) || {}).length, 'PV', 'PV') + ' en catalogo'
        : '',
    ]);
  }

  function contrato(before, after) {
    const pv = arrayDiff(
      (before && before.variables_proceso) || [],
      (after && after.variables_proceso) || []
    );
    const sp = arrayDiff(
      (before && before.setpoints) || [],
      (after && after.setpoints) || []
    );
    return join([
      pv.added.length
        ? count(pv.added.length, 'PV agregada', 'PV agregadas') + ': ' + fmtList(pv.added) : '',
      pv.removed.length
        ? count(pv.removed.length, 'PV quitada', 'PV quitadas') + ': ' + fmtList(pv.removed) : '',
      sp.added.length
        ? count(sp.added.length, 'SP agregado', 'SP agregados') + ': ' + fmtList(sp.added) : '',
      sp.removed.length
        ? count(sp.removed.length, 'SP quitado', 'SP quitados') + ': ' + fmtList(sp.removed) : '',
      (!pv.added.length && !pv.removed.length && !sp.added.length && !sp.removed.length)
        ? count(((after && after.variables_proceso) || []).length, 'PV', 'PV') + ', '
          + count(((after && after.setpoints) || []).length, 'SP', 'SP')
        : '',
    ]);
  }

  function permisivos(before, after) {
    const kd = keyDiff(before, after);
    let condBefore = 0;
    let condAfter = 0;
    Object.keys(before || {}).forEach(function (k) {
      condBefore += ((before[k] || []).length);
    });
    Object.keys(after || {}).forEach(function (k) {
      condAfter += ((after[k] || []).length);
    });
    const condDelta = condAfter - condBefore;
    let condPart = '';
    if (condDelta > 0) condPart = count(condDelta, 'condicion agregada', 'condiciones agregadas');
    else if (condDelta < 0) condPart = count(-condDelta, 'condicion quitada', 'condiciones quitadas');
    return join([
      kd.added.length
        ? count(kd.added.length, 'permisivo nuevo', 'permisivos nuevos') + ': ' + fmtList(kd.added) : '',
      kd.removed.length
        ? count(kd.removed.length, 'permisivo quitado', 'permisivos quitados') + ': ' + fmtList(kd.removed) : '',
      condPart,
      (!kd.added.length && !kd.removed.length && !condPart)
        ? count(Object.keys(after || {}).length, 'permisivo', 'permisivos')
          + ', ' + count(condAfter, 'condicion', 'condiciones')
        : '',
    ]);
  }

  function filtros(before, after) {
    const kd = keyDiff(before, after);
    const changed = keysChanged(before, after).filter(function (k) {
      return kd.added.indexOf(k) < 0 && kd.removed.indexOf(k) < 0;
    });
    return join([
      kd.added.length
        ? count(kd.added.length, 'PV nueva', 'PV nuevas') + ': ' + fmtList(kd.added) : '',
      kd.removed.length
        ? count(kd.removed.length, 'PV quitada', 'PV quitadas') + ': ' + fmtList(kd.removed) : '',
      changed.length
        ? count(changed.length, 'PV ajustada', 'PV ajustadas') + ': ' + fmtList(changed) : '',
    ]);
  }

  function tracking(before, after) {
    const kd = keyDiff(before, after);
    const changed = keysChanged(before, after).filter(function (k) {
      return kd.added.indexOf(k) < 0 && kd.removed.indexOf(k) < 0;
    });
    return join([
      kd.added.length
        ? count(kd.added.length, 'familia nueva', 'familias nuevas') + ': ' + fmtList(kd.added) : '',
      kd.removed.length
        ? count(kd.removed.length, 'familia quitada', 'familias quitadas') + ': ' + fmtList(kd.removed) : '',
      changed.length
        ? count(changed.length, 'familia ajustada', 'familias ajustadas') + ': ' + fmtList(changed) : '',
    ]);
  }

  function defuzzy(before, after) {
    const kd = keyDiff(before, after);
    let acciones = 0;
    Object.keys(after || {}).forEach(function (fam) {
      acciones += Object.keys((after[fam] && after[fam].steps_por_accion) || {}).length;
    });
    return join([
      count(Object.keys(after || {}).length, 'familia', 'familias') + ' en total',
      count(acciones, 'accion', 'acciones') + ' en tablas',
      kd.added.length
        ? count(kd.added.length, 'familia nueva', 'familias nuevas') + ': ' + fmtList(kd.added) : '',
      kd.removed.length
        ? count(kd.removed.length, 'familia quitada', 'familias quitadas') + ': ' + fmtList(kd.removed) : '',
    ]);
  }

  function fuzzyVar(cfg) {
    if (!cfg) return '';
    const filas = Object.keys(cfg.labels || {}).length;
    const cols = (cfg.offset || []).length;
    return join([
      'tipo ' + (cfg.type || '?'),
      count(filas, 'etiqueta', 'etiquetas'),
      count(cols, 'punto en eje', 'puntos en eje'),
    ]);
  }

  function pendientes(before, after) {
    const kd = keyDiff(before, after);
    return join([
      count(Object.keys(after || {}).length, 'pendiente', 'pendientes') + ' en total',
      kd.added.length
        ? count(kd.added.length, 'pendiente nueva', 'pendientes nuevas') + ': ' + fmtList(kd.added) : '',
      kd.removed.length
        ? count(kd.removed.length, 'pendiente quitada', 'pendientes quitadas') + ': ' + fmtList(kd.removed) : '',
    ]);
  }

  function regla(rule, isNew) {
    const nCond = ((rule && rule.if) || []).length;
    const nActs = ((rule && rule.then) || []).length;
    return join([
      isNew ? 'regla nueva' : 'regla actualizada',
      count(nCond, 'condicion', 'condiciones'),
      count(nActs, 'accion', 'acciones'),
      (rule && rule.bloque) ? ('bloque ' + rule.bloque) : '',
    ]);
  }

  function estado(nombre, tipo, nCond, isNew) {
    return join([
      (isNew ? 'estado nuevo: ' : 'estado actualizado: ') + nombre,
      'tipo ' + tipo,
      count(nCond, 'condicion', 'condiciones'),
    ]);
  }

  function wait(nombre, sp, isNew) {
    return join([
      (isNew ? 'wait nuevo: ' : 'wait actualizado: ') + nombre,
      sp ? ('SP ' + sp) : 'sin SP vinculado',
    ]);
  }

  function tagsDelta(beforeN, afterN, name) {
    const delta = (afterN || 0) - (beforeN || 0);
    if (name && delta > 0) return 'tag nuevo: ' + name;
    if (name && delta < 0) return 'tag eliminado: ' + name;
    if (delta > 0) return count(delta, 'tag agregado', 'tags agregados');
    if (delta < 0) return count(-delta, 'tag eliminado', 'tags eliminados');
    return count(afterN, 'tag', 'tags') + ' en total';
  }

  function syncList(agregadas, quitadas, labelSingular, labelPlural) {
    const a = (agregadas || []).length;
    const q = (quitadas || []).length;
    return join([
      a ? count(a, labelSingular + ' agregada', labelPlural + ' agregadas')
        + (agregadas.length ? ': ' + fmtList(agregadas) : '') : '',
      q ? count(q, labelSingular + ' quitada', labelPlural + ' quitadas')
        + (quitadas.length ? ': ' + fmtList(quitadas) : '') : '',
    ]);
  }

  function importApply(aplicados) {
    if (!aplicados) return '';
    const mods = [];
    Object.keys(aplicados).forEach(function (k) {
      if (k === 'post_sync') return;
      if (aplicados[k]) mods.push(k);
    });
    const ps = aplicados.post_sync || {};
    const extra = [];
    if (ps.filtros_sync && (ps.filtros_sync.agregadas || []).length) {
      extra.push('+' + ps.filtros_sync.agregadas.length + ' filtros');
    }
    if (ps.tracking_sync && (ps.tracking_sync.agregadas || []).length) {
      extra.push('+' + ps.tracking_sync.agregadas.length + ' tracking');
    }
    return join([
      count(mods.length, 'modulo importado', 'modulos importados'),
      mods.length ? fmtList(mods, 8) : '',
      extra.length ? extra.join(', ') : '',
    ]);
  }

  function exportModules(selected) {
    const n = Object.values(selected || {}).filter(Boolean).length;
    const names = Object.keys(selected || {}).filter(function (k) { return selected[k]; });
    return join([
      count(n, 'modulo en paquete', 'modulos en paquete'),
      names.length ? fmtList(names, 8) : '',
    ]);
  }

  function changedFields(before, after, labels) {
    labels = labels || {};
    const changed = [];
    Object.keys(labels).forEach(function (k) {
      const bv = before && before[k];
      const av = after && after[k];
      if (String(bv) !== String(av)) changed.push(labels[k] || k);
    });
    return changed.length
      ? count(changed.length, 'campo cambiado', 'campos cambiados') + ': ' + fmtList(changed, 6)
      : '';
  }

  global.ActivitySummary = {
    snap: snap,
    getSnap: getSnap,
    count: count,
    join: join,
    fmtList: fmtList,
    keyDiff: keyDiff,
    arrayDiff: arrayDiff,
    variables: variables,
    contrato: contrato,
    permisivos: permisivos,
    filtros: filtros,
    tracking: tracking,
    defuzzy: defuzzy,
    fuzzyVar: fuzzyVar,
    pendientes: pendientes,
    regla: regla,
    estado: estado,
    wait: wait,
    tagsDelta: tagsDelta,
    syncList: syncList,
    importApply: importApply,
    exportModules: exportModules,
    changedFields: changedFields,
  };
})(typeof window !== 'undefined' ? window : this);
