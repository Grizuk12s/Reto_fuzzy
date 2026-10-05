// Todos los SAG en la misma seccion (2026-10-02).
//
// Con varios SAG (router), cada seccion del editor (Contrato, Variables,
// Tracking, Filtros, Fuzzy, Aceleracion, Estados, Permisivos, Reglas,
// Defuzzy) muestra un bloque por SAG, cada uno con el color de su SAG y
// EDITABLE ahi mismo:
//   - el bloque del SAG de la pagina es el editor de siempre;
//   - los bloques de los demas SAG cargan ESE MISMO editor embebido
//     (/espesador?_sag=<id>&_embed=1#<seccion>), fijado a su SAG por el
//     script del router: todo lo que se guarde ahi va a ese SAG.
// Asi cada seccion queda multi-SAG sin reescribir sus editores.
//
// Waits no pasa por aqui: tiene su propia version nativa (WaitsMulti).
// Sin router (un solo equipo) este archivo no hace nada.
(function () {
  var qs = new URLSearchParams(location.search);
  var EMBED = qs.get('_embed') === '1' && window.top !== window.self;
  window.SE_EMBED = EMBED;
  var SECCIONES = ['contrato', 'variables', 'tracking', 'filtros', 'fuzzy', 'aceleracion',
                   'estados', 'permisivos', 'reglas', 'defuzzy'];

  function rgba(hex, a) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m) return 'rgba(122,139,148,' + a + ')';
    return 'rgba(' + parseInt(m[1], 16) + ',' + parseInt(m[2], 16) + ',' + parseInt(m[3], 16) + ',' + a + ')';
  }
  function el(tag, css, texto) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (texto !== undefined) e.textContent = texto;
    return e;
  }

  // =====================================================================
  // 1) Modo EMBEBIDO: esta pagina es el bloque de otro SAG dentro de un iframe
  // =====================================================================
  if (EMBED) {
    document.documentElement.classList.add('se-embed');
    var css = el('style');
    css.textContent = [
      'html.se-embed,html.se-embed body{background:transparent!important}',
      'html.se-embed body{display:block!important;min-height:0!important}',
      'html.se-embed .sidebar,html.se-embed .alert-rail,html.se-embed #alertRail,',
      'html.se-embed .activity-bar,html.se-embed #activityBar,',
      'html.se-embed #license-expired-banner{display:none!important}',
      'html.se-embed .main{padding:14px 16px 16px!important;overflow:visible!important}',
      // el titulo y la explicacion ya estan arriba en la pagina: aqui solo los botones
      'html.se-embed .seccion>.top-bar:first-of-type>div:first-child{display:none!important}',
      'html.se-embed .seccion>.top-bar:first-of-type{justify-content:flex-end!important;margin-bottom:12px!important}',
      // los textos de ayuda (data-se-ayuda) se muestran una sola vez, arriba de los bloques
      'html.se-embed [data-se-ayuda]{display:none!important}'
    ].join('\n');
    (document.head || document.documentElement).appendChild(css);

    var frame = window.frameElement;
    // El alto del iframe sigue al contenido (sin scroll anidado).
    function ajustarAlto(minimo) {
      if (!frame || !document.body) return;
      var main = document.querySelector('.main') || document.body;
      var h = Math.ceil(main.getBoundingClientRect().height) + 4;
      frame.style.height = Math.max(h, minimo || 0, 120) + 'px';
    }
    // Los modales son position:fixed DENTRO del iframe, que mide todo el
    // contenido: centrados quedarian fuera de la vista. Se bajan hasta la
    // parte del bloque que el usuario esta viendo.
    function ajustarModales() {
      if (!frame) return;
      var minimo = 0;
      var altoVista = 800;
      try { altoVista = window.top.innerHeight; } catch (e) {}
      document.querySelectorAll('.modal-overlay').forEach(function (ov) {
        if (getComputedStyle(ov).display === 'none') return;
        var r = frame.getBoundingClientRect();
        var arriba = Math.max(0, -r.top) + 24;
        ov.style.alignItems = 'flex-start';
        ov.style.paddingTop = arriba + 'px';
        var m = ov.querySelector('.modal') || ov.firstElementChild;
        if (!m) return;
        // Dentro del iframe "vh" es el alto del bloque, no de la pantalla: el
        // modal se limita al alto de la ventana real y el bloque crece lo
        // necesario para contenerlo entero.
        m.style.maxHeight = (altoVista - 48) + 'px';
        m.style.overflowY = 'auto';
        minimo = Math.max(minimo, arriba + Math.min(m.scrollHeight, altoVista - 48) + 40);
      });
      ajustarAlto(minimo);
    }
    // Enlaces a otras paginas: en la ventana principal, no dentro del bloque.
    document.addEventListener('click', function (ev) {
      var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
      if (!a) return;
      var raw = a.getAttribute('href') || '';
      if (!raw || /^javascript:/i.test(raw)) return;
      if (raw.charAt(0) === '#') {
        ev.preventDefault();
        try { window.top.location.hash = raw; } catch (e) {}
        return;
      }
      a.target = '_top';
    }, true);
    function arrancar() {
      if (window.ResizeObserver) {
        var ro = new ResizeObserver(function () { ajustarAlto(0); });
        ro.observe(document.querySelector('.main') || document.body);
      }
      ajustarAlto(0);
      setInterval(ajustarModales, 250);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancar);
    else arrancar();
    return;
  }

  // =====================================================================
  // 2) Pagina normal con varios SAG: un bloque por SAG en cada seccion
  // =====================================================================
  function multi() { return !!(window.SE_SAG && (window.SE_SAG.todos || []).length > 1); }
  var montadas = {};

  function cabecera(s, aqui, extra) {
    var h = el('div', 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:11px 16px;' +
      'background:' + rgba(s.color, .1) + ';border-bottom:1px solid ' + rgba(s.color, .3));
    h.appendChild(el('span', 'width:12px;height:12px;border-radius:50%;flex-shrink:0;background:' + s.color));
    h.appendChild(el('b', 'font-size:1rem;color:#e8eef1', s.nombre));
    if (aqui) h.appendChild(el('span', 'font-size:.75rem;color:#a2b1b9', 'SAG de esta pagina'));
    var der = el('span', 'margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap');
    if (extra) der.appendChild(extra);
    h.appendChild(der);
    return {cab: h, der: der};
  }

  function bloque(s) {
    return el('div', 'margin-bottom:18px;border:1px solid ' + rgba(s.color, .45) + ';border-left:5px solid ' +
      s.color + ';border-radius:0 12px 12px 0;background:' + rgba(s.color, .035) + ';overflow:hidden');
  }

  function montar(sec) {
    var seccion = document.getElementById('seccion-' + sec);
    if (!seccion) return;
    var top = seccion.querySelector(':scope > .top-bar');
    var aqui = window.SE_SAG.actual;

    // -- textos de ayuda: UNA vez, arriba de los bloques ----------------------
    // Explican como funciona la seccion, que es igual para todos los SAG; no
    // tienen que repetirse dentro de cada bloque. Se marcan con data-se-ayuda
    // en la plantilla; si el atributo trae texto, se muestran como desplegable
    // con ese titulo.
    var ayudas = Array.prototype.slice.call(seccion.querySelectorAll('[data-se-ayuda]'));
    var zonaAyuda = null;
    if (ayudas.length) {
      zonaAyuda = el('div', 'margin:0 0 16px');
      zonaAyuda.setAttribute('data-ms-ayuda', sec);
      ayudas.forEach(function (n) {
        var titulo = n.getAttribute('data-se-ayuda');
        n.removeAttribute('data-se-ayuda');
        if (titulo) {
          var d = el('details', 'margin-bottom:12px');
          var sm = el('summary', 'cursor:pointer;color:#4fb3d9;font-size:.86rem;font-weight:600', titulo);
          d.appendChild(sm);
          var caja = el('div', 'margin-top:8px;padding:12px 14px;border:1px solid #23323b;border-radius:8px;background:#101a20');
          caja.appendChild(n);
          n.style.margin = '0';
          d.appendChild(caja);
          zonaAyuda.appendChild(d);
        } else {
          zonaAyuda.appendChild(n);
        }
      });
    }

    // -- bloque del SAG de la pagina: el contenido de siempre, envuelto ------
    var propio = bloque(aqui);
    propio.setAttribute('data-ms-propio', sec);
    var c = cabecera(aqui, true);
    propio.appendChild(c.cab);
    var cuerpo = el('div', 'padding:16px');
    propio.appendChild(cuerpo);
    // Los botones de la seccion (Guardar, Vaciar, + Nuevo...) son de ESTE SAG:
    // pasan a su cabecera para que no parezcan de todos.
    if (top && top.children.length > 1) {
      var botones = top.children[top.children.length - 1];
      c.der.appendChild(botones);
    }
    var hijos = Array.prototype.slice.call(seccion.childNodes);
    var despuesDelTop = !top;
    hijos.forEach(function (n) {
      if (n === top) { despuesDelTop = true; return; }
      if (despuesDelTop) cuerpo.appendChild(n);
    });
    if (zonaAyuda) seccion.appendChild(zonaAyuda);
    seccion.appendChild(propio);

    // -- bloques de los demas SAG: el mismo editor, embebido -------------------
    window.SE_SAG.todos.forEach(function (s) {
      if (s.id === aqui.id) return;
      var b = bloque(s);
      var src = '/espesador?_sag=' + encodeURIComponent(s.id) + '&_embed=1#' + sec;
      var recargar = el('button', 'background:transparent;border:1px solid #35474f;color:#a2b1b9;' +
        'padding:4px 10px;border-radius:7px;font-size:.75rem;cursor:pointer', '↻ Recargar');
      recargar.title = 'Volver a leer ' + s.nombre;
      var cab = cabecera(s, false, recargar);
      b.appendChild(cab.cab);
      var ifr = el('iframe', 'display:block;width:100%;height:220px;border:0;background:transparent');
      ifr.title = sec + ' de ' + s.nombre;
      ifr.setAttribute('loading', 'lazy');
      ifr.src = src;
      recargar.onclick = function () { ifr.src = 'about:blank'; setTimeout(function () { ifr.src = src; }, 30); };
      b.appendChild(ifr);
      seccion.appendChild(b);
    });
    montadas[sec] = true;
  }

  function activar() {
    if (!multi()) return;
    var sec = (location.hash || '#reglas').replace(/^#/, '');
    if (SECCIONES.indexOf(sec) === -1 || montadas[sec]) return;
    montar(sec);
  }

  window.MultiSeccion = {activar: activar, secciones: SECCIONES};
  window.addEventListener('hashchange', activar);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', activar);
  else activar();
})();
