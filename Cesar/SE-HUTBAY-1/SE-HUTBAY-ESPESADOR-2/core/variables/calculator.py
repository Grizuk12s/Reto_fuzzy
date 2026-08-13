# -*- coding: utf-8 -*-
"""Lógica genérica de cálculo de variables derivadas — sin datos de proceso.

Separa la mecánica de transformación de datos de las definiciones
declarativas de cada proceso.

Tipos de cálculo soportados en `definiciones`:
  "aritmetica"    : operación elemental entre 2 columnas.
                    operaciones: 'suma', 'resta', 'multiplicacion', 'division'.
  "rolling_delta" : cambio de una columna en ventana temporal (min).
  "rolling_std"   : desviación estándar móvil en ventana (min).

Chaining — las definiciones se procesan en orden; una variable calculada
puede usarse como argumento de la siguiente.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# OPERACIONES ARITMÉTICAS
# ============================================================
_OPERACIONES_ARITMETICAS: dict[str, callable] = {
    "suma":           lambda a, b: float(a) + float(b),
    "resta":          lambda a, b: float(a) - float(b),
    "multiplicacion": lambda a, b: float(a) * float(b),
    "division":       lambda a, b: float(a) / float(b) if abs(float(b)) > 1e-12 else float("nan"),
}


def calcular_variable(operacion: str, valor_a: float, valor_b: float) -> float:
    """Aplica una operación aritmética básica sobre dos valores escalares.

    Parámetros
    ----------
    operacion : str
        Una de: 'suma', 'resta', 'multiplicacion', 'division'.
    valor_a, valor_b : float
        Operandos.

    Ejemplo
    -------
    >>> calcular_variable('suma', 1200.0, 1300.0)
    2500.0
    """
    if operacion not in _OPERACIONES_ARITMETICAS:
        raise ValueError(
            f"Operacion '{operacion}' no soportada. "
            f"Validas: {sorted(_OPERACIONES_ARITMETICAS)}"
        )
    return _OPERACIONES_ARITMETICAS[operacion](valor_a, valor_b)


# ============================================================
# HELPERS ROLLING
# ============================================================
def _muestras_en_ventana(dt_s: float, ventana_min: float) -> int:
    return max(2, int(round(ventana_min * 60.0 / max(dt_s, 1e-6))))


def _rolling_delta(serie: pd.Series, ventana: int) -> pd.Series:
    """delta[k] = serie[k] - serie[k - (ventana - 1)]. Rellena con 0."""
    return serie.diff(periods=ventana - 1).fillna(0.0)


def _rolling_std(serie: pd.Series, ventana: int) -> pd.Series:
    """Desviación estándar móvil. min_periods=2 para evitar NaN iniciales."""
    return serie.rolling(window=ventana, min_periods=2).std().fillna(0.0)


# ============================================================
# FUNCIÓN PRINCIPAL
# ============================================================
def calcular_variables_df(
    df: pd.DataFrame,
    dt_s: float,
    definiciones: dict,
    inplace: bool = False,
) -> pd.DataFrame:
    """Calcula todas las variables derivadas y las agrega al DataFrame.

    Parámetros
    ----------
    df : pd.DataFrame
        DataFrame con al menos las columnas fuente requeridas.
    dt_s : float
        Intervalo de muestreo en segundos.
    definiciones : dict
        Definiciones declarativas a aplicar (DEFINICIONES_CALCULADAS del proceso).
    inplace : bool
        Si True, modifica df directamente. Si False (default), trabaja en copia.

    Retorna
    -------
    pd.DataFrame con las columnas calculadas agregadas.
    """
    result = df if inplace else df.copy()

    for nombre, defn in definiciones.items():
        tipo = str(defn.get("tipo", "")).lower().strip()

        # ------ aritmetica ----------------------------------
        if tipo == "aritmetica":
            operacion = str(defn["operacion"]).lower().strip()
            args = list(defn["args"])
            if len(args) != 2:
                raise ValueError(
                    f"Variable calculada '{nombre}': 'aritmetica' requiere "
                    f"exactamente 2 columnas en 'args', se recibieron {len(args)}: {args}"
                )
            col_a, col_b = args[0], args[1]
            for col in (col_a, col_b):
                if col not in result.columns:
                    raise KeyError(
                        f"Variable calculada '{nombre}': columna fuente '{col}' "
                        "no encontrada en el DataFrame."
                    )
            a = result[col_a].astype(float)
            b = result[col_b].astype(float)
            if operacion == "suma":
                result[nombre] = a + b
            elif operacion == "resta":
                result[nombre] = a - b
            elif operacion == "multiplicacion":
                result[nombre] = a * b
            elif operacion == "division":
                result[nombre] = np.where(b.abs() > 1e-12, a / b.replace(0, np.nan), np.nan)
            else:
                raise ValueError(
                    f"Variable calculada '{nombre}': operacion '{operacion}' no soportada. "
                    f"Validas: {sorted(_OPERACIONES_ARITMETICAS)}"
                )

        # ------ rolling_delta --------------------------------
        elif tipo == "rolling_delta":
            arg = str(defn["arg"])
            ventana_min = float(defn.get("ventana_min", 30.0))
            ventana = _muestras_en_ventana(dt_s, ventana_min)
            if arg not in result.columns:
                raise KeyError(
                    f"Variable calculada '{nombre}': columna fuente '{arg}' "
                    "no encontrada en el DataFrame."
                )
            result[nombre] = _rolling_delta(result[arg].astype(float), ventana)

        # ------ rolling_std ----------------------------------
        elif tipo == "rolling_std":
            arg = str(defn["arg"])
            ventana_min = float(defn.get("ventana_min", 30.0))
            ventana = _muestras_en_ventana(dt_s, ventana_min)
            if arg not in result.columns:
                raise KeyError(
                    f"Variable calculada '{nombre}': columna fuente '{arg}' "
                    "no encontrada en el DataFrame."
                )
            result[nombre] = _rolling_std(result[arg].astype(float), ventana)

        # ------ tipo desconocido ----------------------------
        else:
            raise ValueError(
                f"Variable calculada '{nombre}': tipo '{tipo}' no soportado. "
                "Tipos validos: 'aritmetica', 'rolling_delta', 'rolling_std'."
            )

    return result


# ============================================================
# HELPER: detección automática de dt_s
# ============================================================
def detectar_dt_s(df: pd.DataFrame, col_t: str = "t_s") -> float:
    """Estima dt_s en segundos usando la mediana de diferencias (robusto ante gaps).

    Parámetros
    ----------
    df : pd.DataFrame
    col_t : str — columna de tiempo en segundos.

    Retorna
    -------
    float : dt_s estimado (>= 1 segundo).
    """
    if col_t not in df.columns or len(df) < 2:
        raise ValueError(
            f"No se puede detectar dt_s: columna '{col_t}' no encontrada "
            "o DataFrame con menos de 2 filas."
        )
    diffs = df[col_t].sort_values().diff().dropna()
    dt = float(diffs.median())
    if dt < 1.0:
        raise ValueError(
            f"dt_s detectado ({dt:.3f} s) es menor a 1 segundo. "
            "Verifica que la columna de tiempo esté en segundos."
        )
    return dt
