# -*- coding: utf-8 -*-
"""Variables calculadas EN VIVO — evaluacion incremental, tick a tick.

`core.variables.calculator` calcula sobre un DataFrame completo: sirve para el
runner de CSV y para la simulacion, pero no para el motor, que ve una muestra
por vez y no tiene el futuro. Este modulo es el equivalente online: mantiene el
estado minimo entre ticks y devuelve el valor de cada definicion en el instante
actual.

Diferencias de fondo con la version batch, todas deliberadas:

* **Se mide en segundos de proceso, no en muestras.** La version batch traduce
  `ventana_min` a un numero de filas con un `dt_s` fijo. El motor corre en ciclo
  libre: el periodo cambia tick a tick, asi que una ventana en muestras no
  significa nada. Aqui la ventana se evalua contra el `t_s` real de cada
  muestra, igual que el filtro Exp-Q y las pendientes.

* **Degrada, no revienta.** Si a una definicion le falta una fuente en este
  tick (una PV con calidad mala, por ejemplo), esa variable no se produce y se
  reporta en `omitidas`. La version batch lanza `KeyError`, que en vivo se
  llevaria el tick entero.

* **El buffer esta decimado.** Una ventana de 30 min a 10 tick/s serian 18.000
  muestras por variable. Se guarda como mucho `MAX_MUESTRAS` por variable,
  espaciadas uniformemente en la ventana, asi que el costo por tick no depende
  de la velocidad del lazo.
"""
from __future__ import annotations

import math
from collections import deque


# Muestras maximas por variable con ventana. Con 240 puntos, una ventana de
# 30 min guarda una muestra cada 7,5 s: de sobra para un delta o una
# desviacion estandar de tendencia, y acotado para el lazo mas rapido.
MAX_MUESTRAS = 240

TIPOS_CON_VENTANA = ("rolling_delta", "rolling_std")

_OPERACIONES = {
    "suma":           lambda a, b: a + b,
    "resta":          lambda a, b: a - b,
    "multiplicacion": lambda a, b: a * b,
    # La division por cero no es un error del operador: es un estado del
    # proceso. Se reporta como "no calculable" y la variable no se produce,
    # en vez de propagar un NaN que despues fuzzifica cualquier cosa.
    "division":       lambda a, b: (a / b) if abs(b) > 1e-12 else None,
}


def normalizar_definiciones(definiciones) -> list[dict]:
    """Acepta la lista de `variables.json` o el dict del proceso.

    Devuelve siempre una lista ORDENADA: el orden es el que permite encadenar
    (una calculada puede ser argumento de la siguiente).
    """
    if isinstance(definiciones, dict):
        return [{"nombre": nombre, **cfg} for nombre, cfg in definiciones.items()]
    return [dict(d) for d in (definiciones or []) if isinstance(d, dict)]


def fuentes_de_definicion(defn: dict) -> list[str]:
    """Nombres que una definicion necesita para poder calcularse."""
    if str(defn.get("tipo", "")).lower() == "aritmetica":
        return [str(a) for a in (defn.get("args") or [])]
    return [str(defn["arg"])] if defn.get("arg") else []


class VariablesOnline:
    """Calcula las variables derivadas de un tick, manteniendo las ventanas.

    Uso:
        vc = VariablesOnline(definiciones)
        calculadas, omitidas = vc.actualizar({"pv_a": 1.0, ...}, t_s=12.3)
    """

    def __init__(self, definiciones=None, max_muestras: int = MAX_MUESTRAS):
        self._defs = normalizar_definiciones(definiciones)
        self._max = max(4, int(max_muestras))
        self._hist: dict[str, deque] = {}

    # --------------------------------------------------------
    @property
    def definiciones(self) -> list[dict]:
        return list(self._defs)

    @property
    def nombres(self) -> list[str]:
        return [str(d["nombre"]) for d in self._defs if d.get("nombre")]

    def reset(self) -> None:
        """Olvida las ventanas. Se llama al arrancar el motor."""
        self._hist.clear()

    # --------------------------------------------------------
    def _muestras(self, nombre: str, valor: float, t_s: float,
                  ventana_s: float) -> list[tuple[float, float]]:
        """Agrega la muestra actual y devuelve las que caen en la ventana.

        Decimacion: solo se guarda una muestra nueva si paso al menos
        `ventana_s / max_muestras` desde la ultima. La muestra ACTUAL siempre
        cuenta — es la que da el extremo derecho del delta — asi que se
        devuelve aparte aunque no se haya guardado.
        """
        buf = self._hist.setdefault(nombre, deque())
        paso_min = float(ventana_s) / float(self._max)
        if not buf or (float(t_s) - buf[-1][0]) >= paso_min:
            buf.append((float(t_s), float(valor)))
        corte = float(t_s) - float(ventana_s)
        while buf and buf[0][0] < corte:
            buf.popleft()
        muestras = list(buf)
        if not muestras or muestras[-1][0] < float(t_s):
            muestras.append((float(t_s), float(valor)))
        return muestras

    # --------------------------------------------------------
    def actualizar(self, valores: dict, t_s: float) -> tuple[dict, list[str]]:
        """Calcula las definiciones sobre los valores de este tick.

        `valores` es el espacio de nombres visible: PV filtradas, crudas y
        setpoints. Las calculadas se van agregando a medida que se resuelven,
        de modo que una puede alimentar a la siguiente.

        Devuelve (calculadas, omitidas). `omitidas` trae un motivo por
        variable, en texto, para que la traza pueda explicarlo.
        """
        espacio = dict(valores or {})
        calculadas: dict[str, float] = {}
        omitidas: list[str] = []

        for defn in self._defs:
            nombre = str(defn.get("nombre") or "")
            if not nombre:
                continue
            tipo = str(defn.get("tipo", "")).lower().strip()

            faltan = [f for f in fuentes_de_definicion(defn) if f not in espacio]
            if faltan:
                # Tipico: la PV de origen no se pudo leer en este tick. La
                # variable no se produce y las reglas que la nombran quedan
                # "no evaluable", que es exactamente lo que hay que ver.
                omitidas.append(f"{nombre} (falta {', '.join(faltan)})")
                continue

            try:
                valor = self._calcular(defn, tipo, espacio, t_s)
            except Exception as exc:                     # definicion mal formada
                omitidas.append(f"{nombre} ({exc})")
                continue

            if valor is None or not math.isfinite(valor):
                omitidas.append(f"{nombre} (no calculable en este tick)")
                continue

            calculadas[nombre] = float(valor)
            espacio[nombre] = float(valor)

        return calculadas, omitidas

    # --------------------------------------------------------
    def _calcular(self, defn: dict, tipo: str, espacio: dict, t_s: float):
        nombre = str(defn["nombre"])

        if tipo == "aritmetica":
            op = str(defn.get("operacion", "")).lower().strip()
            if op not in _OPERACIONES:
                raise ValueError(f"operacion '{op}' no soportada")
            args = [str(a) for a in (defn.get("args") or [])]
            if len(args) != 2:
                raise ValueError("'aritmetica' necesita exactamente 2 argumentos")
            a, b = float(espacio[args[0]]), float(espacio[args[1]])
            return _OPERACIONES[op](a, b)

        if tipo in TIPOS_CON_VENTANA:
            ventana_s = float(defn.get("ventana_min", 30.0)) * 60.0
            if ventana_s <= 0.0:
                raise ValueError("'ventana_min' debe ser > 0")
            arg = str(defn["arg"])
            muestras = self._muestras(nombre, float(espacio[arg]), t_s, ventana_s)

            if tipo == "rolling_delta":
                # Cuanto cambio la variable en la ventana. Con una sola
                # muestra el delta es 0 por definicion, no "sin dato": el
                # motor recien arranco y todavia no vio ningun cambio.
                return muestras[-1][1] - muestras[0][1]

            # rolling_std: con menos de 2 puntos no hay dispersion que medir.
            if len(muestras) < 2:
                return 0.0
            ys = [y for _, y in muestras]
            media = sum(ys) / len(ys)
            var = sum((y - media) ** 2 for y in ys) / (len(ys) - 1)
            return math.sqrt(var)

        raise ValueError(f"tipo '{tipo}' no soportado")
