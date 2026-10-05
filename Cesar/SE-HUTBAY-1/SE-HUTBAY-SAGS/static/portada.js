// Portada comun de la planta: vive en el contenedor del Hopper (puerto 5000).
// Este contenedor (SAG) no tiene portada propia que mostrar: los enlaces de
// "Inicio" (logo Hudbay, firma ReTO, href="/") se reescriben para volver a la
// portada del 5000, en el mismo host desde el que se abrio esta pagina.
// Para cambiar el puerto de la portada, editar SOLO esta constante.
(function () {
  var PORTADA_PUERTO = 5000;
  var url = window.location.protocol + '//' + window.location.hostname + ':' + PORTADA_PUERTO + '/';
  document.querySelectorAll('a[href="/"]').forEach(function (a) {
    a.href = url;
    a.title = 'Volver a la portada de la planta (' + url + ')';
  });
})();
