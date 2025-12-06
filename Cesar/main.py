# main.py
import time
from db_connection import crear_conexion, leer_entradas, insertar_salida_random


def main():
    print("Iniciando software Reto Fuzzy...")
    conn = None

    try:
        conn = crear_conexion()
        print("Conexión a PostgreSQL establecida.")

        # Bucle infinito
        while True:
            print("\n--- Nueva iteración ---")

            # 1) Leer variables de la tabla entradas
            entradas = leer_entradas(conn)
            print(f"Entradas leídas ({len(entradas)} filas):")
            for fila in entradas:
                # fila = (id_entradas, n1, n2)
                print(f"  id={fila[0]}, n1={fila[1]}, n2={fila[2]}")

            # 2) Escribir números random en la tabla salidas
            n1, n2 = insertar_salida_random(conn)
            print(f"Insertada nueva salida: n1={n1}, n2={n2}")

            # 3) Esperar 10 segundos
            print("Esperando 10 segundos...\n")
            time.sleep(10)

    except KeyboardInterrupt:
        print("\nInterrupción manual (Ctrl + C). Cerrando...")

    except Exception as e:
        print(f"\n[ERROR] Ocurrió una excepción: {e}")

    finally:
        if conn is not None:
            conn.close()
            print("Conexión a PostgreSQL cerrada.")


if __name__ == "__main__":
    main()
