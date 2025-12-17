from opcua import Client, ua
import time

KEP_HOST = "opc.tcp://localhost:49320"

def conectar_opc():
    try:
        client = Client(KEP_HOST)
        client.connect()
        print("✅ Conectado a OPC UA")
        return client
    except Exception as e:
        print(f"❌ Error al conectar: {e}")
        return None

def desconectar_opc(client):
    try:
        client.disconnect()
        print("🔻 Conexión OPC cerrada")
    except:
        pass

def leer_tag(client, nodeid):
    try:
        node = client.get_node(nodeid)
        return node.get_value()
    except Exception as e:
        print(f"❌ Error leyendo {nodeid}: {e}")
        raise e   # <- IMPORTANTE: lanza error para activar reconexión
