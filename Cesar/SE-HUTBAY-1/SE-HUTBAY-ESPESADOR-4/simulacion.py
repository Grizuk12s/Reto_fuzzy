# -*- coding: utf-8 -*-
"""Simulacion del sistema experto Espesador con datos aleatorios (standalone -- Prototipo_2).

Genera un DataFrame con datos sinteticos del proceso de espesado:
- Variables de proceso (PV) que el motor fuzzifica.
- Variables crudas de sensores (tonelajes SAG, presiones de bombas,
  turbiedad), a partir de las cuales el runner calcula automaticamente
  las variables derivadas (delta tonelaje, desv. estandar, diferenciales).

Los datos se construyen en 3 fases (estable -> alerta -> recuperacion) para
ejercitar reglas de los bloques critico y de estabilidad.
"""

import os
import sys

# Asegurar que este directorio este en el path para imports planos.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from runner import correr_prueba_general


# ============================================================
# 1. PARAMETROS OPERACIONALES
# ============================================================

# Setpoints iniciales (claves segun SETPOINT_KEYS en config.py).
SETPOINTS_BASE = {
    "sp_tonelaje":   2200.0,   # t/h
    "sp_floculante": 25.0,     # g/t
    "sp_vel_bomba":  55.0,     # % velocidad bomba descarga
}

# Limites por familia de SP para clipear los efectos defuzzy.
LIMITES_SP = {
    "sp_tonelaje":   (1500.0, 3500.0),
    "sp_floculante": (5.0,    80.0),
    "sp_vel_bomba":  (20.0,   95.0),
}

# Limites fuzzy fijos por variable de proceso (lmin, lmax).
# Si en el futuro estos limites varian dia a dia, basta con escribir las
# columnas {var}_lmin / {var}_lmax fila a fila en el DataFrame.
LIMITES_FUZZY = {
    "torque":               (40.0,  90.0),    # %
    "bed_mass":             (200.0, 900.0),   # t
    "bed_level":            (0.8,   4.0),     # m
    "densidad":             (55.0,  78.0),    # % solidos
    "torque_bomba":         (30.0,  90.0),    # %
    "potencia_bomba":       (150.0, 750.0),   # kW
    "presion_descarga":     (5.0,   22.0),    # bar
    "presion_diferencial":  (1.0,   12.0),    # bar
    "nivel_rastra":         (0.0,   20.0),    # %
}


# ============================================================
# 2. GENERACION DE DATOS
# ============================================================

def _tres_fases(n: int, valor_inicio: float, valor_pico: float, valor_final: float) -> np.ndarray:
    """Construye una serie en 3 fases (estable -> alerta -> recuperacion)."""
    n1 = int(n * 0.35)
    n2 = int(n * 0.35)
    n3 = n - n1 - n2
    return np.concatenate([
        np.linspace(valor_inicio, valor_inicio, n1),
        np.linspace(valor_inicio, valor_pico, n2),
        np.linspace(valor_pico, valor_final, n3),
    ])


def generar_datos_proceso(n_muestras: int = 240, dt_s: float = 60.0, seed: int = 42) -> pd.DataFrame:
    """Genera datos sinteticos del Espesador.

    Devuelve un DataFrame listo para el runner v2: incluye todas las PV,
    sus limites fuzzy y las variables crudas de sensores. El runner aplica
    `calcular_variables_df()` para producir las variables externas.
    """
    rng = np.random.default_rng(seed)
    t_s = np.arange(0, n_muestras * dt_s, dt_s, dtype=float)
    n = len(t_s)

    # --- Variables de proceso (PV) con anomalias para disparar reglas ---
    torque              = _tres_fases(n, 70.0,  92.0, 72.0) + rng.normal(0, 1.2, n)
    bed_mass            = _tres_fases(n, 500.0, 870.0, 520.0) + rng.normal(0, 12.0, n)
    bed_level           = _tres_fases(n, 2.4,   1.0,  2.5) + rng.normal(0, 0.08, n)
    densidad            = _tres_fases(n, 66.0,  77.0, 65.0) + rng.normal(0, 0.4, n)
    torque_bomba        = _tres_fases(n, 60.0,  88.0, 62.0) + rng.normal(0, 1.5, n)
    potencia_bomba      = _tres_fases(n, 400.0, 720.0, 420.0) + rng.normal(0, 8.0, n)
    presion_descarga    = _tres_fases(n, 12.0,  21.0, 13.0) + rng.normal(0, 0.3, n)
    presion_diferencial = _tres_fases(n, 5.0,   1.4,  5.2) + rng.normal(0, 0.15, n)
    nivel_rastra        = _tres_fases(n, 3.0,   18.0, 4.0) + rng.normal(0, 0.4, n)

    # --- Variables crudas de sensores (las consume calcular_variables_df) ---
    tonelaje_sag_1   = _tres_fases(n, 2000.0, 2600.0, 2050.0) + rng.normal(0, 25.0, n)
    tonelaje_sag_2   = _tres_fases(n, 2050.0, 2700.0, 2080.0) + rng.normal(0, 25.0, n)
    tonelaje_relave  = _tres_fases(n, 3800.0, 4900.0, 3900.0) + rng.normal(0, 35.0, n)
    presion_bomba_1  = _tres_fases(n, 14.0,   20.0,  14.5) + rng.normal(0, 0.4, n)
    presion_bomba_2  = _tres_fases(n, 14.0,   19.0,  14.0) + rng.normal(0, 0.4, n)
    turbiedad_agua   = _tres_fases(n, 25.0,   60.0,  28.0) + rng.normal(0, 2.0, n)

    # --- Ventanas de 10 min (informativas) ---
    ventana_dur_s = 600.0
    ventana_idx = (t_s // ventana_dur_s).astype(int)
    ventana_nombre = [f"V{int(vi):02d}" for vi in ventana_idx]

    datos = {
        "t_s": t_s,
        # PV
        "torque":              torque,
        "bed_mass":            bed_mass,
        "bed_level":           bed_level,
        "densidad":            densidad,
        "torque_bomba":        torque_bomba,
        "potencia_bomba":      potencia_bomba,
        "presion_descarga":    presion_descarga,
        "presion_diferencial": presion_diferencial,
        "nivel_rastra":        nivel_rastra,
        # Crudas
        "tonelaje_sag_1":   tonelaje_sag_1,
        "tonelaje_sag_2":   tonelaje_sag_2,
        "tonelaje_relave":  tonelaje_relave,
        "presion_bomba_1":  presion_bomba_1,
        "presion_bomba_2":  presion_bomba_2,
        "turbiedad_agua":   turbiedad_agua,
        # Ventanas
        "ventana_idx":     ventana_idx,
        "ventana_nombre":  ventana_nombre,
    }

    # Limites fuzzy por variable (constantes en la simulacion).
    for var, (lmin, lmax) in LIMITES_FUZZY.items():
        datos[f"{var}_lmin"] = np.full(n, lmin, dtype=float)
        datos[f"{var}_lmax"] = np.full(n, lmax, dtype=float)

    # SPs como columnas (el runner los lee desde setpoints_base, pero
    # COLUMNAS_ENTRADA los mapea por nombre).
    for sp, valor in SETPOINTS_BASE.items():
        datos[sp] = np.full(n, valor, dtype=float)

    return pd.DataFrame(datos)


# ============================================================
# 3. EJECUCION
# ============================================================

def main() -> dict:
    print("=" * 80)
    print("  SIMULACION DEL SISTEMA EXPERTO ESPESADOR")
    print("  Datos aleatorios -- Prototipo_2")
    print("=" * 80)

    df_data = generar_datos_proceso(n_muestras=240, dt_s=60.0, seed=42)
    print(f"\nDatos generados: {len(df_data)} muestras, "
          f"duracion: {df_data['t_s'].max():.0f} s "
          f"({df_data['t_s'].max() / 60:.1f} min).")
    print("\nPrimeras 5 filas (PV principales):")
    cols_pv = [
        "t_s", "torque", "bed_mass", "bed_level", "densidad",
        "presion_descarga", "presion_diferencial", "nivel_rastra",
    ]
    print(df_data[cols_pv].head().to_string(index=False))

    print(f"\nSetpoints iniciales: {SETPOINTS_BASE}")
    print(f"Limites de SP:       {LIMITES_SP}")

    print("\n" + "-" * 80)
    print("  EJECUTANDO SISTEMA EXPERTO ESPESADOR (v2)...")
    print("-" * 80 + "\n")

    resultados = correr_prueba_general(
        df_data=df_data,
        setpoints_base=SETPOINTS_BASE,
        limites_sp=LIMITES_SP,
        min_belief=0.05,
        verbose=True,
        calcular_vars=True,
        dt_s=60.0,
    )

    df_res = resultados["resultados"]
    df_ev = resultados["eventos"]

    print("\n" + "=" * 80)
    print("  RESUMEN DE LA SIMULACION")
    print("=" * 80)
    print(f"Muestras procesadas: {len(df_res)}")
    print(f"Eventos disparados:  {len(df_ev)}")

    if not df_res.empty:
        ultima = df_res.iloc[-1]
        print("\nSetpoints finales:")
        for sp in SETPOINTS_BASE.keys():
            print(f"  {sp:<14} = {float(ultima[sp]):.3f}")

    if not df_ev.empty:
        print("\nDetalle de eventos (primeros 15):")
        cols_ev = [c for c in ("t_s", "regla_id", "bloque", "acciones", "belief") if c in df_ev.columns]
        print(df_ev[cols_ev].head(15).to_string(index=False))
    else:
        print("\nNo se disparo ninguna regla durante la simulacion.")

    print("\n" + "=" * 80)
    print("  SIMULACION COMPLETADA")
    print("=" * 80)

    return resultados


if __name__ == "__main__":
    main()
