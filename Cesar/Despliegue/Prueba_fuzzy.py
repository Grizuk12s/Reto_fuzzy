import seaborn as sns
import pandas as pd
import numpy as np
import time
import skfuzzy as fuzz
import matplotlib.pyplot as plt
from fuzzy_low_offset import crear_clase_fuzzy_high, crear_clase_fuzzy_Low
from fuzzy_low_offset import crear_clase_fuzzy_norm, crear_clase_fuzzy_pendiente


# Creamos un nuevo conjunto difuso llamado pot_mol_bolas
PotMolBolas = crear_clase_fuzzy_Low(
    "pot_mol_bolas",
    offset=[-10, 0, 10, 20, 40, 60, 70, 80],   # eje X
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],   # conjunto bajo
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],  # conjunto medio
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]    # conjunto alto
)

# Instancia de la clase generada
fuzzy_pot_bolas = PotMolBolas()

# Ingreso manual de pv y lmin
pv = float(input("Ingrese pv: "))
lmin = float(input("Ingrese lmin: "))

# Evaluar valores
dominante_pot_bolas, valor_inferencia_pot_bolas,offset_pot_bolas, pertenencias_pot_bolas = fuzzy_pot_bolas.evaluar(pv, lmin)

print("\n--- RESULTADOS DIFUSOS ---")
print(f"Offset calculado: {offset_pot_bolas}")
print(f"Conjunto dominante: {dominante_pot_bolas}")
print("Pertenencias:")
for k, v in pertenencias_pot_bolas.items():
    print(f"  {k}: {v}")

fuzzy_conj_1 = dominante_pot_bolas
fuzzy_valor_1 = valor_inferencia_pot_bolas
print (fuzzy_conj_1)
print (fuzzy_valor_1)

#---------------------------------------------------------------
# Creamos un nuevo conjunto difuso llamado pot_sag1
Potsag1 = crear_clase_fuzzy_high(
    "pot_sag1",
    offset=[-10, 0, 10, 20, 40, 60, 70, 80],   # eje X
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],   # conjunto bajo
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],  # conjunto ok
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]    # conjunto alto
)

# Instancia de la clase generada
fuzzy_pot_sag1 = Potsag1()

# Ingreso manual de pv y lmin
pv_pot_sag1 = float(input("Ingrese pv: "))
lmax_pot_sag1 = float(input("Ingrese lmax: "))

# Evaluar valores
dominante_pot_sag1, valor_inferencia_pot_sag1,offset_pot_sag1, pertenencias_pot_sag1 = fuzzy_pot_sag1.evaluar(pv_pot_sag1, lmax_pot_sag1)

print("\n--- RESULTADOS DIFUSOS ---")
print(f"Offset calculado: {offset_pot_sag1}")
print(f"Conjunto dominante: {dominante_pot_sag1}")
print("Pertenencias:")
for k, v in pertenencias_pot_sag1.items():
    print(f"  {k}: {v}")

fuzzy_conj_2 = dominante_pot_sag1
fuzzy_valor_2 = valor_inferencia_pot_sag1
print (fuzzy_conj_2)
print (fuzzy_valor_2)


#---------------------------------------------------------------
# Creamos un nuevo conjunto difuso llamado pot_nivel1
fuzzynivel1 = crear_clase_fuzzy_norm(
    "fuzzy_nivel1",
    offset=[-1.1, -1, 0, 0.5, 0.7, 0.8, 1.0, 1.1],   # eje X
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],   # conjunto bajo
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],  # conjunto ok
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]    # conjunto alto
)

# Instancia de la clase generada
fuzzy_nivel1 = fuzzynivel1()

# Ingreso manual de pv y lmin
pv_nivel1 = float(input("Ingrese pv: "))
lmax_nivel1 = float(input("Ingrese lmax: "))
lmin_nivel1 = float(input("Ingrese lmin: "))

# Evaluar valores
dominante_nivel1, valor_inferencia_nivel1,offset_nivel1, pertenencias_nivel1 = fuzzy_nivel1.evaluar(pv_nivel1, lmax_nivel1, lmin_nivel1)
print("\n--- RESULTADOS DIFUSOS ---")
print(f"Offset calculado: {offset_nivel1}")
print(f"Conjunto dominante: {dominante_nivel1}")
print("Pertenencias:")
for k, v in pertenencias_nivel1.items():
    print(f"  {k}: {v}")

fuzzy_conj_3 = dominante_nivel1
fuzzy_valor_3 = valor_inferencia_nivel1
print (fuzzy_conj_3)
print (fuzzy_valor_3)


PendienteGeneral = crear_clase_fuzzy_pendiente(
    "PendienteGeneral",
    x=[-20, -10, -5, 0, 5, 10, 20],
    subiendo=[1, 1, 0.5, 0, 0, 0, 0],
    estable=[0, 0, 0.5, 1, 0.5, 0, 0],
    bajando=[0, 0, 0, 0, 0.5, 1, 1]
)

# Instanciar la clase generada
fp = PendienteGeneral()

# Ingresar valor manualmente o desde un cálculo
pend = float(input("Ingrese pendiente: "))

# Evaluar
dom_pdte_pot, valor_pdte_pot, pertenencias_pdte_pot, grado_pdte_pot = fp.evaluar(pend)

print("\n--- RESULTADO DE PENDIENTE DIFUSA ---")
print("Pendiente evaluada:", valor_pdte_pot)
print("Conjunto dominante:", dom_pdte_pot)
print("Pertenencias:", pertenencias_pdte_pot)
print("Valor de inferencia:", grado_pdte_pot)

fuzzy_pendt_pot = dom_pdte_pot
fuzzy_pendt_pot = grado_pdte_pot




# Si quieres graficar:
#fuzzy_pot_bolas.graficar()
#fuzzy_pot_sag1.graficar()

