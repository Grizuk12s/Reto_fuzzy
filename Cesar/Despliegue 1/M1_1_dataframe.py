import numpy as np
import pandas as pd

def dataframe_M1 ():
    duracion_horas = 3
    n_muestras = duracion_horas * 60 * 60  # 10800
    inicio = "2025-01-01 00:00:00"
    tiempo = pd.date_range(start=inicio, periods=n_muestras, freq="S")
    t = np.arange(n_muestras)
    humedad_base = 20 + 3 * np.sin(2 * np.pi * t / (60 * 15)) - 0.002 * (t / 60)
    humedad_base = np.clip(humedad_base, 8, 30)
    np.random.seed(42)
    ruido = np.random.normal(loc=0.0, scale=0.8, size=n_muestras)
    humedad = humedad_base + ruido
    idx_picos = np.random.choice(n_muestras, size=25, replace=False)
    humedad[idx_picos] += np.random.choice([5, -5, 8, -8], size=25)
    df = pd.DataFrame({"humedad": humedad}, index=tiempo)
    df.index.name = "timestamp"
    return df