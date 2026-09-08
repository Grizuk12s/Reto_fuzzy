#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Siembra los tags PCS7 de planta en config/espesador/tags.json.

Se ejecuta al levantar el contenedor (ver docker-entrypoint.sh). Es
IDEMPOTENTE y CONSERVADOR:

  - Si el tag ya existe, NO lo toca. Nunca pisa una categoria ni un rol
    que hayas asignado a mano en la pagina de Tags.
  - Suspende (enabled=false) los tags `RETO.*` de la maqueta anterior,
    salvo que ya los hayas suspendido o reactivado tu.
  - Carga `generator.ranges` solo para las senales con tipo (PV, Maximo,
    Minimo, SP, SP_experto). Las filas sin senal del Excel — handshake,
    heartbeat y selectores — quedan fuera: son booleanos de protocolo y
    llenarlos de ruido rompe el enclavamiento del lazo experto.

Uso:
    python scripts/seed_tags_planta.py              # siembra
    python scripts/seed_tags_planta.py --dry-run    # muestra sin escribir
    python scripts/seed_tags_planta.py --reactivar-reto   # no suspende RETO.*
"""
from __future__ import annotations

import argparse
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from core.jsonio import escribir_json_atomico  # noqa: E402  (necesita RAIZ en sys.path)

CATALOGO = os.path.join(RAIZ, "config", "espesador", "tags_planta_pcs7.json")
TAGS_JSON = os.path.join(RAIZ, "config", "espesador", "tags.json")


def cargar(path: str, defecto=None):
    if not os.path.exists(path):
        return defecto
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sembrar(dry_run: bool = False, suspender_reto: bool = True) -> dict:
    catalogo = cargar(CATALOGO)
    if not catalogo:
        raise SystemExit(f"No se encontro el catalogo: {CATALOGO}")

    store = cargar(TAGS_JSON, {"tags": [], "next_id": 1}) or {"tags": [], "next_id": 1}
    store.setdefault("tags", [])
    store.setdefault("next_id", 1)
    existentes = {t["name"] for t in store["tags"]}

    agregados, omitidos, suspendidos = [], [], []
    ranges = {}

    for item in catalogo["tags"]:
        nombre = item["name"]

        # Ranges del generador: solo las senales con tipo.
        if item.get("generar") and item.get("rango"):
            lo, hi, ruido = item["rango"]
            ranges[nombre] = {"min": lo, "max": hi, "noise": ruido}

        if nombre in existentes:
            omitidos.append(nombre)
            continue

        store["tags"].append({
            "id": store["next_id"],
            "name": nombre,
            "data_type": item.get("data_type", "Float"),
            "enabled": True,
            "categoria": item.get("categoria") or "otro",
            # El rol se asigna a mano: ninguna senal de planta calza sola
            # con el contrato del espesador.
            "rol": "",
            "pseudonimo": item.get("comentario", ""),
            "unidad_ing": item.get("unidad", ""),
            "equipo": item.get("equipo", ""),
            "instrumento": "",
        })
        store["next_id"] += 1
        agregados.append(nombre)

    # Suspender la maqueta RETO.* para que no compita con las senales reales.
    if suspender_reto:
        for t in store["tags"]:
            if t["name"].startswith("RETO.") and t.get("enabled", True):
                t["enabled"] = False
                suspendidos.append(t["name"])

    # Los ranges de la maqueta ya no aplican: se reemplazan por los PCS7.
    gen = store.setdefault("generator", {})
    gen.setdefault("intervalo_s", 5.0)
    gen.setdefault("n_ciclo", 60)
    gen["ranges"] = ranges

    # Heartbeat apuntando a los tags reales del handshake del DCS.
    hb = store.setdefault("heartbeat", {})
    if hb.get("tag_out", "").startswith("RETO.") or not hb.get("tag_out"):
        hb["tag_out"] = "PCS7.OS01.PU009_Exp_HB"
        hb["tag_in"] = "PCS7.OS01.PU009_Exp_Enable_FBK"
        hb.setdefault("intervalo_s", 2.0)
        hb.setdefault("value_a", 0.0)
        hb.setdefault("value_b", 1.0)
        hb["data_type"] = "Boolean"
        hb.setdefault("enabled", True)

    if not dry_run:
        escribir_json_atomico(TAGS_JSON, store)

    return {
        "agregados": agregados,
        "omitidos": omitidos,
        "suspendidos": suspendidos,
        "ranges": len(ranges),
        "sin_generar": [i["name"] for i in catalogo["tags"] if not i.get("generar")],
    }


def main():
    ap = argparse.ArgumentParser(description="Siembra los tags PCS7 de planta")
    ap.add_argument("--dry-run", action="store_true", help="No escribe, solo informa")
    ap.add_argument("--reactivar-reto", action="store_true",
                    help="No suspende los tags RETO.* de la maqueta")
    args = ap.parse_args()

    r = sembrar(dry_run=args.dry_run, suspender_reto=not args.reactivar_reto)

    print("=" * 62)
    print("  Seed de tags PCS7" + ("  [DRY-RUN]" if args.dry_run else ""))
    print("=" * 62)
    print(f"  Agregados        : {len(r['agregados'])}")
    for n in r["agregados"]:
        print(f"      + {n}")
    print(f"  Ya existian      : {len(r['omitidos'])}  (no se tocaron)")
    print(f"  RETO.* suspendidos: {len(r['suspendidos'])}")
    print(f"  Ranges cargados  : {r['ranges']}")
    print(f"  Sin generar      : {len(r['sin_generar'])}  (filas sin senal: handshake/heartbeat)")
    for n in r["sin_generar"]:
        print(f"      - {n}")
    print()
    print("  Falta asignar el ROL de cada tag en /espesador (pagina Tags KEPserver).")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
