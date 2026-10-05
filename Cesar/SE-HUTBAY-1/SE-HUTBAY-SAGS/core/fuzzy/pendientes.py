# -*- coding: utf-8 -*-
"""Fuzzy de PENDIENTE configurable — uno o varios por variable.

Hasta el 2026-08-21 las pendientes eran `PEND_MODELOS`, un dict hardcodeado
en `fuzzys_models_espesador.py` con las cuatro variables calibradas del
espesador y cinco placeholders. Eso dejaba tres cosas rotas a la vez:

* No se podian configurar. No habia equivalente a `fuzzy.json` para ellas, asi
  que una planta distinta no tenia forma de declarar ninguna.
* **El tiempo de calculo no era asignable.** `evaluar_pendiente_var` recibia
  `ventana_s=60.0` como valor por defecto y NADIE se lo pasaba nunca: toda
  pendiente media exactamente un minuto, dijera lo que dijera la
  documentacion.
* Solo podia haber UNA por variable, porque la clave era `pend_<var>`. No se
  podia mirar la misma senal a 5 min y a 30 min, que es justo lo que
  distingue un arranque de una deriva lenta.

Este modulo las convierte en objetos de primera clase: cada pendiente tiene
nombre propio, su variable de origen, su ventana y sus etiquetas, y se
declaran en `pendientes.json`.

Identidad: el nombre lo elige el operador y **queda congelado**. Derivarlo de
la ventana (`pend_torque_5min`) parecia mas predecible, pero entonces cambiar
la ventana de 5 a 10 min renombraria la variable y dejaria mudas en silencio
todas las reglas que la nombran — exactamente el problema que el proyecto ya
habia decidido evitar con los identificadores de tag.
"""
from __future__ import annotations

from collections import deque

import numpy as np

from core.fuzzy.templates import crear_clase_fuzzy_pendiente


# Igual que en las variables calculadas: el buffer se decima para que el costo
# por tick no dependa de la velocidad del lazo.
MAX_MUESTRAS = 240

# Minimo de puntos para que una regresion signifique algo.
MIN_PUNTOS = 3

ETIQUETAS_PENDIENTE_DEFAULT = {
    "DEC":    [1.0, 0.5, 0.0, 0.0, 0.0],
    "STABLE": [0.0, 0.5, 1.0, 0.5, 0.0],
    "INC":    [0.0, 0.0, 0.0, 0.5, 1.0],
}

# Eje por defecto en unidades de ingenieria POR MINUTO. Es una plantilla
# neutra: hay que calibrarla mirando la variable real.
EJE_PENDIENTE_DEFAULT = [-1.0, -0.25, 0.0, 0.25, 1.0]


# La ventana se declara en SEGUNDOS. Hasta el 2026-09-22 se declaraba en
# minutos (`ventana_min`), y eso obligaba a escribir 0.33 para pedir 20 s —
# justo el rango que interesa cuando la pendiente se sintoniza contra el filtro
# (2x a 4x su ventana). `ventana_min` se sigue aceptando al LEER para no romper
# los pendientes.json que ya estan en disco; al guardar se escribe `ventana_s`.
def ventana_s_de_spec(spec: dict) -> float | None:
    """Ventana en segundos de una spec, venga en `ventana_s` o en `ventana_min`.

    Devuelve None si no hay ninguna de las dos o no es un numero > 0. El
    orden importa: si estan las dos, manda `ventana_s`, que es la nueva.
    """
    if not isinstance(spec, dict):
        return None
    if spec.get("ventana_s") is not None:
        bruto, factor = spec.get("ventana_s"), 1.0
    elif spec.get("ventana_min") is not None:
        bruto, factor = spec.get("ventana_min"), 60.0
    else:
        return None
    try:
        valor = float(bruto) * factor
    except (TypeError, ValueError):
        return None
    return valor if valor > 0.0 else None


def construir_registry_pendientes(cfg: dict) -> dict:
    """Arma los modelos difusos de pendiente desde `pendientes.json`.

    cfg: {<nombre>: {"variable": str, "ventana_s": float,
                     "x": [...], "labels": {<ETIQUETA>: [...]}}}

    `ventana_min` (minutos) se sigue aceptando: es la forma vieja y hay
    archivos en disco con ella. Ver `ventana_s_de_spec`.

    Igual que `construir_registry_fuzzy`, las entradas mal formadas se omiten
    en vez de romper el arranque: una config a medio escribir no puede dejar
    a la planta sin experto.
    """
    registry: dict = {}
    for nombre, spec in (cfg or {}).items():
        if not isinstance(spec, dict):
            continue
        variable = str(spec.get("variable") or "").strip()
        eje = spec.get("x")
        labels = spec.get("labels") or {}
        if not variable or not isinstance(eje, list) or len(eje) < 3:
            continue
        if not isinstance(labels, dict) or not labels:
            continue
        if any(not isinstance(v, list) or len(v) != len(eje) for v in labels.values()):
            continue
        ventana_s = ventana_s_de_spec(spec)
        if ventana_s is None:
            continue
        try:
            Klass = crear_clase_fuzzy_pendiente(
                f"{nombre}_cfg",
                [float(x) for x in eje],
                **{str(k): [float(x) for x in v] for k, v in labels.items()},
            )
            registry[str(nombre)] = {
                "variable":   variable,
                "ventana_s":  ventana_s,
                "min_puntos": max(2, int(spec.get("min_puntos", MIN_PUNTOS))),
                # Nombre de la aceleracion que RATIFICA esta pendiente, o "".
                # Ver `ratificar` mas abajo.
                "ratifica_con": str(spec.get("ratifica_con") or "").strip(),
                "model":      Klass(),
            }
        except (TypeError, ValueError):
            continue
    return registry


# ============================================================
# Ratificacion de una pendiente con una aceleracion
# ------------------------------------------------------------
# Una pendiente mide la tendencia sobre una ventana LARGA (minutos). Eso es lo
# que la hace util —no se deja llevar por el ruido— y tambien lo que la hace
# llegar tarde: sigue diciendo "sube" un rato despues de que la variable dejo
# de subir, porque la mayor parte de su ventana todavia esta subiendo.
#
# La aceleracion mira una ventana CORTA (5 a 10 s) y produce `rate`: la
# derivada EN EL EXTREMO DERECHO, o sea lo que la variable esta haciendo ahora.
# Ratificar es preguntarle a ese rate si la tendencia larga sigue vigente:
#
#   rate y pendiente al mismo lado  -> la tendencia se sostiene: pasa como esta
#   |rate| dentro de su banda muerta -> no alcanza para desmentir: pasa como
#                                       esta (no confirma, pero tampoco niega)
#   rate al lado contrario           -> DESMENTIDA: la pendiente se evalua con
#                                       valor CERO
#
# Por que cero y no una etiqueta fija: las etiquetas las nombra el operador
# (INC/DEC/STABLE es solo la plantilla, puede ser SUBIENDO/QUIETO/CAE_RAPIDO).
# Forzar "STABLE" obligaria a adivinar cual es la neutra de cada planta.
# Evaluar el mismo modelo difuso en 0.0 no necesita saber ningun nombre: se
# activa la etiqueta que el operador haya puesto sobre el cero de SU eje, con
# la forma que le haya dado. El SE deja de afirmar que se mueve, y no afirma
# nada nuevo en su lugar.
#
# El caso del rate en banda muerta PASA a proposito. Tratarlo como "no
# confirmado" dejaria la pendiente muda en operacion normal y tranquila, que
# es justo cuando mas se la necesita.
# ============================================================

def ratificar(pendiente: float, acel: dict | None) -> tuple[float, str]:
    """(valor a fuzzificar, motivo). `motivo` vacio = no hubo nada que hacer."""
    if not isinstance(acel, dict):
        return float(pendiente), ""
    try:
        rate = float(acel.get("rate"))
        banda = abs(float(acel.get("umbral_rate", 0.0)))
    except (TypeError, ValueError):
        return float(pendiente), ""

    p = float(pendiente)
    if p == 0.0:
        return p, ""
    if abs(rate) <= banda:
        # Rate practicamente nulo: no dice nada en ninguna direccion.
        return p, ""
    if (rate > 0.0) == (p > 0.0):
        return p, "ratificada"
    return 0.0, (f"desmentida por la aceleracion (rate {rate:+.4g} u/s contra "
                 f"pendiente {p:+.4g} u/min): se evalua como pendiente nula")


class PendientesOnline:
    """Calcula y fuzzifica las pendientes de un tick.

    Uso:
        p = PendientesOnline(cfg)
        entradas, omitidas = p.actualizar({"torque": 71.2, ...}, t_s=12.3)

    `entradas` tiene la misma forma que una entrada de `fuzzy_out`, asi que se
    mezcla directamente y las reglas la nombran como a cualquier variable.
    """

    def __init__(self, cfg: dict | None = None, max_muestras: int = MAX_MUESTRAS):
        self._registry = construir_registry_pendientes(cfg or {})
        self._max = max(4, int(max_muestras))
        self._hist: dict[str, deque] = {}
        self._t_inicio: dict[str, float] = {}

    @property
    def nombres(self) -> list[str]:
        return sorted(self._registry)

    def variables_fuente(self) -> set[str]:
        return {m["variable"] for m in self._registry.values()}

    def reset(self) -> None:
        self._hist.clear()
        self._t_inicio.clear()

    # --------------------------------------------------------
    def _muestras(self, nombre: str, valor: float, t_s: float,
                  ventana_s: float) -> list[tuple[float, float]]:
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
    def actualizar(self, valores: dict, t_s: float,
                   aceleraciones: dict | None = None) -> tuple[dict, list[str]]:
        """Devuelve ({nombre: entrada_fuzzy}, omitidas).

        `aceleraciones` es la salida de `AceleracionesOnline.actualizar` de
        ESTE MISMO tick. Solo se usa para las pendientes que declaran
        `ratifica_con`; ver `ratificar`.

        Una pendiente NO se produce hasta que la ventana este cubierta. Con
        medio minuto de historia, una ventana de 30 min no puede decir si el
        proceso esta estable: reportar `STABLE` mientras tanto seria afirmar
        algo que el motor no puede sostener, y las reglas actuarian sobre esa
        afirmacion. Mismo criterio que no fuzzificar una PV sin sus limites:
        la variable no existe y las reglas que la nombran quedan
        `no_evaluable`, con el motivo a la vista en la traza.
        """
        out: dict = {}
        omitidas: list[str] = []

        for nombre, meta in self._registry.items():
            var = meta["variable"]
            if var not in (valores or {}):
                omitidas.append(f"{nombre} (sin dato de {var})")
                continue

            ventana_s = float(meta["ventana_s"])
            t0 = self._t_inicio.setdefault(nombre, float(t_s))
            muestras = self._muestras(nombre, float(valores[var]), t_s, ventana_s)

            edad = float(t_s) - float(t0)
            if edad < ventana_s or len(muestras) < int(meta["min_puntos"]):
                faltan = max(0.0, ventana_s - edad)
                omitidas.append(f"{nombre} (ventana incompleta: faltan {faltan:.0f} s)")
                continue

            pendiente = _pendiente_por_minuto(muestras)

            # --- Ratificacion con una aceleracion de ventana corta ---------
            medida = pendiente
            motivo = ""
            ratificador = meta.get("ratifica_con") or ""
            if ratificador:
                acel = (aceleraciones or {}).get(ratificador)
                if not isinstance(acel, dict):
                    # Se pidio ratificar y no hay con que. NO se emite la
                    # pendiente sin ratificar: seria afirmar justo lo que el
                    # operador pidio comprobar. Mismo criterio fail-closed que
                    # el NOT del motor y que una PV sin limites.
                    omitidas.append(
                        f"{nombre} (ratificador '{ratificador}' no disponible "
                        "en este tick)")
                    continue
                pendiente, motivo = ratificar(pendiente, acel)

            dom, valor, pert, inf = meta["model"].evaluar(pendiente)
            out[nombre] = {
                "dom": str(dom).strip().upper(),
                "val": float(pert.get(str(dom).strip().upper(), inf)),
                "offset": float(pendiente),
                "pert": {str(k).upper(): float(v) for k, v in pert.items()},
                # Metadatos para la traza: sin esto, una pendiente que no se
                # mueve y una mal calibrada se ven identicas.
                # `slope_per_min` es lo que se FUZZIFICO; `slope_medido` es lo
                # que dio la regresion. Son distintos solo cuando la
                # aceleracion desmintio la tendencia, y verlos separados es lo
                # que permite entender por que el SE no actuo.
                "slope_per_min": float(pendiente),
                "slope_medido": float(medida),
                "ratifica_con": ratificador,
                "ratificacion": motivo,
                "variable": var,
                "ventana_s": ventana_s,
                "n_puntos": len(muestras),
                "es_pendiente": True,
            }

        return out, omitidas


def _pendiente_por_minuto(muestras: list[tuple[float, float]]) -> float:
    """Regresion lineal sobre la ventana, en unidades de ingenieria por minuto.

    Se usa la recta y no `(ultimo - primero)` porque una sola muestra ruidosa
    en cualquiera de los dos extremos definiria toda la tendencia.
    """
    t_vals = np.array([t for t, _ in muestras], dtype=float)
    y_vals = np.array([y for _, y in muestras], dtype=float)
    t_ref = t_vals - t_vals[0]
    if np.allclose(t_ref.max() - t_ref.min(), 0.0):
        return 0.0
    m, _ = np.polyfit(t_ref, y_vals, 1)
    return float(m * 60.0)
