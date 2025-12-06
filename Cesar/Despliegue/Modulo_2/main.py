import time
from opc_functions import conectar_opc, desconectar_opc, leer_tag

TAGS = {
    "Potencia_SAG":  "ns=2;s=RETO.IN.Potencia_SAG",
    "Potencia_Bolas": "ns=2;s=RETO.IN.Potencia_Bolas",
    "Nivel_Molino":  "ns=2;s=RETO.IN.Nivel_Molino"
}

def ejecutar_ciclo(client):
    """Ejecuta una iteración del main usando la conexión OPC."""
    print("\n===== Lectura de Tags =====")
    for nombre, nodeid in TAGS.items():
        valor = leer_tag(client, nodeid)  # <-- si falla, esto lanza excepción
        print(f"{nombre}: {valor}")

def main():

    while True:  
        client = conectar_opc()
        if not client:
            print("⏳ Reintentando conexión en 5 segundos...")
            time.sleep(5)
            continue

        try:
            while True:
                ejecutar_ciclo(client)
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
