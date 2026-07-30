# -*- coding: utf-8 -*-
"""Calculo de variables derivadas a partir de variables crudas (sensores).

Separa la logica de transformacion de datos de la logica del experto fuzzy.
Las variables "externas" que antes se asumian pre-calculadas aguas arriba
ahora se obtienen aqui, a partir de los sensores reales del proceso.

Estructura de modulos
---------------------
  calculos_variables.py    : definiciones declarativas de sensores y variables
                             calculadas (VARIABLES_CRUDAS,
                             DEFINICIONES_CALCULADAS).
  variables_calculadas.py  : funciones que ejecutan los calculos
                             (calcular_variable, calcular_variables_df,
                             detectar_dt_s).

Tipos de calculo soportados en DEFINICIONES_CALCULADAS
------------------------------------------------------
  "aritmetica"    : operacion elemental entre 2 columnas existentes.
                    operaciones: 'suma', 'resta', 'multiplicacion', 'division'.
  "rolling_delta" : cambio de una columna en una ventana de tiempo (min).
  "rolling_std"   : desviacion estandar movil de una columna en ventana (min).

Chaining (dependencias entre calculadas)
-----------------------------------------
Las definiciones se procesan en orden de aparicion en DEFINICIONES_CALCULADAS.
Una variable calculada puede usarse como argumento de la siguiente
(ej. "tonelaje_sag_total" se calcula primero y luego se usa en "rolling_delta").
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .calculos_variables import DEFINICIONES_CALCULADAS, VARIABLES_CRUDAS


# ============================================================
# FUNCION ARITMETICA GENERICA
# ============================================================
_OPERACIONES_ARITMETICAS: dict[str, callable] = {
    "suma":           lambda a, b: float(a) + float(b),
    "resta":          lambda a, b: float(a) - float(b),
    "multiplicacion": lambda a, b: float(a) * float(b),
    "division":       lambda a, b: float(a) / float(b) if abs(float(b)) > 1e-12 else float("nan"),
}


def calcular_variable(operacion: str, valor_a: float, valor_b: float) -> float:
    """Aplica una operacion aritmetica basica sobre dos valores escalares.

    Parametros
    ----------
    operacion : str
        Una de: 'suma', 'resta', 'multiplicacion', 'division'.
    valor_a : float
        Primer operando.
    valor_b : float
        Segundo operando.

    Retorna
    -------
    float : resultado de la operacion.

    Ejemplo
    -------
    >>> calcular_variable('suma', 1200.0, 1300.0)
    2500.0
    >>> calcular_variable('resta', 2500.0, 2100.0)
    400.0
    >>> calcular_variable('division', 100.0, 4.0)
    25.0
    """
    if operacion not in _OPERACIONES_ARITMETICAS:
        raise ValueError(
            f"Operacion '{operacion}' no soportada. "
            f"Validas: {sorted(_OPERACIONES_ARITMETICAS)}"
        )
    return _OPERACIONES_ARITMETICAS[operacion](valor_a, valor_b)


# ============================================================
# HELPERS ROLLING (para series pandas)
# ============================================================
def _muestras_en_ventana(dt_s: float, ventana_min: float) -> int:
    """Convierte una ventana en minutos a numero de muestras segun dt_s."""
    return max(2, int(round(ventana_min * 60.0 / max(dt_s, 1e-6))))


def _rolling_delta(serie: pd.Series, ventana: int) -> pd.Series:
    """Cambio de la serie en `ventana` muestras atras (delta temporal).

    delta[k] = serie[k] - serie[k - (ventana - 1)]
    Los primeros puntos sin historia suficiente se rellenan con 0.
    """
    return serie.diff(periods=ventana - 1).fillna(0.0)


def _rolling_std(serie: pd.Series, ventana: int) -> pd.Series:
    """Desviacion estandar movil en `ventana` muestras.

    Usa min_periods=2 para que los primeros puntos no queden como NaN.
    """
    return serie.rolling(window=ventana, min_periods=2).std().fillna(0.0)


# ============================================================
# FUNCION PRINCIPAL: aplica definiciones sobre un DataFrame
# ============================================================
def calcular_variables_df(
    df: pd.DataFrame,
    dt_s: float,
    definiciones: dict | None = None,
    inplace: bool = False,
) -> pd.DataFrame:
    """Calcula todas las variables derivadas y las agrega al DataFrame.

    Las columnas fuente de cada definicion deben existir en `df`
    (ya sea como variables crudas o como resultado de una definicion previa).
    Si falta una columna, se lanza KeyError con un mensaje descriptivo.

    Parametros
    ----------
    df : pd.DataFrame
        DataFrame con al menos las columnas en VARIABLES_CRUDAS.
    dt_s : float
        Intervalo de muestreo en segundos. Necesario para calculos de ventana
        (rolling_delta, rolling_std).
    definiciones : dict | None
        Definiciones a aplicar. Por defecto: DEFINICIONES_CALCULADAS.
        El usuario puede pasar un subset o un dict propio.
    inplace : bool
        Si True, modifica df directamente. Si False (default), crea una copia.

    Retorna
    -------
    pd.DataFrame
        El mismo DataFrame con las columnas calculadas agregadas.

    Ejemplo de uso en el runner
    ---------------------------
    df_enriquecido = calcular_variables_df(df_raw, dt_s=60.0)
    """
    if definiciones is None:
        definiciones = DEFINICIONES_CALCULADAS

    result = df if inplace else df.copy()

    for nombre, defn in definiciones.items():
        tipo = str(defn.get("tipo", "")).lower().strip()

        # ------ tipo: aritmetica --------------------------------
        if tipo == "aritmetica":
            operacion = str(defn["operacion"]).lower().strip()
            args = list(defn["args"])
            if len(args) != 2:
                raise ValueError(
                    f"Variable calculada '{nombre}': tipo 'aritmetica' requiere "
                    f"exactamente 2 columnas en 'args', se recibieron {len(args)}: {args}"
                )
            col_a, col_b = args[0], args[1]

            if col_a not in result.columns:
                raise KeyError(
                    f"Variable calculada '{nombre}': columna fuente '{col_a}' "
                    "no encontrada en el DataFrame. "
                    "Verifica VARIABLES_CRUDAS o el orden de DEFINICIONES_CALCULADAS."
                )
            if col_b not in result.columns:
                raise KeyError(
                    f"Variable calculada '{nombre}': columna fuente '{col_b}' "
                    "no encontrada en el DataFrame. "
                    "Verifica VARIABLES_CRUDAS o el orden de DEFINICIONES_CALCULADAS."
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
                # Proteccion contra division por cero: produce NaN donde b ~ 0
                result[nombre] = np.where(b.abs() > 1e-12, a / b.replace(0, np.nan), np.nan)
            else:
                raise ValueError(
                    f"Variable calculada '{nombre}': operacion '{operacion}' no soportada. "
                    f"Validas: {sorted(_OPERACIONES_ARITMETICAS)}"
                )

        # ------ tipo: rolling_delta -----------------------------
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

        # ------ tipo: rolling_std -------------------------------
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

        # ------ tipo desconocido --------------------------------
        else:
            raise ValueError(
                f"Variable calculada '{nombre}': tipo '{tipo}' no soportado. "
                "Tipos validos: 'aritmetica', 'rolling_delta', 'rolling_std'."
            )

    return result


# ============================================================
# HELPER: deteccion automatica de dt_s desde un DataFrame
# ============================================================
def detectar_dt_s(df: pd.DataFrame, col_t: str = "t_s") -> float:
    """Estima el intervalo de muestreo en segundos a partir de la columna de tiempo.

    Usa la mediana de las diferencias para ser robusto ante gaps puntuales.

    Parametros
    ----------
    df : pd.DataFrame
    col_t : str
        Nombre de la columna de tiempo en segundos.

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
            "Verifica que la columna de tiempo este en segundos."
        )
    return dt
