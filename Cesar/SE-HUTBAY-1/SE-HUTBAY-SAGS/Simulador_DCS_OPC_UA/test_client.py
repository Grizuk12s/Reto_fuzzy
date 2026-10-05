# -*- coding: utf-8 -*-
"""
Cliente de verificacion del simulador DCS.

Comprueba, sin necesidad de KEPserver, que:
  1) el endpoint OPC UA acepta conexion anonima sin seguridad,
  2) los 22 NodeIds del catalogo existen y devuelven valor + calidad Good,
  3) el handshake completo funciona: ENABLE_EXT + SELECTOR + heartbeat ->
     ENABLE_FBK=1 -> el SP del PID empieza a seguir a SPEED_EXPERTO,
  4) el fail-safe funciona: al dejar de escribir el heartbeat, ENABLE_FBK cae
     y el DCS simulado retoma el control local.

Uso:
    python test_client.py                                   # solo lectura
    python test_client.py --url opc.tcp://192.168.137.1:4840/dcs/
    python test_client.py --handshake --escribir 78         # prueba completa
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from asyncua import Client, ua

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGO = os.path.join(HERE, "tags_planta.json")

T = {
    "sp_exp":   "AS301/PU009_EXPERTO.SPEED_EXPERTO",
    "enable":   "AS301/PU009_EXPERTO.ENABLE_EXT",
    "fbk":      "AS301/PU009_EXPERTO.ENABLE_FBK",
    "selector": "AS301/PU009_EXPERTO.SELECTOR_LAZO_EXPERTO",
    "heart":    "AS301/PU009_EXPERTO.HEART_INT",
    "sp_pid":   "OS_SERVER01::3251LIC1480B/PID.SP#Value",
    "vel_pv":   "OS_SERVER01::3251PU009/M.PV#Value",
    "hopper":   "OS_SERVER01::3251LIC1480A/PID.PV_Out#Value",
}


async def leer_todo(cli, idx, catalogo) -> int:
    print(f"{'TAG':<58} {'VALOR':>10}  {'UNI':<7} {'AC':<3} CALIDAD")
    print("-" * 100)
    fallos = 0
    for t in catalogo["tags"]:
        nodo = cli.get_node(ua.NodeId(t["tag"], idx))
        try:
            dv = await nodo.read_data_value()
            ok = dv.StatusCode.is_good()
            fallos += 0 if ok else 1
            v = dv.Value.Value
            txt = f"{float(v):>10.3f}" if isinstance(v, (int, float)) and \
                not isinstance(v, bool) else f"{str(v):>10}"
            print(f"{t['tag']:<58} {txt}  {t['unidad']:<7} {t['acceso']:<3} "
                  f"{'Good' if ok else dv.StatusCode}")
        except Exception as exc:  # noqa: BLE001
            fallos += 1
            print(f"{t['tag']:<58} {'ERROR':>10}  {t['unidad']:<7} "
                  f"{t['acceso']:<3} {exc}")
    print("-" * 100)
    print(f"{len(catalogo['tags'])} tags leidos, {fallos} con problema.\n")
    return fallos


async def handshake(cli, idx, valor_sp: float, segundos: int):
    n = {k: cli.get_node(ua.NodeId(v, idx)) for k, v in T.items()}

    async def w(k, v, vt):
        await n[k].write_value(ua.DataValue(ua.Variant(v, vt)))

    B, F, I = ua.VariantType.Boolean, ua.VariantType.Float, ua.VariantType.Int32

    print("== FASE 1: pedir el lazo sin habilitacion del operador ==")
    await w("enable", True, B)
    await asyncio.sleep(2)
    print(f"  ENABLE_EXT=1, SELECTOR=0 -> ENABLE_FBK={await n['fbk'].read_value()}"
          "   (correcto: el DCS no entrega el lazo)\n")

    print("== FASE 2: operador habilita + heartbeat vivo ==")
    await w("selector", True, B)
    await w("sp_exp", float(valor_sp), F)
    contador = 0
    for i in range(segundos):
        contador += 1
        await w("heart", contador, I)
        await asyncio.sleep(1)
        print(f"  t+{i+1:>2}s  FBK={int(await n['fbk'].read_value())}  "
              f"SP_exp={await n['sp_exp'].read_value():6.2f}  "
              f"SP_PID={await n['sp_pid'].read_value():6.2f}  "
              f"Vel={await n['vel_pv'].read_value():6.2f}  "
              f"Hopper={await n['hopper'].read_value():6.2f}")

    print("\n== FASE 3: fail-safe -- se corta el heartbeat ==")
    for i in range(14):
        await asyncio.sleep(1)
        fbk = int(await n["fbk"].read_value())
        print(f"  t+{i+1:>2}s  FBK={fbk}  "
              f"SP_PID={await n['sp_pid'].read_value():6.2f}  "
              f"Vel={await n['vel_pv'].read_value():6.2f}"
              f"{'   <-- el DCS retomo el control local' if not fbk else ''}")
        if not fbk and i > 2:
            break

    await w("enable", False, B)
    await w("selector", False, B)
    print("\nHandshake liberado (ENABLE_EXT=0, SELECTOR=0).")


async def run(args):
    with open(CATALOGO, encoding="utf-8") as fh:
        catalogo = json.load(fh)

    async with Client(url=args.url) as cli:
        idx = await cli.get_namespace_index(catalogo["namespace_uri"])
        print(f"Conectado a {args.url}   (namespace index = {idx})\n")
        fallos = await leer_todo(cli, idx, catalogo)
        if args.handshake:
            await handshake(cli, idx, args.escribir, args.segundos)
        return 1 if fallos else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="opc.tcp://127.0.0.1:4840/dcs/")
    ap.add_argument("--handshake", action="store_true",
                    help="Ejecuta la prueba de toma de lazo y fail-safe")
    ap.add_argument("--escribir", type=float, default=78.0,
                    help="Valor de SPEED_EXPERTO durante la prueba")
    ap.add_argument("--segundos", type=int, default=12,
                    help="Segundos con el heartbeat vivo")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
