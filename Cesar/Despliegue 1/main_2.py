# ============================
# Main.py
# ============================
import time
import traceback
from pathlib import Path

from opc_functions import conectar_opc, desconectar_opc, leer_tag

# ✅ Importa módulos (para que el reload funcione)
import M1_1_dataframe as m_df
import M1_2_filtros as m_f
import M2_1_dataframe as m2_df

# ✅ Watchdog/validaciones (archivo nuevo que creamos)
from M1_0_watchdog import (
    acquire_lock, heartbeat_lock, release_lock,
    file_exists, get_mtime, reload_if_changed,
    log_ok, log_wr
)

# ============================
# Rutas / lock / tiempos
# ============================
BASE_DIR = Path(__file__).resolve().parent
FILE_DF = BASE_DIR / "M1_1_dataframe.py"
FILE_DF_M2 = BASE_DIR / "M2_1_dataframe.py"
FILE_FILTROS = BASE_DIR / "M1_2_filtros.py"
LOCK_FILE = BASE_DIR / ".main.lock"

HEARTBEAT_SEC = 2.0
SLEEP_OK = 10
SLEEP_ERROR = 3

# ============================
# OPC tags
# ============================
TAGS = {
    "Potencia_SAG":   "ns=2;s=RETO.IN.Potencia_SAG",
    "Potencia_Bolas": "ns=2;s=RETO.IN.Potencia_Bolas",
    "Nivel_Molino":   "ns=2;s=RETO.IN.Nivel_Molino"
}

def ejecutar_ciclo(client):
    """Ejecuta una iteración del main usando la conexión OPC."""
    print("\n===== Lectura de Tags =====")
    for nombre, nodeid in TAGS.items():
        valor = leer_tag(client, nodeid)  # si falla lanza excepción
        print(f"{nombre}: {valor}")

# ============================
# Parámetros de tu pipeline
# ============================
columna = "humedad"
N = 2000
n_generaciones_m1 = 50
n_generaciones_m2 = 20

window = 5
center = False   # TIEMPO REAL: causal (no usar True)
alpha = 0.2
suffix = None
fc_min = 5.0
order = 3
fs = 1.0
min_periods = 1

BOL_filtrar_promedio_movil = True
BOL_filtrar_mediana_movil = True
BOL_filtrar_ema = True
BOL_filtrar_butterworth = False


def main():
    # ✅ Evitar 2 instancias simultáneas
    if not acquire_lock(LOCK_FILE, heartbeat_sec=HEARTBEAT_SEC):
        return

    last_heartbeat = 0.0
    last_mtime_df = get_mtime(FILE_DF)
    last_mtime_df_m2 = get_mtime(FILE_DF_M2)
    last_mtime_filtros = get_mtime(FILE_FILTROS)

    while True:
        client = None
        try:
            # ✅ Conectar OPC (loop externo: reconexión)
            client = conectar_opc()
            if not client:
                log_wr("⏳ No conectó OPC. Reintentando en 5 segundos...")
                time.sleep(5)
                continue

            # ✅ Generar DF base (una vez por conexión, como tu main original)
            try:
                df = m_df.dataframe_M1()
                log_ok(f"DF generado | filas={len(df)} cols={len(df.columns)}")
            except Exception:
                log_wr("Error generando df con dataframe_M1():\n" + traceback.format_exc())
                time.sleep(SLEEP_ERROR)
                continue
            
            # ✅ Generar DF base (una vez por conexión, como tu main original)
            try:
                df_m2 = m2_df.M2_dataframe()
                log_ok(f"DF generado | filas={len(df)} cols={len(df.columns)}")
            except Exception:
                log_wr("Error generando df con dataframe_M2():\n" + traceback.format_exc())
                time.sleep(SLEEP_ERROR)
                continue

            # ✅ Loop interno: ciclo normal
            while True:
                # Heartbeat lock
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_SEC:
                    heartbeat_lock(LOCK_FILE)
                    last_heartbeat = now

                # Validar archivos
                if not (file_exists(FILE_DF) and file_exists(FILE_FILTROS)):
                    log_wr("Faltan archivos. Esperando...")
                    time.sleep(SLEEP_ERROR)
                    continue

                # Reload si cambian
                last_mtime_df = reload_if_changed(m_df, "M1_1_dataframe", FILE_DF, last_mtime_df)
                last_mtime_df_m2 = reload_if_changed(m_df, "M2_1_dataframe", FILE_DF_M2, last_mtime_df_m2)
                last_mtime_filtros = reload_if_changed(m_f, "M1_2_filtros", FILE_FILTROS, last_mtime_filtros)

                # Leer OPC (si falla, reconectar)
                try:
                    ejecutar_ciclo(client)
                except Exception:
                    log_wr("Error leyendo tags OPC:\n" + traceback.format_exc())
                    raise  # fuerza salida al try externo para reconectar

                # Procesamiento por generaciones
                try:
                    for i in range(0, len(df), n_generaciones_m1):
                        print(f"🧩 Generación idx={i} → filas={min(n_generaciones_m1, len(df)-i)}")
                        df_gen = df.iloc[i:i+n_generaciones_m1].copy()

                        df_ma, df_med, df_ema, df_but = m_f.panel_control(
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
                            BOL_filtrar_promedio_movil=BOL_filtrar_promedio_movil,
                            BOL_filtrar_mediana_movil=BOL_filtrar_mediana_movil,
                            BOL_filtrar_ema=BOL_filtrar_ema,
                            BOL_filtrar_butterworth=BOL_filtrar_butterworth
                        )
                        
                        # Aquí podrías usar df_ma/df_med/df_ema/df_but (guardar, enviar, etc.)
                    
                    for i in range(0, len(df_m2), n_generaciones_m2):
                        print(f"🧩 Generación idx={i} → filas={min(n_generaciones_m2, len(df)-i)}")
                        df_gen_m2 = df_m2.iloc[i:i+n_generaciones_m2].copy()
                        df_gen_m2
                        break
                        
                except Exception:
                    log_wr("Error procesando generaciones/panel_control:\n" + traceback.format_exc())
                    time.sleep(SLEEP_ERROR)

                time.sleep(SLEEP_OK)

        except KeyboardInterrupt:
            log_wr("Interrumpido por usuario (Ctrl+C). Cerrando...")
            log_wr("No se reiniciara el sistema por intervencion del usuario (Ctrl+C)")
            break

        except Exception as e:
            log_wr(f"⚠️ Error en ciclo principal: {e}")
            log_wr("🔄 Reiniciando conexión OPC...")

        finally:
            try:
                if client:
                    desconectar_opc(client)
            except Exception:
                log_wr("Error desconectando OPC:\n" + traceback.format_exc())

            log_wr("🕗 Reintentando en 3s...")
            time.sleep(3)

    # Limpieza lockfile al salir
    release_lock(LOCK_FILE)


if __name__ == "__main__":
    try:
        main()
    finally:
        release_lock(LOCK_FILE)

