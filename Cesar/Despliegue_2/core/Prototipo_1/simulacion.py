# -*- coding: utf-8 -*-
"""Simulación del sistema experto con datos aleatorios (standalone — Prototipo_1).

Genera un DataFrame con datos de proceso sintéticos que varían
de forma realista, inyecta los parámetros operacionales requeridos,
ejecuta el sistema experto y muestra los resultados.
"""

import sys
import os

# Asegurar que este directorio esté en el path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from runner import correr_prueba_general

# ============================================================
# 1. PARAMETROS OPERACIONALES (inyectados desde fuera del core)
# ============================================================

# Setpoints iniciales
SETPOINTS_BASE = {
    "sp_ton": 1500.0,   # ton/h
    "sp_am": 250.0,     # m3/h agua molino
    "sp_ac": 120.0,     # m3/h agua cajón
    "sp_rpm": 900.0,    # RPM bomba
}

# Límites de setpoints para clip y cálculo de magnitudes
LIMITES_SP = {
    "ton": (1000.0, 2200.0),
    "am":  (100.0, 450.0),
    "ac":  (50.0, 250.0),
    "rpm": (400.0, 1400.0),
}

# Límites fuzzy fijos para la simulación
LIMITES_FUZZY = {
    "potencia_lmin": 2800.0,
    "potencia_lmax": 5200.0,
    "nivel_lmin": 40.0,
    "nivel_lmax": 90.0,
    "presion_lmin": 8.0,
    "presion_lmax": 22.0,
    "p80_lmin": 140.0,
    "p80_lmax": 260.0,
    "densidad_lmin": 1.35,
    "densidad_lmax": 1.85,
}

# Meta flags
META_FLAGS = {"__R15": "OFF"}


# ============================================================
# 2. GENERACION DE DATOS ALEATORIOS REALISTAS
# ============================================================

def generar_datos_proceso(n_muestras: int = 200, dt_s: float = 5.0, seed: int = 42) -> pd.DataFrame:
    """
    Genera un DataFrame con datos de proceso sintéticos.

    Los valores simulan un proceso de molienda con:
    - Tendencias suaves (random walk con drift)
    - Ruido de medición
    - Periodos de anomalía (potencia baja, nivel alto, etc.)
    """
    rng = np.random.default_rng(seed)

    t_s = np.arange(0, n_muestras * dt_s, dt_s, dtype=float)
    n = len(t_s)

    # --- Potencia: oscila entre 2900 y 5100 con tendencias ---
    # Creamos 3 fases: normal, caída, recuperación
    fase1 = int(n * 0.4)
    fase2 = int(n * 0.3)
    fase3 = n - fase1 - fase2

    pot_base = np.concatenate([
        np.linspace(4000, 3800, fase1),       # operación normal, leve caída
        np.linspace(3800, 3050, fase2),        # caída de potencia (problema)
        np.linspace(3050, 4200, fase3),        # recuperación
    ])
    potencia = pot_base + rng.normal(0, 40, n)

    # --- Nivel: sube cuando la potencia cae ---
    nivel_base = np.concatenate([
        np.linspace(65, 70, fase1),
        np.linspace(70, 85, fase2),           # nivel sube con el problema
        np.linspace(85, 60, fase3),
    ])
    nivel = nivel_base + rng.normal(0, 1.5, n)

    # --- Presión: correlaciona parcialmente con nivel ---
    presion_base = np.concatenate([
        np.linspace(14, 15, fase1),
        np.linspace(15, 20, fase2),           # presión sube
        np.linspace(20, 13, fase3),
    ])
    presion = presion_base + rng.normal(0, 0.5, n)

    # --- P80: sube cuando hay problema ---
    p80_base = np.concatenate([
        np.linspace(200, 210, fase1),
        np.linspace(210, 255, fase2),          # p80 sube (molienda gruesa)
        np.linspace(255, 195, fase3),
    ])
    p80 = p80_base + rng.normal(0, 2, n)

    # --- Densidad: varia moderadamente ---
    densidad_base = np.concatenate([
        np.linspace(1.58, 1.62, fase1),
        np.linspace(1.62, 1.75, fase2),        # densidad sube
        np.linspace(1.75, 1.55, fase3),
    ])
    densidad = densidad_base + rng.normal(0, 0.02, n)

    # Asignar ventanas de 10 minutos
    ventana_dur_s = 600.0
    ventana_idx = (t_s // ventana_dur_s).astype(int)
    ventana_nombre = [f"V{int(vi):02d}" for vi in ventana_idx]

    df = pd.DataFrame({
        "t_s": t_s,
        "potencia": potencia,
        "nivel": nivel,
        "presion": presion,
        "p80": p80,
        "densidad": densidad,
        # Setpoints iniciales (el experto los sobrescribirá internamente)
        "sp_ton": SETPOINTS_BASE["sp_ton"],
        "sp_am": SETPOINTS_BASE["sp_am"],
        "sp_ac": SETPOINTS_BASE["sp_ac"],
        "sp_rpm": SETPOINTS_BASE["sp_rpm"],
        # Límites fuzzy (fijos en esta simulación)
        "potencia_lmin": LIMITES_FUZZY["potencia_lmin"],
        "potencia_lmax": LIMITES_FUZZY["potencia_lmax"],
        "nivel_lmin": LIMITES_FUZZY["nivel_lmin"],
        "nivel_lmax": LIMITES_FUZZY["nivel_lmax"],
        "presion_lmin": LIMITES_FUZZY["presion_lmin"],
        "presion_lmax": LIMITES_FUZZY["presion_lmax"],
        "p80_lmin": LIMITES_FUZZY["p80_lmin"],
        "p80_lmax": LIMITES_FUZZY["p80_lmax"],
        "densidad_lmin": LIMITES_FUZZY["densidad_lmin"],
        "densidad_lmax": LIMITES_FUZZY["densidad_lmax"],
        # Ventanas
        "ventana_idx": ventana_idx,
        "ventana_nombre": ventana_nombre,
    })

    return df


# ============================================================
# 3. EJECUCION
# ============================================================

def main():
    print("=" * 80)
    print("  SIMULACION DEL SISTEMA EXPERTO DIFUSO")
    print("  Datos aleatorios — Prototipo 1")
    print("=" * 80)

    # Generar datos
    df_data = generar_datos_proceso(n_muestras=200, dt_s=5.0, seed=42)
    print(f"\nDatos generados: {len(df_data)} muestras, "
          f"duración: {df_data['t_s'].max():.0f} s ({df_data['t_s'].max()/60:.1f} min)")
    print(f"\nPrimeras 5 filas de datos de entrada:")
    print(df_data[["t_s", "potencia", "nivel", "presion", "p80", "densidad"]].head().to_string(index=False))

    print(f"\nSetpoints iniciales: {SETPOINTS_BASE}")
    print(f"Límites de SP: {LIMITES_SP}")

    # Ejecutar el sistema experto
    print("\n" + "-" * 80)
    print("  EJECUTANDO SISTEMA EXPERTO...")
    print("-" * 80 + "\n")

    resultados = correr_prueba_general(
        df_data=df_data,
        setpoints_base=SETPOINTS_BASE,
        limites_sp=LIMITES_SP,
        meta_flags=META_FLAGS,
        min_belief=0.05,
        verbose=True,
    )

    # Resumen adicional
    df_res = resultados["resultados"]
    df_ev = resultados["eventos"]

    print("\n" + "=" * 80)
    print("  RESUMEN DE LA SIMULACION")
    print("=" * 80)

    print(f"\nMuestras procesadas:    {len(df_res)}")
    print(f"Eventos disparados:     {len(df_ev)}")

    if not df_ev.empty:
        print(f"\nSetpoints finales:")
        ultima = df_res.iloc[-1]
        print(f"  sp_ton = {ultima['sp_ton']:.2f}")
        print(f"  sp_am  = {ultima['sp_am']:.2f}")
        print(f"  sp_ac  = {ultima['sp_ac']:.2f}")
        print(f"  sp_rpm = {ultima['sp_rpm']:.2f}")

        print(f"\nDetalle de eventos (primeros 15):")
        cols_ev = ["t_s", "regla_id", "acciones", "belief"]
        cols_ev = [c for c in cols_ev if c in df_ev.columns]
        print(df_ev[cols_ev].head(15).to_string(index=False))
    else:
        print("\nNo se disparó ninguna regla durante la simulación.")

    print("\n" + "=" * 80)
    print("  SIMULACION COMPLETADA EXITOSAMENTE")
    print("=" * 80)

    return resultados


if __name__ == "__main__":
    main()
