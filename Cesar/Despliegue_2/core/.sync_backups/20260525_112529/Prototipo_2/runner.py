# -*- coding: utf-8 -*-
"""Runner del sistema experto Espesador (v2) — standalone (Prototipo_2).

Pipeline por fila:
  0. Calcular variables derivadas desde crudas (variables_calculadas.py)  <- NUEVO v2
  1. Extraer PV crudas del row -> dict {var: float}
  2. Filtrar con Exp-Q (config_por_variable obligatoria)
  3. Fuzzificar PV filtradas + calcular pendientes
  4. Expandir etiquetas compuestas (NO-X, CERCA_ALTO, CERCA_BAJO)
  5. Evaluar permisivos -> inyectar como pseudo-variables __PERM_X (ON/OFF)
  6. Motor de reglas con jerarquia de bloques (decision C)
  7. Aplicar acciones disparadas sobre SPs (defuzzy Sugeno por tabla)

Cambios respecto a v1
---------------------
- El DataFrame de entrada ahora puede incluir solo variables CRUDAS de sensores
  (tonelaje_sag_1, tonelaje_sag_2, tonelaje_relave, presion_bomba_1,
  presion_bomba_2, turbiedad_agua).
- El runner llama a calcular_variables_df() en el paso 0 para producir
  automaticamente las variables externas (tonelaje_sag_delta_30min, etc.).
- El usuario puede deshabilitar este paso con calcular_vars=False si ya
  entrega las columnas calculadas en el DataFrame.
- Se agrego el parametro dt_s (intervalo de muestreo en segundos). Si es None
  se detecta automaticamente desde la columna TIME_KEY.
"""

from __future__ import annotations

import pandas as pd

import motor
from config import (
    COLUMNAS_ENTRADA,
    LIMITES_FUZZY_POR_VARIABLE,
    SP_FAMILIA_A_KEY,
    SETPOINT_KEYS,
    TIME_KEY,
    VARIABLES_PROCESO,
)
from defuzzy_actions import apply_actions
from exp_q_filter import ExpQFilter, CONFIG_FILTRO_ESPESADOR_DEFAULT
from fuzzys_eval import evaluar_fuzzys, evaluar_pendiente_var, expandir_etiquetas_compuestas
from fuzzys_models_espesador import FUZZY_MODELOS, PEND_MODELOS
from permisivos import PERMISIVOS, evaluar_permisivos, inyectar_permisivos_en_fuzzy_out
from reglas_espesador import REGLAS_ESPESADOR


# ============================================================
# Helper para cargar reglas desde reglas.json (modo standalone)
# ------------------------------------------------------------
# Permite editar las reglas en vivo desde la UI Flask sin tener
# que reiniciar el proceso.
# ============================================================
import json as _json
import os as _os

REGLAS_JSON_PATH = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "reglas.json"
)


def cargar_reglas_json(path: str | None = None) -> list[dict]:
    """Carga reglas desde reglas.json. Si el archivo no existe o esta vacio,
    retorna las reglas por defecto del experto (REGLAS_ESPESADOR).
    """
    ruta = path or REGLAS_JSON_PATH
    if not _os.path.exists(ruta):
        return list(REGLAS_ESPESADOR)
    try:
        with open(ruta, "r", encoding="utf-8") as _f:
            datos = _json.load(_f)
    except (OSError, ValueError):
        return list(REGLAS_ESPESADOR)
    if not isinstance(datos, list) or not datos:
        return list(REGLAS_ESPESADOR)

    # JSON serializa tuplas como listas. El motor solo reconoce hojas como
    # tuple(var,label), asi que coercemos recursivamente cualquier lista de
    # 2 strings -> tuple, dentro de AND/OR/NOT y a top-level.
    def _coerce(node):
        if isinstance(node, list):
            if len(node) == 2 and all(isinstance(x, str) for x in node):
                return (node[0], node[1])
            return [_coerce(x) for x in node]
        if isinstance(node, dict):
            return {k: _coerce(v) for k, v in node.items()}
        return node

    for regla in datos:
        regla["if"] = [_coerce(c) for c in regla.get("if", [])]
    return datos


# ============================================================
# Helper para cargar config Exp-Q desde filtros.json
# ------------------------------------------------------------
# Si filtros.json falta o esta vacio, se devuelven los defaults de
# `exp_q_filter.CONFIG_FILTRO_ESPESADOR_DEFAULT`.
# ============================================================
FILTROS_JSON_PATH = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "filtros.json"
)


def cargar_filtros_json(path: str | None = None) -> dict:
    ruta = path or FILTROS_JSON_PATH
    if not _os.path.exists(ruta):
        return {k: dict(v) for k, v in CONFIG_FILTRO_ESPESADOR_DEFAULT.items()}
    try:
        with open(ruta, "r", encoding="utf-8") as _f:
            datos = _json.load(_f)
    except (OSError, ValueError):
        return {k: dict(v) for k, v in CONFIG_FILTRO_ESPESADOR_DEFAULT.items()}
    if not isinstance(datos, dict) or not datos:
        return {k: dict(v) for k, v in CONFIG_FILTRO_ESPESADOR_DEFAULT.items()}
    # Sanitizar tipos: q float, window_size int.
    out = {}
    for var, cfg in datos.items():
        if not isinstance(cfg, dict):
            continue
        try:
            q = float(cfg.get("q", 0.0))
            ws = int(cfg.get("window_size", 1))
        except (TypeError, ValueError):
            continue
        out[str(var)] = {"q": q, "window_size": max(1, ws)}
    return out or {k: dict(v) for k, v in CONFIG_FILTRO_ESPESADOR_DEFAULT.items()}


# ============================================================
# Helper para cargar tablas Defuzzy desde defuzzy.json
# ------------------------------------------------------------
# Schema:
#   { "<sp_familia>": {"belief_axis": [...], "steps_por_accion": {"AUMENTAR_FUERTE":[...], ...}} }
# Si falta o esta vacio, devuelve un deepcopy de DEFUZZY_POR_FAMILIA del core.
# ============================================================
DEFUZZY_JSON_PATH = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "defuzzy.json"
)


def _defuzzy_defaults_deepcopy() -> dict:
    from defuzzy_actions import DEFUZZY_POR_FAMILIA as _D
    out = {}
    for fam, tabla in _D.items():
        out[fam] = {
            "belief_axis": list(tabla["belief_axis"]),
            "steps_por_accion": {k: list(v) for k, v in tabla["steps_por_accion"].items()},
        }
    return out


def cargar_defuzzy_json(path: str | None = None) -> dict:
    ruta = path or DEFUZZY_JSON_PATH
    if not _os.path.exists(ruta):
        return _defuzzy_defaults_deepcopy()
    try:
        with open(ruta, "r", encoding="utf-8") as _f:
            datos = _json.load(_f)
    except (OSError, ValueError):
        return _defuzzy_defaults_deepcopy()
    if not isinstance(datos, dict) or not datos:
        return _defuzzy_defaults_deepcopy()
    return datos


# ============================================================
# Helper para cargar membresias fuzzy desde fuzzy.json
# ------------------------------------------------------------
# Schema:
#   { "<var>": {"offset": [..], "labels": {"HIGH":[..], "OK":[..], "LOW":[..]}} }
# El tipo (high/low/norm) NO es editable: se toma del FUZZY_MODELOS del core.
# ============================================================
FUZZY_JSON_PATH = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "fuzzy.json"
)


def _fuzzy_defaults_from_modelos() -> dict:
    out = {}
    for var, entry in FUZZY_MODELOS.items():
        mdl = entry["model"]
        out[var] = {
            "type":   entry["type"],
            "offset": [float(x) for x in list(mdl.offset)],
            "labels": {str(k): [float(x) for x in list(v)] for k, v in mdl.conjuntos.items()},
        }
    return out


def cargar_fuzzy_json(path: str | None = None) -> dict:
    ruta = path or FUZZY_JSON_PATH
    defaults = _fuzzy_defaults_from_modelos()
    if not _os.path.exists(ruta):
        return defaults
    try:
        with open(ruta, "r", encoding="utf-8") as _f:
            datos = _json.load(_f)
    except (OSError, ValueError):
        return defaults
    if not isinstance(datos, dict) or not datos:
        return defaults
    return datos

from variables_calculadas import (
    calcular_variables_df,
    detectar_dt_s,
    DEFINICIONES_CALCULADAS,
)


# ============================================================
# Helpers para extraer datos del DataFrame
# ============================================================
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
    return {sp: _resolver_float(row, sp, columnas_entrada) for sp in SETPOINT_KEYS}


# ============================================================
# Fuzzificacion + pendientes para una fila
# ============================================================
def _evaluar_estado_fuzzy(
    row: pd.Series,
    hist: dict,
    columnas_entrada: dict | None = None,
    meta_flags: dict | None = None,
    inputs_override: dict | None = None,
) -> dict:
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    if inputs_override is not None:
        inputs = {var: float(inputs_override[var]) for var in VARIABLES_PROCESO}
    else:
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


# ============================================================
# Pipeline principal
# ============================================================
def correr_prueba_general(
    df_data: pd.DataFrame,
    reglas: list[dict] | None = None,
    min_belief: float = 0.05,
    verbose: bool = True,
    columnas_entrada: dict | None = None,
    setpoints_base: dict | None = None,
    limites_sp: dict | None = None,
    meta_flags: dict | None = None,
    filtro_exp_q: ExpQFilter | None = None,
    usar_filtro_exp_q: bool = True,
    config_filtro: dict | None = None,
    permisivos_config: dict | None = None,
    min_mu_permisivo: float = 0.50,
    # --- Nuevos parametros v2 ---
    calcular_vars: bool = True,
    dt_s: float | None = None,
    definiciones_calculadas: dict | None = None,
) -> dict:
    """Ejecuta el flujo completo del experto sobre un DataFrame.

    Parametros
    ----------
    df_data : pd.DataFrame
        Datos del proceso. En v2 puede contener variables crudas de sensores
        (tonelaje_sag_1, tonelaje_sag_2, tonelaje_relave, presion_bomba_1,
        presion_bomba_2, turbiedad_agua); el runner calcula las derivadas
        automaticamente antes del pipeline si calcular_vars=True.
    setpoints_base : dict  OBLIGATORIO
        SPs iniciales. Claves: sp_tonelaje, sp_floculante, sp_vel_bomba.
    limites_sp : dict  OBLIGATORIO
        Limites por familia de SP. Ejemplo:
            {"sp_tonelaje":   (LL, HL),
             "sp_floculante": (LL, HL),
             "sp_vel_bomba":  (LL, HL)}
    calcular_vars : bool (default True)
        Si True, llama a calcular_variables_df() antes del pipeline para
        producir las columnas externas derivadas desde variables crudas.
        Poner en False solo si el DataFrame ya tiene esas columnas.
    dt_s : float | None
        Intervalo de muestreo en segundos. Si es None, se detecta
        automaticamente desde la columna TIME_KEY.
    definiciones_calculadas : dict | None
        Definiciones a usar en calcular_variables_df(). Si es None, usa
        variables_calculadas.DEFINICIONES_CALCULADAS.
    """
    if setpoints_base is None:
        raise ValueError("setpoints_base es obligatorio; no se fija en el nucleo.")
    if limites_sp is None:
        raise ValueError("limites_sp es obligatorio; no se fija en el nucleo.")

    reglas = list(REGLAS_ESPESADOR if reglas is None else reglas)
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    permisivos_config = PERMISIVOS if permisivos_config is None else permisivos_config

    # ---- Paso 0 (v2): Calcular variables derivadas desde crudas ----
    if calcular_vars:
        col_t = _resolver_col(TIME_KEY, columnas_entrada)
        if dt_s is None:
            dt_s_efectivo = detectar_dt_s(df_data, col_t=col_t)
        else:
            dt_s_efectivo = float(dt_s)
        defs = definiciones_calculadas if definiciones_calculadas is not None else DEFINICIONES_CALCULADAS
        df_data = calcular_variables_df(df_data, dt_s=dt_s_efectivo, definiciones=defs)

    # ---- Filtro ----
    if usar_filtro_exp_q:
        if filtro_exp_q is not None:
            filtro = filtro_exp_q
        else:
            cfg = config_filtro if config_filtro is not None else CONFIG_FILTRO_ESPESADOR_DEFAULT
            filtro = ExpQFilter(config_por_variable=cfg)
        filtro.reset()
    else:
        filtro = None

    hist: dict = {}
    last_action_time: dict = {}
    setpoints_actuales = dict(setpoints_base)

    rows_resultado = []
    eventos = []

    df_iter = df_data.sort_values(_resolver_col(TIME_KEY, columnas_entrada))
    for _, row in df_iter.iterrows():
        t_s = _resolver_float(row, TIME_KEY, columnas_entrada)

        inputs_raw = extraer_inputs_desde_row(row, columnas_entrada)

        if filtro is not None:
            inputs = filtro.actualizar(inputs_raw)
        else:
            inputs = dict(inputs_raw)

        fuzzy_out = _evaluar_estado_fuzzy(
            row,
            hist,
            columnas_entrada=columnas_entrada,
            meta_flags=meta_flags,
            inputs_override=inputs,
        )

        estados_permisivos = evaluar_permisivos(
            permisivos_config,
            fuzzy_out=fuzzy_out,
            row=row,
            inputs=inputs,
            setpoints=setpoints_actuales,
            columnas_entrada=columnas_entrada,
            min_mu_default=min_mu_permisivo,
        )
        fuzzy_out = inyectar_permisivos_en_fuzzy_out(fuzzy_out, estados_permisivos)

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
                acciones_belief = [(a, float(evento["belief"])) for a in evento.get("acciones", [])]
                setpoints_antes = dict(setpoints_actuales)
                setpoints_actuales = apply_actions(
                    acciones_con_belief=acciones_belief,
                    setpoints=setpoints_actuales,
                    limites_sp=limites_sp,
                )
                familias = list(evento.get("familias_cooldown", []))
                eventos.append(
                    {
                        "t_s": t_s,
                        "t_min": t_s / 60.0,
                        "ventana_idx": int(row.get("ventana_idx", -1)) if "ventana_idx" in row else -1,
                        "ventana_nombre": row.get("ventana_nombre", "") if "ventana_nombre" in row else "",
                        "regla_id": str(evento["id"]),
                        "bloque": str(evento.get("bloque", "")),
                        "n_acciones": len(acciones_belief),
                        "acciones": " | ".join(a for a, _ in acciones_belief),
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
                "ventana_idx": int(row.get("ventana_idx", -1)) if "ventana_idx" in row else -1,
                "ventana_nombre": row.get("ventana_nombre", "") if "ventana_nombre" in row else "",
                **{k: float(v) for k, v in inputs_raw.items()},
                **{f"{k}_filt": float(v) for k, v in inputs.items()},
                **{k: float(v) for k, v in setpoints_actuales.items()},
                **{f"perm_{k}": bool(v) for k, v in estados_permisivos.items()},
                "permisivos": " | ".join(
                    f"{k}={'ON' if v else 'OFF'}" for k, v in estados_permisivos.items()
                ),
                "n_reglas_activadas": len(fired),
                "reglas_activadas": " | ".join(str(e["id"]) for e in fired),
                "bloques_activados": " | ".join(str(e.get("bloque", "")) for e in fired),
                "acciones_activadas": " | ".join(" | ".join(e.get("acciones", [])) for e in fired),
                "familias_activadas": " | ".join(" | ".join(e.get("familias_cooldown", [])) for e in fired),
            }
        )

    df_resultados = pd.DataFrame(rows_resultado)
    df_eventos = pd.DataFrame(eventos)

    if verbose:
        print("=" * 110)
        print("PRUEBA GENERAL DEL SISTEMA EXPERTO ESPESADOR  [v2 -- variables calculadas automaticamente]")
        print("=" * 110)
        if calcular_vars:
            print(f"Variables calculadas automaticamente (dt_s={dt_s_efectivo:.1f} s)")
        else:
            print("Variables calculadas: DESACTIVADO (se usan columnas preexistentes en el DF)")
        if filtro is not None:
            print(f"Filtro Exp-Q activo: {filtro}")
        else:
            print("Filtro Exp-Q DESACTIVADO (se usan PV crudas)")
        if permisivos_config:
            print(f"Permisivos en configuracion: {len(permisivos_config)}")
        else:
            print("Permisivos en configuracion: 0")
        print(f"Reglas cargadas: {len(reglas)}")
        print(f"Muestras evaluadas: {len(df_resultados):,}")
        print(f"Eventos disparados: {len(df_eventos):,}")
        if not df_eventos.empty:
            print("\nActivaciones por regla:")
            print(df_eventos["regla_id"].value_counts().sort_index().to_string())
            print("\nActivaciones por bloque:")
            print(df_eventos["bloque"].value_counts().to_string())
        print("=" * 110)

    return {
        "data_proceso": df_data.copy(),
        "resultados": df_resultados,
        "eventos": df_eventos,
        "filtro_exp_q": filtro,
    }
