# ============================================================
# fuzzys_templates.py
# ------------------------------------------------------------
# FACTORIES FUZZY (tu estilo: tablas discretas + np.interp)
# - Low / High / Norm usan la misma base (evita duplicación)
# - Pendiente y Varianza como antes
# ============================================================

from __future__ import annotations
import numpy as np


def _build_interp_mfs(x_axis: np.ndarray, conjuntos: dict) -> dict:
    """
    Construye mf_{nombre} como funciones de interpolación sobre x_axis.
    Retorna un dict {nombre: callable(x)->mu}.
    """
    mfs = {}
    for nombre, valores in conjuntos.items():
        vec = np.array(valores, dtype=float)
        # Capturamos vec como default arg para evitar bug de closures
        def mf(x, v=vec, axis=x_axis):
            return np.interp(x, axis, v, left=v[0], right=v[-1])
        mfs[str(nombre)] = mf
    return mfs


def crear_clase_fuzzy_offset(nombre_clase: str,
                             offset: list,
                             offset_fn,
                             offset_args: tuple[str, ...],
                             **conjuntos):
    """
    Factory genérica para fuzzys basados en:
      - eje offset (discretizado)
      - conjuntos (tablas de membresía)
      - offset_fn(pv, **kwargs) -> off

    offset_args define qué argumentos requiere offset_fn:
      - ("lmin",) para Low
      - ("lmax",) para High
      - ("lmin","lmax") para Norm

    evaluar(...) siempre retorna:
      (dominante, val_dom, off, pertenencias)
    """
    class FuzzyTemplate:
        def __init__(self):
            self.offset = np.array(offset, dtype=float)
            self.conjuntos = {str(k): np.array(v, dtype=float) for k, v in conjuntos.items()}
            self.mfs = _build_interp_mfs(self.offset, self.conjuntos)

        def calcular_offset(self, pv, **kwargs):
            return float(offset_fn(float(pv), **kwargs))

        def evaluar(self, pv, *args):
            kwargs = {}
            if len(args) != len(offset_args):
                raise TypeError(
                    f"{nombre_clase}.evaluar espera {len(offset_args)} args ({offset_args}), "
                    f"pero recibió {len(args)}."
                )
            for k, v in zip(offset_args, args):
                kwargs[k] = float(v)

            off = self.calcular_offset(pv, **kwargs)

            pertenencias = {}
            for nombre, mf in self.mfs.items():
                pertenencias[nombre] = float(np.clip(mf(off), 0.0, 1.0))

            dominante = max(pertenencias, key=pertenencias.get)
            val_dom = float(pertenencias[dominante])
            return dominante, val_dom, float(off), pertenencias

    FuzzyTemplate.__name__ = nombre_clase
    return FuzzyTemplate


# -----------------------------
# Wrappers con tu nomenclatura
# -----------------------------

def crear_clase_fuzzy_Low(nombre_clase, offset, **conjuntos):
    # offset = pv - lmin
    def offset_fn(pv, lmin):
        return pv - lmin

    return crear_clase_fuzzy_offset(
        nombre_clase=nombre_clase,
        offset=offset,
        offset_fn=offset_fn,
        offset_args=("lmin",),
        **conjuntos
    )


def crear_clase_fuzzy_high(nombre_clase, offset, **conjuntos):
    # offset = lmax - pv
    def offset_fn(pv, lmax):
        return lmax - pv

    return crear_clase_fuzzy_offset(
        nombre_clase=nombre_clase,
        offset=offset,
        offset_fn=offset_fn,
        offset_args=("lmax",),
        **conjuntos
    )


def crear_clase_fuzzy_norm(nombre_clase, offset, **conjuntos):
    # offset = (pv - lmin) / (lmax - lmin)
    def offset_fn(pv, lmin, lmax):
        den = (lmax - lmin)
        if abs(den) < 1e-12:
            return 0.5
        return (pv - lmin) / den

    return crear_clase_fuzzy_offset(
        nombre_clase=nombre_clase,
        offset=offset,
        offset_fn=offset_fn,
        offset_args=("lmin", "lmax"),
        **conjuntos
    )


# -----------------------------
# Pendiente (igual que tu lógica)
# -----------------------------
def crear_clase_fuzzy_pendiente(nombre_clase, x, **conjuntos):
    class FuzzyPendienteTemplate:
        def __init__(self):
            self.x = np.array(x, dtype=float)
            self.conjuntos = {str(k): np.array(v, dtype=float) for k, v in conjuntos.items()}
            self.mfs = _build_interp_mfs(self.x, self.conjuntos)

        def evaluar(self, pendiente):
            p = float(pendiente)

            pertenencias = {}
            for nombre, mf in self.mfs.items():
                pertenencias[nombre] = float(np.clip(mf(p), 0.0, 1.0))

            dominante = max(pertenencias, key=pertenencias.get)
            inferencia = float(pertenencias[dominante])
            return dominante, p, pertenencias, inferencia

    FuzzyPendienteTemplate.__name__ = nombre_clase
    return FuzzyPendienteTemplate


# -----------------------------
# Varianza (igual que tu lógica)
# -----------------------------
def crear_clase_fuzzy_varianza(nombre_clase, x, **conjuntos):
    class FuzzyVarianzaTemplate:
        def __init__(self):
            self.x = np.array(x, dtype=float)
            self.conjuntos = {str(k): np.array(v, dtype=float) for k, v in conjuntos.items()}
            self.mfs = _build_interp_mfs(self.x, self.conjuntos)

        def evaluar(self, varianza):
            v = float(varianza)

            pertenencias = {}
            for nombre, mf in self.mfs.items():
                pertenencias[nombre] = float(np.clip(mf(v), 0.0, 1.0))

            dominante = max(pertenencias, key=pertenencias.get)
            inferencia = float(pertenencias[dominante])
            return dominante, v, pertenencias, inferencia

    FuzzyVarianzaTemplate.__name__ = nombre_clase
    return FuzzyVarianzaTemplate







