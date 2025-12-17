import time
from opc_functions import conectar_opc, desconectar_opc, leer_tag
from M1_1_dataframe import dataframe_M1
from M1_2_filtros import panel_control

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt
from ipywidgets import interact, IntSlider, FloatSlider, Dropdown


TAGS = {"Potencia_SAG":  "ns=2;s=RETO.IN.Potencia_SAG",
    "Potencia_Bolas": "ns=2;s=RETO.IN.Potencia_Bolas",
    "Nivel_Molino":  "ns=2;s=RETO.IN.Nivel_Molino"}

def ejecutar_ciclo(client):
    """Ejecuta una iteración del main usando la conexión OPC."""
    print("\n===== Lectura de Tags =====")
    for nombre, nodeid in TAGS.items():
        valor = leer_tag(client, nodeid)  # <-- si falla, esto lanza excepción
        print(f"{nombre}: {valor}")



columna = "humedad"
N = 2000
n_generaciones = 50
window = 5
center = False   # TIEMPO REAL: causal (no usar True)
alpha = 0.2
suffix = None    # o "v1", etc.
fc_min = 5.0                # minutos
order = 3
fs = 1.0
min_periods = 1

BOL_filtrar_promedio_movil = True
BOL_filtrar_mediana_movil = True
BOL_filtrar_ema = True
BOL_filtrar_butterworth = False


def main():
    while True:  
        client = conectar_opc()
        if not client:
            print("⏳ Reintentando conexión en 5 segundos...")
            time.sleep(5)
            continue

        try:
            df = dataframe_M1()
            while True:
                ejecutar_ciclo(client)
                
                
                for i in range(0, len(df), n_generaciones):
                    print (f'Ver iteracion: {i}')
                    df_gen = df.iloc[i:i+n_generaciones].copy()   # último bloque puede tener < 20
                
                    df_ma, df_med, df_ema, df_but = panel_control(
                        df_input=df_gen,
                        columna=columna,
                        window=window,
                        center=center,
                        alpha=alpha,
                        min_periods=min_periods,
                        fc_min=fc_min,
                        order=order,
                        fs=fs,
                        N=N,
                        suffix=suffix,
                        verbose=True,
                        BOL_filtrar_promedio_movil = BOL_filtrar_promedio_movil,
                        BOL_filtrar_mediana_movil = BOL_filtrar_mediana_movil,
                        BOL_filtrar_ema = BOL_filtrar_ema,
                        BOL_filtrar_butterworth = BOL_filtrar_butterworth
                    )


                
                
                time.sleep(10)

        except Exception as e:
            print(f"⚠️ Error en ciclo principal: {e}")
            print("🔄 Reiniciando conexión OPC...")

        finally:
            desconectar_opc(client)
            print("🕗 Reintentando en 3s...")
            time.sleep(3)

if __name__ == "__main__":
    main()
