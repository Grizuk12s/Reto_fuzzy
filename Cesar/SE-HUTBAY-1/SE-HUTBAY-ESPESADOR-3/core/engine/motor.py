# -*- coding: utf-8 -*-
"""Motor de reglas difusas — lógica genérica pura.

Soporta:
- Operadores lógicos OR / AND / NOT en condiciones de reglas
- Jerarquía de bloques configurable (no independientes y independientes)
- Waits declarativos por acción/regla
- Múltiples acciones en `then`

El dict `bloques` define la jerarquía del proceso y se pasa siempre
como parámetro — este módulo no importa nada específico de ningún proceso.

Estructura de `bloques`:
    {
        "nombre_bloque": {
            "level": int,          # orden de evaluación (menor = más crítico)
            "independent": bool,   # True = siempre evalúa, no bloquea ni es bloqueado
        },
        ...
    }
"""
from __future__ import annotations

from waits import normalizar_accion_regla


# ============================================================
# Helpers de waits
# ============================================================
def _wait_esta_activo(wait_spec: dict, estado_waits: dict, t_s: float) -> bool:
    estado = estado_waits.get(str(wait_spec["wait_id"]))
    if not estado:
        return False
    duracion = float(estado.get("duracion_s", 0.0))
    t_inicio = float(estado.get("t_ultima_activacion_s", -1e18))
    return (float(t_s) - t_inicio) < duracion


def _activar_waits(wait_specs: list[dict], estado_waits: dict, t_s: float,
                   regla_id: str) -> dict:
    """Arma los waits de una regla que acaba de disparar.

    Devuelve el estado PREVIO de cada wait tocado ({wait_id: estado|None}),
    que es lo que `revertir_waits` necesita para deshacer el armado si la
    accion resulto no tener efecto. Ver esa funcion.
    """
    previos: dict = {}
    for wait in wait_specs:
        wid = str(wait["wait_id"])
        previos.setdefault(wid, estado_waits.get(wid))
        estado_waits[wid] = {
            "wait_id": wid,
            "tipo": str(wait.get("tipo", "")),
            "accion": str(wait.get("accion", "")),
            "accion_referencia": wait.get("accion_referencia"),
            "variable_controlada": wait.get("variable_controlada"),
            "descripcion": str(wait.get("descripcion", "")),
            "duracion_s": float(wait.get("duracion_s", 0.0)),
            "t_ultima_activacion_s": float(t_s),
            "regla_id": str(regla_id),
        }
    return previos


def revertir_waits(evento: dict, estado_waits: dict) -> list[str]:
    """Deshace los waits que armo un disparo que NO movio ningun setpoint.

    El wait existe para darle tiempo al proceso a responder a un cambio. Si
    no hubo cambio no hay nada que esperar, y dejar el wait armado silencia a
    la regla durante minutos por una accion que no ocurrio. El caso tipico es
    un SP pegado a su limite: la regla dispara, el defuzzy calcula, el clipeo
    lo deja igual y la regla se auto-bloquea sin haber hecho nada.

    Se restaura el estado EXACTO que tenia cada wait antes del disparo (o se
    borra si no existia), de modo que un wait que ya venia corriendo por otra
    regla conserva su cuenta original.

    Nota de alcance: los waits se arman en el momento del disparo a proposito,
    para que bloqueen a las reglas que se evaluan DESPUES dentro del mismo
    barrido. Revertir despues no le devuelve el turno a una regla que quedo
    bloqueada en ese tick; lo recupera en el siguiente, que en ciclo libre
    llega en decimas de segundo.

    Devuelve los wait_id revertidos.
    """
    previos = (evento or {}).get("waits_previos")
    if not previos:
        return []
    revertidos = []
    for wid, estado in previos.items():
        if estado is None:
            estado_waits.pop(wid, None)
        else:
            estado_waits[wid] = estado
        revertidos.append(wid)
    return revertidos


def _deduplicar_waits(wait_specs: list[dict]) -> list[dict]:
    unicos: dict[str, dict] = {}
    for wait in wait_specs:
        unicos[str(wait["wait_id"])] = dict(wait)
    return list(unicos.values())


# ============================================================
# Evaluación de condiciones con operadores lógicos
# ============================================================
def mu_condicion(fuzzy_out: dict, var: str, label: str) -> float:
    pert = (fuzzy_out.get(str(var), {}) or {}).get("pert", {}) or {}
    return float(pert.get(str(label).upper(), 0.0))


def _resolver_condicion_declarativa(condicion):
    if (
        isinstance(condicion, dict)
        and "condicion" in condicion
        and condicion.get("tipo") in {"estado", "subestado", "fuerza"}
    ):
        return condicion["condicion"]
    return condicion


def evaluar_condicion(condicion, fuzzy_out: dict) -> float:
    condicion = _resolver_condicion_declarativa(condicion)

    if isinstance(condicion, tuple):
        if len(condicion) != 2:
            raise ValueError(f"Tupla de condicion mal formada: {condicion}")
        var, label = condicion
        return mu_condicion(fuzzy_out, var, label)

    if isinstance(condicion, dict):
        if "OR" in condicion:
            items = condicion["OR"]
            if not items:
                return 0.0
            return float(max(evaluar_condicion(c, fuzzy_out) for c in items))
        if "AND" in condicion:
            items = condicion["AND"]
            if not items:
                return 1.0
            return float(min(evaluar_condicion(c, fuzzy_out) for c in items))
        if "NOT" in condicion:
            interna = condicion["NOT"]
            # Fail-CLOSED: una variable que el pipeline no produjo da mu=0, y
            # negar eso daba 1.0 — la regla disparaba SIEMPRE con belief pleno
            # y el permisivo quedaba concedido para siempre. Justo al reves de
            # lo que quiere decir "no se pudo evaluar". Si falta el dato, la
            # condicion vale 0 y el reporte la marca como no_evaluable.
            faltantes = variables_de_condicion(interna) - set(fuzzy_out or {})
            if faltantes:
                return 0.0
            return float(max(0.0, 1.0 - evaluar_condicion(interna, fuzzy_out)))

    if isinstance(condicion, list):
        if not condicion:
            return 1.0
        return float(min(evaluar_condicion(c, fuzzy_out) for c in condicion))

    raise ValueError(f"Condicion no reconocida: {condicion!r}")


def fuerza_activacion(fuzzy_out: dict, condiciones) -> float:
    if condiciones is None:
        return 1.0
    if not condiciones:
        return 1.0
    return float(min(evaluar_condicion(c, fuzzy_out) for c in condiciones))


def fuerza_regla(fuzzy_out: dict, fuerza, fallback: float) -> float:
    if fuerza is None:
        return float(fallback)

    fuerza = _resolver_condicion_declarativa(fuerza)

    if isinstance(fuerza, list):
        if not fuerza:
            return float(fallback)
        return float(max(evaluar_condicion(c, fuzzy_out) for c in fuerza))

    return float(evaluar_condicion(fuerza, fuzzy_out))


# ============================================================
# Acciones de una regla
# ============================================================
def _normalizar_acciones(regla: dict) -> list[dict]:
    acciones = (regla or {}).get("then", [])
    if isinstance(acciones, str):
        acciones = [acciones]
    return [normalizar_accion_regla(a, regla_id=str((regla or {}).get("id", ""))) for a in acciones]


# ============================================================
# Evaluación de un conjunto de reglas (un bloque)
# ============================================================
def variables_de_condicion(condicion) -> set:
    """Variables fuzzy referenciadas por una condicion (recursivo).

    Solo para diagnostico: permite saber si una regla es evaluable con
    las variables disponibles. No participa en ninguna decision.
    """
    condicion = _resolver_condicion_declarativa(condicion)
    if isinstance(condicion, tuple):
        return {str(condicion[0])} if len(condicion) == 2 else set()
    if isinstance(condicion, dict):
        for op in ("OR", "AND"):
            if op in condicion:
                out = set()
                for c in condicion[op]:
                    out |= variables_de_condicion(c)
                return out
        if "NOT" in condicion:
            return variables_de_condicion(condicion["NOT"])
        return set()
    if isinstance(condicion, list):
        out = set()
        for c in condicion:
            out |= variables_de_condicion(c)
        return out
    return set()


def variables_de_regla(regla: dict) -> set:
    """Todas las variables fuzzy que una regla necesita para ser evaluable."""
    out = set()
    for c in (regla or {}).get("if", []) or []:
        out |= variables_de_condicion(c)
    if (regla or {}).get("fuerza") is not None:
        out |= variables_de_condicion(regla["fuerza"])
    return out


def pares_de_condicion(condicion) -> set:
    """Pares (variable, etiqueta) referenciados por una condicion.

    Es `variables_de_condicion` pero conservando la etiqueta. Sirve para
    saber que reglas dependen de una etiqueta CONCRETA de una variable, no
    solo de la variable: borrar o renombrar la fila 'OK' de un fuzzy deja
    muertas las reglas que la nombran, aunque la variable siga existiendo.

    Solo para diagnostico: no participa en ninguna decision.
    """
    condicion = _resolver_condicion_declarativa(condicion)
    if isinstance(condicion, tuple):
        return {(str(condicion[0]), str(condicion[1]).upper())} if len(condicion) == 2 else set()
    if isinstance(condicion, dict):
        for op in ("OR", "AND"):
            if op in condicion:
                out = set()
                for c in condicion[op]:
                    out |= pares_de_condicion(c)
                return out
        if "NOT" in condicion:
            return pares_de_condicion(condicion["NOT"])
        return set()
    if isinstance(condicion, list):
        # Una hoja recien leida de reglas.json todavia es ["var", "ETIQUETA"]:
        # `cargar_reglas_json` la convierte a tupla, pero este helper tambien
        # se llama sobre el JSON crudo. Se aceptan las dos formas.
        if len(condicion) == 2 and all(isinstance(x, str) for x in condicion):
            return {(str(condicion[0]), str(condicion[1]).upper())}
        out = set()
        for c in condicion:
            out |= pares_de_condicion(c)
        return out
    return set()


def pares_de_regla(regla: dict) -> set:
    """Todos los pares (variable, etiqueta) que una regla nombra."""
    out = set()
    for c in (regla or {}).get("if", []) or []:
        out |= pares_de_condicion(c)
    if (regla or {}).get("fuerza") is not None:
        out |= pares_de_condicion(regla["fuerza"])
    return out


def _evaluar_set_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    estado_waits: dict,
    min_belief: float,
    reporte: list | None = None,
) -> list[dict]:
    """Evalua un bloque de reglas.

    `reporte` es opcional y solo para observabilidad: si se pasa una lista,
    se le agrega una entrada por regla explicando si disparo y, si no, por
    que. No altera el resultado ni el orden de evaluacion.
    """
    fired = []
    reglas_ordenadas = sorted(reglas, key=lambda r: float(r.get("priority", 0.0)), reverse=True)

    def _reportar(regla, estado, motivo, **extra):
        """Registra el desenlace de una regla. NUNCA altera el flujo."""
        if reporte is None:
            return
        faltantes = sorted(variables_de_regla(regla) - set(fuzzy_out or {}))
        reporte.append({
            "id": str(regla.get("id", "?")),
            "bloque": str(regla.get("bloque", "estabilidad")),
            "priority": float(regla.get("priority", 0.0)),
            # no_evaluable = le faltan variables; el motivo explica el desenlace
            "estado": ("no_evaluable" if (faltantes and estado != "disparo") else estado),
            "motivo": motivo,
            "variables_faltantes": faltantes,
            **extra,
        })

    for regla in reglas_ordenadas:
        try:
            acciones = _normalizar_acciones(regla)
        except Exception as exc:
            # Una regla mal formada (tipico: un wait sin duracion_s) hacia
            # estallar evaluar_reglas ENTERO desde la primera linea del bucle:
            # se perdia el tick completo, incluidas las reglas criticas de
            # otros bloques, la escritura al DCS y hasta la traza. Ahora la
            # regla rota se aisla y el resto del barrido sigue.
            _reportar(regla, "invalida", f"Regla mal formada: {exc}")
            continue

        mu_activacion = fuerza_activacion(fuzzy_out, regla.get("if", []))
        if mu_activacion <= 0.0:
            _reportar(regla, "descartada", "Activacion (IF) en 0",
                      mu_activacion=round(mu_activacion, 4))
            continue

        mu_fuerza = fuerza_regla(fuzzy_out, regla.get("fuerza"), fallback=mu_activacion)
        belief = float(regla.get("weight", 1.0)) * mu_fuerza
        if belief < float(min_belief):
            _reportar(regla, "descartada",
                      f"Belief {belief:.3f} < minimo {float(min_belief):.3f}",
                      mu_activacion=round(mu_activacion, 4), belief=round(belief, 4))
            continue

        acciones_nombres = [str(a["accion"]) for a in acciones]
        waits_bloqueantes = []
        waits_reinicio = []
        bloqueada = False

        for accion_spec in acciones:
            for wait in accion_spec.get("waits", []):
                waits_bloqueantes.append(wait)
                if _wait_esta_activo(wait, estado_waits, t_s):
                    bloqueada = True
                    break
            if bloqueada:
                break
            waits_reinicio.extend(accion_spec.get("reiniciar_waits", []))

        if bloqueada:
            _reportar(regla, "descartada", "Bloqueada por wait activo",
                      mu_activacion=round(mu_activacion, 4), belief=round(belief, 4),
                      wait_bloqueante=str(waits_bloqueantes[-1]["wait_id"])
                      if waits_bloqueantes else None)
            continue

        waits_bloqueantes = _deduplicar_waits(waits_bloqueantes)
        waits_reinicio = _deduplicar_waits(waits_reinicio)
        waits_activados = _deduplicar_waits(waits_bloqueantes + waits_reinicio)

        evento = {
            "t_s": float(t_s),
            "id": str(regla["id"]),
            "bloque": str(regla.get("bloque", "estabilidad")),
            "acciones": list(acciones_nombres),
            "accion": " | ".join(acciones_nombres),
            "acciones_detalle": list(acciones),
            "belief": belief,
            "mu_activacion": mu_activacion,
            "mu_fuerza": mu_fuerza,
            "priority": float(regla.get("priority", 0.0)),
            "conds": list(regla.get("if", [])),
            "fuerza": regla.get("fuerza"),
            "waits_bloqueantes": [str(w["wait_id"]) for w in waits_bloqueantes],
            "waits_reiniciados": [str(w["wait_id"]) for w in waits_reinicio],
            "waits_activados": [str(w["wait_id"]) for w in waits_activados],
            "variables_controladas": sorted({
                str(w["variable_controlada"])
                for w in waits_activados
                if w.get("variable_controlada") is not None
            }),
        }
        fired.append(evento)

        _reportar(regla, "disparo", "Disparo",
                  mu_activacion=round(mu_activacion, 4), belief=round(belief, 4),
                  acciones=list(acciones_nombres))

        # Se arman aqui para que bloqueen a las reglas que faltan evaluar en
        # este mismo barrido. El estado previo viaja en el evento: si la
        # accion termina no moviendo ningun setpoint, quien la aplica llama a
        # `revertir_waits` y la regla no queda castigada por no haber actuado.
        evento["waits_previos"] = _activar_waits(
            waits_activados, estado_waits, t_s, str(regla["id"]))

    return fired


# ============================================================
# Evaluación completa con jerarquía de bloques
# ============================================================
def evaluar_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    bloques: dict,
    estado_waits: dict | None = None,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
) -> dict:
    """Evalúa reglas respetando la jerarquía de bloques dada.

    Parámetros
    ----------
    reglas : list[dict]
        Reglas a evaluar. Cada regla debe tener 'bloque', 'if', 'then', etc.
    fuzzy_out : dict
        Salida del fuzzificador ({var: {"pert": {label: mu}}}).
    t_s : float
        Timestamp actual en segundos.
    bloques : dict
        Definición de la jerarquía de bloques del proceso:
            {"nombre": {"level": int, "independent": bool}, ...}
    estado_waits : dict | None
        Estado mutable de los waits declarativos.
    min_belief : float
        Umbral mínimo de belief para que una regla dispare.
    """
    if estado_waits is None:
        estado_waits = {} if last_action_time is None else last_action_time

    bloques_a_reglas: dict[str, list[dict]] = {}
    for r in reglas:
        b = str(r.get("bloque", "estabilidad"))
        bloques_a_reglas.setdefault(b, []).append(r)

    bloques_jerarquicos = sorted(
        [b for b, meta in bloques.items() if not meta.get("independent", False)],
        key=lambda b: float(bloques[b].get("level", 99)),
    )
    bloques_independientes = [
        b for b, meta in bloques.items() if meta.get("independent", False)
    ]

    all_fired = []
    bloque_jerarquico_disparo = False
    # Reporte de observabilidad: una entrada por regla con su desenlace.
    evaluadas: list[dict] = []

    for bloque in bloques_jerarquicos:
        reglas_bloque = bloques_a_reglas.get(bloque, [])
        if bloque_jerarquico_disparo:
            # Bloque saltado: un bloque mas critico ya disparo.
            for r in reglas_bloque:
                evaluadas.append({
                    "id": str(r.get("id", "?")),
                    "bloque": bloque,
                    "priority": float(r.get("priority", 0.0)),
                    "estado": "no_evaluada",
                    "motivo": "Bloque omitido: un bloque mas critico ya disparo",
                    "variables_faltantes": [],
                })
            continue
        if not reglas_bloque:
            continue
        fired = _evaluar_set_reglas(reglas_bloque, fuzzy_out, t_s, estado_waits,
                                    min_belief, reporte=evaluadas)
        if fired:
            bloque_jerarquico_disparo = True
            all_fired.extend(fired)

    for bloque in bloques_independientes:
        reglas_bloque = bloques_a_reglas.get(bloque, [])
        if not reglas_bloque:
            continue
        fired = _evaluar_set_reglas(reglas_bloque, fuzzy_out, t_s, estado_waits,
                                    min_belief, reporte=evaluadas)
        all_fired.extend(fired)

    belief_accion: dict[str, float] = {}
    for ev in all_fired:
        for accion in ev["acciones"]:
            belief_accion[accion] = max(float(belief_accion.get(accion, 0.0)), float(ev["belief"]))

    return {
        "fired": all_fired,
        "belief_accion": belief_accion,
        "estado_waits": estado_waits,
        "last_action_time": estado_waits,
        # Aditivo: desenlace de cada regla para la pagina de traza.
        "evaluadas": sorted(evaluadas, key=lambda e: -e["priority"]),
    }


def motor_reglas(
    reglas: list[dict],
    fuzzy_out: dict,
    t_s: float,
    bloques: dict,
    estado_waits: dict | None = None,
    last_action_time: dict | None = None,
    min_belief: float = 0.05,
) -> dict:
    """Alias retrocompatible para pruebas unitarias."""
    return evaluar_reglas(
        reglas=reglas,
        fuzzy_out=fuzzy_out,
        t_s=t_s,
        bloques=bloques,
        estado_waits=estado_waits,
        last_action_time=last_action_time,
        min_belief=min_belief,
    )
