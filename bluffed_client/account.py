from typing import Optional

import requests

from .errors import BluffedError


class AccountError(BluffedError):
    pass


class AccountClient:
    """Owner-authenticated access to /api/agents* and /api/me — the same
    endpoints the /developers page calls from a signed-in browser session.
    Signs in with email/password and carries the resulting session cookie
    on every request after that, so this can fund and sweep agents without
    a human clicking through the UI."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._http = requests.Session()

    def sign_in(self, email: str, password: str) -> None:
        self._post("/api/auth/sign-in/email", {"email": email, "password": password})

    def balance(self) -> dict:
        return self._get("/api/me")

    def list_agents(self) -> list:
        return self._get("/api/agents")["agents"]

    def create_agent(self, name: str, mode: str) -> dict:
        return self._post("/api/agents", {"name": name, "mode": mode})

    def fund(self, agent_id: str, micros: int) -> None:
        self._post(f"/api/agents/{agent_id}/fund", {"micros": micros})

    def sweep(self, agent_id: str, micros: Optional[int] = None) -> None:
        body = {"micros": micros} if micros is not None else {}
        self._post(f"/api/agents/{agent_id}/sweep", body)

    def rotate_key(self, agent_id: str) -> dict:
        return self._post(f"/api/agents/{agent_id}/rotate-key", {})

    def _get(self, path: str) -> dict:
        resp = self._http.get(f"{self.base_url}{path}")
        return self._unwrap(resp)

    def _post(self, path: str, body: dict) -> dict:
        resp = self._http.post(f"{self.base_url}{path}", json=body)
        return self._unwrap(resp)

    def _unwrap(self, resp: requests.Response) -> dict:
        if not resp.ok:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise AccountError(message or f"{resp.status_code} {resp.reason}")
        return resp.json() if resp.content else {}
