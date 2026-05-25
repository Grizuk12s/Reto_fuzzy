from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


WORKSPACE_ROOT = Path(__file__).resolve().parent
CORE_DIR = WORKSPACE_ROOT / "core"
DEFAULT_TARGET_NAME = "Prototipo_2"
DEFAULT_BACKUP_ROOT = WORKSPACE_ROOT / ".sync_backups"
DEFAULT_REPORT_ROOT = WORKSPACE_ROOT / ".sync_reports"

AUTO_SYNC_RULES: tuple[tuple[str, str], ...] = (
    ("config.py", "Contrato de datos comun al motor."),
    ("defuzzy_actions.py", "Traduccion de acciones compartida."),
    ("fuzzys_eval.py", "Evaluacion fuzzy compartida."),
    ("fuzzys_templates.py", "Fabricas de modelos fuzzy compartidas."),
    ("motor.py", "Motor de reglas compartido."),
)

AUTO_ADAPT_RULES: tuple[tuple[str, str], ...] = (
    ("fuzzys_models_1A.py", "Se genera desde core adaptando imports para el modo standalone."),
    ("runner.py", "Se genera desde core y preserva reglas.json para el modo standalone."),
)

MANUAL_REVIEW_RULES: tuple[tuple[str, str], ...] = (
    ("reglas_estrategia_correcta.py", "Solo es fallback; revisar tambien la migracion de reglas.json."),
)

PROTECTED_ONLY: tuple[tuple[str, str], ...] = (
    ("app.py", "UI y API Flask propias del prototipo standalone."),
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
    strategy: str
    source_exists: bool
    target_exists: bool
    state: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            f"Sincroniza de forma segura la lista blanca desde core/ hacia {DEFAULT_TARGET_NAME}/. "
            "Por defecto solo reporta cambios; usa --apply para copiar archivos."
        )
    )
    parser.add_argument(
        "--target-name",
        default=DEFAULT_TARGET_NAME,
        help=(
            "Nombre de la carpeta prototipo destino bajo el workspace root. "
            f"Valor por defecto: {DEFAULT_TARGET_NAME}."
        ),
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expect_replace(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"No se pudo adaptar {label}: se esperaban 1 coincidencia y se encontraron {count}.")
    return text.replace(old, new, 1)


def adapt_fuzzys_models(source_text: str, target_name: str) -> str:
    adapted = source_text
    adapted = expect_replace(
        adapted,
        "from . import fuzzys_templates\n",
        "import fuzzys_templates\n",
        "fuzzys_models_1A.py imports",
    )
    adapted = expect_replace(
        adapted,
        "# fuzzys_models_1A.py\n",
        f"# fuzzys_models_1A.py  (standalone — {target_name})\n",
        "fuzzys_models_1A.py cabecera",
    )
    return adapted


def adapt_runner(source_text: str, target_name: str) -> str:
    adapted = source_text
    adapted = expect_replace(
        adapted,
        '"""Runner reusable del sistema experto.\n\nEste runner pertenece al núcleo, por lo que no fija límites numéricos,\nsetpoints base ni parámetros de simulación. Todo eso debe ser inyectado\ndesde la capa de integración o desde las pruebas.\n"""\n',
        '"""Runner reusable del sistema experto (standalone — ' + target_name + ').\n\nSoporta carga de reglas desde reglas.json para edición en vivo.\n"""\n',
        "runner.py docstring",
    )
    adapted = expect_replace(
        adapted,
        "import pandas as pd\n",
        "import json\nimport os\nimport pandas as pd\n",
        "runner.py imports stdlib",
    )
    adapted = expect_replace(adapted, "from . import motor\n", "import motor\n", "runner.py import motor")
    adapted = expect_replace(adapted, "from .config import (\n", "from config import (\n", "runner.py import config")
    adapted = expect_replace(
        adapted,
        "from .defuzzy_actions import apply_action\n",
        "from defuzzy_actions import apply_action\n",
        "runner.py import defuzzy_actions",
    )
    adapted = expect_replace(
        adapted,
        "from .fuzzys_eval import evaluar_fuzzys, evaluar_pendiente_var, expandir_etiquetas_compuestas\n",
        "from fuzzys_eval import evaluar_fuzzys, evaluar_pendiente_var, expandir_etiquetas_compuestas\n",
        "runner.py import fuzzys_eval",
    )
    adapted = expect_replace(
        adapted,
        "from .fuzzys_models_1A import FUZZY_MODELOS, PEND_MODELOS\n",
        "from fuzzys_models_1A import FUZZY_MODELOS, PEND_MODELOS\n",
        "runner.py import fuzzys_models_1A",
    )
    adapted = expect_replace(
        adapted,
        "from .reglas_estrategia_correcta import REGLAS\n\n\ndef _resolver_col",
        "from reglas_estrategia_correcta import REGLAS as REGLAS_DEFAULT\n\nREGLAS_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), \"reglas.json\")\n\n\ndef cargar_reglas_json(path: str | None = None) -> list[dict]:\n    \"\"\"Carga reglas desde reglas.json. Si no existe, retorna las reglas por defecto.\"\"\"\n    path = path or REGLAS_JSON_PATH\n    if not os.path.exists(path):\n        return list(REGLAS_DEFAULT)\n    with open(path, \"r\", encoding=\"utf-8\") as f:\n        reglas = json.load(f)\n    for regla in reglas:\n        regla[\"if\"] = [tuple(condicion) if isinstance(condicion, list) else condicion for condicion in regla.get(\"if\", [])]\n    return reglas\n\n\ndef _resolver_col",
        "runner.py reglas json block",
    )
    adapted = expect_replace(
        adapted,
        "def correr_prueba_general(\n    df_data: pd.DataFrame,\n    reglas: list[dict] | None = None,\n    min_belief: float = 0.05,\n    verbose: bool = True,\n    columnas_entrada: dict | None = None,\n    setpoints_base: dict | None = None,\n    limites_sp: dict | None = None,\n    meta_flags: dict | None = None,\n) -> dict:\n    if setpoints_base is None:\n",
        "def correr_prueba_general(\n    df_data: pd.DataFrame,\n    reglas: list[dict] | None = None,\n    min_belief: float = 0.05,\n    verbose: bool = True,\n    columnas_entrada: dict | None = None,\n    setpoints_base: dict | None = None,\n    limites_sp: dict | None = None,\n    meta_flags: dict | None = None,\n    usar_reglas_json: bool = False,\n) -> dict:\n    \"\"\"Ejecuta la prueba general del sistema experto.\n\n    Si usar_reglas_json=True, las reglas se cargan desde reglas.json\n    (permite edición en vivo desde la interfaz Flask).\n    \"\"\"\n    if setpoints_base is None:\n",
        "runner.py firma",
    )
    adapted = expect_replace(
        adapted,
        "    reglas = list(REGLAS if reglas is None else reglas)\n    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada\n",
        "    if reglas is not None:\n        reglas = list(reglas)\n    elif usar_reglas_json:\n        reglas = cargar_reglas_json()\n    else:\n        reglas = list(REGLAS_DEFAULT)\n    columnas_entrada = COLUMNAS_ENTRADA if columnas_entrada is None else columnas_entrada\n",
        "runner.py seleccion de reglas",
    )
    return adapted


ADAPTERS: dict[str, Callable[[str, str], str]] = {
    "fuzzys_models_1A.py": adapt_fuzzys_models,
    "runner.py": adapt_runner,
}


def render_expected_text(name: str, source: Path, target_name: str) -> str:
    adapter = ADAPTERS.get(name)
    if adapter is None:
        raise ValueError(f"No existe adaptador registrado para: {name}")
    return adapter(source.read_text(encoding="utf-8"), target_name)


def compare_pair(name: str, reason: str, target_dir: Path, strategy: str = "copy") -> PairStatus:
    source = CORE_DIR / name
    target = target_dir / name
    source_exists = source.is_file()
    target_exists = target.is_file()

    if not source_exists:
        state = "missing-source"
    elif not target_exists:
        state = "missing-target"
    elif strategy == "adapt":
        expected_text = render_expected_text(name, source, target_dir.name)
        target_text = target.read_text(encoding="utf-8")
        state = "same" if sha256_text(expected_text) == sha256_text(target_text) else "different"
    elif sha256_file(source) == sha256_file(target):
        state = "same"
    else:
        state = "different"

    return PairStatus(
        name=name,
        source=source,
        target=target,
        reason=reason,
        strategy=strategy,
        source_exists=source_exists,
        target_exists=target_exists,
        state=state,
    )


def build_pair_statuses(
    rules: tuple[tuple[str, str], ...],
    target_dir: Path,
    strategy: str = "copy",
) -> list[PairStatus]:
    return [compare_pair(name, reason, target_dir, strategy=strategy) for name, reason in rules]


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


def ensure_layout(target_dir: Path) -> list[str]:
    errors: list[str] = []
    if not CORE_DIR.is_dir():
        errors.append(f"No se encontro el directorio canonico: {CORE_DIR}")
    if not target_dir.is_dir():
        errors.append(f"No se encontro el directorio del prototipo: {target_dir}")
    return errors


def ensure_backup_dir(root: Path, target_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / timestamp / target_name
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def ensure_report_path(root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"sync_report_{timestamp}.md"


def write_status_target(status: PairStatus, target_name: str) -> None:
    if status.strategy == "adapt":
        status.target.write_text(
            render_expected_text(status.name, status.source, target_name),
            encoding="utf-8",
        )
        return
    shutil.copy2(status.source, status.target)


def copy_with_backup(
    statuses: list[PairStatus],
    backup_root: Path,
    target_name: str,
) -> tuple[list[str], Path | None]:
    changed = [status for status in statuses if status.state in {"different", "missing-target"}]
    if not changed:
        return [], None

    backup_dir: Path | None = None
    applied: list[str] = []
    for status in changed:
        status.target.parent.mkdir(parents=True, exist_ok=True)
        if status.target.exists():
            if backup_dir is None:
                backup_dir = ensure_backup_dir(backup_root, target_name)
            shutil.copy2(status.target, backup_dir / status.name)
        write_status_target(status, target_name)
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
    target_dir: Path,
    target_name: str,
    mode: str,
    auto_statuses: list[PairStatus],
    adapted_statuses: list[PairStatus],
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
        f"# Reporte de sincronizacion core -> {target_name}",
        "",
        "## Resumen",
        "",
        f"- Fecha: `{timestamp}`",
        f"- Modo: `{mode}`",
        f"- Estado resumido: `{summary_state}`",
        f"- Codigo de salida esperado: `{exit_code}`",
        f"- Directorio canonico: `{CORE_DIR}`",
        f"- Directorio objetivo: `{target_dir}`",
        "",
        "## Resultado",
        "",
        f"- Archivos auto-sync evaluados: `{len(auto_statuses)}`",
        f"- Archivos auto-adaptados evaluados: `{len(adapted_statuses)}`",
        f"- Archivos sincronizables pendientes: `{len(pending)}`",
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
    lines.extend(markdown_table("Archivos auto-adaptados", adapted_statuses, sync_mode=(mode == "apply")))
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
    target_name = str(args.target_name).strip()
    if not target_name:
        parser.error("--target-name no puede ser vacio.")
    target_dir = WORKSPACE_ROOT / target_name

    layout_errors = ensure_layout(target_dir)
    if layout_errors:
        for error in layout_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    auto_statuses = build_pair_statuses(AUTO_SYNC_RULES, target_dir)
    adapted_statuses = build_pair_statuses(AUTO_ADAPT_RULES, target_dir, strategy="adapt")
    manual_statuses = build_pair_statuses(MANUAL_REVIEW_RULES, target_dir)

    protected_statuses = [
        PairStatus(
            name=name,
            source=CORE_DIR / name,
            target=target_dir / name,
            reason=reason,
            strategy="protected",
            source_exists=(CORE_DIR / name).is_file(),
            target_exists=(target_dir / name).is_file(),
            state="protected",
        )
        for name, reason in PROTECTED_ONLY
    ]

    header = f"Sincronizacion segura core -> {target_name}"
    print(header)
    print("=" * len(header))
    print(f"Modo: {'apply' if args.apply else 'dry-run'}")
    print()

    print_section("Archivos auto-sync", auto_statuses, sync_mode=args.apply)
    print_section("Archivos auto-adaptados", adapted_statuses, sync_mode=args.apply)
    print_section("Archivos con revision manual", manual_statuses, sync_mode=False)
    print_section("Archivos protegidos del prototipo", protected_statuses, sync_mode=False)

    syncable_statuses = [*auto_statuses, *adapted_statuses]
    pending = [status for status in syncable_statuses if status.state in {"different", "missing-target"}]
    missing_sources = [status for status in syncable_statuses if status.state == "missing-source"]
    applied: list[str] = []
    backup_dir: Path | None = None
    exit_code = 0

    if missing_sources:
        print("No se puede aplicar la sincronizacion porque faltan archivos canonicos en core/.")
        exit_code = 2
    elif args.apply:
        applied, backup_dir = copy_with_backup(syncable_statuses, args.backup_root, target_name)
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
            print("Dry-run: hay archivos sincronizables pendientes de actualizar.")
        else:
            print("Dry-run: la sincronizacion ya esta al dia.")

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
            target_dir=target_dir,
            target_name=target_name,
            mode="apply" if args.apply else "dry-run",
            auto_statuses=auto_statuses,
            adapted_statuses=adapted_statuses,
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