from opcua import Client
import time
from fuzzy_low_offset import (
    crear_clase_fuzzy_high,
    crear_clase_fuzzy_Low,
    crear_clase_fuzzy_norm,
    crear_clase_fuzzy_pendiente,
    crear_clase_defuzzy,
    motor_fuzzy_complejo,
    crear_filtro_pendiente
)

# ================================================================
# DEFINICIÓN DE CLASES DIFUSAS
# ================================================================


########## Conjuntos Fuzzy
### Fuzzy low, normal, hight

PotMolBolas = crear_clase_fuzzy_Low(   ############ TEMPLATE LOW
    "pot_mol_bolas",    ##### 1 FUZZY de potencia de bolas Bolas, este es el LOW y esto es un instrumento.
    offset=[-10, 0, 10, 20, 40, 60, 70, 80],
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]
)   ######## Cada lctura, se debe generar un fuzzy pendiente
fuzzy_pot_bolas = PotMolBolas()

Potsag1 = crear_clase_fuzzy_high( ############ TEMPLATE HIGH
    "pot_sag1",
    offset=[-10, 0, 10, 20, 40, 60, 70, 80],
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]
)
fuzzy_pot_sag1 = Potsag1()

fuzzynivel1 = crear_clase_fuzzy_norm(  ############ TEMPLATE NORMAL
    "fuzzy_nivel1",
    offset=[-1.1, -1, 0, 0.5, 0.7, 0.8, 1.0, 1.1], 
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]
)
fuzzy_nivel1 = fuzzynivel1()

PendienteGeneral = crear_clase_fuzzy_pendiente(
    "PendienteGeneral",
    x=[-8, -5, -1, 0, 1, 5, 8],
    subiendo=[1, 1, 0.5, 0, 0, 0, 0],
    estable=[0, 0, 0.5, 1, 0.5, 0, 0],
    bajando=[0, 0, 0, 0, 0.5, 1, 1]
)
fp = PendienteGeneral()

DefuzzyRegla = crear_clase_defuzzy(
    "DefuzzyRegla",
    belief=[0, 0.5, 1.0],
    inc_rap=[0, 25, 50],
    inc=[0, 10, 25],
    dec=[0, -10, -25],
    dec_rap=[0, -25, -50]
)
dfz = DefuzzyRegla()



# ================================================================
# OPC UA CLIENTE
# ================================================================

client = Client("opc.tcp://localhost:4840")
client.connect()
print("Cliente conectado a OPC UA")

pot_bolas = client.get_node("ns=2;i=2")
pot_sag   = client.get_node("ns=2;i=3")
nivel     = client.get_node("ns=2;i=4")
setpoint  = client.get_node("ns=2;i=5")

# ================================================================
# CREAR FILTROS DE PENDIENTE (def)
# ================================================================

filtro_bolas = crear_filtro_pendiente()
filtro_sag   = crear_filtro_pendiente()
filtro_nivel = crear_filtro_pendiente()

# ================================================================
# LOOP PRINCIPAL
# ================================================================
while True:
    try:
        pv_bolas = pot_bolas.get_value()
        pv_sag   = pot_sag.get_value()
        pv_nivel = nivel.get_value()

        t = time.time()

        # ================================
        # CALCULAR PENDIENTES FILTRADAS
        # ================================
        pend_bolas = filtro_bolas(t, pv_bolas)
        pend_sag   = filtro_sag(t, pv_sag)
        pend_nivel = filtro_nivel(t, pv_nivel)

        # ================================
        # FUZZIFICAR PENDIENTE FILTRADA (SAG)
        # ================================
        dom_pend_sag, mu_pend_sag, _, _ = fp.evaluar(pend_sag)
        dom_pend_bolas, mu_pend_bolas, _, _ = fp.evaluar(pend_bolas)
        dom_pend_nivel, mu_pend_nivel, _, _ = fp.evaluar(pend_nivel)


        # Fuzzy principales
        dom_bolas, mu_bolas, _, _ = fuzzy_pot_bolas.evaluar(pv_bolas, 0)
        dom_sag,   mu_sag,   _, _ = fuzzy_pot_sag1.evaluar(pv_sag, 0)
        dom_nivel, mu_nivel, _, _ = fuzzy_nivel1.evaluar(pv_nivel, 1, 0)

        # Motor difuso
        conjunto_salida, mu_salida = motor_fuzzy_complejo(
            dom_bolas, mu_bolas,
            dom_sag,   mu_sag,
            dom_nivel, mu_nivel,
            dom_pend_sag,  mu_pend_sag
        )

        step = dfz.evaluar_manual(mu_salida, conjunto_salida)

        print("\n--- OPC DATA ---")
        print("Potencia Bolas:", pv_bolas, "   Pend Filtrada:", round(pend_bolas,5))
        print("conjunto:", dom_bolas, "   valor inferencia" , mu_bolas)
        print("pendiente:", dom_pend_bolas, "   valor inferencia" , mu_pend_bolas)
        print("Potencia SAG:  ", pv_sag,   "   Pend Filtrada:", round(pend_sag,5))
        print("conjunto:", dom_sag, "   valor inferencia" , mu_sag)
        print("pendiente:", dom_pend_sag, "   valor inferencia" , mu_pend_sag)
        print("Nivel Molino:  ", pv_nivel, "   Pend Filtrada:", round(pend_nivel,5))
        print("conjunto:", dom_nivel, "   valor inferencia" , mu_nivel)
        print("pendiente:", dom_pend_nivel, "   valor inferencia" , mu_pend_nivel)

        print("Step calculado:", step)

        setpoint.set_value(float(step))
        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        break

client.disconnect()
print("Cliente desconectado.")