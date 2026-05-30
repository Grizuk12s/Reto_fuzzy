# -*- coding: utf-8 -*-
"""Runner reusable del sistema experto (standalone — Prototipo_2).

Soporta carga de reglas desde reglas.json para edición en vivo.
"""

from __future__ import annotations

import json
import os
import pandas as pd

import motor
from config import (
    COLUMNAS_ENTRADA,
    LIMITES_FUZZY_POR_VARIABLE,
    SP_FAMILIA_A_KEY,
    TIME_KEY,
    VARIABLES_PROCESO,
)
from defuzzy_actions import apply_action
from fuzzys_eval import evaluar_fuzzys, evaluar_pendiente_var, expandir_etiquetas_compuestas
from fuzzys_models_1A import FUZZY_MODELOS, PEND_MODELOS
from reglas_estrategia_correcta import REGLAS as REGLAS_DEFAULT

REGLAS_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reglas.json")


def cargar_reglas_json(path: str | None = None) -> list[dict]:
    """Carga reglas desde reglas.json. Si no existe, retorna las reglas por defecto."""
    path = path or REGLAS_JSON_PATH
    if not os.path.exists(path):
        return list(REGLAS_DEFAULT)
    with open(path, "r", encoding="utf-8") as f:
        reglas = json.load(f)
    for regla in reglas:
        regla["if"] = [tuple(condicion) if isinstance(condicion, list) else condicion for condicion in regla.get("if", [])]
    return reglas


def _resolver_col(role: str, columnas_entrada: dict) -> str:
    return str(columnas_entrada.get(role, role))


def _resolver_float(row: pd.Series, role: str, columnas_entrada: dict) -> float:
    col = _resolver_col(role, columnas_entrada)
    return float(row[col])


def extraer_inputs_desde_row(row: pd.Series, columnas_entrada: dict | None = None) -> dict:
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    return {var: _resolver_float(row, var, columnas_entrada) for var in VARIABLES_PROCESO}


def extraer_limites_fuzzy_desde_row(row: pd.Series, columnas_entrada: dict | None = None) -> dict:
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    limites = {}
    for var, meta in LIMITES_FUZZY_POR_VARIABLE.items():
        limites[var] = {
            "lmin": _resolver_float(row, meta["lmin"], columnas_entrada),
            "lmax": _resolver_float(row, meta["lmax"], columnas_entrada),
        }
    return limites


def extraer_setpoints_desde_row(row: pd.Series, columnas_entrada: dict | None = None) -> dict:
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    return {
        "sp_ton": _resolver_float(row, "sp_ton", columnas_entrada),
        "sp_am": _resolver_float(row, "sp_am", columnas_entrada),
        "sp_ac": _resolver_float(row, "sp_ac", columnas_entrada),
        "sp_rpm": _resolver_float(row, "sp_rpm", columnas_entrada),
    }


def aplicar_acciones_sobre_setpoints(acciones: list[str], setpoints: dict, limites_sp: dict) -> dict:
    ton_ll, ton_hl = limites_sp["ton"]
    am_ll, am_hl = limites_sp["am"]
    ac_ll, ac_hl = limites_sp["ac"]
    rpm_ll, rpm_hl = limites_sp["rpm"]

    sp_ton = float(setpoints["sp_ton"])
    sp_am = float(setpoints["sp_am"])
    sp_ac = float(setpoints["sp_ac"])
    sp_rpm = float(setpoints["sp_rpm"])

    for accion in acciones:
        sp_ton, sp_am, sp_ac, sp_rpm = apply_action(
            action=str(accion),
            sp_ton=sp_ton,
            sp_am=sp_am,
            sp_ac=sp_ac,
            sp_rpm=sp_rpm,
            ton_ll=ton_ll,
            ton_hl=ton_hl,
            am_ll=am_ll,
            am_hl=am_hl,
            ac_ll=ac_ll,
            ac_hl=ac_hl,
            rpm_ll=rpm_ll,
            rpm_hl=rpm_hl,
        )

    return {"sp_ton": sp_ton, "sp_am": sp_am, "sp_ac": sp_ac, "sp_rpm": sp_rpm}


def _evaluar_estado_fuzzy(
    row: pd.Series,
    hist: dict,
    columnas_entrada: dict | None = None,
    meta_flags: dict | None = None,
) -> dict:
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    inputs = extraer_inputs_desde_row(row, columnas_entrada)
    limites_fuzzy = extraer_limites_fuzzy_desde_row(row, columnas_entrada)
    fuzzy_out = evaluar_fuzzys(inputs, limites_fuzzy, FUZZY_MODELOS)

    t_s = _resolver_float(row, TIME_KEY, columnas_entrada)
    for var in PEND_MODELOS.keys():
        fuzzy_out[f"pend_{var}"] = evaluar_pendiente_var(
            var_name=var,
            pv=float(inputs[var]),
            t_s=t_s,
            hist=hist,
            PEND_MODELOS=PEND_MODELOS,
        )

    fuzzy_out = expandir_etiquetas_compuestas(fuzzy_out, meta_flags=meta_flags or {})
    return fuzzy_out


def correr_prueba_general(
    df_data: pd.DataFrame,
    reglas: list[dict] | None = None,
    min_belief: float = 0.05,
    verbose: bool = True,
    columnas_entrada: dict | None = None,
    setpoints_base: dict | None = None,
    limites_sp: dict | None = None,
    meta_flags: dict | None = None,
    usar_reglas_json: bool = False,
) -> dict:
    """Ejecuta la prueba general del sistema experto.

    Si usar_reglas_json=True, las reglas se cargan desde reglas.json
    (permite edición en vivo desde la interfaz Flask).
    """
    if setpoints_base is None:
        raise ValueError("setpoints_base es obligatorio fuera del core; no se fija en el núcleo.")
    if limites_sp is None:
        raise ValueError("limites_sp es obligatorio fuera del core; no se fija en el núcleo.")

    if reglas is not None:
        reglas = list(reglas)
    elif usar_reglas_json:
        reglas = cargar_reglas_json()
    else:
        reglas = list(REGLAS_DEFAULT)
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada

    hist = {}
    last_action_time = {}
    setpoints_actuales = dict(setpoints_base)

    rows_resultado = []
    eventos = []

    df_iter = df_data.sort_values(_resolver_col(TIME_KEY, columnas_entrada))
    for _, row in df_iter.iterrows():
        t_s = _resolver_float(row, TIME_KEY, columnas_entrada)
        inputs = extraer_inputs_desde_row(row, columnas_entrada)
        fuzzy_out = _evaluar_estado_fuzzy(row, hist, columnas_entrada=columnas_entrada, meta_flags=meta_flags)

        motor_out = motor.evaluar_reglas(
            reglas=reglas,
            fuzzy_out=fuzzy_out,
            t_s=t_s,
            last_action_time=last_action_time,
            min_belief=min_belief,
        )
        last_action_time = motor_out["last_action_time"]

        fired = motor_out["fired"]
        if fired:
            for evento in fired:
                acciones = list(evento.get("acciones", []))
                setpoints_antes = dict(setpoints_actuales)
                setpoints_actuales = aplicar_acciones_sobre_setpoints(acciones, setpoints_actuales, limites_sp)
                familias = list(evento.get("familias_cooldown", []))
                eventos.append(
                    {
                        "t_s": t_s,
                        "t_min": t_s / 60.0,
                        "ventana_idx": int(row.get("ventana_idx", -1)),
                        "ventana_nombre": row.get("ventana_nombre", ""),
                        "regla_id": str(evento["id"]),
                        "n_acciones": len(acciones),
                        "acciones": " | ".join(acciones),
                        "belief": float(evento["belief"]),
                        "familias_cooldown": " | ".join(familias),
                        "sp_afectados": " | ".join(SP_FAMILIA_A_KEY.get(f, "") for f in familias),
                        **{f"antes_{k}": float(v) for k, v in setpoints_antes.items()},
                        **{f"despues_{k}": float(v) for k, v in setpoints_actuales.items()},
                    }
                )

        rows_resultado.append(
            {
                "t_s": t_s,
                "t_min": t_s / 60.0,
                "t_h": t_s / 3600.0,
                "ventana_idx": int(row.get("ventana_idx", -1)),
                "ventana_nombre": row.get("ventana_nombre", ""),
                **inputs,
                **{k: float(v) for k, v in setpoints_actuales.items()},
                "n_reglas_activadas": len(fired),
                "reglas_activadas": " | ".join(str(e["id"]) for e in fired),
                "acciones_activadas": " | ".join(" | ".join(e.get("acciones", [])) for e in fired),
                "familias_activadas": " | ".join(" | ".join(e.get("familias_cooldown", [])) for e in fired),
            }
        )

    df_resultados = pd.DataFrame(rows_resultado)
    df_eventos = pd.DataFrame(eventos)
    df_resumen_ventanas = resumir_ventanas(df_resultados, df_eventos)

    if verbose:
        print("=" * 110)
        print("PRUEBA GENERAL DEL SISTEMA EXPERTO | Todas las reglas juntas")
        print("=" * 110)
        print(f"Muestras evaluadas: {len(df_resultados):,}")
        print(f"Eventos disparados: {len(df_eventos):,}")
        if not df_eventos.empty:
            print("\nActivaciones por regla:")
            print(df_eventos["regla_id"].value_counts().sort_index().to_string())
        print("\nResumen por ventana de 10 minutos:")
        print(df_resumen_ventanas.to_string(index=False))
        print("=" * 110)

    return {
        "data_proceso": df_data.copy(),
        "resultados": df_resultados,
        "eventos": df_eventos,
        "resumen_ventanas": df_resumen_ventanas,
    }


def resumir_ventanas(df_resultados: pd.DataFrame, df_eventos: pd.DataFrame) -> pd.DataFrame:
    base = df_resultados[["ventana_idx", "ventana_nombre"]].drop_duplicates().sort_values("ventana_idx")
    if df_eventos.empty:
        return base.assign(hay_activacion=False, n_activaciones=0, reglas="", acciones="")

    activaciones = (
        df_eventos.groupby(["ventana_idx", "ventana_nombre"], as_index=False)
        .agg(
            hay_activacion=("regla_id", lambda s: True),
            n_activaciones=("regla_id", "size"),
            reglas=("regla_id", lambda s: " | ".join(pd.unique(s.astype(str)))),
            acciones=("acciones", lambda s: " | ".join(pd.unique(s.astype(str)))),
        )
    )

    out = base.merge(activaciones, on=["ventana_idx", "ventana_nombre"], how="left")
    out["hay_activacion"] = out["hay_activacion"].fillna(False)
    out["n_activaciones"] = out["n_activaciones"].fillna(0).astype(int)
    out["reglas"] = out["reglas"].fillna("")
    out["acciones"] = out["acciones"].fillna("")
    return out.reset_index(drop=True)
