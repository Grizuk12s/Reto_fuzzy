import os
import time
import traceback
import importlib
from pathlib import Path

# -----------------------
# Logging simple (visual)
# -----------------------
def log_ok(msg): print(f"✅ {msg}")
def log_no(msg): print(f"⛔ {msg}")
def log_wr(msg): print(f"⚠️ {msg}")
def log_re(msg): print(f"🔄 {msg}")

# -----------------------
# Lockfile (anti 2 mains)
# -----------------------
def acquire_lock(lock_path: Path, heartbeat_sec: float = 2.0) -> bool:
    """
    Evita que se ejecuten 2 instancias del main.
    Si el lock existe y está "vivo" (mtime reciente), se rechaza.
    Si está viejo, se considera stale y se toma.
    """
    try:
        if lock_path.exists():
            age = time.time() - lock_path.stat().st_mtime
            if age < (heartbeat_sec * 3):
                log_no(f"Ya hay otra instancia ejecutándose (lock activo: {lock_path.name}).")
                return False
            log_wr(f"Lock viejo detectado. Reclamando lock: {lock_path.name}")

        lock_path.write_text(str(os.getpid()), encoding="utf-8")
        log_ok(f"Lock adquirido: {lock_path.name} (PID {os.getpid()})")
        return True

    except Exception as e:
        log_wr(f"No se pudo crear lockfile: {e}. Continuaré igual.")
        return True

def heartbeat_lock(lock_path: Path):
    """Actualiza mtime del lock para indicar que el proceso sigue vivo."""
    try:
        lock_path.touch()
    except Exception as e:
        log_wr(f"No se pudo actualizar heartbeat del lock: {e}")

def release_lock(lock_path: Path):
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass

# -----------------------
# Archivos + cambios
# -----------------------
def file_exists(path: Path) -> bool:
    if path.exists():
        log_ok(f"Archivo existe: {path.name}")
        return True
    log_no(f"Archivo NO existe: {path.name}")
    return False


def get_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return -1.0

# -----------------------
# Reload seguro de módulos
# -----------------------
def safe_reload(module, module_name: str) -> bool:
    """Recarga módulo. Si falla, no bota el main y retorna False."""
    try:
        importlib.reload(module)
        log_ok(f"Módulo recargado OK: {module_name}")
        return True
    except Exception:
        log_wr(f"Error recargando {module_name}:\n{traceback.format_exc()}")
        return False

def reload_if_changed(
    module,
    module_name: str,
    file_path: Path,
    last_mtime: float
) -> float:
    """
    Si detecta cambio (mtime distinto), intenta reload.
    Si el reload sale OK, devuelve el nuevo mtime.
    Si falla, devuelve el mtime anterior (para reintentar en el próximo ciclo).
    """
    m_now = get_mtime(file_path)
    if m_now != last_mtime:
        log_re(f"Cambio detectado en {file_path.name} → reload")
        ok = safe_reload(module, module_name)
        if ok:
            return m_now
    return last_mtime
