from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent
CORE_DIR = WORKSPACE_ROOT / "core"
PROTOTIPO_DIR = WORKSPACE_ROOT / "Prototipo_1"
DEFAULT_BACKUP_ROOT = WORKSPACE_ROOT / ".sync_backups"
DEFAULT_REPORT_ROOT = WORKSPACE_ROOT / ".sync_reports"

AUTO_SYNC_RULES: tuple[tuple[str, str], ...] = (
    ("config.py", "Contrato de datos comun al motor."),
    ("defuzzy_actions.py", "Traduccion de acciones compartida."),
    ("fuzzys_eval.py", "Evaluacion fuzzy compartida."),
    ("fuzzys_templates.py", "Fabricas de modelos fuzzy compartidas."),
    ("motor.py", "Motor de reglas compartido."),
)

MANUAL_REVIEW_RULES: tuple[tuple[str, str], ...] = (
    ("fuzzys_models_1A.py", "Requiere adaptar imports relativos para el modo standalone."),
    ("runner.py", "Debe preservar soporte de reglas.json y usar_reglas_json."),
    ("reglas_estrategia_correcta.py", "Solo es fallback; revisar tambien la migracion de reglas.json."),
)

PROTECTED_ONLY: tuple[tuple[str, str], ...] = (
    ("app.py", "UI y API Flask propias de Prototipo_1."),
    ("simulacion.py", "Simulacion y parametros de prueba propios del prototipo."),
    ("reglas.json", "Fuente activa de reglas en vivo del prototipo."),
    ("requirements.txt", "Dependencias del modo standalone."),
    ("README.txt", "Documentacion operativa del prototipo."),
    ("proyecto_contexto.md", "Contexto funcional del prototipo."),
)


@dataclass(frozen=True)
class PairStatus:
    name: str
    source: Path
    target: Path
    reason: str
    source_exists: bool
    target_exists: bool
    state: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sincroniza de forma segura la lista blanca desde core/ hacia Prototipo_1/. "
            "Por defecto solo reporta cambios; usa --apply para copiar archivos."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica la sincronizacion de la lista blanca y crea backups de los archivos reemplazados.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Sale con codigo 1 si hay archivos auto-sync desalineados.",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=DEFAULT_BACKUP_ROOT,
        help="Directorio raiz para backups al usar --apply.",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Genera un reporte Markdown de la ejecucion en .sync_reports/ o en la ruta indicada.",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=DEFAULT_REPORT_ROOT,
        help="Directorio raiz para reportes Markdown al usar --write-report.",
    )
    return parser


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_pair(name: str, reason: str) -> PairStatus:
    source = CORE_DIR / name
    target = PROTOTIPO_DIR / name
    source_exists = source.is_file()
    target_exists = target.is_file()

    if not source_exists:
        state = "missing-source"
    elif not target_exists:
        state = "missing-target"
    elif sha256_file(source) == sha256_file(target):
        state = "same"
    else:
        state = "different"

    return PairStatus(
        name=name,
        source=source,
        target=target,
        reason=reason,
        source_exists=source_exists,
        target_exists=target_exists,
        state=state,
    )


def build_pair_statuses(rules: tuple[tuple[str, str], ...]) -> list[PairStatus]:
    return [compare_pair(name, reason) for name, reason in rules]


def format_state(status: PairStatus, sync_mode: bool) -> str:
    if status.state == "protected":
        return "protected"
    if status.state == "same":
        return "up-to-date"
    if status.state == "different":
        return "will-update" if sync_mode else "out-of-sync"
    if status.state == "missing-target":
        return "will-create" if sync_mode else "missing-in-prototipo"
    return "missing-in-core"


def print_section(title: str, statuses: list[PairStatus], sync_mode: bool) -> None:
    print(title)
    print("-" * len(title))
    if not statuses:
        print("  (sin elementos)")
        print()
        return

    for status in statuses:
        label = format_state(status, sync_mode)
        print(f"- {status.name}: {label}")
        print(f"  {status.reason}")
    print()


def ensure_layout() -> list[str]:
    errors: list[str] = []
    if not CORE_DIR.is_dir():
        errors.append(f"No se encontro el directorio canonico: {CORE_DIR}")
    if not PROTOTIPO_DIR.is_dir():
        errors.append(f"No se encontro el directorio del prototipo: {PROTOTIPO_DIR}")
    return errors


def ensure_backup_dir(root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / timestamp / "Prototipo_1"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def ensure_report_path(root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"sync_report_{timestamp}.md"


def copy_with_backup(statuses: list[PairStatus], backup_root: Path) -> tuple[list[str], Path | None]:
    changed = [status for status in statuses if status.state in {"different", "missing-target"}]
    if not changed:
        return [], None

    backup_dir: Path | None = None
    applied: list[str] = []
    for status in changed:
        status.target.parent.mkdir(parents=True, exist_ok=True)
        if status.target.exists():
            if backup_dir is None:
                backup_dir = ensure_backup_dir(backup_root)
            shutil.copy2(status.target, backup_dir / status.name)
        shutil.copy2(status.source, status.target)
        applied.append(status.name)
    return applied, backup_dir


def markdown_table(title: str, statuses: list[PairStatus], sync_mode: bool) -> list[str]:
    lines = [f"## {title}", "", "| Archivo | Estado | Motivo |", "|---|---|---|"]
    if not statuses:
        lines.append("| _(sin elementos)_ | - | - |")
        lines.append("")
        return lines

    for status in statuses:
        state = format_state(status, sync_mode)
        reason = status.reason.replace("|", "\\|")
        lines.append(f"| `{status.name}` | `{state}` | {reason} |")
    lines.append("")
    return lines


def write_markdown_report(
    report_root: Path,
    mode: str,
    auto_statuses: list[PairStatus],
    manual_statuses: list[PairStatus],
    protected_statuses: list[PairStatus],
    pending: list[PairStatus],
    applied: list[str],
    backup_dir: Path | None,
    exit_code: int,
) -> Path:
    report_path = ensure_report_path(report_root)
    timestamp = datetime.now().isoformat(timespec="seconds")
    summary_state = "sin cambios" if not pending and not applied else "con pendientes" if pending and not applied else "aplicado"

    lines = [
        "# Reporte de sincronizacion core -> Prototipo_1",
        "",
        "## Resumen",
        "",
        f"- Fecha: `{timestamp}`",
        f"- Modo: `{mode}`",
        f"- Estado resumido: `{summary_state}`",
        f"- Codigo de salida esperado: `{exit_code}`",
        f"- Directorio canonico: `{CORE_DIR}`",
        f"- Directorio objetivo: `{PROTOTIPO_DIR}`",
        "",
        "## Resultado",
        "",
        f"- Archivos auto-sync evaluados: `{len(auto_statuses)}`",
        f"- Archivos auto-sync pendientes: `{len(pending)}`",
        f"- Archivos aplicados en esta ejecucion: `{len(applied)}`",
        f"- Archivos con revision manual: `{len(manual_statuses)}`",
        f"- Archivos protegidos: `{len(protected_statuses)}`",
    ]

    if applied:
        lines.extend(["", "### Cambios aplicados", ""])
        for name in applied:
            lines.append(f"- `{name}`")

    if backup_dir is not None:
        lines.extend(["", f"- Backups: `{backup_dir}`"])

    lines.extend(["", *markdown_table("Archivos auto-sync", auto_statuses, sync_mode=(mode == "apply"))])
    lines.extend(markdown_table("Archivos con revision manual", manual_statuses, sync_mode=False))
    lines.extend(markdown_table("Archivos protegidos del prototipo", protected_statuses, sync_mode=False))

    if manual_statuses:
        lines.extend(["## Revision manual posterior", ""])
        for status in manual_statuses:
            lines.append(f"- `{status.name}`: {status.reason}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    layout_errors = ensure_layout()
    if layout_errors:
        for error in layout_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    auto_statuses = build_pair_statuses(AUTO_SYNC_RULES)
    manual_statuses = build_pair_statuses(MANUAL_REVIEW_RULES)

    protected_statuses = [
        PairStatus(
            name=name,
            source=CORE_DIR / name,
            target=PROTOTIPO_DIR / name,
            reason=reason,
            source_exists=(CORE_DIR / name).is_file(),
            target_exists=(PROTOTIPO_DIR / name).is_file(),
            state="protected",
        )
        for name, reason in PROTECTED_ONLY
    ]

    print("Sincronizacion segura core -> Prototipo_1")
    print("========================================")
    print(f"Modo: {'apply' if args.apply else 'dry-run'}")
    print()

    print_section("Archivos auto-sync", auto_statuses, sync_mode=args.apply)
    print_section("Archivos con revision manual", manual_statuses, sync_mode=False)
    print_section("Archivos protegidos del prototipo", protected_statuses, sync_mode=False)

    pending = [status for status in auto_statuses if status.state in {"different", "missing-target"}]
    missing_sources = [status for status in auto_statuses if status.state == "missing-source"]
    applied: list[str] = []
    backup_dir: Path | None = None
    exit_code = 0

    if missing_sources:
        print("No se puede aplicar la sincronizacion porque faltan archivos canonicos en core/.")
        exit_code = 2
    elif args.apply:
        applied, backup_dir = copy_with_backup(auto_statuses, args.backup_root)
        if applied:
            print("Cambios aplicados:")
            for name in applied:
                print(f"- {name}")
            if backup_dir is not None:
                print()
                print(f"Backups guardados en: {backup_dir}")
        else:
            print("No habia cambios pendientes en la lista blanca.")
    else:
        if pending:
            print("Dry-run: hay archivos auto-sync pendientes de actualizar.")
        else:
            print("Dry-run: la lista blanca ya esta sincronizada.")

    if manual_statuses:
        print()
        print("Revision manual recomendada despues de sincronizar:")
        for status in manual_statuses:
            print(f"- {status.name}: {status.state}")

    if exit_code == 0 and args.check and pending:
        exit_code = 1

    if args.write_report:
        report_path = write_markdown_report(
            report_root=args.report_root,
            mode="apply" if args.apply else "dry-run",
            auto_statuses=auto_statuses,
            manual_statuses=manual_statuses,
            protected_statuses=protected_statuses,
            pending=pending,
            applied=applied,
            backup_dir=backup_dir,
            exit_code=exit_code,
        )
        print()
        print(f"Reporte Markdown generado en: {report_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())