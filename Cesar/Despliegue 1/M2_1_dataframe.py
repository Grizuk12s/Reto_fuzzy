import pandas as pd
import numpy as np

def M2_dataframe():
    np.random.seed(42)
    
    N = 500  # número de minutos simulados
    
    df = pd.DataFrame()
    
    # Tonelaje base (no lo usamos en fuzzy, pero sirve para coherencia)
    df["tonelaje"] = 1400 + np.random.normal(0, 30, N)
    
    # Potencia SAG correlacionada al tonelaje
    df["pot_sag"] = 22 + (df["tonelaje"] - 1400) / 80 + np.random.normal(0, 0.5, N)
    df["lmax_sag"] = 24  # límite fijo
    
    # Potencia Bolas (ruido moderado)
    df["pot_bolas"] = 740 + np.random.normal(0, 15, N)
    df["lmin_bolas"] = 700
    
    # Nivel del cajón (ruido + correlación con agua ciclones)
    df["nivel"] = 62 + np.random.normal(0, 4, N)
    df["lmin_nivel"] = 50
    df["lmax_nivel"] = 70
    
    # Presión de ciclones correlacionada a tonelaje, nivel y densidad
    df["presion"] = 14 + (df["tonelaje"] - 1400) / 200 + (df["nivel"] - 60) / 10 + np.random.normal(0, 1.2, N)
    df["lmax_pres"] = 25
    
    # Densidad correlacionada al P80 y presión
    df["densidad"] = 1.35 + np.random.normal(0, 0.03, N)
    df["lmax_dens"] = 1.60
    
    # P80 correlacionado a densidad y presión
    df["p80"] = 180 + (df["densidad"] - 1.35) * 200 - (df["presion"] - 15) * 4 + np.random.normal(0, 5, N)
    df["lmax_p80"] = 300
    
    # Ajustar valores fuera de rango
    df["pot_sag"] = df["pot_sag"].clip(18, 26)
    df["nivel"] = df["nivel"].clip(45, 80)
    df["presion"] = df["presion"].clip(5, 25)
    df["densidad"] = df["densidad"].clip(1.20, 1.50)
    df["p80"] = df["p80"].clip(100, 280)
    # ================================
    # FORZAR EVENTOS PARA ACTIVAR REGLAS
    # ================================
    
    # Evento 1 – P80 muy alto (molienda gruesa)
    df.loc[50:80, "p80"] += 60
    
    # Evento 2 – Potencia SAG baja (carga baja o tapón)
    df.loc[120:150, "pot_sag"] -= 3
    
    # Evento 3 – Presión muy alta (sobrecarga ciclones)
    df.loc[200:230, "presion"] += 5
    
    # Evento 4 – Densidad muy alta (agua insuficiente)
    df.loc[300:330, "densidad"] += 0.10
    
    # Clip para evitar valores irreales
    df["pot_sag"] = df["pot_sag"].clip(16, 26)
    df["presion"] = df["presion"].clip(5, 25)
    df["densidad"] = df["densidad"].clip(1.20, 1.60)
    df["p80"] = df["p80"].clip(100, 350)
    
    return df