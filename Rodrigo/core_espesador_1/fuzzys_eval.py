# ============================================================
# fuzzys_eval.py
# ------------------------------------------------------------
# Evaluacion fuzzy de PV, calculo de pendientes y expansion de
# etiquetas compuestas usadas por las reglas del Espesador:
#   - NO-HIGH, NO-LOW, NO-OK
#   - NO-DEC, NO-INC, NO-STABLE
#   - CERCA_ALTO  = min(mu_OK, mu_HIGH)
#   - CERCA_BAJO  = min(mu_OK, mu_LOW)
# ============================================================

from __future__ import annotations
from copy import deepcopy


def _upper_keys(d):
    return {str(k).upper(): float(v) for k, v in (d or {}).items()}


def _upper_dom(s):
    return str(s).strip().upper()


# ============================================================
# Evaluacion base
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
    """Pendiente fuzzy estimada por regresion lineal en una ventana."""
    import numpy as np

    modelo = PEND_MODELOS[var_name]

    prev_hist = hist.get(var_name)
    if prev_hist is None:
        hist[var_name] = []
    elif isinstance(prev_hist, tuple) and len(prev_hist) == 2:
        hist[var_name] = [(float(prev_hist[0]), float(prev_hist[1]))]
    elif not isinstance(prev_hist, list):
        hist[var_name] = []

    hist[var_name].append((float(t_s), float(pv)))

    t_min_aceptable = float(t_s) - float(ventana_s)
    hist[var_name] = [(tt, yy) for tt, yy in hist[var_name] if float(tt) >= t_min_aceptable]

    puntos = hist[var_name]

    if len(puntos) < int(min_puntos):
        slope_per_min = 0.0
    else:
        t_vals = np.array([float(p[0]) for p in puntos], dtype=float)
        y_vals = np.array([float(p[1]) for p in puntos], dtype=float)
        t_ref = t_vals - t_vals[0]
        if np.allclose(t_ref.max() - t_ref.min(), 0.0):
            slope_per_min = 0.0
        else:
            m, b = np.polyfit(t_ref, y_vals, 1)
            slope_per_min = float(m * 60.0)
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
# Etiquetas compuestas
# ============================================================
def expandir_etiquetas_compuestas(fuzzy_out: dict,
                                  meta_flags: dict | None = None) -> dict:
    """
    Agrega etiquetas derivadas que aparecen en las reglas/permisivos:
      - NO-<LABEL> = 1 - mu(<LABEL>)  para cada etiqueta presente
      - CERCA_ALTO = min(mu_OK, mu_HIGH)   (transicion OK->HIGH)
      - CERCA_BAJO = min(mu_OK, mu_LOW)    (transicion OK->LOW)
      - flags meta (ej. __R15=OFF si quedara alguno en uso)
    """
    out = deepcopy(fuzzy_out)

    for var, info in out.items():
        pert = info.setdefault("pert", {})

        # Genericos: NO-<X> para cada etiqueta existente
        for label in list(pert.keys()):
            no_label = f"NO-{label}"
            if no_label not in pert:
                pert[no_label] = float(max(0.0, 1.0 - pert.get(label, 0.0)))

        # CERCA_ALTO / CERCA_BAJO si existen las tres etiquetas L/O/H
        if "OK" in pert and "HIGH" in pert:
            pert.setdefault("CERCA_ALTO", float(min(pert["OK"], pert["HIGH"])))
        if "OK" in pert and "LOW" in pert:
            pert.setdefault("CERCA_BAJO", float(min(pert["OK"], pert["LOW"])))

    # Meta-flags ON/OFF
    meta_flags = meta_flags or {}
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
