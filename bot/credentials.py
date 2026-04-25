"""
Credential file I/O — secure read/write for agent-wallet, owner-wallet, credentials, intake.
All sensitive files stored in dev-agent/ with restricted permissions.
"""
import json
import os
import stat
import contextvars
from pathlib import Path
from typing import Any, Optional

# Context variable to hold the ID of the current running bot across async tasks
current_bot_id = contextvars.ContextVar("current_bot_id", default="default")

from bot.utils.logger import get_logger

log = get_logger(__name__)

def _get_dev_dir() -> Path:
    """Get the dev-agent directory specific to the current context."""
    bot_id = current_bot_id.get()
    if bot_id == "default":
        return Path("dev-agent")
    return Path(f"data/{bot_id}/dev-agent")

def _path_creds() -> Path: return _get_dev_dir() / "credentials.json"
def _path_intake() -> Path: return _get_dev_dir() / "owner-intake.json"
def _path_agent() -> Path: return _get_dev_dir() / "agent-wallet.json"
def _path_owner() -> Path: return _get_dev_dir() / "owner-wallet.json"

def _ensure_dir():
    """Create dev-agent/ directory if missing."""
    _get_dev_dir().mkdir(parents=True, exist_ok=True)


def _write_secure(path: Path, data: dict):
    """Write JSON file with restricted permissions (owner-only read/write)."""
    _ensure_dir()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass  # Windows may not support chmod fully


def _read_json(path: Path) -> Optional[dict]:
    """Read JSON file, return None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to read {path}: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────

def is_first_run() -> bool:
    """First-run if credentials.json or owner-intake.json is missing."""
    return not _path_creds().exists() or not _path_intake().exists()


def load_credentials() -> Optional[dict]:
    return _read_json(_path_creds())


def save_credentials(data: dict):
    p = _path_creds()
    _write_secure(p, data)
    log.info("Credentials saved to %s", p)


def load_owner_intake() -> Optional[dict]:
    return _read_json(_path_intake())


def save_owner_intake(data: dict):
    p = _path_intake()
    _write_secure(p, data)
    log.info("Owner intake saved to %s", p)


def load_agent_wallet() -> Optional[dict]:
    return _read_json(_path_agent())


def save_agent_wallet(address: str, private_key: str):
    p = _path_agent()
    _write_secure(p, {
        "address": address,
        "privateKey": private_key,
    })
    log.info("Agent wallet saved to %s", p)


def load_owner_wallet() -> Optional[dict]:
    return _read_json(_path_owner())


def save_owner_wallet(address: str, private_key: str):
    p = _path_owner()
    _write_secure(p, {
        "address": address,
        "privateKey": private_key,
    })
    log.info("Owner wallet saved to %s", p)


def get_api_key() -> str:
    """Resolve API key from env → credentials file."""
    from bot.config import API_KEY
    if API_KEY:
        return API_KEY
    creds = load_credentials()
    return creds.get("api_key", "") if creds else ""


def get_agent_private_key() -> str:
    """Resolve agent PK from env → wallet file."""
    from bot.config import AGENT_PRIVATE_KEY
    if AGENT_PRIVATE_KEY and current_bot_id.get() == "default":
        return AGENT_PRIVATE_KEY
    wallet = load_agent_wallet()
    return wallet.get("privateKey", "") if wallet else ""


def get_owner_private_key() -> str:
    """Resolve owner PK from env → wallet file (advanced mode only)."""
    from bot.config import OWNER_PRIVATE_KEY
    if OWNER_PRIVATE_KEY:
        return OWNER_PRIVATE_KEY
    wallet = load_owner_wallet()
    return wallet.get("privateKey", "") if wallet else ""


def update_env_file(key: str, value: str):
    """Update or append a key=value in .env file."""
    # In multi-agent mode, skip updating .env to avoid concurrent file corruption
    if current_bot_id.get() != "default":
        return

    env_path = Path(".env")
    lines = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
