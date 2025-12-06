# opcua_sim_server.py
from opcua import Server, ua
import random
import time
import math

def make_signal(base, amp, noise, t):
    val = base + amp * math.sin(0.1 * t) + random.uniform(-noise, noise)
    return max(0, min(100, val))

def main():
    server = Server()
    server.set_endpoint("opc.tcp://0.0.0.0:4840")
    uri = "http://example.com/sim"
    idx = server.register_namespace(uri)

    objects = server.get_objects_node()
    sim = objects.add_object(idx, "SimuladorMolino")

    # 3 señales de lectura
    pot_bolas = sim.add_variable(idx, "Potencia_Bolas", 60.0)
    pot_sag = sim.add_variable(idx, "Potencia_SAG", 90.0)
    nivel = sim.add_variable(idx, "Nivel_Molino", 45.0)

    # escritura: setpoint para tonelaje
    setpoint_ton = sim.add_variable(idx, "Setpoint_Tonelaje", 0.0)
    setpoint_ton.set_writable()

    pot_bolas.set_writable()
    pot_sag.set_writable()
    nivel.set_writable()

    server.start()
    print("Servidor OPC UA iniciado...")

    t = 0
    try:
        while True:
            pot_bolas.set_value(make_signal(60, 20, 5, t))
            pot_sag.set_value(make_signal(90, 15, 3, t))
            nivel.set_value(make_signal(45, 10, 2, t))

            t += 1
            time.sleep(1)

    finally:
        server.stop()
        print("Servidor detenido.")

if __name__ == "__main__":
    main()