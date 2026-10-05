# -*- coding: utf-8 -*-
"""Reinicio en caliente: aplicar configuracion sin soltar el lazo ni las ventanas.

Aplicar un cambio con el motor corriendo costaba dos cosas que no tenian nada
que ver con el cambio: el SP se pegaba al del DCS (porque `stop()` suelta
ENABLE_EXT y el DCS retira el FBK) y el SE quedaba mudo hasta rellenar las
ventanas de pendientes y aceleraciones. Estos tests fijan que el reinicio en
caliente no haga ninguna de las dos, y — igual de importante — que SI vacie el
buffer cuando la ventana cambio, porque ahi el buffer viejo describe otra
pregunta.

Las fixtures se reusan de `test_pipeline`: son las que apuntan TODOS los JSON
a temporales. Sin eso un test leeria la calibracion viva de la planta, que es
la leccion A17/A19 del backlog.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.state as st
from tests.test_pipeline import kep, store, config_completa      # noqa: F401


PEND = {
    "pend_pv0_2min": {
        "variable": None,               # se completa con la PV del contrato
        "ventana_min": 2.0,
        "x": [-1.0, -0.25, 0.0, 0.25, 1.0],
        "labels": {"DEC":    [1.0, 0.5, 0.0, 0.0, 0.0],
                   "INC":    [0.0, 0.0, 0.0, 0.5, 1.0],
                   "STABLE": [0.0, 0.5, 1.0, 0.5, 0.0]},
    }
}


def _cfg_pendiente(pv, ventana_min=2.0):
    cfg = {k: dict(v) for k, v in PEND.items()}
    cfg["pend_pv0_2min"]["variable"] = pv
    cfg["pend_pv0_2min"]["ventana_min"] = ventana_min
    return cfg


def _motor(monkeypatch, cfg_pend):
    monkeypatch.setattr(st, "pendientes_definidas", lambda: cfg_pend)
    monkeypatch.setattr(st, "_alerts", st.AlertCollector())
    motor = st.SEEngine()
    motor._piso_s = 1.0
    motor._init_state()
    return motor


def _correr(motor, segundos, paso_s=1.0):
    """Corre ticks adelantando el reloj del motor, sin dormir.

    El reloj sale de `time.monotonic() - _t0`, asi que se adelanta corriendo
    `_t0` hacia atras. Cubrir una ventana de dos minutos en tiempo real haria
    que este test costara dos minutos.
    """
    objetivo = motor._t_s + segundos
    while motor._t_s < objetivo:
        motor._run_tick()
        motor._t0 -= paso_s


def _pendiente_viva(motor):
    tz = st._get_trazas(1)
    tz = tz[0] if tz else {}
    mudas = {str(m).split(" ", 1)[0] for m in (tz.get("pendientes_omitidas") or [])}
    return "pend_pv0_2min" not in mudas


# ---------------------------------------------------------------
# Lo que el reinicio en caliente NO debe perder
# ---------------------------------------------------------------
def test_el_reinicio_caliente_conserva_la_ventana_de_la_pendiente(store, kep,
                                                                  config_completa,
                                                                  monkeypatch):
    """Es el motivo de todo el mecanismo.

    En frio, guardar una regla dejaba al SE sin poder evaluar NINGUNA que
    nombrara la pendiente durante toda su ventana — 2 min con esta config.
    """
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    motor = _motor(monkeypatch, _cfg_pendiente(pv))

    motor._run_tick()
    assert not _pendiente_viva(motor), "recien arrancado no puede haber pendiente"
    _correr(motor, 125.0)
    assert _pendiente_viva(motor), "con la ventana cubierta tiene que producirse"

    snap = motor._snapshot_caliente()
    motor._init_state()                          # recarga la config nueva
    avisos = motor._restaurar_caliente(snap)     # devuelve lo que no cambio
    motor._run_tick()

    assert avisos == []
    assert _pendiente_viva(motor), \
        "el reinicio en caliente no puede obligar a rellenar la ventana"


def test_el_reinicio_frio_si_vacia_la_ventana(store, kep, config_completa,
                                              monkeypatch):
    """El contraste: sin transplante, el SE queda mudo. Fija la diferencia."""
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    motor = _motor(monkeypatch, _cfg_pendiente(pv))
    motor._run_tick()
    _correr(motor, 125.0)
    assert _pendiente_viva(motor)

    motor._init_state()                          # frio: sin restaurar nada
    motor._run_tick()
    assert not _pendiente_viva(motor)


def test_el_reinicio_caliente_no_toca_el_objetivo_interno_del_sp(store, kep,
                                                                 config_completa,
                                                                 monkeypatch):
    """El SP interno no puede saltar por aplicar una configuracion.

    `_init_state()` resiembra los SP leyendo el DCS, que es lo correcto en un
    arranque frio. En caliente el SE puede venir a mitad de una rampa: adoptar
    el tag del DCS perderia el objetivo que las reglas ya decidieron.
    """
    from config import VARIABLES_PROCESO, SETPOINT_KEYS
    pv, sp0 = VARIABLES_PROCESO[0], SETPOINT_KEYS[0]
    motor = _motor(monkeypatch, _cfg_pendiente(pv))
    motor._run_tick()
    motor._setpoints[sp0] = 77.0                 # el SE decidio moverse aca

    snap = motor._snapshot_caliente()
    motor._init_state()
    motor._restaurar_caliente(snap)

    assert motor._setpoints[sp0] == 77.0


def test_el_reinicio_caliente_conserva_el_reloj_del_motor(store, kep,
                                                          config_completa,
                                                          monkeypatch):
    """El reloj NO puede volver a cero.

    Todo lo fechado (buffers, waits, retencion del ultimo valor bueno, rate
    limit) esta en la escala de `_t_s`. Con el reloj en cero esas marcas
    quedarian en el futuro y el motor las leeria como edades negativas: o sea,
    dentro de la ventana para siempre.
    """
    from config import VARIABLES_PROCESO
    motor = _motor(monkeypatch, _cfg_pendiente(VARIABLES_PROCESO[0]))
    motor._run_tick()
    _correr(motor, 30.0)
    t_antes = motor._t_s

    snap = motor._snapshot_caliente()
    motor._init_state()
    motor._restaurar_caliente(snap)

    assert motor._t_s >= t_antes


def test_el_reinicio_caliente_conserva_lo_ya_escrito_al_dcs(store, kep,
                                                            config_completa,
                                                            monkeypatch):
    """Sin esto, el write-on-change se cree en su primer tick y reescribe todo.

    `_sp_escritos` va indexado por TAG y no por familia. Filtrarlo contra
    `_setpoints` (que va por familia) lo vaciaba entero, y el reinicio mandaba
    al DCS un write de cada setpoint — justo el "salto al aplicar cambios" que
    el mecanismo existe para evitar.
    """
    from config import VARIABLES_PROCESO
    motor = _motor(monkeypatch, _cfg_pendiente(VARIABLES_PROCESO[0]))
    motor._run_tick()
    escritos_antes = dict(motor._sp_escritos)
    assert escritos_antes, "el primer tick tiene que haber escrito algo"

    snap = motor._snapshot_caliente()
    motor._init_state()
    motor._restaurar_caliente(snap)

    assert motor._sp_escritos == escritos_antes


# ---------------------------------------------------------------
# Lo que el reinicio en caliente SI debe vaciar
# ---------------------------------------------------------------
def test_cambiar_la_ventana_vacia_el_buffer_y_lo_avisa(store, kep,
                                                       config_completa,
                                                       monkeypatch):
    """Un buffer con otra ventana describe otra pregunta.

    Las muestras estan decimadas con otro paso y la cobertura medida seria
    falsa. Se arranca vacio — mismo criterio que 'sin historia no se afirma
    nada' — y se avisa, para que no parezca que el SE no arranco.
    """
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    motor = _motor(monkeypatch, _cfg_pendiente(pv))
    motor._run_tick()
    _correr(motor, 125.0)
    assert _pendiente_viva(motor)

    snap = motor._snapshot_caliente()
    monkeypatch.setattr(st, "pendientes_definidas",
                        lambda: _cfg_pendiente(pv, ventana_min=5.0))
    motor._init_state()
    avisos = motor._restaurar_caliente(snap)
    motor._run_tick()

    assert any("pend_pv0_2min" in a for a in avisos), avisos
    assert not _pendiente_viva(motor)


def test_resintonizar_el_filtro_vacia_su_buffer_y_lo_avisa(store, kep,
                                                           config_completa,
                                                           monkeypatch):
    """Los buckets del Exp-Q dependen de `ventana_s`: con otra, no sirven."""
    import runner
    from config import VARIABLES_PROCESO
    pv = VARIABLES_PROCESO[0]
    motor = _motor(monkeypatch, _cfg_pendiente(pv))
    _correr(motor, 20.0)
    assert motor._filtro.longitud_buffer(pv) > 0

    snap = motor._snapshot_caliente()
    monkeypatch.setattr(runner, "cargar_filtros_json",
                        lambda path=None: {v: {"q": 0.4, "ventana_s": 10.0}
                                           for v in VARIABLES_PROCESO})
    motor._init_state()
    avisos = motor._restaurar_caliente(snap)

    assert any("filtro" in a for a in avisos), avisos
    assert motor._filtro.longitud_buffer(pv) == 0


# ---------------------------------------------------------------
# Registro de cortes: lo que consumen las OTRAS pestanas
# ---------------------------------------------------------------
def test_el_corte_se_abre_y_se_cierra_con_el_motor(monkeypatch):
    """La franja celeste del Explorador sale de aca, no de una cuenta local."""
    antes = len(st.cortes_motor())
    corte = st._abrir_corte("prueba", caliente=True)
    assert corte["fin_ms"] is None, "un corte abierto = el motor sigue abajo"
    assert len(st.cortes_motor()) == antes + 1

    gen_antes = st.generacion_motor()
    st._cerrar_corte(corte)
    assert corte["fin_ms"] is not None
    assert st.generacion_motor() == gen_antes + 1, \
        "la generacion es lo que hace que las otras pestanas releean"


def test_un_corte_sin_cerrar_no_deja_dos_abiertos(monkeypatch):
    """Dos tramos abiertos pintarian desde el primero hasta el borde, siempre."""
    a = st._abrir_corte("primero")
    b = st._abrir_corte("segundo")
    assert a["fin_ms"] is not None, "abrir el segundo tiene que cerrar el primero"
    assert b["fin_ms"] is None
    st._cerrar_corte(b)


def test_cortes_motor_recorta_a_la_ventana_pedida():
    corte = st._abrir_corte("ventana")
    st._cerrar_corte(corte)
    ini, fin = corte["inicio_ms"], corte["fin_ms"]
    assert corte in st.cortes_motor(ini - 1000, fin + 1000)
    # Muy en el pasado: ese tramo no cae en la ventana.
    assert corte not in st.cortes_motor(ini - 10_000_000, ini - 9_000_000)


def test_status_publica_la_generacion(store, kep, config_completa, monkeypatch):
    motor = _motor(monkeypatch, {})
    assert "generacion" in motor.status()


# ---------------------------------------------------------------
# Concurrencia: dos pestanas del panel reiniciando a la vez
# ---------------------------------------------------------------
def test_reinicios_simultaneos_no_dejan_dos_motores(store, kep, config_completa,
                                                    monkeypatch):
    """Regresion. gunicorn corre con `threads = 4`, asi que dos requests se
    atienden en paralelo de verdad, y con el auto-reinicio a 1 s basta tener
    DOS PESTANAS del panel abiertas para que las dos manden su reinicio casi a
    la vez.

    Sin el candado: entre que `_parar_hilo` pone `_running=False` y `start()`
    lo vuelve a poner en True hay una ventana en la que el segundo pasa el
    chequeo y se lanza un SEGUNDO hilo — dos motores escribiendo los mismos SP
    al DCS. Verificado antes del arreglo; tambien reventaba con AttributeError
    porque `_thread` quedaba en None a mitad del `join`.
    """
    import threading

    from config import VARIABLES_PROCESO
    motor = _motor(monkeypatch, _cfg_pendiente(VARIABLES_PROCESO[0]))
    motor.start(piso_s=0.05)
    try:
        barrera = threading.Barrier(3)
        errores = []

        def _reiniciar():
            barrera.wait()
            try:
                motor.reiniciar(caliente=True)
            except Exception as exc:                        # noqa: BLE001
                errores.append(repr(exc))

        hilos = [threading.Thread(target=_reiniciar) for _ in range(3)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=30)

        assert errores == [], errores
        vivos = [t for t in threading.enumerate()
                 if t is not threading.current_thread() and t.is_alive()
                 and t not in hilos]
        assert len(vivos) <= 1, f"quedaron {len(vivos)} motores vivos"
        assert motor._running is True
    finally:
        motor.stop()
