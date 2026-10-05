# -*- coding: utf-8 -*-
"""Promedio y desviacion estandar del nivel del Hopper.

Que hace
--------
Muestrea el nivel cada `muestreo_s` (1 s) MIENTRAS EL MOTOR ESTA ENCENDIDO y
guarda las muestras en memoria, solo las que caben en la ventana maxima.

Dos lecturas de lo mismo:

- **Calculo periodico** (`calculo`): cada `recalculo_min` minutos (30 por
  defecto) se toma la ventana de los ultimos `ventana_min` minutos y se guarda
  promedio y desviacion estandar con su hora. Es el numero "oficial": cambia
  una vez por periodo y se puede citar en un reporte.
- **En vivo** (`en_vivo`): la misma ventana, deslizante, recalculada al pedirla.

El rango (`ventana_min`) y el periodo (`recalculo_min`) son editables y se
persisten en `config/espesador/estadistica.json`. El tope del rango
(`ventana_max_min`) tambien es configuracion y no una constante enterrada: aun
no esta decidido cual sera. `TOPE_DURO_MIN` (24 h) solo protege la memoria:
1 muestra por segundo son 86.400 muestras en 24 h.

Desviacion estandar
-------------------
MUESTRAL (n-1). Con minutos de datos a 1 Hz la diferencia con la poblacional es
despreciable; se informa `n` para que se pueda comprobar.

Esta clase no conoce Flask ni el motor: recibe dos funciones (`leer_valor`,
`esta_corriendo`) para poder probarla sin hilos ni reloj real.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Optional

from core.jsonio import escribir_json_atomico

TOPE_DURO_MIN = 1440            # 24 h: limite de memoria, no de negocio

DEFAULTS = {
    "ventana_min":     30,      # rango sobre el que se calcula
    "recalculo_min":   30,      # cada cuanto se fija el calculo periodico
    "ventana_max_min": 1440,    # tope del rango editable
}


def _entero(v, defecto: int, lo: int, hi: int) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return defecto
    return max(lo, min(hi, n))


def normalizar_cfg(crudo: Optional[dict]) -> dict:
    """Devuelve una config valida, completando y acotando lo que venga."""
    crudo = crudo if isinstance(crudo, dict) else {}
    tope = _entero(crudo.get("ventana_max_min"), DEFAULTS["ventana_max_min"],
                   5, TOPE_DURO_MIN)
    return {
        "ventana_max_min": tope,
        "ventana_min":   _entero(crudo.get("ventana_min"), DEFAULTS["ventana_min"], 1, tope),
        "recalculo_min": _entero(crudo.get("recalculo_min"), DEFAULTS["recalculo_min"], 1, tope),
    }


def estadistica_de(valores) -> dict:
    """Promedio, desviacion estandar muestral, minimo y maximo de `valores`."""
    n = len(valores)
    if n == 0:
        return {"n": 0, "promedio": None, "desv": None, "min": None, "max": None}
    media = math.fsum(valores) / n
    if n < 2:
        desv = None
    else:
        desv = math.sqrt(math.fsum((x - media) ** 2 for x in valores) / (n - 1))
    return {"n": n, "promedio": media, "desv": desv,
            "min": min(valores), "max": max(valores)}


class EstadisticaNivel:
    def __init__(self, ruta_cfg: str,
                 leer_valor: Callable[[], Optional[float]],
                 esta_corriendo: Callable[[], bool],
                 reloj: Callable[[], float] = time.time,
                 muestreo_s: float = 1.0):
        self._ruta = ruta_cfg
        self._leer_valor = leer_valor
        self._esta_corriendo = esta_corriendo
        self._reloj = reloj
        self._muestreo_s = muestreo_s
        self._lock = threading.Lock()
        self._muestras: deque = deque()          # (t, valor), t ascendente
        self._calculo: Optional[dict] = None
        self._proximo: Optional[float] = None
        self._corria = False
        self._hilo: Optional[threading.Thread] = None
        self._cfg = self._cargar()

    # ---------------- configuracion ----------------
    def _cargar(self) -> dict:
        import json, os
        crudo = None
        try:
            if os.path.exists(self._ruta):
                with open(self._ruta, encoding="utf-8") as f:
                    crudo = json.load(f)
        except Exception:                                   # noqa: BLE001
            crudo = None                # corrupto o ilegible: se usan defaults
        return normalizar_cfg(crudo)

    def config(self) -> dict:
        with self._lock:
            return dict(self._cfg)

    def configurar(self, ventana_min=None, recalculo_min=None, ventana_max_min=None) -> dict:
        """Cambia y persiste la config. Lo no enviado se conserva."""
        with self._lock:
            nuevo = dict(self._cfg)
            if ventana_max_min is not None:
                nuevo["ventana_max_min"] = ventana_max_min
            if ventana_min is not None:
                nuevo["ventana_min"] = ventana_min
            if recalculo_min is not None:
                nuevo["recalculo_min"] = recalculo_min
            self._cfg = normalizar_cfg(nuevo)
            escribir_json_atomico(self._ruta, self._cfg)
            # El periodo nuevo se aplica desde ahora, no desde el ultimo calculo.
            if self._proximo is not None:
                base = self._calculo["ts"] if self._calculo else self._reloj()
                self._proximo = base + self._cfg["recalculo_min"] * 60
            return dict(self._cfg)

    # ---------------- muestreo ----------------
    def muestrear(self) -> None:
        """Un paso del muestreador. Lo llama el hilo (o un test) cada segundo."""
        ahora = self._reloj()
        corre = bool(self._esta_corriendo())
        with self._lock:
            if corre and not self._corria:
                # Arranque: el primer calculo periodico llega un periodo despues.
                self._proximo = ahora + self._cfg["recalculo_min"] * 60
            self._corria = corre
            if not corre:
                return
            v = self._leer_valor()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                self._muestras.append((ahora, float(v)))
            corte = ahora - self._cfg["ventana_max_min"] * 60
            while self._muestras and self._muestras[0][0] < corte:
                self._muestras.popleft()
            if self._proximo is not None and ahora >= self._proximo:
                self._fijar_calculo(ahora)

    def _ventana(self, ahora: float, ventana_min: int) -> list:
        corte = ahora - ventana_min * 60
        return [v for t, v in self._muestras if t >= corte]

    def _fijar_calculo(self, ahora: float) -> None:
        est = estadistica_de(self._ventana(ahora, self._cfg["ventana_min"]))
        est.update(ts=ahora, ventana_min=self._cfg["ventana_min"])
        self._calculo = est
        self._proximo = ahora + self._cfg["recalculo_min"] * 60

    def calcular_ahora(self) -> dict:
        """Fija el calculo periodico en este instante (boton 'Calcular ahora')."""
        with self._lock:
            self._fijar_calculo(self._reloj())
            return dict(self._calculo)

    # ---------------- lectura ----------------
    def estado(self) -> dict:
        ahora = self._reloj()
        with self._lock:
            cfg = dict(self._cfg)
            en_vivo = estadistica_de(self._ventana(ahora, cfg["ventana_min"]))
            return {
                "config":       cfg,
                "corriendo":    self._corria,
                "n_muestras":   len(self._muestras),
                "cubierto_s":   round(ahora - self._muestras[0][0], 1) if self._muestras else 0.0,
                "en_vivo":      en_vivo,
                "calculo":      dict(self._calculo) if self._calculo else None,
                "proximo_ts":   self._proximo if self._corria else None,
                "ahora":        ahora,
            }

    # ---------------- hilo ----------------
    def iniciar(self) -> None:
        with self._lock:
            if self._hilo is not None and self._hilo.is_alive():
                return
            self._hilo = threading.Thread(target=self._loop, daemon=True,
                                          name="estadistica-nivel")
            self._hilo.start()

    def _loop(self) -> None:
        while True:
            try:
                self.muestrear()
            except Exception:                               # noqa: BLE001
                pass            # el muestreador no puede morir por una lectura mala
            time.sleep(self._muestreo_s)
