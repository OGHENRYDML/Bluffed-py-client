import json
import os
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".bluffed"
SESSION_FILE = CONFIG_DIR / "session.json"
AGENTS_DIR = CONFIG_DIR / "agents"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def save_session(base_url: str, cookies: dict) -> None:
    _ensure_dir(CONFIG_DIR)
    SESSION_FILE.write_text(json.dumps({"base_url": base_url, "cookies": cookies}))
    os.chmod(SESSION_FILE, 0o600)


def load_session() -> Optional[dict]:
    if not SESSION_FILE.exists():
        return None
    return json.loads(SESSION_FILE.read_text())


def save_agent_key(agent_id: str, api_key: str) -> Path:
    _ensure_dir(AGENTS_DIR)
    path = AGENTS_DIR / f"{agent_id}.key"
    path.write_text(api_key)
    os.chmod(path, 0o600)
    return path


def load_agent_key(agent_id: str) -> Optional[str]:
    path = AGENTS_DIR / f"{agent_id}.key"
    return path.read_text().strip() if path.exists() else None
