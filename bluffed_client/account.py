from typing import Optional

import requests

from .defaults import DEFAULT_BASE_URL
from .errors import BluffedError
from .wallet import Wallet

DEFAULT_CHAIN_ID = 103  # Solana devnet — matches the server's siws.ts default


class AccountError(BluffedError):
    pass


class AccountClient:
    """Owner-authenticated access to /api/agents*, /api/me, /api/deposit,
    and /api/withdraw — the same endpoints /developers and /play call from a
    signed-in browser session. Signs in with either email/password or a
    Solana wallet (SIWS) and carries the resulting session cookie on every
    request after that, so this can fund, sweep, deposit, and withdraw
    without a human clicking through the UI."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http = requests.Session()

    def sign_in(self, email: str, password: str) -> None:
        self._post("/api/auth/sign-in/email", {"email": email, "password": password})

    def sign_in_with_wallet(self, wallet: Wallet, chain_id: int = DEFAULT_CHAIN_ID) -> None:
        """Sign in with a Solana keypair instead of email/password — no
        inbox required. Proves control of the private key by signing a
        server-issued nonce; the account is created automatically on first
        sign-in for a given wallet."""
        nonce = self._post("/api/auth/siws/nonce", {"walletAddress": wallet.address, "chainId": chain_id})["nonce"]
        message = f"Sign in to Bluffed\nNonce: {nonce}"
        self._post(
            "/api/auth/siws/verify",
            {
                "message": message,
                "signature": wallet.sign(message),
                "walletAddress": wallet.address,
                "chainId": chain_id,
            },
        )

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

    def deposit_address(self) -> str:
        """A Solana address unique to this account — send USDC here to fund
        your balance. Watched automatically (usually credited within a
        minute or two); pass the tx signature to confirm_deposit() to credit
        it immediately instead of waiting."""
        return self._get("/api/deposit")["address"]

    def confirm_deposit(self, tx_sig: str) -> dict:
        return self._post("/api/deposit", {"txSig": tx_sig})

    def poll_deposit(self) -> dict:
        return self._get("/api/deposit/poll")

    def withdraw(self, to_address: str, micros: int) -> dict:
        return self._post("/api/withdraw", {"toAddress": to_address, "micros": micros})

    def withdrawal_status(self, withdrawal_id: str) -> dict:
        return self._get(f"/api/withdraw/{withdrawal_id}")

    def export_cookies(self) -> dict:
        """The session cookie, to persist and restore with import_cookies —
        avoids signing in again on every process."""
        return dict(self._http.cookies)

    def import_cookies(self, cookies: dict) -> None:
        self._http.cookies.update(cookies)

    def _get(self, path: str) -> dict:
        resp = self._request(self._http.get, f"{self.base_url}{path}", timeout=self.timeout)
        return self._unwrap(resp)

    def _post(self, path: str, body: dict) -> dict:
        resp = self._request(self._http.post, f"{self.base_url}{path}", json=body, timeout=self.timeout)
        return self._unwrap(resp)

    def _request(self, method, *args, **kwargs) -> requests.Response:
        try:
            return method(*args, **kwargs)
        except requests.RequestException as exc:
            # A timeout or connection failure raises the raw requests
            # exception, not an AccountError — every other failure mode
            # from this class (4xx/5xx, bad JSON) already comes back as
            # one, so callers only have to handle one exception type.
            raise AccountError(str(exc)) from exc

    def _unwrap(self, resp: requests.Response) -> dict:
        if not resp.ok:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise AccountError(message or f"{resp.status_code} {resp.reason}")
        return resp.json() if resp.content else {}
