# -*- coding: utf-8 -*-
"""
Simulador de DCS como servidor OPC UA.
=======================================

Emula el DCS de planta exponiendo, por OPC UA, las 22 senales de la matriz de
tags (3251-Hopper, 3251-PU-009, 3261-AG-004, 3261-PU-025/026, 3264-PIC-2391,
mas el handshake ENABLE / HEARTBEAT / SELECTOR del bloque PU009_EXPERTO).

Sirve para probar la cadena completa SIN planta:

    [este simulador]  --OPC UA-->  [KEPserver EX: driver "OPC UA Client"]
                                          |
                                          v
                                   [SE-Espesador en la VM Linux]
                                    (se conecta a KEPserver por OPC UA)

LOGICA DE HANDSHAKE SIMULADA
----------------------------
El simulador solo entrega el lazo al experto si se cumplen las 4 condiciones:

    ENABLE_FBK =  ENABLE_EXT                (el SE pide el lazo)
              AND SELECTOR_LAZO_EXPERTO     (el operador lo habilito)
              AND LIC_AUTO                  (el lazo de nivel esta en auto)
              AND heartbeat vivo            (HEART_INT o HEART_BIT cambiaron
                                             en los ultimos N segundos)

Mientras ENABLE_FBK sea False, el SP del PID IGNORA a SPEED_EXPERTO y sigue el
control local. Asi se puede probar el "fail-safe": basta con dejar de escribir
el heartbeat y ver como el DCS simulado retoma el control solo.

Uso
---
    pip install -r requirements.txt
    python dcs_opcua_server.py                      # 0.0.0.0:4840
    python dcs_opcua_server.py --port 4841
    python dcs_opcua_server.py --host 192.168.137.1 --periodo 1.0

Endpoint por defecto:  opc.tcp://<host>:4840/dcs/
Seguridad:             None + Anonymous  (igual que la demo de KEPserver)

NodeIds
-------
Cada senal tiene NodeId string en el namespace 2, identico al tag del DCS:

    ns=2;s=OS_SERVER01::3251LIC1480A/PID.PV_Out#Value

Ademas queda navegable por arbol:
    Objects > DCS > OS_SERVER01 > 3251LIC1480A > PID > PV_Out#Value
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import re
import sys
import time

from asyncua import Server, ua

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGO = os.path.join(HERE, "tags_planta.json")

log = logging.getLogger("dcs-sim")


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------
UA_TIPOS = {
    "Float": ua.VariantType.Float,
    "Double": ua.VariantType.Double,
    "Int32": ua.VariantType.Int32,
    "Boolean": ua.VariantType.Boolean,
}


def cast(tipo: str, v):
    if tipo == "Boolean":
        return bool(v)
    if tipo == "Int32":
        return int(v)
    return float(v)


# ---------------------------------------------------------------------------
# Modelo de proceso + handshake
# ---------------------------------------------------------------------------
class ProcesoEspesador:
    """Simulacion simple pero coherente entre senales.

    - El nivel del hopper sube con la alimentacion y baja con la bomba PU-009.
    - El SP del PID sigue a SPEED_EXPERTO SOLO si ENABLE_FBK esta activo;
      si no, hace control local de nivel sobre el hopper.
    - La velocidad real persigue al SP con retardo de 1er orden.
    - La corriente del motor es funcion de la velocidad.
    - El nivel del TK-004 depende de PU-025/PU-026.
    - La presion 2391 depende de la velocidad de PU-025.
    """

    def __init__(self, periodo: float, watchdog_s: float = 10.0):
        self.dt = periodo
        self.watchdog_s = watchdog_s
        self.t = 0.0

        # --- Estados analogicos del proceso ---
        self.hopper = 62.0
        self.speed_pv = 55.0
        self.sp_pid = 55.0
        self.tk004 = 58.0
        self.pu025 = 62.0
        self.pu026 = 0.0
        self.presion = 310.0
        self.corriente = 95.0
        self.agua_sello = 8.5

        # --- Escrituras externas (las hace el SE via KEPserver) ---
        self.sp_experto = 55.0
        self.speed_max = 90.0
        self.speed_min = 25.0
        self.enable_ext = False
        self.selector_experto = False
        self.heart_int = 0
        self.heart_bit = False

        # --- Estado del DCS ---
        self.lic_auto = True          # el lazo de nivel esta en automatico
        self.enable_fbk = False

        # --- Watchdog del heartbeat ---
        self._hb_prev = (0, False)
        self._hb_ts = time.monotonic()

    # -- utilidades ---------------------------------------------------------
    @staticmethod
    def _clamp(x, lo, hi):
        return max(lo, min(hi, x))

    @property
    def lic_manual(self) -> bool:
        return not self.lic_auto

    @property
    def selector_lic(self) -> bool:
        return not self.selector_experto

    @property
    def heartbeat_vivo(self) -> bool:
        return (time.monotonic() - self._hb_ts) <= self.watchdog_s

    def _refrescar_watchdog(self):
        actual = (self.heart_int, self.heart_bit)
        if actual != self._hb_prev:
            self._hb_prev = actual
            self._hb_ts = time.monotonic()

    # -- paso de simulacion -------------------------------------------------
    def paso(self):
        dt = self.dt
        self.t += dt
        ruido = random.gauss

        # ---- Handshake -----------------------------------------------------
        self._refrescar_watchdog()
        fbk_prev = self.enable_fbk
        self.enable_fbk = bool(
            self.enable_ext
            and self.selector_experto
            and self.lic_auto
            and self.heartbeat_vivo
        )
        if fbk_prev != self.enable_fbk:
            log.warning(
                "ENABLE_FBK -> %s  (ext=%s selector=%s auto=%s heartbeat=%s)",
                self.enable_fbk, self.enable_ext, self.selector_experto,
                self.lic_auto, self.heartbeat_vivo,
            )

        # ---- SP del PID ----------------------------------------------------
        if self.enable_fbk:
            # Modo experto: sigue SPEED_EXPERTO recortado por los limites APC
            objetivo = self._clamp(self.sp_experto, self.speed_min, self.speed_max)
        else:
            # Control local: PID de nivel del hopper contra 60 %
            error = self.hopper - 60.0
            objetivo = self._clamp(55.0 + 1.8 * error, 25.0, 90.0)
        self.sp_pid += (objetivo - self.sp_pid) * min(1.0, dt / 5.0)

        # ---- Velocidad real: 1er orden (tau ~ 12 s) + ruido ----------------
        self.speed_pv += (self.sp_pid - self.speed_pv) * (dt / 12.0)
        self.speed_pv = self._clamp(self.speed_pv + ruido(0, 0.15), 0.0, 100.0)

        # ---- Alimentacion al hopper ----------------------------------------
        alim = 55.0 + 12.0 * math.sin(self.t / 420.0) + ruido(0, 1.2)
        descarga = self.speed_pv * 0.95
        self.hopper = self._clamp(
            self.hopper + (alim - descarga) * dt / 90.0 + ruido(0, 0.05),
            0.0, 100.0,
        )

        # ---- Corriente del motor PU-009 ------------------------------------
        self.corriente = self._clamp(
            18.0 + 1.45 * self.speed_pv + ruido(0, 1.0), 0.0, 200.0
        )

        # ---- Flujo de agua de sello ----------------------------------------
        obj_sello = 8.5 if self.speed_pv > 5.0 else 0.4
        self.agua_sello += (obj_sello - self.agua_sello) * dt / 8.0
        self.agua_sello = self._clamp(self.agua_sello + ruido(0, 0.06), 0.0, 20.0)

        # ---- PU-025 en servicio, PU-026 stand-by ---------------------------
        self.pu025 = self._clamp(
            62.0 + 6.0 * math.sin(self.t / 600.0) + ruido(0, 0.2), 0.0, 100.0
        )
        self.pu026 = self._clamp(self.pu026 + ruido(0, 0.02), 0.0, 100.0)

        # ---- Nivel del cajon 3261-AG-004 -----------------------------------
        entrada_tk = self.speed_pv * 0.9
        salida_tk = (self.pu025 + self.pu026) * 0.58
        self.tk004 = self._clamp(
            self.tk004 + (entrada_tk - salida_tk) * dt / 110.0 + ruido(0, 0.05),
            0.0, 100.0,
        )

        # ---- Presion de descarga -------------------------------------------
        obj_p = 60.0 + 4.1 * self.pu025
        self.presion += (obj_p - self.presion) * dt / 10.0
        self.presion = self._clamp(self.presion + ruido(0, 1.5), 0.0, 600.0)

    # -- mapeo dinamica -> valor -------------------------------------------
    def valor(self, dinamica: str):
        tabla = {
            "hopper_level_a": lambda: self.hopper,
            "hopper_level_b": lambda: self.hopper + random.gauss(0, 0.12),
            "pid_sp": lambda: self.sp_pid,
            "pu009_speed_pv": lambda: self.speed_pv,
            "pu009_corriente": lambda: self.corriente,
            "flujo_agua_sello": lambda: self.agua_sello,
            "tk004_level_a": lambda: self.tk004,
            "tk004_level_b": lambda: self.tk004 + random.gauss(0, 0.15),
            "pu025_speed": lambda: self.pu025,
            "pu026_speed": lambda: self.pu026,
            "presion_2391": lambda: self.presion,
            "enable_fbk": lambda: self.enable_fbk,
            "lic_auto": lambda: self.lic_auto,
            "lic_manual": lambda: self.lic_manual,
            "selector_lic": lambda: self.selector_lic,
        }
        fn = tabla.get(dinamica)
        return fn() if fn else None


# Tags que escribe el mundo exterior -> atributo del modelo
ESCRITOS = {
    "AS301/PU009_EXPERTO.SPEED_EXPERTO": "sp_experto",
    "AS301/PU009_EXPERTO.PU009_SPEED_MAX": "speed_max",
    "AS301/PU009_EXPERTO.PU009_SPEED_MIN": "speed_min",
    "AS301/PU009_EXPERTO.ENABLE_EXT": "enable_ext",
    "AS301/PU009_EXPERTO.SELECTOR_LAZO_EXPERTO": "selector_experto",
    "AS301/PU009_EXPERTO.HEART_INT": "heart_int",
    "AS301/PU009_EXPERTO.HEART_BIT": "heart_bit",
}


# ---------------------------------------------------------------------------
# Construccion del address space
# ---------------------------------------------------------------------------
_SEP = re.compile(r"::|/|\.")


def partes_del_tag(tag: str) -> list[str]:
    """'OS_SERVER01::3251LIC1480A/PID.PV_Out#Value'
       -> ['OS_SERVER01', '3251LIC1480A', 'PID', 'PV_Out#Value']"""
    return [p for p in _SEP.split(tag) if p]


async def construir(server: Server, idx: int, catalogo: dict):
    objects = server.nodes.objects
    raiz = await objects.add_folder(ua.NodeId("DCS", idx), "DCS")

    carpetas: dict[str, object] = {"": raiz}
    nodos: list[tuple] = []

    for t in catalogo["tags"]:
        partes = partes_del_tag(t["tag"])
        ruta, padre = "", raiz
        for p in partes[:-1]:
            ruta = f"{ruta}/{p}"
            if ruta not in carpetas:
                carpetas[ruta] = await padre.add_folder(
                    ua.NodeId(f"FOLDER:{ruta}", idx), p
                )
            padre = carpetas[ruta]

        tipo = t.get("tipo", "Float")
        vtype = UA_TIPOS.get(tipo, ua.VariantType.Float)
        nodo = await padre.add_variable(
            ua.NodeId(t["tag"], idx),                  # NodeId string == tag DCS
            partes[-1],                                 # BrowseName = hoja
            ua.Variant(cast(tipo, t["inicial"]), vtype),
            datatype=ua.NodeId(getattr(ua.ObjectIds, tipo)),
        )
        await nodo.set_writable(True)
        await nodo.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.LocalizedText(
                f"{t['equipo']} | {t['senal']} | {t['comentario']} | "
                f"{t['unidad']} | {t['acceso']}"
            )),
        )
        nodos.append((nodo, t))
        log.info("  [%-3s %-8s] ns=%d;s=%s", t["acceso"], tipo, idx, t["tag"])

    hb = await raiz.add_variable(ua.NodeId("SIM.Heartbeat", idx), "SIM.Heartbeat", 0)
    await hb.set_writable(False)
    return nodos, hb


async def bucle(nodos, hb, proceso: ProcesoEspesador, periodo: float):
    tick = 0
    while True:
        # 1) leer lo que hayan escrito KEPserver / el SE
        for nodo, t in nodos:
            attr = ESCRITOS.get(t["tag"])
            if attr:
                try:
                    setattr(proceso, attr, cast(t["tipo"], await nodo.read_value()))
                except Exception:  # noqa: BLE001
                    pass

        # 2) avanzar el modelo
        proceso.paso()

        # 3) publicar lo que genera el DCS
        for nodo, t in nodos:
            if t["dinamica"] == "externo":
                continue
            v = proceso.valor(t["dinamica"])
            if v is None:
                continue
            if t["tipo"] in ("Float", "Double"):
                lo, hi = t["rango"]
                v = round(max(lo, min(hi, float(v))), 3)
            await nodo.write_value(
                ua.DataValue(ua.Variant(cast(t["tipo"], v),
                                        UA_TIPOS.get(t["tipo"], ua.VariantType.Float)))
            )

        tick += 1
        await hb.write_value(tick)
        if tick % 30 == 0:
            log.info(
                "t=%6.0fs | %s | Hopper %5.1f%% | SP_exp %5.1f | SP %5.1f | "
                "Vel %5.1f%% | I %5.1fA | TK004 %5.1f%% | P %5.0f kPa",
                proceso.t,
                "EXPERTO" if proceso.enable_fbk else "LOCAL  ",
                proceso.hopper, proceso.sp_experto, proceso.sp_pid,
                proceso.speed_pv, proceso.corriente, proceso.tk004, proceso.presion,
            )
        await asyncio.sleep(periodo)


async def main_async(args):
    with open(CATALOGO, encoding="utf-8") as fh:
        catalogo = json.load(fh)

    endpoint = f"opc.tcp://{args.host}:{args.port}/dcs/"

    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("Simulador DCS - RETO Hutbay")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    server.set_security_IDs(["Anonymous"])

    idx = await server.register_namespace(catalogo["namespace_uri"])
    proceso = ProcesoEspesador(
        args.periodo, float(catalogo.get("watchdog_heartbeat_s", 10.0))
    )

    log.info("Namespace index = %d  (%s)", idx, catalogo["namespace_uri"])
    log.info("Nodos creados:")
    nodos, hb = await construir(server, idx, catalogo)

    async with server:
        log.info("=" * 78)
        log.info("Servidor OPC UA activo en: %s", endpoint)
        log.info("Seguridad: None | Usuario: Anonymous | Periodo: %.1fs", args.periodo)
        log.info("Watchdog heartbeat: %.1fs", proceso.watchdog_s)
        log.info("Para tomar el lazo: ENABLE_EXT=1, SELECTOR_LAZO_EXPERTO=1 y")
        log.info("escribir HEART_INT periodicamente -> ENABLE_FBK pasa a 1.")
        log.info("Ctrl+C para detener.")
        log.info("=" * 78)
        await bucle(nodos, hb, proceso, args.periodo)


def main():
    ap = argparse.ArgumentParser(description="Simulador DCS -> servidor OPC UA")
    ap.add_argument("--host", default="0.0.0.0",
                    help="IP de escucha (default 0.0.0.0 = todas las interfaces)")
    ap.add_argument("--port", type=int, default=4840, help="Puerto TCP (default 4840)")
    ap.add_argument("--periodo", type=float, default=1.0,
                    help="Periodo de actualizacion en segundos (default 1.0)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Log de asyncua")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("asyncua").setLevel(
        logging.INFO if args.verbose else logging.WARNING
    )

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.info("Simulador detenido por el usuario.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
