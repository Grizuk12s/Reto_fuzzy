// Fase 5 — "Otros SAG": muestra, debajo de una tabla de este SAG, la misma
// tabla de cada uno de los demas SAG, en SOLO LECTURA y con su color.
//
// Solo actua detras del router (window.SE_SAG con mas de un SAG); sin router
// no hace nada. Los datos se piden a cada SAG con SE_SAG.fetchDe, asi que el
// backend no cambia: cada SAG ya responde su propia API.
//
// Uso:
//   OtrosSag.montar({
//     id: 'waits-otros',            // id del contenedor (se crea si no existe)
//     despuesDe: 'waits-table',     // se inserta despues de este elemento
//     endpoint: '/api/waits',
//     ancla: 'waits',               // seccion a la que volver al editar en el otro SAG
//     columnas: [['Nombre', f => f.nombre], ...],
//     filas: data => data,          // de la respuesta a un array de filas
//     vacio: 'Sin waits.'
//   });
(function () {
  function el(tag, css, texto) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (texto !== undefined && texto !== null) e.textContent = texto;
    return e;
  }

  function bloque(s, filas, opts, error) {
    var b = el('div', 'margin-top:14px;border:1px solid #23323b;border-left:4px solid ' + s.color +
      ';border-radius:0 10px 10px 0;background:#101a20;overflow:hidden');
    var cab = el('div', 'display:flex;align-items:center;gap:9px;padding:10px 14px;border-bottom:1px solid #1b2a32');
    cab.appendChild(el('span', 'width:10px;height:10px;border-radius:50%;background:' + s.color));
    cab.appendChild(el('b', 'font-size:.9rem;color:#e8eef1', s.nombre));
    cab.appendChild(el('span', 'font-size:.72rem;color:#7a8b94', error ? '' : (filas.length + ' fila(s)')));
    var editar = el('a', 'margin-left:auto;font-size:.78rem;color:#4fb3d9;text-decoration:none', 'Editar en ' + s.nombre + ' →');
    editar.href = '/gestion/seleccionar/' + encodeURIComponent(s.id) + '?volver=' +
      encodeURIComponent(location.pathname + (opts.ancla ? '#' + opts.ancla : ''));
    cab.appendChild(editar);
    b.appendChild(cab);
    if (error) {
      b.appendChild(el('div', 'padding:12px 14px;font-size:.82rem;color:#ef4444', error));
      return b;
    }
    if (!filas.length) {
      b.appendChild(el('div', 'padding:12px 14px;font-size:.82rem;color:#7a8b94', opts.vacio || 'Sin datos.'));
      return b;
    }
    var t = el('table', 'width:100%;margin:0;opacity:.92');
    var tr = el('tr');
    opts.columnas.forEach(function (c) { tr.appendChild(el('th', null, c[0])); });
    var thead = el('thead'); thead.appendChild(tr); t.appendChild(thead);
    var tb = el('tbody');
    filas.forEach(function (f) {
      var r = el('tr');
      opts.columnas.forEach(function (c) {
        var v;
        try { v = c[1](f); } catch (e) { v = ''; }
        r.appendChild(el('td', 'font-size:.82rem', (v === undefined || v === null || v === '') ? '-' : String(v)));
      });
      tb.appendChild(r);
    });
    t.appendChild(tb);
    b.appendChild(t);
    return b;
  }

  window.OtrosSag = {
    activo: function () { return !!(window.SE_SAG && (window.SE_SAG.todos || []).length > 1); },
    montar: async function (opts) {
      if (!this.activo()) return;
      var cont = document.getElementById(opts.id);
      if (!cont) {
        var ref = document.getElementById(opts.despuesDe);
        if (!ref) return;
        cont = el('div', 'margin-top:26px');
        cont.id = opts.id;
        ref.after(cont);
      }
      var otros = window.SE_SAG.todos.filter(function (s) { return s.id !== window.SE_SAG.actual.id; });
      var res = await Promise.all(otros.map(function (s) {
        return window.SE_SAG.fetchDe(s.id, opts.endpoint)
          .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Error ' + r.status)); })
          .then(function (d) { return {ok: true, d: d}; })
          .catch(function (e) { return {ok: false, e: e.message || String(e)}; });
      }));
      var titulo = el('div', 'display:flex;align-items:baseline;gap:10px;flex-wrap:wrap');
      titulo.appendChild(el('h3', 'font-size:1rem;color:#e8eef1;margin:0', 'Otros SAG'));
      titulo.appendChild(el('span', 'font-size:.78rem;color:#7a8b94',
        'Solo lectura. Para cambiarlos, entra a ese SAG.'));
      var hijos = [titulo];
      otros.forEach(function (s, i) {
        var x = res[i];
        if (!x.ok) { hijos.push(bloque(s, [], opts, 'No responde: ' + x.e)); return; }
        var filas;
        try { filas = opts.filas(x.d) || []; } catch (e) { filas = []; }
        hijos.push(bloque(s, filas, opts));
      });
      cont.replaceChildren.apply(cont, hijos);
    }
  };
})();
