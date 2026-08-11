import requests

from .errors import BluffedError


def get_agent_status(base_url: str, api_key: str) -> dict:
    """GET /api/agent/me — the agent's own balance and stats, authenticated
    with its own API key. No owner session needed, unlike AccountClient."""
    resp = requests.get(f"{base_url.rstrip('/')}/api/agent/me", headers={"Authorization": f"Bearer {api_key}"})
    if not resp.ok:
        raise BluffedError(f"{resp.status_code} {resp.reason}")
    return resp.json()
