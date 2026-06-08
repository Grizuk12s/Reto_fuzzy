# ============================================================
# exp_q_filter.py
# ------------------------------------------------------------
# FILTRO EXPONENCIAL-CUADRATICO (Exp-Q) POR VARIABLE
#
# Cada variable tiene su propio (q, window_size). NO hay defaults
# globales ni fallback: si una variable que llega en `actualizar`
# no esta en `config_por_variable`, el filtro LANZA un error.
# Esto es intencional (decision 6): si algo no esta configurado,
# queremos enterarnos en seguida, no que pase silenciosamente.
#
# Decaimiento gaussiano de pesos:
#     w[i] = exp(-q * i^2)   (i=0 muestra actual)
#     y[k] = ( Sum w[i] * x[k-i] ) / ( Sum w[i] )   para i = 0..N-1
#
# Guia de sintonizacion de q:
#     q ~ 0.05  -> suavizado fuerte, lag alto
#     q ~ 0.15  -> balance por defecto
#     q ~ 0.40  -> casi sin filtrado, respuesta rapida
# ============================================================

from __future__ import annotations

from collections import deque
import math


def _compute_weights(q: float, window_size: int) -> list[float]:
    """Precomputa w[i] = exp(-q * i^2) para i = 0..window_size-1."""
    if float(q) < 0.0:
        raise ValueError(f"q debe ser >= 0 (recibido: {q})")
    if int(window_size) <= 0:
        raise ValueError(f"window_size debe ser >= 1 (recibido: {window_size})")
    return [math.exp(-float(q) * (i ** 2)) for i in range(int(window_size))]


class ExpQFilter:
    """Filtro Exp-Q con (q, window_size) independientes por variable.

    API
    ----
    ExpQFilter(config_por_variable={"torque": {"q": 0.15, "window_size": 10}, ...})

    Cada entrada de config_por_variable debe tener al menos las claves
    `q` y `window_size`. Si falta alguna -> error en el constructor.

    Si en runtime `actualizar(inputs)` recibe una variable que NO esta en
    `config_por_variable`, se lanza KeyError. Esto evita que pases por
    accidente variables sin filtrar pensando que estan filtradas.
    """

    def __init__(self, config_por_variable: dict[str, dict]):
        if not isinstance(config_por_variable, dict) or not config_por_variable:
            raise ValueError(
                "ExpQFilter requiere 'config_por_variable' no vacio. "
                "Ejemplo: {'torque': {'q': 0.15, 'window_size': 10}, ...}"
            )

        self._config: dict[str, dict] = {}
        self._weights: dict[str, list[float]] = {}
        self._buffers: dict[str, deque] = {}

        for var, cfg in config_por_variable.items():
            if not isinstance(cfg, dict):
                raise ValueError(
                    f"config_por_variable[{var!r}] debe ser dict, no {type(cfg).__name__}"
                )
            if "q" not in cfg or "window_size" not in cfg:
                raise ValueError(
                    f"config_por_variable[{var!r}] requiere claves 'q' y 'window_size'. "
                    f"Recibido: {sorted(cfg.keys())}"
                )
            q = float(cfg["q"])
            ws = int(cfg["window_size"])
            self._config[var] = {"q": q, "window_size": ws}
            self._weights[var] = _compute_weights(q, ws)
            self._buffers[var] = deque(maxlen=ws)

    @property
    def variables(self) -> tuple[str, ...]:
        return tuple(self._config.keys())

    def reset(self) -> None:
        """Limpia todos los buffers (no toca la config)."""
        for buf in self._buffers.values():
            buf.clear()

    def actualizar(self, inputs: dict) -> dict:
        """Filtra cada variable de `inputs`.

        Lanza KeyError si llega una variable sin config. Devuelve un dict
        NUEVO con las salidas filtradas; no muta `inputs`.
        """
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
            salida[k] = self._filtrar_uno(k, fv)
        return salida

    def update(self, inputs: dict) -> dict:
        """Alias en ingles."""
        return self.actualizar(inputs)

    def pesos_normalizados(self, var: str) -> list[float]:
        if var not in self._weights:
            raise KeyError(f"Variable {var!r} no configurada.")
        total = sum(self._weights[var]) or 1.0
        return [w / total for w in self._weights[var]]

    def pesos_crudos(self, var: str) -> list[float]:
        if var not in self._weights:
            raise KeyError(f"Variable {var!r} no configurada.")
        return list(self._weights[var])

    def longitud_buffer(self, var: str) -> int:
        if var not in self._buffers:
            raise KeyError(f"Variable {var!r} no configurada.")
        return len(self._buffers[var])

    def config_de(self, var: str) -> dict:
        if var not in self._config:
            raise KeyError(f"Variable {var!r} no configurada.")
        return dict(self._config[var])

    def _filtrar_uno(self, var: str, x: float) -> float:
        buf = self._buffers[var]
        buf.append(float(x))

        # Muestras en orden temporal decreciente:
        # muestras[0] = la mas reciente -> w[0]
        muestras = list(buf)[::-1]
        n = len(muestras)
        w = self._weights[var][:n]

        s_w = sum(w)
        if s_w <= 0.0:
            return float(x)

        acum = sum(wi * mi for wi, mi in zip(w, muestras))
        return float(acum / s_w)

    def __repr__(self) -> str:
        partes = ", ".join(
            f"{var}(q={c['q']}, ws={c['window_size']})"
            for var, c in self._config.items()
        )
        return f"ExpQFilter({partes})"


# ============================================================
# Configuracion por defecto para el Espesador
# ------------------------------------------------------------
# El runner usa este dict si no se le pasa filtro propio. Ajustalo a
# gusto en la prueba; la idea es justamente que cada variable tenga
# su propio par (q, window_size).
# ============================================================
CONFIG_FILTRO_ESPESADOR_DEFAULT: dict[str, dict] = {
    "torque":              {"q": 0.15, "window_size": 10},
    "bed_mass":            {"q": 0.20, "window_size": 8},
    "bed_level":           {"q": 0.10, "window_size": 15},
    "densidad":            {"q": 0.15, "window_size": 10},
    "torque_bomba":        {"q": 0.15, "window_size": 10},
    "potencia_bomba":      {"q": 0.15, "window_size": 10},
    "presion_descarga":    {"q": 0.15, "window_size": 10},
    "presion_diferencial": {"q": 0.15, "window_size": 10},
    "nivel_rastra":        {"q": 0.10, "window_size": 15},
}
