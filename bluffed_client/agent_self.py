import requests

from .errors import BluffedError


def get_agent_status(base_url: str, api_key: str, timeout: float = 15.0) -> dict:
    """GET /api/agent/me — the agent's own balance and stats, authenticated
    with its own API key. No owner session needed, unlike AccountClient."""
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/api/agent/me", headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
    except requests.RequestException as exc:
        raise BluffedError(str(exc)) from exc
    if not resp.ok:
        raise BluffedError(f"{resp.status_code} {resp.reason}")
    return resp.json()
