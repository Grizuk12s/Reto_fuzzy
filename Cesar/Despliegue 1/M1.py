# ============================================================
# 1) IMPORTAR LIBRERÍAS
# ============================================================
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt

from ipywidgets import interact, IntSlider, FloatSlider, Dropdown

plt.rcParams["figure.figsize"] = (12, 4)

# ============================================================
# 2) GENERAR UNA SEÑAL DE PROCESO (HUMEDAD) CON RUIDO
# ============================================================
duracion_horas = 3
fs = 1  # 1 dato por segundo
n_muestras = duracion_horas * 60 * 60  # 10800

inicio = "2025-01-01 00:00:00"
tiempo = pd.date_range(start=inicio, periods=n_muestras, freq="S")
t = np.arange(n_muestras)

# Señal base (tendencia lenta + oscilación suave)
humedad_base = 20 + 3 * np.sin(2 * np.pi * t / (60 * 15)) - 0.002 * (t / 60)
humedad_base = np.clip(humedad_base, 8, 30)

# Agregar ruido blanco + picos -> lo que entrega el sensor
np.random.seed(42)
ruido = np.random.normal(loc=0.0, scale=0.8, size=n_muestras)
humedad = humedad_base + ruido

idx_picos = np.random.choice(n_muestras, size=25, replace=False)
humedad[idx_picos] += np.random.choice([5, -5, 8, -8], size=25)

# DataFrame con SOLO la señal medida (puedes agregar más columnas si quieres)
df = pd.DataFrame({"humedad": humedad}, index=tiempo)
df.index.name = "timestamp"

print("Columnas del df:", df.columns.tolist())
print(df.head())

# ============================================================
# 3) DEFINIR FUNCIONES DE FILTRADO
# ============================================================

def filtrar_promedio_movil(df, columna, window=5, center=True, nueva_col=None):
    """
    Filtro 1: Promedio móvil.
    - window: tamaño de ventana (nº de muestras)
    - center: True = ventana centrada
    """
    if nueva_col is None:
        nueva_col = f"{columna}_ma{window}"
    df[nueva_col] = df[columna].rolling(window=window, center=center).mean()
    return df

def filtrar_mediana_movil(df, columna, window=5, center=True, nueva_col=None):
    """
    Filtro 2: Mediana móvil.
    Más robusto a picos/outliers.
    """
    if nueva_col is None:
        nueva_col = f"{columna}_mediana{window}"
    df[nueva_col] = df[columna].rolling(window=window, center=center).median()
    return df

def filtrar_ema(df, columna, alpha=0.2, nueva_col=None):
    """
    Filtro 3: Promedio móvil exponencial (EMA).
    - alpha: 0–1, más chico = más suave.
    """
    if nueva_col is None:
        nueva_col = f"{columna}_ema{alpha}".replace(".", "_")
    df[nueva_col] = df[columna].ewm(alpha=alpha, adjust=False).mean()
    return df

def filtrar_butterworth(df, columna, fs=1.0, fc=1/300, order=3, nueva_col=None):
    """
    Filtro 4: Butterworth pasa-bajos.
    - fs : frecuencia de muestreo [Hz]
    - fc : frecuencia de corte [Hz]
    - order: orden del filtro
    """
    if nueva_col is None:
        nueva_col = f"{columna}_butter"

    w_norm = fc / (fs / 2)  # frecuencia normalizada
    b, a = butter(order, w_norm, btype="low", analog=False)

    x = df[columna].values
    x_filtrado = filtfilt(b, a, x)

    df[nueva_col] = x_filtrado
    return df

# ============================================================
# 4) PANEL DE CONTROL INTERACTIVO (4 GRÁFICOS SEPARADOS)
# ============================================================

columnas_numericas = df.select_dtypes(include=["float64", "int64"]).columns.tolist()

def panel_control(columna,
                  window,
                  alpha,
                  fc_min,
                  order,
                  N):
    """
    Actualiza filtros y muestra 4 gráficos separados:
    - Original vs Promedio móvil
    - Original vs Mediana móvil
    - Original vs EMA
    - Original vs Butterworth
    """
    df_local = df.copy()

    # Convertir fc de minutos a Hz
    fc = 1.0 / (fc_min * 60.0)

    # Aplicar filtros
    df_local = filtrar_promedio_movil(df_local, columna,
                                      window=window, center=True,
                                      nueva_col=f"{columna}_ma")
    df_local = filtrar_mediana_movil(df_local, columna,
                                     window=window, center=True,
                                     nueva_col=f"{columna}_mediana")
    df_local = filtrar_ema(df_local, columna,
                           alpha=alpha,
                           nueva_col=f"{columna}_ema")
    df_local = filtrar_butterworth(df_local, columna,
                                   fs=fs, fc=fc, order=order,
                                   nueva_col=f"{columna}_butter")

    # Recortar para graficar
    N = min(N, len(df_local))
    df_plot = df_local.iloc[:N]

    # --- Gráfico 1: Promedio móvil ---
    plt.figure()
    plt.plot(df_plot.index, df_plot[columna],
             label=f"{columna} (original)", linewidth=0.8, alpha=0.8)
    plt.plot(df_plot.index, df_plot[f"{columna}_ma"],
             label=f"Promedio móvil (window={window})", linewidth=1.5)
    plt.ylabel("Valor")
    plt.xlabel("Tiempo")
    plt.title(f"Filtro 1: Promedio móvil - {columna}")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()

    # --- Gráfico 2: Mediana móvil ---
    plt.figure()
    plt.plot(df_plot.index, df_plot[columna],
             label=f"{columna} (original)", linewidth=0.8, alpha=0.8)
    plt.plot(df_plot.index, df_plot[f"{columna}_mediana"],
             label=f"Mediana móvil (window={window})", linewidth=1.5)
    plt.ylabel("Valor")
    plt.xlabel("Tiempo")
    plt.title(f"Filtro 2: Mediana móvil - {columna}")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()

    # --- Gráfico 3: EMA ---
    plt.figure()
    plt.plot(df_plot.index, df_plot[columna],
             label=f"{columna} (original)", linewidth=0.8, alpha=0.8)
    plt.plot(df_plot.index, df_plot[f"{columna}_ema"],
             label=f"EMA (alpha={alpha:.2f})", linewidth=1.5)
    plt.ylabel("Valor")
    plt.xlabel("Tiempo")
    plt.title(f"Filtro 3: EMA - {columna}")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()

    # --- Gráfico 4: Butterworth ---
    plt.figure()
    plt.plot(df_plot.index, df_plot[columna],
             label=f"{columna} (original)", linewidth=0.8, alpha=0.8)
    plt.plot(df_plot.index, df_plot[f"{columna}_butter"],
             label=f"Butterworth (fc={fc_min} min, orden={order})", linewidth=1.5)
    plt.ylabel("Valor")
    plt.xlabel("Tiempo")
    plt.title(f"Filtro 4: Butterworth - {columna}")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()

# Lanzar el panel
interact(
    panel_control,
    columna=Dropdown(options=columnas_numericas, value="humedad", description="Columna"),
    window=IntSlider(min=3, max=61, step=2, value=5, description="Window"),
    alpha=FloatSlider(min=0.05, max=0.8, step=0.05, value=0.2, description="Alpha EMA"),
    fc_min=IntSlider(min=1, max=60, step=1, value=5, description="Fc [min]"),
    order=IntSlider(min=1, max=6, step=1, value=3, description="Orden"),
    N=IntSlider(min=500, max=len(df), step=500, value=2000, description="Muestras")
)
