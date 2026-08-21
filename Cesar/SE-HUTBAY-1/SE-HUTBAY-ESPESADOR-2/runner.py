# -*- coding: utf-8 -*-
"""Runner del sistema experto Espesador (v3).

Pipeline por fila:
  0. Calcular variables derivadas desde crudas (variables_calculadas.py)
  1. Extraer PV crudas del row -> dict {var: float}
  2. Filtrar con Exp-Q (config_por_variable obligatoria)
  3. Fuzzificar PV filtradas + calcular pendientes
  4. Expandir etiquetas compuestas (NO-X, CERCA_ALTO, CERCA_BAJO)
  5. Evaluar permisivos -> inyectar como pseudo-variables __PERM_X (ON/OFF)
  6. Motor de reglas con jerarquía de bloques + waits declarativos
  7. Aplicar acciones disparadas sobre SPs (defuzzy Sugeno por tabla)

Desacoplamiento (IT-4):
  - correr_prueba_general() acepta fuzzy_modelos, pend_modelos,
    variables_proceso como parámetros explícitos.
  - Cuando son None, carga desde los módulos del Espesador (lazy import).
  - Los imports de módulos Espesador son lazy (dentro de funciones),
    no a nivel de módulo.
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
from core.engine.defuzzy import apply_actions_tabla
from core.filters.exp_q import (
    ExpQFilter,
    CONFIG_FILTRO_ESPESADOR_DEFAULT,
    PERIODO_LEGACY_S,
)
from core.fuzzy.evaluator import evaluar_fuzzys, evaluar_pendiente_var, expandir_etiquetas_compuestas
from permisivos import PERMISIVOS, evaluar_permisivos, inyectar_permisivos_en_fuzzy_out


# ============================================================
# Helper para cargar reglas desde reglas.json (modo standalone)
# ============================================================
import json as _json
import os as _os

_CFG_DIR = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "config", "espesador"
)  # IT-9: JSONs movidos a config/espesador/

REGLAS_JSON_PATH = _os.path.join(_CFG_DIR, "reglas.json")


def cargar_reglas_json(path: str | None = None) -> list[dict]:
    """Carga reglas desde reglas.json. Fallback: REGLAS_ESPESADOR (lazy import)."""
    def _defaults():
        from reglas_espesador import REGLAS_ESPESADOR
        return list(REGLAS_ESPESADOR)

    ruta = path or REGLAS_JSON_PATH
    if not _os.path.exists(ruta):
        return _defaults()
    try:
        with open(ruta, "r", encoding="utf-8") as _f:
            datos = _json.load(_f)
    except (OSError, ValueError):
        return _defaults()
    # Una lista vacia es un estado VALIDO: "este cliente todavia no tiene
    # reglas". Antes caia a las 28 del espesador y blanquear no servia.
    if not isinstance(datos, list):
        return _defaults()

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
        if "fuerza" in regla and regla["fuerza"] is not None:
            regla["fuerza"] = _coerce(regla["fuerza"])
    return [r for r in datos if r.get("enabled", True)]


# ============================================================
# Helper para cargar config Exp-Q desde filtros.json
# ============================================================
FILTROS_JSON_PATH = _os.path.join(_CFG_DIR, "filtros.json")


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
    out = {}
    for var, cfg in datos.items():
        if not isinstance(cfg, dict):
            continue
        try:
            q = float(cfg.get("q", 0.0))
            # `ventana_s` es la forma vigente (segundos de proceso). Un
            # filtros.json viejo trae `window_size` (numero de muestras):
            # se traduce con el periodo que tenia el lazo entonces, para
            # que el filtro siga comportandose igual.
            if "ventana_s" in cfg:
                ventana_s = float(cfg["ventana_s"])
            elif "window_size" in cfg:
                ventana_s = max(1, int(cfg["window_size"])) * PERIODO_LEGACY_S
            else:
                continue
        except (TypeError, ValueError):
            continue
        if ventana_s <= 0.0:
            continue
        out[str(var)] = {"q": q, "ventana_s": ventana_s}
    return out or {k: dict(v) for k, v in CONFIG_FILTRO_ESPESADOR_DEFAULT.items()}


# ============================================================
# Helper para cargar tablas Defuzzy desde defuzzy.json
# ============================================================
DEFUZZY_JSON_PATH = _os.path.join(_CFG_DIR, "defuzzy.json")


def _defuzzy_defaults_deepcopy() -> dict:
    from defuzzy_actions import DEFUZZY_POR_FAMILIA as _D
    return {
        fam: {
            "belief_axis": list(tabla["belief_axis"]),
            "steps_por_accion": {k: list(v) for k, v in tabla["steps_por_accion"].items()},
        }
        for fam, tabla in _D.items()
    }


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
# Helper para cargar membresías fuzzy desde fuzzy.json
# ============================================================
FUZZY_JSON_PATH = _os.path.join(_CFG_DIR, "fuzzy.json")


def _fuzzy_defaults_from_modelos() -> dict:
    from fuzzys_models_espesador import FUZZY_MODELOS as _FM
    return {
        var: {
            "type":   entry["type"],
            "offset": [float(x) for x in list(entry["model"].offset)],
            "labels": {str(k): [float(x) for x in list(v)] for k, v in entry["model"].conjuntos.items()},
        }
        for var, entry in _FM.items()
    }


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


# ============================================================
# Helper para cargar variables desde variables.json
# ============================================================
VARIABLES_JSON_PATH = _os.path.join(_CFG_DIR, "variables.json")


def _variables_defaults_from_core() -> dict:
    from variables_calculadas import VARIABLES_CRUDAS as _CRUDAS_CORE, DEFINICIONES_CALCULADAS as _DEFS
    crudas = {k: str(v) for k, v in _CRUDAS_CORE.items()}
    definiciones = []
    for nombre, cfg in _DEFS.items():
        item = {"nombre": nombre, "descripcion": str(cfg.get("descripcion", "")), "tipo": cfg["tipo"]}
        if cfg["tipo"] == "aritmetica":
            item["operacion"] = cfg["operacion"]
            item["args"] = list(cfg["args"])
        else:
            item["arg"] = cfg["arg"]
            item["ventana_min"] = float(cfg["ventana_min"])
        definiciones.append(item)
    return {"crudas": crudas, "definiciones": definiciones}


def cargar_variables_json(path: str | None = None) -> dict:
    ruta = path or VARIABLES_JSON_PATH
    defaults = _variables_defaults_from_core()
    if not _os.path.exists(ruta):
        return defaults
    try:
        with open(ruta, "r", encoding="utf-8") as _f:
            datos = _json.load(_f)
    except (OSError, ValueError):
        return defaults
    if not isinstance(datos, dict) or "definiciones" not in datos:
        return defaults
    return datos


def definiciones_lista_a_dict(definiciones_lista: list) -> dict:
    out = {}
    for item in definiciones_lista:
        if not isinstance(item, dict) or "nombre" not in item:
            continue
        nombre = item["nombre"]
        cfg = {"descripcion": item.get("descripcion", ""), "tipo": item["tipo"]}
        if item["tipo"] == "aritmetica":
            cfg["operacion"] = item["operacion"]
            cfg["args"] = list(item["args"])
        else:
            cfg["arg"] = item["arg"]
            cfg["ventana_min"] = float(item["ventana_min"])
        out[nombre] = cfg
    return out


# ============================================================
# Helper para cargar permisivos desde permisivos.json
# ============================================================
PERMISIVOS_JSON_PATH = _os.path.join(_CFG_DIR, "permisivos.json")


def _permisivos_defaults_deepcopy() -> dict:
    import copy as _copy
    return _copy.deepcopy(PERMISIVOS)


def cargar_permisivos_json(path: str | None = None) -> dict:
    ruta = path or PERMISIVOS_JSON_PATH
    if not _os.path.exists(ruta):
        return _permisivos_defaults_deepcopy()
    try:
        with open(ruta, "r", encoding="utf-8") as _f:
            datos = _json.load(_f)
    except (OSError, ValueError):
        return _permisivos_defaults_deepcopy()
    # Idem: un objeto vacio significa "sin permisivos", no "usa la plantilla".
    if not isinstance(datos, dict):
        return _permisivos_defaults_deepcopy()
    return datos


# ============================================================
# Importar helpers de variables calculadas (lazy via variables_calculadas)
# ============================================================
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


def extraer_inputs_desde_row(
    row: pd.Series,
    columnas_entrada: dict | None = None,
    variables_proceso: list[str] | None = None,
) -> dict:
    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    if variables_proceso is None:
        variables_proceso = VARIABLES_PROCESO
    return {var: _resolver_float(row, var, columnas_entrada) for var in variables_proceso}


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
# Fuzzificación + pendientes para una fila
# ============================================================
def _evaluar_estado_fuzzy(
    row: pd.Series,
    hist: dict,
    columnas_entrada: dict | None = None,
    meta_flags: dict | None = None,
    inputs_override: dict | None = None,
    fuzzy_modelos: dict | None = None,
    pend_modelos: dict | None = None,
    variables_proceso: list[str] | None = None,
) -> dict:
    """Fuzzifica una fila del DataFrame.

    Parámetros
    ----------
    fuzzy_modelos : dict | None
        Registry de modelos fuzzy por variable. Si None, usa FUZZY_MODELOS
        del módulo fuzzys_models_espesador (lazy import).
    pend_modelos : dict | None
        Registry de modelos de pendiente por variable. Si None, usa
        PEND_MODELOS de fuzzys_models_espesador (lazy import).
    variables_proceso : list[str] | None
        Lista de variables PV a extraer. Si None, usa VARIABLES_PROCESO de config.
    """
    if fuzzy_modelos is None or pend_modelos is None:
        from fuzzys_models_espesador import FUZZY_MODELOS as _FM, PEND_MODELOS as _PM
        fuzzy_modelos = fuzzy_modelos if fuzzy_modelos is not None else _FM
        pend_modelos = pend_modelos if pend_modelos is not None else _PM

    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    if variables_proceso is None:
        variables_proceso = VARIABLES_PROCESO

    if inputs_override is not None:
        inputs = {var: float(inputs_override[var]) for var in variables_proceso}
    else:
        inputs = extraer_inputs_desde_row(row, columnas_entrada, variables_proceso)

    limites_fuzzy = extraer_limites_fuzzy_desde_row(row, columnas_entrada)
    fuzzy_out = evaluar_fuzzys(inputs, limites_fuzzy, fuzzy_modelos)

    t_s = _resolver_float(row, TIME_KEY, columnas_entrada)
    # PEND_MODELOS sigue hardcodeado con las variables del espesador (pendiente
    # conocido). Sin este guard, cualquier contrato que no las incluya reventaba
    # con KeyError en TODOS los ticks. Se evalua la pendiente de las variables
    # que realmente estan en el input; una regla que nombre una tendencia sin
    # modelo queda "no_evaluable" y el motor lo explica en la traza.
    for var in pend_modelos.keys():
        if var not in inputs:
            continue
        fuzzy_out[f"pend_{var}"] = evaluar_pendiente_var(
            var_name=var,
            pv=float(inputs[var]),
            t_s=t_s,
            hist=hist,
            PEND_MODELOS=pend_modelos,
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
    calcular_vars: bool = True,
    dt_s: float | None = None,
    definiciones_calculadas: dict | None = None,
    # --- IT-4: parámetros de proceso (lazy-import si None) ---
    fuzzy_modelos: dict | None = None,
    pend_modelos: dict | None = None,
    variables_proceso: list[str] | None = None,
    # Tablas Sugeno explicitas. Si es None se usa el dict global del modulo
    # defuzzy_actions (compatibilidad con los call sites viejos); pasarlas
    # evita mutar ese global, que el motor en vivo usa en otro hilo.
    defuzzy_por_familia: dict | None = None,
) -> dict:
    """Ejecuta el flujo completo del experto sobre un DataFrame.

    Parámetros de proceso (IT-4)
    ----------------------------
    fuzzy_modelos : dict | None
        Registry {var: {"type": str, "model": obj}} de modelos fuzzy.
        Si None, se carga desde fuzzys_models_espesador.FUZZY_MODELOS.
    pend_modelos : dict | None
        Registry {var: obj} de modelos de pendiente.
        Si None, se carga desde fuzzys_models_espesador.PEND_MODELOS.
    variables_proceso : list[str] | None
        Lista de variables PV a extraer del DataFrame.
        Si None, se usa config.VARIABLES_PROCESO.
    """
    if setpoints_base is None:
        raise ValueError("setpoints_base es obligatorio; no se fija en el nucleo.")
    if limites_sp is None:
        raise ValueError("limites_sp es obligatorio; no se fija en el nucleo.")

    # Resolver defaults de proceso con lazy imports
    if reglas is None:
        from reglas_espesador import REGLAS_ESPESADOR
        reglas = list(REGLAS_ESPESADOR)
    else:
        reglas = list(reglas)

    if fuzzy_modelos is None or pend_modelos is None:
        from fuzzys_models_espesador import FUZZY_MODELOS as _FM, PEND_MODELOS as _PM
        fuzzy_modelos = fuzzy_modelos if fuzzy_modelos is not None else _FM
        pend_modelos = pend_modelos if pend_modelos is not None else _PM

    if variables_proceso is None:
        variables_proceso = VARIABLES_PROCESO

    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada
    permisivos_config = PERMISIVOS if permisivos_config is None else permisivos_config

    # ---- Paso 0: Calcular variables derivadas desde crudas ----
    dt_s_efectivo = None
    if calcular_vars:
        col_t = _resolver_col(TIME_KEY, columnas_entrada)
        dt_s_efectivo = float(dt_s) if dt_s is not None else detectar_dt_s(df_data, col_t=col_t)
        defs = definiciones_calculadas if definiciones_calculadas is not None else DEFINICIONES_CALCULADAS
        df_data = calcular_variables_df(df_data, dt_s=dt_s_efectivo, definiciones=defs)

    # ---- Filtro Exp-Q ----
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
    estado_waits: dict = {}
    setpoints_actuales = dict(setpoints_base)
    rows_resultado = []
    eventos = []

    df_iter = df_data.sort_values(_resolver_col(TIME_KEY, columnas_entrada))
    for _, row in df_iter.iterrows():
        t_s = _resolver_float(row, TIME_KEY, columnas_entrada)
        inputs_raw = extraer_inputs_desde_row(row, columnas_entrada, variables_proceso)

        # El filtro pesa por edad real de la muestra: se le pasa el t_s de la
        # fila para que sobre datos historicos se comporte igual que en vivo.
        inputs = (filtro.actualizar(inputs_raw, t_s=t_s)
                  if filtro is not None else dict(inputs_raw))

        fuzzy_out = _evaluar_estado_fuzzy(
            row,
            hist,
            columnas_entrada=columnas_entrada,
            meta_flags=meta_flags,
            inputs_override=inputs,
            fuzzy_modelos=fuzzy_modelos,
            pend_modelos=pend_modelos,
            variables_proceso=variables_proceso,
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
            estado_waits=estado_waits,
            min_belief=min_belief,
        )
        estado_waits = motor_out["estado_waits"]

        fired = motor_out["fired"]
        if fired:
            for evento in fired:
                acciones_belief = [(a, float(evento["belief"])) for a in evento.get("acciones", [])]
                setpoints_antes = dict(setpoints_actuales)
                # Con tablas explicitas no se toca el dict global del modulo:
                # ese global lo esta usando el motor EN VIVO en otro hilo, y
                # pisarlo desde una simulacion cambiaba el comportamiento del
                # SE en produccion (y abria una ventana con la tabla vacia).
                if defuzzy_por_familia is not None:
                    setpoints_actuales = apply_actions_tabla(
                        acciones_belief, setpoints_actuales, limites_sp,
                        defuzzy_por_familia,
                    )
                else:
                    setpoints_actuales = apply_actions(
                        acciones_con_belief=acciones_belief,
                        setpoints=setpoints_actuales,
                        limites_sp=limites_sp,
                    )
                eventos.append({
                    "t_s": t_s,
                    "t_min": t_s / 60.0,
                    "ventana_idx": int(row.get("ventana_idx", -1)) if "ventana_idx" in row else -1,
                    "ventana_nombre": row.get("ventana_nombre", "") if "ventana_nombre" in row else "",
                    "regla_id": str(evento["id"]),
                    "bloque": str(evento.get("bloque", "")),
                    "n_acciones": len(acciones_belief),
                    "acciones": " | ".join(a for a, _ in acciones_belief),
                    "belief": float(evento["belief"]),
                    "waits_bloqueantes": " | ".join(evento.get("waits_bloqueantes", [])),
                    "waits_reiniciados": " | ".join(evento.get("waits_reiniciados", [])),
                    "waits_activados": " | ".join(evento.get("waits_activados", [])),
                    "variables_controladas": " | ".join(evento.get("variables_controladas", [])),
                    "sp_afectados": " | ".join(
                        SP_FAMILIA_A_KEY.get(f, "") for f in evento.get("variables_controladas", [])
                    ),
                    **{f"antes_{k}": float(v) for k, v in setpoints_antes.items()},
                    **{f"despues_{k}": float(v) for k, v in setpoints_actuales.items()},
                })

        rows_resultado.append({
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
            "variables_controladas_activadas": " | ".join(
                " | ".join(e.get("variables_controladas", [])) for e in fired
            ),
            "waits_activados": " | ".join(" | ".join(e.get("waits_activados", [])) for e in fired),
        })

    df_resultados = pd.DataFrame(rows_resultado)
    df_eventos = pd.DataFrame(eventos)

    if verbose:
        print("=" * 110)
        print("PRUEBA GENERAL DEL SISTEMA EXPERTO ESPESADOR  [v3 -- waits declarativos por accion]")
        print("=" * 110)
        if calcular_vars:
            print(f"Variables calculadas automaticamente (dt_s={dt_s_efectivo:.1f} s)")
        else:
            print("Variables calculadas: DESACTIVADO (columnas preexistentes en el DF)")
        print(f"Filtro Exp-Q: {'activo' if filtro is not None else 'DESACTIVADO'}")
        print(f"Permisivos en configuracion: {len(permisivos_config)}")
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
