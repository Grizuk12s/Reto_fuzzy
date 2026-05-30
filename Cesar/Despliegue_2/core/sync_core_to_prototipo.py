from __future__ import annotations

import argparse
import hashlib
import re
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


# ============================================================
# Reglas de sincronizacion (Espesador v2)
# ------------------------------------------------------------
# AUTO_SYNC      : copia byte a byte (modulos sin imports relativos).
# AUTO_ADAPT     : se reescribe `from .x import ...` y `from . import x`
#                  como imports planos para el modo standalone.
# MANUAL_REVIEW  : se reporta pero no se toca automaticamente.
# PROTECTED_ONLY : pertenece al prototipo y nunca se sobreescribe.
# ============================================================

AUTO_SYNC_RULES: tuple[tuple[str, str], ...] = (
    ("config.py", "Contrato de datos del experto."),
    ("calculos_variables.py", "Definiciones declarativas de variables crudas y calculadas."),
    ("defuzzy_actions.py", "Tablas defuzzy estilo Sugeno y aplicacion de acciones."),
    ("exp_q_filter.py", "Filtro Exp-Q por variable."),
    ("fuzzys_eval.py", "Evaluacion fuzzy y etiquetas compuestas."),
    ("fuzzys_templates.py", "Fabricas Low/High/Norm/Pendiente."),
    ("permisivos.py", "Permisivos operacionales (OR/AND/NOT)."),
    ("reglas_espesador.py", "Reglas del experto Espesador (default)."),
    ("reglas_estrategia_correcta.py", "Reglas v1 legacy mantenidas para referencia."),
)

AUTO_ADAPT_RULES: tuple[tuple[str, str], ...] = (
    ("fun_calc_variables.py", "Funciones de calculo de variables derivadas; imports planos."),
    ("variables_calculadas.py", "Shim de compatibilidad; imports planos."),
    ("fuzzys_models_espesador.py", "Modelos fuzzy del Espesador; imports planos."),
    ("motor.py", "Motor de reglas; imports planos."),
    ("runner.py", "Pipeline completo; imports planos + helper reglas.json."),
    ("fuzzys_models_1A.py", "Modelos fuzzy v1 legacy; imports planos."),
)

MANUAL_REVIEW_RULES: tuple[tuple[str, str], ...] = (
    # vacio en v2: las reglas viven en reglas_espesador.py (auto-sync) y en
    # Prototipo_2/reglas.json (protegido).
)

PROTECTED_ONLY: tuple[tuple[str, str], ...] = (
    ("app.py", "UI y API Flask propias del prototipo standalone."),
    ("simulacion.py", "Simulacion y parametros de prueba propios del prototipo."),
    ("reglas.json", "Fuente activa de reglas en vivo del prototipo."),
    ("filtros.json", "Config Exp-Q (q, window_size) editable en vivo desde la UI."),
    ("defuzzy.json", "Tablas Sugeno por familia de SP editables en vivo desde la UI."),
    ("fuzzy.json", "Membresias fuzzy (offset + HIGH/OK/LOW) editables en vivo desde la UI."),
    ("variables.json", "Catalogo de variables crudas y definiciones calculadas editables en vivo."),
    ("permisivos.json", "Permisivos operacionales (OR/AND/NOT) editables en vivo desde la UI."),
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


# ============================================================
# Adaptadores
# ============================================================

_RE_FROM_DOT_MODULE = re.compile(r"^from\s+\.([a-zA-Z_][\w]*)\s+import\s+", re.MULTILINE)
_RE_FROM_DOT_BARE = re.compile(r"^from\s+\.\s+import\s+([a-zA-Z_][\w]*)", re.MULTILINE)
_RE_RELATIVE_PARENT = re.compile(r"^from\s+\.\.", re.MULTILINE)


def flatten_relative_imports(text: str) -> str:
    """Convierte imports relativos del paquete core en imports planos.

    - `from .modulo import X`  ->  `from modulo import X`
    - `from . import modulo`   ->  `import modulo`
    - `from ..xxx import Y`    ->  no soportado (se lanza ValueError).
    """
    if _RE_RELATIVE_PARENT.search(text):
        raise ValueError(
            "El nucleo contiene un import relativo con `..` que no se puede aplanar automaticamente."
        )
    text = _RE_FROM_DOT_MODULE.sub(r"from \1 import ", text)
    text = _RE_FROM_DOT_BARE.sub(r"import \1", text)
    return text


def adapt_generic(source_text: str, target_name: str) -> str:
    """Adaptador por defecto: solo aplana imports relativos."""
    return flatten_relative_imports(source_text)


_RUNNER_REGLAS_HELPER = '''

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


# ============================================================
# Helper para cargar variables crudas y definiciones calculadas
# ------------------------------------------------------------
# Schema variables.json:
#   { "crudas": {<nombre>: <descripcion>},
#     "definiciones": [ {nombre, descripcion, tipo, ...}, ... ] }
# ------------------------------------------------------------
# tipos soportados:
#   - aritmetica   : operacion + args [a, b]
#   - rolling_delta: arg + ventana_min
#   - rolling_std  : arg + ventana_min
# ============================================================
VARIABLES_JSON_PATH = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "variables.json"
)


def _variables_defaults_from_core() -> dict:
    from variables_calculadas import VARIABLES_CRUDAS as _CRUDAS_CORE
    crudas = {k: str(v) for k, v in _CRUDAS_CORE.items()}
    definiciones = []
    for nombre, cfg in DEFINICIONES_CALCULADAS.items():
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
    """Convierte la lista ordenada del JSON al dict que espera el runner."""
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
# ------------------------------------------------------------
# Schema:
#   { "<NOMBRE_PERMISIVO>": [ <condicion>, ... ] }
# donde cada <condicion> puede ser:
#   {"var": <str>, "op": <str>, "value": <num>}
#   {"fuzzy_var": <str>, "label": <str>, "min_mu": <num>}
#   {"OR":  [<condicion>, ...]}
#   {"AND": [<condicion>, ...]}
#   {"NOT": <condicion>}
# Si falta o esta vacio, devuelve deepcopy de PERMISIVOS del core.
# ============================================================
PERMISIVOS_JSON_PATH = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)), "permisivos.json"
)


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
    if not isinstance(datos, dict) or not datos:
        return _permisivos_defaults_deepcopy()
    return datos

'''


def adapt_runner(source_text: str, target_name: str) -> str:
    """Adaptador del runner: imports planos + helper de reglas.json."""
    adapted = flatten_relative_imports(source_text)

    # Cabecera: marcar la version standalone para que sea evidente al abrir el archivo.
    header_marker = '"""Runner del sistema experto Espesador (v2).'
    if header_marker in adapted:
        adapted = adapted.replace(
            header_marker,
            f'"""Runner del sistema experto Espesador (v2) — standalone ({target_name}).',
            1,
        )

    # Inyecta el helper justo despues del ultimo `from ... import` superior.
    # Lo metemos al final del bloque de imports para que `REGLAS_ESPESADOR`
    # ya este disponible cuando se referencie en cargar_reglas_json().
    anchor = "from reglas_espesador import REGLAS_ESPESADOR\n"
    if anchor not in adapted:
        raise ValueError(
            "No se pudo localizar el import de REGLAS_ESPESADOR en runner.py para inyectar el helper."
        )
    adapted = adapted.replace(anchor, anchor + _RUNNER_REGLAS_HELPER, 1)
    return adapted


ADAPTERS: dict[str, Callable[[str, str], str]] = {
    "runner.py": adapt_runner,
}


def render_expected_text(name: str, source: Path, target_name: str) -> str:
    adapter = ADAPTERS.get(name, adapt_generic)
    return adapter(source.read_text(encoding="utf-8"), target_name)


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            f"Sincroniza de forma segura el nucleo core/ hacia {DEFAULT_TARGET_NAME}/. "
            "Por defecto solo reporta cambios; usa --apply para copiar archivos."
        )
    )
    parser.add_argument(
        "--target-name",
        default=DEFAULT_TARGET_NAME,
        help=f"Nombre de la carpeta destino bajo el workspace root. Default: {DEFAULT_TARGET_NAME}.",
    )
    parser.add_argument("--apply", action="store_true", help="Aplica la sincronizacion y crea backups.")
    parser.add_argument("--check", action="store_true", help="Sale con codigo 1 si hay desalineados.")
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--write-report", action="store_true", help="Genera un reporte Markdown.")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    return parser


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        name=name, source=source, target=target, reason=reason, strategy=strategy,
        source_exists=source_exists, target_exists=target_exists, state=state,
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
    changed = [s for s in statuses if s.state in {"different", "missing-target"}]
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
    report_root: Path, target_dir: Path, target_name: str, mode: str,
    auto_statuses: list[PairStatus], adapted_statuses: list[PairStatus],
    manual_statuses: list[PairStatus], protected_statuses: list[PairStatus],
    pending: list[PairStatus], applied: list[str], backup_dir: Path | None,
    exit_code: int,
) -> Path:
    report_path = ensure_report_path(report_root)
    timestamp = datetime.now().isoformat(timespec="seconds")
    summary_state = (
        "sin cambios" if not pending and not applied
        else "con pendientes" if pending and not applied
        else "aplicado"
    )
    lines = [
        f"# Reporte de sincronizacion core -> {target_name}", "",
        "## Resumen", "",
        f"- Fecha: `{timestamp}`",
        f"- Modo: `{mode}`",
        f"- Estado resumido: `{summary_state}`",
        f"- Codigo de salida esperado: `{exit_code}`",
        f"- Directorio canonico: `{CORE_DIR}`",
        f"- Directorio objetivo: `{target_dir}`",
        "", "## Resultado", "",
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
            name=name, source=CORE_DIR / name, target=target_dir / name,
            reason=reason, strategy="protected",
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
    pending = [s for s in syncable_statuses if s.state in {"different", "missing-target"}]
    missing_sources = [s for s in syncable_statuses if s.state == "missing-source"]
    applied: list[str] = []
    backup_dir: Path | None = None
    exit_code = 0

    if missing_sources:
        print("No se puede aplicar la sincronizacion porque faltan archivos canonicos en core/.")
        for s in missing_sources:
            print(f"- {s.name}")
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
            report_root=args.report_root, target_dir=target_dir, target_name=target_name,
            mode="apply" if args.apply else "dry-run",
            auto_statuses=auto_statuses, adapted_statuses=adapted_statuses,
            manual_statuses=manual_statuses, protected_statuses=protected_statuses,
            pending=pending, applied=applied, backup_dir=backup_dir, exit_code=exit_code,
        )
        print()
        print(f"Reporte Markdown generado en: {report_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
