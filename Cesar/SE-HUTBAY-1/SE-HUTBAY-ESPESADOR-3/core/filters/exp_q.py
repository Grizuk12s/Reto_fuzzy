# ============================================================
# exp_q.py
# ------------------------------------------------------------
# FILTRO EXPONENCIAL-CUADRATICO (Exp-Q) POR VARIABLE
#
# Cada variable tiene su propio (q, ventana_s). NO hay defaults
# globales ni fallback: si una variable que llega en `actualizar`
# no esta en `config_por_variable`, el filtro LANZA un error.
# Esto es intencional (decision 6): si algo no esta configurado,
# queremos enterarnos en seguida, no que pase silenciosamente.
#
# ------------------------------------------------------------
# POR QUE EL FILTRO MIDE SEGUNDOS Y NO MUESTRAS
# ------------------------------------------------------------
# La version anterior pesaba por POSICION de muestra:
#     w[i] = exp(-q * i^2)      (i = 0 la mas reciente)
# y guardaba las ultimas `window_size` muestras. Eso funciona solo
# mientras el lazo corre a periodo fijo. Con el SEEngine en ciclo
# libre el periodo lo fija la latencia de OPC-UA y varia tick a
# tick: "las ultimas 10 muestras" pasaban a significar 50 s o 0,5 s
# segun la carga del momento, con la misma configuracion.
#
# Ahora el peso depende de la EDAD REAL de cada muestra:
#     tau      = ventana_s / PASOS_REFERENCIA
#     w(dt)    = exp(-q * (dt / tau)^2)
#     y        = sum(w_j * x_j) / sum(w_j)
# donde dt es cuantos segundos hace que se tomo la muestra. El
# comportamiento del filtro es identico corra el lazo a 5 s o a
# 50 ms: la unica diferencia es cuantas muestras caen dentro de la
# misma ventana de tiempo (y mas muestras = mejor promedio).
#
# PASOS_REFERENCIA = 10 conserva el significado historico de `q`:
# la config vieja {q, window_size} equivale exactamente a
# {q, ventana_s = window_size * periodo_del_lazo} cuando
# window_size = 10. Por eso `q` mantiene su guia de sintonizacion:
#     q ~ 0.05  -> suavizado fuerte, lag alto
#     q ~ 0.15  -> balance por defecto
#     q ~ 0.40  -> casi sin filtrado, respuesta rapida
# y `ventana_s` dice, en segundos de proceso, cuanto pasado mira.
#
# ------------------------------------------------------------
# COSTO ACOTADO: BUCKETS DE DECIMACION
# ------------------------------------------------------------
# En ciclo libre el filtro recibe muestras muy seguido. Guardar una
# por tick haria crecer la ventana sin techo (a 20 tick/s, una
# ventana de 50 s son 1000 puntos por variable, recalculados en
# CADA tick). En vez de eso las muestras se agrupan en buckets de
# ancho `ventana_s / MUESTRAS_OBJETIVO`, promediando dentro de cada
# bucket. El costo por tick queda acotado a ~MUESTRAS_OBJETIVO
# terminos por variable, corra el lazo a la velocidad que corra, y
# el promedio dentro del bucket ADEMAS mejora el rechazo de ruido.
#
# A 5 s de periodo con ventana_s = 50 el bucket mide 0,5 s, o sea
# que cada tick cae en su propio bucket y el resultado es
# numericamente el mismo que la version por muestras.
# ============================================================

from __future__ import annotations

import math
import time

# Cuantos "pasos de referencia" entran en una ventana. Fija la
# escala de `q` para que su sintonizacion historica siga valiendo.
PASOS_REFERENCIA = 10.0

# Techo de puntos por variable dentro de la ventana. Acota el costo
# por tick cuando el lazo corre libre.
MUESTRAS_OBJETIVO = 100

# Peso por debajo del cual una muestra ya no aporta nada al
# promedio: se usa para podar la cola de la ventana.
PESO_DESPRECIABLE = 1e-6


def _validar(q: float, ventana_s: float) -> tuple[float, float]:
    q = float(q)
    ventana_s = float(ventana_s)
    if q < 0.0:
        raise ValueError(f"q debe ser >= 0 (recibido: {q})")
    if ventana_s <= 0.0:
        raise ValueError(f"ventana_s debe ser > 0 segundos (recibido: {ventana_s})")
    return q, ventana_s


class ExpQFilter:
    """Filtro Exp-Q con (q, ventana_s) independientes por variable.

    API
    ----
    ExpQFilter(config_por_variable={"torque": {"q": 0.15, "ventana_s": 50.0}, ...})
    filtro.actualizar({"torque": 12.3}, t_s=123.4)

    Cada entrada de config_por_variable debe tener las claves `q` y
    `ventana_s`. Se acepta tambien la forma antigua `window_size`, que se
    convierte a `ventana_s` usando `periodo_legacy_s` (ver abajo); esto
    existe solo para no romper configuraciones viejas todavia en disco.

    `t_s` es el reloj en segundos. Si no se pasa, se usa time.monotonic().
    En la simulacion por DataFrame se pasa el t_s de la fila, de modo que
    el filtro se comporta igual sobre datos historicos que en vivo.

    Si en runtime `actualizar(inputs)` recibe una variable que NO esta en
    `config_por_variable`, se lanza KeyError. Esto evita que pases por
    accidente variables sin filtrar pensando que estan filtradas.
    """

    def __init__(self, config_por_variable: dict[str, dict],
                 periodo_legacy_s: float = 5.0):
        if not isinstance(config_por_variable, dict) or not config_por_variable:
            raise ValueError(
                "ExpQFilter requiere 'config_por_variable' no vacio. "
                "Ejemplo: {'torque': {'q': 0.15, 'ventana_s': 50.0}, ...}"
            )

        self._config: dict[str, dict] = {}
        # Por variable: lista de buckets [t_centro, suma, n] en orden temporal.
        self._buffers: dict[str, list[list[float]]] = {}

        for var, cfg in config_por_variable.items():
            if not isinstance(cfg, dict):
                raise ValueError(
                    f"config_por_variable[{var!r}] debe ser dict, no {type(cfg).__name__}"
                )
            if "q" not in cfg:
                raise ValueError(
                    f"config_por_variable[{var!r}] requiere la clave 'q'. "
                    f"Recibido: {sorted(cfg.keys())}"
                )
            if "ventana_s" in cfg:
                ventana_s = cfg["ventana_s"]
            elif "window_size" in cfg:
                # Config antigua por muestras: se traduce a segundos con el
                # periodo que tenia el lazo cuando se escribio esa config.
                ventana_s = float(cfg["window_size"]) * float(periodo_legacy_s)
            else:
                raise ValueError(
                    f"config_por_variable[{var!r}] requiere 'ventana_s' (segundos). "
                    f"Recibido: {sorted(cfg.keys())}"
                )
            q, ventana_s = _validar(cfg["q"], ventana_s)
            self._config[var] = {
                "q": q,
                "ventana_s": ventana_s,
                "tau_s": ventana_s / PASOS_REFERENCIA,
                "bucket_s": ventana_s / float(MUESTRAS_OBJETIVO),
            }
            self._buffers[var] = []

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(self._config.keys())

    def reset(self) -> None:
        """Limpia todos los buffers (no toca la config)."""
        for buf in self._buffers.values():
            buf.clear()

    def actualizar(self, inputs: dict, t_s: float | None = None) -> dict:
        """Filtra cada variable de `inputs` con el reloj `t_s` (segundos).

        Lanza KeyError si llega una variable sin config. Devuelve un dict
        NUEVO con las salidas filtradas; no muta `inputs`.
        """
        t = time.monotonic() if t_s is None else float(t_s)
        salida: dict = {}
        for k, v in inputs.items():
            if k not in self._config:
                raise KeyError(
                    f"Variable {k!r} no esta configurada en ExpQFilter. "
                    f"Variables configuradas: {sorted(self._config.keys())}. "
                    "Agregala en config_por_variable o sacala del input."
                )
            try:
                fv = float(v)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"Variable {k!r} debe ser numerica para el filtro (recibido: {v!r})"
                ) from exc
            salida[k] = self._filtrar_uno(k, fv, t)
        return salida

    def update(self, inputs: dict, t_s: float | None = None) -> dict:
        """Alias en ingles."""
        return self.actualizar(inputs, t_s)

    def config_de(self, var: str) -> dict:
        if var not in self._config:
            raise KeyError(f"Variable {var!r} no configurada.")
        return dict(self._config[var])

    def longitud_buffer(self, var: str) -> int:
        """Cuantos buckets vivos hay en la ventana (no cuantas muestras)."""
        if var not in self._buffers:
            raise KeyError(f"Variable {var!r} no configurada.")
        return len(self._buffers[var])

    def muestras_en_ventana(self, var: str) -> int:
        """Cuantas muestras crudas hay representadas en la ventana."""
        if var not in self._buffers:
            raise KeyError(f"Variable {var!r} no configurada.")
        return int(sum(b[2] for b in self._buffers[var]))

    def pesos_normalizados(self, var: str, t_s: float | None = None) -> list[float]:
        """Peso de cada bucket vivo, del mas reciente al mas viejo."""
        if var not in self._buffers:
            raise KeyError(f"Variable {var!r} no configurada.")
        buf = self._buffers[var]
        if not buf:
            return []
        t = float(buf[-1][0]) if t_s is None else float(t_s)
        pesos = [self._peso(var, t - b[0]) for b in reversed(buf)]
        total = sum(pesos) or 1.0
        return [w / total for w in pesos]

    # ------------------------------------------------------------

    def _peso(self, var: str, dt: float) -> float:
        cfg = self._config[var]
        u = max(0.0, float(dt)) / cfg["tau_s"]
        exponente = cfg["q"] * u * u
        if exponente > 700.0:            # evita OverflowError en math.exp
            return 0.0
        return math.exp(-exponente)

    def _filtrar_uno(self, var: str, x: float, t: float) -> float:
        cfg = self._config[var]
        buf = self._buffers[var]

        # El reloj puede retroceder (reinicio del motor, DataFrame
        # desordenado): se descarta lo anterior en vez de calcular
        # edades negativas.
        if buf and t < buf[-1][0]:
            buf.clear()

        # --- Acumular en el bucket vigente o abrir uno nuevo ---
        if buf and (t - buf[-1][0]) < cfg["bucket_s"]:
            b = buf[-1]
            b[1] += x
            b[2] += 1.0
        else:
            buf.append([t, x, 1.0])

        # --- Podar lo que ya no pesa ---
        limite = t - cfg["ventana_s"]
        i = 0
        while i < len(buf) - 1 and buf[i][0] < limite:
            i += 1
        if i:
            del buf[:i]

        # --- Promedio ponderado por edad ---
        acum = 0.0
        s_w = 0.0
        for t_b, suma, n in buf:
            w = self._peso(var, t - t_b)
            if w < PESO_DESPRECIABLE:
                continue
            acum += w * (suma / n)
            s_w += w

        if s_w <= 0.0:
            return float(x)
        return float(acum / s_w)

    def __repr__(self) -> str:
        partes = ", ".join(
            f"{var}(q={c['q']}, ventana_s={c['ventana_s']})"
            for var, c in self._config.items()
        )
        return f"ExpQFilter({partes})"


# ============================================================
# Configuracion por defecto para el Espesador
# ------------------------------------------------------------
# El runner usa este dict si no se le pasa filtro propio. Las ventanas
# estan en SEGUNDOS DE PROCESO: son las mismas que aplicaban antes con
# el lazo a 5 s (window_size * 5), asi que el comportamiento historico
# se conserva pero ahora es independiente de la velocidad del lazo.
# ============================================================
CONFIG_FILTRO_ESPESADOR_DEFAULT: dict[str, dict] = {
    "torque":              {"q": 0.15, "ventana_s": 50.0},
    "bed_mass":            {"q": 0.20, "ventana_s": 40.0},
    "bed_level":           {"q": 0.10, "ventana_s": 75.0},
    "densidad":            {"q": 0.15, "ventana_s": 50.0},
    "torque_bomba":        {"q": 0.15, "ventana_s": 50.0},
    "potencia_bomba":      {"q": 0.15, "ventana_s": 50.0},
    "presion_descarga":    {"q": 0.15, "ventana_s": 50.0},
    "presion_diferencial": {"q": 0.15, "ventana_s": 50.0},
    "nivel_rastra":        {"q": 0.10, "ventana_s": 75.0},
}

# Periodo que tenia el lazo cuando la config se escribia en `window_size`.
# Se usa para migrar filtros.json viejos sin cambiarles el comportamiento.
PERIODO_LEGACY_S = 5.0
