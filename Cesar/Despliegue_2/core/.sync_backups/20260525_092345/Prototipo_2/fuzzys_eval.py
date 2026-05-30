
# ============================================================
# fuzzys_eval.py
# ------------------------------------------------------------
# EVALUACIÓN FUZZY (modificado para pruebas por regla)
# - evaluar_fuzzys(...)
# - evaluar_pendiente_var(...)
# - expandir_etiquetas_compuestas(...)
# ============================================================

from __future__ import annotations
from copy import deepcopy


def _upper_keys(d):
    return {str(k).upper(): float(v) for k, v in (d or {}).items()}


def _upper_dom(s):
    return str(s).strip().upper()


# ============================================================
# Evaluación base
# ============================================================
def evaluar_fuzzys(inputs: dict,
                   limites: dict,
                   modelos_registry: dict) -> dict:
    out = {}
    for var, meta in modelos_registry.items():
        tipo = meta["type"]
        modelo = meta["model"]

        if var not in inputs:
            continue

        pv = float(inputs[var])
        lim = limites.get(var, {})

        if tipo == "low":
            dom, val, off, pert = modelo.evaluar(pv, lim["lmin"])
        elif tipo == "high":
            dom, val, off, pert = modelo.evaluar(pv, lim["lmax"])
        elif tipo == "norm":
            dom, val, off, pert = modelo.evaluar(pv, lim["lmin"], lim["lmax"])
        else:
            raise ValueError(f"Tipo de fuzzy desconocido: {tipo}")

        out[var] = {
            "dom": _upper_dom(dom),
            "val": float(val),
            "offset": float(off),
            "pert": _upper_keys(pert),
        }
    return out


# ============================================================
# Pendientes
# ============================================================
def evaluar_pendiente_var(var_name: str,
                          pv: float,
                          t_s: float,
                          hist: dict,
                          PEND_MODELOS: dict,
                          dt_min_floor: float = 1e-6,
                          ventana_s: float = 60.0,
                          min_puntos: int = 3) -> dict:
    """
    Evalúa la pendiente fuzzy usando todos los datos disponibles en una
    ventana temporal hacia atrás.

    La pendiente numérica se estima con una regresión lineal sobre los puntos
    (tiempo, valor) dentro de la ventana, y luego se convierte a unidades por
    minuto antes de pasarla al modelo fuzzy correspondiente.
    """
    import numpy as np

    modelo = PEND_MODELOS[var_name]

    # Compatibilidad con historiales antiguos que guardaban solo el último punto
    prev_hist = hist.get(var_name)
    if prev_hist is None:
        hist[var_name] = []
    elif isinstance(prev_hist, tuple) and len(prev_hist) == 2:
        hist[var_name] = [(float(prev_hist[0]), float(prev_hist[1]))]
    elif not isinstance(prev_hist, list):
        hist[var_name] = []

    # Agregar la muestra actual
    hist[var_name].append((float(t_s), float(pv)))

    # Conservar solo los puntos dentro de la ventana
    t_min_aceptable = float(t_s) - float(ventana_s)
    hist[var_name] = [(tt, yy) for tt, yy in hist[var_name] if float(tt) >= t_min_aceptable]

    puntos = hist[var_name]

    if len(puntos) < int(min_puntos):
        slope_per_min = 0.0
    else:
        t_vals = np.array([float(p[0]) for p in puntos], dtype=float)
        y_vals = np.array([float(p[1]) for p in puntos], dtype=float)

        # Referencia temporal para estabilidad numérica
        t_ref = t_vals - t_vals[0]

        # Evitar ajuste degenerado
        if np.allclose(t_ref.max() - t_ref.min(), 0.0):
            slope_per_min = 0.0
        else:
            m, b = np.polyfit(t_ref, y_vals, 1)
            # m queda en unidades/seg -> convertir a unidades/min
            slope_per_min = float(m * 60.0)

            # Protección numérica extra
            if abs(slope_per_min) < float(dt_min_floor):
                slope_per_min = 0.0

    dom_raw, p, pert_raw, inf = modelo.evaluar(slope_per_min)

    pert = _upper_keys(pert_raw)
    dom = _upper_dom(dom_raw)

    return {
        "dom": dom,
        "val": float(pert.get(dom, inf)),
        "offset": float(slope_per_min),
        "pert": pert,
        "slope_per_min": float(slope_per_min),
        "n_puntos_ventana": int(len(puntos)),
        "ventana_s": float(ventana_s),
    }


# ============================================================
# Etiquetas derivadas para compatibilizar reglas
# ============================================================
def _triangular(x: float, a: float, b: float, c: float) -> float:
    if x <= a or x >= c:
        return 0.0
    if x == b:
        return 1.0
    if x < b:
        return (x - a) / (b - a) if (b - a) != 0 else 0.0
    return (c - x) / (c - b) if (c - b) != 0 else 0.0


def expandir_etiquetas_compuestas(fuzzy_out: dict,
                                  meta_flags: dict | None = None) -> dict:
    """
    Agrega etiquetas que aparecen en las reglas pero no en los fuzzys base:
      - NO-HIGH, NO-LOW
      - NO-DEC, NO-INC
      - CERCA_BAJO (solo para potencia)
      - flags meta, por ejemplo __R15=OFF
    """
    out = deepcopy(fuzzy_out)

    for var, info in out.items():
        pert = info.setdefault("pert", {})

        if "HIGH" in pert:
            pert.setdefault("NO-HIGH", float(max(0.0, 1.0 - pert.get("HIGH", 0.0))))
        if "LOW" in pert:
            pert.setdefault("NO-LOW", float(max(0.0, 1.0 - pert.get("LOW", 0.0))))
        if "DEC" in pert:
            pert.setdefault("NO-DEC", float(max(0.0, 1.0 - pert.get("DEC", 0.0))))
        if "INC" in pert:
            pert.setdefault("NO-INC", float(max(0.0, 1.0 - pert.get("INC", 0.0))))

        if str(var).lower() == "potencia":
            off = float(info.get("offset", 0.0))
            # cercanía al límite bajo: pico en 120 y cae hacia 0 y 500
            pert.setdefault("CERCA_BAJO", float(max(0.0, min(1.0, _triangular(off, 0.0, 120.0, 500.0)))))

    meta_flags = meta_flags or {"__R15": "OFF"}
    for meta_name, active_label in meta_flags.items():
        label = str(active_label).upper()
        entry = out.setdefault(str(meta_name), {"dom": label, "val": 1.0, "offset": 0.0, "pert": {}})
        entry["dom"] = label
        entry["val"] = 1.0
        entry.setdefault("offset", 0.0)
        entry.setdefault("pert", {})
        entry["pert"][label] = 1.0
        if label != "OFF":
            entry["pert"].setdefault("OFF", 0.0)
        if label != "ON":
            entry["pert"].setdefault("ON", 0.0)

    return out
