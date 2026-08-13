#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simulador de planta que escribe DIRECTO en los tags PCS7 del KEPserver.

POR QUE EXISTE
--------------
`Simulador_DCS_OPC_UA/dcs_opcua_server.py` es un servidor OPC UA propio: para
que sus valores lleguen al KEPserver hace falta un canal con driver
"OPC UA Client" apuntando a opc.tcp://<ip>:4840/dcs/.

El canal PCS7 que tienes hoy usa el driver **Simulator** con direcciones K
(K1110, K1112, ...). Los registros K son memoria estatica de lectura/escritura:
guardan lo que se les escriba y NO cambian solos. Por eso los ves en 0.

Este script cierra ese hueco sin reconfigurar KEPserver: se conecta como
cliente OPC-UA al propio KEPserver y escribe los tags PCS7 periodicamente,
con dinamica realista. Es independiente del Sistema Experto — dejalo corriendo
en su propia ventana mientras el SE lee.

    [simular_planta_kepserver.py]  --escribe-->  [KEPserver PCS7.OS01.*]
                                                       ^
                                                       | lee
                                                 [SE-Espesador]

USO
---
    python scripts/simular_planta_kepserver.py
    python scripts/simular_planta_kepserver.py --url opc.tcp://127.0.0.1:49320
    python scripts/simular_planta_kepserver.py --periodo 2 --ciclo-min 3
    python scripts/simular_planta_kepserver.py --handshake     # entrega el lazo al SE
    python scripts/simular_planta_kepserver.py --once          # un solo ciclo y sale

Ctrl+C para detener.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOGO = os.path.join(RAIZ, "config", "espesador", "tags_planta_pcs7.json")

# Estado del handshake cuando se corre con --handshake.
# Reproduce "el operador entrego el lazo al experto" del lado del DCS.
HANDSHAKE_LAZO_ENTREGADO = {
    "PCS7.OS01.PU009_Exp_LIC_Auto":   True,    # lazo de nivel en automatico
    "PCS7.OS01.PU009_Exp_LIC_Manual": False,
    "PCS7.OS01.PU009_Lazo_exp":       True,    # selector lazo experto habilitado
    "PCS7.OS01.PU009_Lazo_exp_LIC":   False,
    "PCS7.OS01.PU009_Exp_Enable_FBK": True,    # el DCS confirma la entrega
}

HANDSHAKE_LAZO_LOCAL = {
    "PCS7.OS01.PU009_Exp_LIC_Auto":   True,
    "PCS7.OS01.PU009_Exp_LIC_Manual": False,
    "PCS7.OS01.PU009_Lazo_exp":       False,
    "PCS7.OS01.PU009_Lazo_exp_LIC":   True,
    "PCS7.OS01.PU009_Exp_Enable_FBK": False,
}


def cargar_catalogo() -> list[dict]:
    with open(CATALOGO, encoding="utf-8") as f:
        return json.load(f)["tags"]


def url_por_defecto() -> str:
    """Toma host/puerto de kepserver.json, pero fuerza localhost si dice docker."""
    cfg_path = os.path.join(RAIZ, "config", "espesador", "kepserver.json")
    host, port = "127.0.0.1", 49320
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        host = cfg.get("host") or host
        port = int(cfg.get("port") or port)
    except (OSError, ValueError, TypeError):
        pass
    # host.docker.internal solo resuelve dentro del contenedor.
    if "docker" in host:
        host = "127.0.0.1"
    return f"opc.tcp://{host}:{port}"


class Senal:
    """Una senal analogica con dinamica: seno lento + ruido, acotado a la banda.

    El seno lento imita la deriva del proceso (llenado/vaciado, cambio de
    carga); el ruido imita el transmisor. Cada senal arranca con una fase
    distinta para que no se muevan todas en bloque.
    """

    def __init__(self, nombre: str, lo: float, hi: float, ruido: float):
        self.nombre = nombre
        self.lo, self.hi, self.ruido = float(lo), float(hi), float(ruido)
        self.constante = abs(hi - lo) < 1e-9 and ruido == 0
        self.centro = (self.lo + self.hi) / 2.0
        self.amplitud = (self.hi - self.lo) / 2.0
        self.fase = random.uniform(0, 2 * math.pi)

    def valor(self, t_s: float, ciclo_s: float) -> float:
        if self.constante:
            return self.lo
        # 0.8 deja margen para que el ruido no sature contra el limite
        base = self.centro + 0.8 * self.amplitud * math.sin(
            2 * math.pi * t_s / ciclo_s + self.fase)
        v = base + random.gauss(0, self.ruido)
        return max(self.lo, min(self.hi, v))


def construir_senales(catalogo: list[dict]) -> list[Senal]:
    senales = []
    for item in catalogo:
        if not item.get("generar") or not item.get("rango"):
            continue
        lo, hi, ruido = item["rango"]
        senales.append(Senal(item["name"], lo, hi, ruido))
    return senales


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Escribe valores dinamicos en los tags PCS7 del KEPserver")
    ap.add_argument("--url", default=None,
                    help="Endpoint OPC-UA del KEPserver (default: kepserver.json)")
    ap.add_argument("--periodo", type=float, default=2.0,
                    help="Segundos entre escrituras (default 2)")
    ap.add_argument("--ciclo-min", type=float, default=5.0,
                    help="Minutos que dura un ciclo completo de la senal (default 5)")
    ap.add_argument("--handshake", action="store_true",
                    help="Entrega el lazo al experto (LIC_AUTO + SELECTOR + FBK)")
    ap.add_argument("--sin-sp-experto", action="store_true",
                    help="No escribe PU009_Exp_Speed_Exp (dejaselo al SE)")
    ap.add_argument("--once", action="store_true", help="Un solo ciclo y termina")
    ap.add_argument("-q", "--quiet", action="store_true", help="Menos salida")
    args = ap.parse_args()

    try:
        from opcua import Client, ua  # type: ignore
    except ImportError:
        print("Falta el paquete 'opcua'.  Instalalo con:  pip install opcua")
        return 1

    url = args.url or url_por_defecto()
    catalogo = cargar_catalogo()
    senales = construir_senales(catalogo)

    if args.sin_sp_experto:
        senales = [s for s in senales if not s.nombre.endswith("Exp_Speed_Exp")]

    booleanos = {}
    if args.handshake:
        booleanos = dict(HANDSHAKE_LAZO_ENTREGADO)

    ciclo_s = max(1.0, args.ciclo_min * 60.0)

    print("=" * 68)
    print("  Simulador de planta -> KEPserver")
    print("=" * 68)
    print(f"  Endpoint    : {url}")
    print(f"  Senales     : {len(senales)}  (periodo {args.periodo}s, ciclo {args.ciclo_min} min)")
    print(f"  Handshake   : {'lazo ENTREGADO al experto' if args.handshake else 'no se toca'}")
    print(f"  SP experto  : {'lo maneja el SE' if args.sin_sp_experto else 'lo escribe este script'}")
    print("=" * 68)

    t0 = time.monotonic()
    tick = 0
    client = None

    try:
        while True:
            t_s = time.monotonic() - t0
            valores = {s.nombre: s.valor(t_s, ciclo_s) for s in senales}

            try:
                if client is None:
                    client = Client(url)
                    client.connect()
                    print(f"[{time.strftime('%H:%M:%S')}] Conectado a {url}")

                escritos = 0
                for nombre, val in valores.items():
                    try:
                        node = client.get_node(f"ns=2;s={nombre}")
                        node.set_value(ua.DataValue(
                            ua.Variant(float(val), ua.VariantType.Float)))
                        escritos += 1
                    except Exception as e:
                        if tick == 0:
                            print(f"   ! {nombre}: {e}")

                for nombre, val in booleanos.items():
                    try:
                        node = client.get_node(f"ns=2;s={nombre}")
                        node.set_value(ua.DataValue(
                            ua.Variant(bool(val), ua.VariantType.Boolean)))
                        escritos += 1
                    except Exception as e:
                        if tick == 0:
                            print(f"   ! {nombre}: {e}")

                if not args.quiet:
                    muestra = ["Hopper_Nivel_PV_A", "PU009_Velocidad_PV",
                               "PU009_Corriente", "PIC2391_Presion"]
                    detalle = "  ".join(
                        f"{m.split('_PV')[0][:14]}={valores.get('PCS7.OS01.' + m, 0):7.2f}"
                        for m in muestra)
                    print(f"[{time.strftime('%H:%M:%S')}] tick {tick:>5}  "
                          f"escritos {escritos:>2}/{len(valores) + len(booleanos)}   {detalle}")

            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] Sin conexion ({e}). Reintento...")
                try:
                    if client:
                        client.disconnect()
                except Exception:
                    pass
                client = None

            tick += 1
            if args.once:
                break
            time.sleep(args.periodo)

    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
    finally:
        try:
            if client:
                client.disconnect()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
