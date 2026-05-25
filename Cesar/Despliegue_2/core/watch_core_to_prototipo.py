from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from sync_core_to_prototipo import (
    AUTO_ADAPT_RULES,
    AUTO_SYNC_RULES,
    CORE_DIR,
    DEFAULT_REPORT_ROOT,
    DEFAULT_TARGET_NAME,
    MANUAL_REVIEW_RULES,
)


WORKSPACE_ROOT = Path(__file__).resolve().parent
SYNC_SCRIPT = WORKSPACE_ROOT / "sync_core_to_prototipo.py"
DEFAULT_INTERVAL_SECONDS = 2.0
MANUAL_REVIEW_REASON_BY_NAME = {name: reason for name, reason in MANUAL_REVIEW_RULES}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Monitorea archivos sincronizables dentro de core/ y ejecuta la sincronizacion "
            "hacia el prototipo standalone cuando detecta cambios."
        )
    )
    parser.add_argument(
        "--target-name",
        default=DEFAULT_TARGET_NAME,
        help=f"Nombre de la carpeta prototipo destino. Valor por defecto: {DEFAULT_TARGET_NAME}.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Intervalo de sondeo en segundos. Valor por defecto: {DEFAULT_INTERVAL_SECONDS}.",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Genera reporte Markdown cada vez que ejecuta la sincronizacion.",
    )
    parser.add_argument(
        "--include-manual-review",
        action="store_true",
        help="Tambien vigila archivos manual-review para emitir alertas y rerun de sync.",
    )
    parser.add_argument(
        "--skip-initial-sync",
        action="store_true",
        help="No ejecuta la sincronizacion inicial al arrancar el watcher.",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Ejecuta solo la sincronizacion inicial y termina.",
    )
    parser.add_argument(
        "--manual-review-report-root",
        type=Path,
        default=DEFAULT_REPORT_ROOT,
        help="Directorio raiz donde se escriben alertas Markdown por cambios manual-review.",
    )
    return parser


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def build_watch_paths(include_manual_review: bool) -> list[Path]:
    rules = [*AUTO_SYNC_RULES, *AUTO_ADAPT_RULES]
    if include_manual_review:
        rules.extend(MANUAL_REVIEW_RULES)
    return [CORE_DIR / name for name, _ in rules]


def snapshot(paths: list[Path]) -> dict[Path, tuple[int, int] | None]:
    state: dict[Path, tuple[int, int] | None] = {}
    for path in paths:
        if path.is_file():
            stat = path.stat()
            state[path] = (stat.st_mtime_ns, stat.st_size)
        else:
            state[path] = None
    return state


def diff_paths(
    previous: dict[Path, tuple[int, int] | None],
    current: dict[Path, tuple[int, int] | None],
) -> list[Path]:
    changed: list[Path] = []
    for path, current_state in current.items():
        if previous.get(path) != current_state:
            changed.append(path)
    return changed


def run_sync(target_name: str, write_report: bool) -> int:
    command = [sys.executable, str(SYNC_SCRIPT), "--apply", "--target-name", target_name]
    if write_report:
        command.append("--write-report")
    log(f"Ejecutando sincronizacion hacia {target_name}...")
    completed = subprocess.run(command, cwd=WORKSPACE_ROOT)
    log(f"Sincronizacion terminada con codigo {completed.returncode}.")
    return completed.returncode


def write_manual_review_report(
    report_root: Path,
    target_name: str,
    changed_paths: list[Path],
) -> Path:
    timestamp = datetime.now()
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"manual_review_alert_{timestamp.strftime('%Y%m%d_%H%M%S')}.md"

    lines = [
        "# Alerta de revision manual core -> " + target_name,
        "",
        "## Resumen",
        "",
        f"- Fecha: `{timestamp.isoformat(timespec='seconds')}`",
        f"- Directorio canonico: `{CORE_DIR}`",
        f"- Directorio objetivo: `{WORKSPACE_ROOT / target_name}`",
        "- Tipo de evento: `manual-review`",
        "- Accion automatica: `sin sincronizacion`",
        "",
        "## Archivos detectados",
        "",
    ]

    for path in changed_paths:
        reason = MANUAL_REVIEW_REASON_BY_NAME.get(path.name, "Revision manual requerida.")
        lines.append(f"- `{path.relative_to(WORKSPACE_ROOT)}`: {reason}")

    lines.extend(
        [
            "",
            "## Accion recomendada",
            "",
            "- Revisar la divergencia entre `core/reglas_estrategia_correcta.py` y `Prototipo_2/reglas.json`.",
            "- Decidir manualmente si el fallback Python debe copiarse, migrarse o dejarse sin cambios.",
        ]
    )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.interval <= 0:
        parser.error("--interval debe ser mayor que 0.")

    watch_paths = build_watch_paths(include_manual_review=args.include_manual_review)
    syncable_names = {name for name, _ in (*AUTO_SYNC_RULES, *AUTO_ADAPT_RULES)}
    manual_review_names = {name for name, _ in MANUAL_REVIEW_RULES}
    log(f"Watcher core -> {args.target_name}")
    log("Archivos monitorizados:")
    for path in watch_paths:
        print(f"- {path.relative_to(WORKSPACE_ROOT)}", flush=True)

    current_snapshot = snapshot(watch_paths)

    if not args.skip_initial_sync:
        run_sync(target_name=args.target_name, write_report=args.write_report)
        current_snapshot = snapshot(watch_paths)

    if args.run_once:
        return 0

    try:
        while True:
            time.sleep(args.interval)
            new_snapshot = snapshot(watch_paths)
            changed = diff_paths(current_snapshot, new_snapshot)
            if not changed:
                continue

            log("Cambios detectados en core:")
            for path in changed:
                print(f"- {path.relative_to(WORKSPACE_ROOT)}", flush=True)

            syncable_changed = [path for path in changed if path.name in syncable_names]
            manual_review_changed = [path for path in changed if path.name in manual_review_names]

            if manual_review_changed:
                log("ALERTA: cambio detectado en archivo de revision manual.")
                report_path = write_manual_review_report(
                    report_root=args.manual_review_report_root,
                    target_name=args.target_name,
                    changed_paths=manual_review_changed,
                )
                log(f"Reporte de revision manual generado en: {report_path}")

            if syncable_changed:
                run_sync(target_name=args.target_name, write_report=args.write_report)
            else:
                log("No se ejecuta sincronizacion automatica porque los cambios detectados requieren revision manual.")

            current_snapshot = snapshot(watch_paths)
    except KeyboardInterrupt:
        log("Watcher detenido por el usuario.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())