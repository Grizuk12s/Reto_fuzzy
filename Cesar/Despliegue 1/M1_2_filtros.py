
# ============================================================
# 1) IMPORTAR LIBRERÍAS
# ============================================================
import pandas as pd
from scipy.signal import butter, filtfilt

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


def panel_control(
    df_input,
    columna,                    # str o list/tuple de str
    window=5,
    center=False,               # rolling causal
    alpha=0.2,
    min_periods=1,
    fc_min=5.0,                 # minutos (Butterworth)
    order=3,
    fs=1.0,
    N=2000,
    suffix=None,
    verbose=True,
    BOL_filtrar_promedio_movil = False,
    BOL_filtrar_mediana_movil = False,
    BOL_filtrar_ema = False,
    BOL_filtrar_butterworth = False
):
    """
    Panel de control de filtrado (TODO dentro de esta función).
    Mantiene 4 filtros:
      - Promedio móvil
      - Mediana móvil
      - EMA
      - Butterworth (filtfilt; no causal)

    La activación de filtros se controla con FLAGS definidos afuera:
      BOL_filtrar_promedio_movil, BOL_filtrar_mediana_movil,
      BOL_filtrar_ema, BOL_filtrar_butterworth

    Retorna: df_ma, df_med, df_ema, df_but (4 DataFrames)
    """
    # -------------------------
    # Normalización y validación
    # -------------------------
    cols = [columna] if isinstance(columna, str) else list(columna)

    faltantes = [c for c in cols if c not in df_input.columns]
    if faltantes:
        raise KeyError(f"Estas columnas no existen en el DataFrame: {faltantes}")

    # Recorte seguro (para panel / gráficos)
    N = min(int(N), len(df_input))
    base = df_input.iloc[:N].copy()

    # Validación numérica
    for c in cols:
        try:
            _ = pd.to_numeric(base[c])
        except Exception as e:
            raise TypeError(f"La columna '{c}' no es numérica ni convertible a numérico.") from e


    # -------------------------
    # Nombres de columnas salida
    # -------------------------
    a_tag = str(alpha).replace(".", "_")

    def name_ma(c):   return f"{c}ma{window}" if suffix is None else f"{c}_ma{window}{suffix}"
    def name_med(c):  return f"{c}mediana{window}" if suffix is None else f"{c}_mediana{window}{suffix}"
    def name_ema(c):  return f"{c}ema{a_tag}" if suffix is None else f"{c}_ema{a_tag}{suffix}"
    def name_but(c):  return f"{c}butter" if suffix is None else f"{c}_butter{suffix}"

    # -------------------------
    # Definición interna de filtros (diccionario)
    # -------------------------
    fc = 1.0 / (float(fc_min) * 60.0)  # Hz desde minutos

    filtros = {
        "Promedio móvil": {
            "flag": BOL_filtrar_promedio_movil,
            "key": "ma",
            "apply": lambda d, c: d.assign(**{
                name_ma(c): pd.to_numeric(d[c], errors="coerce").rolling(
                    window=window, center=center, min_periods=min_periods
                ).mean()
            }),
        },
        "Mediana móvil": {
            "flag": BOL_filtrar_mediana_movil,
            "key": "med",
            "apply": lambda d, c: d.assign(**{
                name_med(c): pd.to_numeric(d[c], errors="coerce").rolling(
                    window=window, center=center, min_periods=min_periods
                ).median()
            }),
        },
        "EMA": {
            "flag": BOL_filtrar_ema,
            "key": "ema",
            "apply": lambda d, c: d.assign(**{
                name_ema(c): pd.to_numeric(d[c], errors="coerce").ewm(
                    alpha=alpha, adjust=False
                ).mean()
            }),
        },
        "Butterworth": {
            "flag": BOL_filtrar_butterworth,
            "key": "but",
            "apply": None,  # se calcula abajo por requerir validaciones extra
        },
    }

    # -------------------------
    # Inicializar salidas (si filtro desactivado, queda base)
    # -------------------------
   
    out = {"ma": None, "med": None, "ema": None, "but": None}
    if verbose:
        print("\nESTADO DE FILTROS\n" + "-" * 30)

    # -------------------------
    # Aplicar MA / Mediana / EMA
    # -------------------------
    for nombre in ["Promedio móvil", "Mediana móvil", "EMA"]:
        cfg = filtros[nombre]
        if verbose:
            print(("ACTIVADO ✅ - " if cfg["flag"] else "DESACTIVADO ⛔ - ") + nombre)

        if cfg["flag"]:
            dtemp = base.copy()
            for c in cols:
                dtemp = cfg["apply"](dtemp, c)
            out[cfg["key"]] = dtemp

    # -------------------------
    # Aplicar Butterworth (si está activado)
    # -------------------------
    cfg_b = filtros["Butterworth"]
    if verbose:
        print(("ACTIVADO ✅ - " if cfg_b["flag"] else "DESACTIVADO ⛔ - ") + "Butterworth")

    if cfg_b["flag"]:
        w_norm = fc / (fs / 2)
        if not (0 < w_norm < 1):
            raise ValueError(f"Frecuencia normalizada inválida: w_norm={w_norm:.6g}. Revisa fs y fc_min.")

        b, a = butter(order, w_norm, btype="low", analog=False)

        padlen = 3 * (max(len(a), len(b)) - 1)
        if len(base) <= padlen:
            raise ValueError(
                f"Serie demasiado corta para filtfilt con order={order}. "
                f"len={len(base)}, padlen={padlen}. Baja order o usa más datos."
            )

        dtemp = base.copy()
        for c in cols:
            x = pd.to_numeric(dtemp[c], errors="coerce").astype(float)
            if x.isna().all():
                raise ValueError(f"La columna '{c}' es todo NaN tras convertir a numérico.")

            if isinstance(dtemp.index, pd.DatetimeIndex):
                x_i = x.interpolate(method="time")
            else:
                x_i = x.interpolate()
            x_i = x_i.ffill().bfill()

            dtemp[name_but(c)] = filtfilt(b, a, x_i.to_numpy())

        out["but"] = dtemp

    if verbose:
        print("-" * 30)
        print(f"Filas utilizadas: {N}\n")

    return out["ma"], out["med"], out["ema"], out["but"]


# #%%%

# for i in range(0, len(df), n_generaciones):
#     print (f'Ver iteracion: {i}')
#     df_gen = df.iloc[i:i+n_generaciones].copy()   # último bloque puede tener < 20
    
#     # =========================
#     # Ejecutar panel (4 retornos)
#     # =========================
#     df_ma, df_med, df_ema, df_but = panel_control(
#         df_input=df_gen,
#         columna=columna,
#         window=window,
#         center=center,
#         alpha=alpha,
#         min_periods=min_periods,
#         fc_min=fc_min,
#         order=order,
#         fs=fs,
#         N=N,
#         suffix=suffix,
#         verbose=True
#     )
        
#     # =========================
#     # Normalizar columna a lista
#     # =========================
#     cols = [columna] if isinstance(columna, str) else list(columna)
    
#     # =========================
#     # Nombres de columnas filtradas
#     # =========================
#     alpha_tag = str(alpha).replace(".", "_")
    
#     def col_ma_name(c):  return f"{c}ma{window}" if suffix is None else f"{c}_ma{window}{suffix}"
#     def col_med_name(c): return f"{c}mediana{window}" if suffix is None else f"{c}_mediana{window}{suffix}"
#     def col_ema_name(c): return f"{c}ema{alpha_tag}" if suffix is None else f"{c}_ema{alpha_tag}{suffix}"
#     def col_but_name(c): return f"{c}butter" if suffix is None else f"{c}_butter{suffix}"
    
#     # =========================
#     # Recortar (solo si existe el df)
#     # =========================
#     N = min(N, len(df))
#     df_plot = df_gen.iloc[:N]
    
#     df_ma_plot  = None if df_ma  is None else df_ma.iloc[:N]
#     df_med_plot = None if df_med is None else df_med.iloc[:N]
#     df_ema_plot = None if df_ema is None else df_ema.iloc[:N]
#     df_but_plot = None if df_but is None else df_but.iloc[:N]
    
#     # =========================
#     # Graficar por variable
#     # =========================
#     for c in cols:
    
#         # --- Gráfico 1: Promedio móvil ---
#         if df_ma_plot is not None:
#             plt.figure()
#             plt.plot(df_plot.index, df_plot[c], label=f"{c} (original)", linewidth=0.8, alpha=0.8)
#             plt.plot(df_ma_plot.index, df_ma_plot[col_ma_name(c)], label=f"Promedio móvil (window={window}, causal)", linewidth=1.5)
#             plt.ylabel("Valor"); plt.xlabel("Tiempo")
#             plt.title(f"Filtro 1: Promedio móvil - {c}")
#             plt.grid(True); plt.legend(); plt.tight_layout(); plt.show()
    
#         # --- Gráfico 2: Mediana móvil ---
#         if df_med_plot is not None:
#             plt.figure()
#             plt.plot(df_plot.index, df_plot[c], label=f"{c} (original)", linewidth=0.8, alpha=0.8)
#             plt.plot(df_med_plot.index, df_med_plot[col_med_name(c)], label=f"Mediana móvil (window={window}, causal)", linewidth=1.5)
#             plt.ylabel("Valor"); plt.xlabel("Tiempo")
#             plt.title(f"Filtro 2: Mediana móvil - {c}")
#             plt.grid(True); plt.legend(); plt.tight_layout(); plt.show()
    
#         # --- Gráfico 3: EMA ---
#         if df_ema_plot is not None:
#             plt.figure()
#             plt.plot(df_plot.index, df_plot[c], label=f"{c} (original)", linewidth=0.8, alpha=0.8)
#             plt.plot(df_ema_plot.index, df_ema_plot[col_ema_name(c)], label=f"EMA (alpha={alpha:.2f}, causal)", linewidth=1.5)
#             plt.ylabel("Valor"); plt.xlabel("Tiempo")
#             plt.title(f"Filtro 3: EMA - {c}")
#             plt.grid(True); plt.legend(); plt.tight_layout(); plt.show()
    
#         # --- Gráfico 4: Butterworth ---
#         if df_but_plot is not None:
#             plt.figure()
#             plt.plot(df_plot.index, df_plot[c], label=f"{c} (original)", linewidth=0.8, alpha=0.8)
#             plt.plot(df_but_plot.index, df_but_plot[col_but_name(c)], label=f"Butterworth (fc={fc_min} min, orden={order})", linewidth=1.5)
#             plt.ylabel("Valor"); plt.xlabel("Tiempo")
#             plt.title(f"Filtro 4: Butterworth - {c}")
#             plt.grid(True); plt.legend(); plt.tight_layout(); plt.show()
    
#     break
#     time.sleep(10)
#     if (i==20):
        
#         break
#%%%%%%%%
############### 1 ARCHIVO DE FILTRADO ############### (7 señales) Cada variable x (MIN, MAX, PV) ##############
############## 6 variables manipuladas x (MIN, MAX, Online, SP DCS, SP Exp) ##############
################ 50 variables aprox (Esto es 1 molino)
############################### Aprox 11 Son casi constantes