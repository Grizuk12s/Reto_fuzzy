# ==============================================================
# ESTRUCTURA DE REGLAS DEL SISTEMA EXPERTO DE MOLIENDA-CLASIFICACIÓN
# Basado 100% en tu tabla de reglas
# ==============================================================

REGLAS = {
    "molino_cargado": [],        # prioridad 1
    "cajon_alto": [],            # prioridad 2
    "p80_alto": [],              # prioridad 3
    "optimizar_potencia": [],    # prioridad 4
    "p80_no_alto": [],           # prioridad 5
    "densidad_presion": [],      # prioridad 6
    "presion_baja": []           # prioridad 7
}


# ==============================================================
# BLOQUE 1 — MOLINO CARGADO (Reglas 1–4)
# ==============================================================

REGLAS["molino_cargado"] += [

    {
        "id": 1,
        "descripcion": "Potencia ALTA, Nivel NO-ALTO, P80 NO-ALTO",
        "si": [
            ("pot_sag", "ALTO"),
            ("nivel", "NO-ALTO"),
            ("p80", "NO-ALTO")
        ],
        "accion": "aumentar_agua_molino_disminuir_agua_cajon",
        "duracion": 20,
        "prioridad": 1
    },

    {
        "id": 2,
        "descripcion": "Potencia ALTA, Nivel ALTO, P80 NO-ALTO",
        "si": [
            ("pot_sag", "ALTO"),
            ("nivel", "ALTO"),
            ("p80", "NO-ALTO")
        ],
        "accion": "disminuir_agua_cajon",
        "duracion": 20,
        "prioridad": 1
    },

    {
        "id": 3,
        "descripcion": "Potencia ALTA, Nivel NO-ALTO, P80 ALTO",
        "si": [
            ("pot_sag", "ALTO"),
            ("nivel", "NO-ALTO"),
            ("p80", "ALTO")
        ],
        "accion": "aumentar_agua_molino",
        "duracion": 20,
        "prioridad": 1
    },

    {
        "id": 4,
        "descripcion": "Potencia ALTA, Nivel ALTO, P80 ALTO",
        "si": [
            ("pot_sag", "ALTO"),
            ("nivel", "ALTO"),
            ("p80", "ALTO")
        ],
        "accion": "disminuir_agua_cajon",
        "duracion": 20,
        "prioridad": 1
    }
]


# ==============================================================
# BLOQUE 2 — CAJÓN ALTO (Reglas 5–7)
# ==============================================================

REGLAS["cajon_alto"] += [

    {
        "id": 5,
        "descripcion": "Nivel ALTO, Presión NO-ALTA",
        "si": [
            ("nivel", "ALTO"),
            ("presion", "NO-ALTO")
        ],
        "accion": "disminuir_agua_cajon",
        "duracion": 20,
        "prioridad": 2
    },

    {
        "id": 6,
        "descripcion": "Nivel ALTO, Presión ALTA, P80 NO-ALTO",
        "si": [
            ("nivel", "ALTO"),
            ("presion", "ALTO"),
            ("p80", "NO-ALTO")
        ],
        "accion": "aumentar_agua_molino",
        "duracion": 20,
        "prioridad": 2
    },

    {
        "id": 7,
        "descripcion": "Nivel ALTO, Presión ALTA, P80 ALTO",
        "si": [
            ("nivel", "ALTO"),
            ("presion", "ALTO"),
            ("p80", "ALTO")
        ],
        "accion": "disminuir_tonelaje",
        "duracion": 20,
        "prioridad": 2
    }
]


# ==============================================================
# BLOQUE 3 — P80 ALTO (Reglas 8–9)
# ==============================================================

REGLAS["p80_alto"] += [

    {
        "id": 8,
        "descripcion": "P80 ALTO, Presión NO-BAJA",
        "si": [
            ("p80", "ALTO"),
            ("presion", "NO-BAJO")
        ],
        "accion": "aumentar_agua_ciclones",
        "duracion": 20,
        "prioridad": 3
    },

    {
        "id": 9,
        "descripcion": "P80 ALTO, Presión BAJA",
        "si": [
            ("p80", "ALTO"),
            ("presion", "BAJO")
        ],
        "accion": "aumentar_tonelaje",
        "duracion": 20,
        "prioridad": 3
    }
]


# ==============================================================
# BLOQUE 4 — OPTIMIZAR POTENCIA (Reglas 10–12)
# ==============================================================

REGLAS["optimizar_potencia"] += [

    {
        "id": 10,
        "descripcion": "Potencia OK, cerca de ALTO",
        "si": [
            ("pot_sag", "OK")
        ],
        "accion": "aumentar_tonelaje",
        "duracion": 120,
        "prioridad": 4
    },

    {
        "id": 11,
        "descripcion": "Potencia OK",
        "si": [
            ("pot_sag", "OK")
        ],
        "accion": "mantener",
        "duracion": 120,
        "prioridad": 4
    },

    {
        "id": 12,
        "descripcion": "Potencia OK, varianza BAJA",
        "si": [
            ("pot_sag", "OK")
        ],
        "accion": "aumentar_tonelaje",
        "duracion": 120,
        "prioridad": 4
    }
]


# ==============================================================
# BLOQUE 5 — P80 NO ALTO (Regla 13)
# ==============================================================

REGLAS["p80_no_alto"] += [
    {
        "id": 13,
        "descripcion": "P80 NO-ALTO",
        "si": [
            ("p80", "NO-ALTO")
        ],
        "accion": "aumentar_tonelaje",
        "duracion": 20,
        "prioridad": 5
    }
]


# ==============================================================
# BLOQUE 6 — DENSIDAD / PRESIÓN (Reglas 14–16)
# ==============================================================

REGLAS["densidad_presion"] += [

    {
        "id": 14,
        "descripcion": "Densidad ALTA",
        "si": [
            ("densidad", "ALTA")
        ],
        "accion": "disminuir_tonelaje",
        "duracion": 20,
        "prioridad": 6
    },

    {
        "id": 15,
        "descripcion": "Densidad OK y Presión ALTA",
        "si": [
            ("densidad", "OK"),
            ("presion", "ALTO")
        ],
        "accion": "aumentar_agua_ciclones",
        "duracion": 20,
        "prioridad": 6
    },

    {
        "id": 16,
        "descripcion": "Densidad BAJA",
        "si": [
            ("densidad", "BAJO")
        ],
        "accion": "disminuir_agua_ciclones",
        "duracion": 20,
        "prioridad": 6
    }
]


# ==============================================================
# BLOQUE 7 — PRESIÓN BAJA (Reglas 17–18)
# ==============================================================

REGLAS["presion_baja"] += [

    {
        "id": 17,
        "descripcion": "Presión BAJA",
        "si": [
            ("presion", "BAJO")
        ],
        "accion": "aumentar_tonelaje",
        "duracion": 20,
        "prioridad": 7
    },

    {
        "id": 18,
        "descripcion": "Presión BAJA, Densidad ALTA",
        "si": [
            ("presion", "BAJO"),
            ("densidad", "ALTO")
        ],
        "accion": "disminuir_tonelaje",
        "duracion": 20,
        "prioridad": 7
    }
]

