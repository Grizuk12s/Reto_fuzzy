historial = []

tiempo_simulado = 0          # segundos
SCAN = 5                     # intervalo de control = 5 segundos

for i in range(len(df)):
    vars_proceso = df.iloc[i].to_dict()

    salida_tick = tick(vars_proceso, tiempo_simulado)

    acc = salida_tick.get("accion_fisica", {})

    historial.append({
        "tiempo_s": tiempo_simulado,
        "regla": salida_tick["regla"].get("regla_activada", None),
        "bloque": salida_tick["regla"].get("bloque", None),
        "accion": acc.get("accion", None),
        "step": acc.get("step", 0),
        "fuerza": acc.get("fuerza", 0),
    })

    tiempo_simulado += SCAN

# df_historial = pd.DataFrame(historial)
# display(df_historial)

# plt.figure(figsize=(14,4))
# plt.plot(df_historial["regla"], marker='o')
# plt.title("Reglas activadas en el tiempo")
# plt.xlabel("Minuto")
# plt.ylabel("Regla")
# plt.grid(True)
# plt.show()
