import json
import queue
import threading
import time
from typing import Any, Optional, Tuple

import websocket

from .actions import Action
from .errors import BluffedError, TableError
from .observation import Observation, parse_observation


class BluffedTableEnv:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        tier_id: str,
        buy_in: int,
        *,
        connect_timeout: float = 10.0,
        step_timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tier_id = tier_id
        self.buy_in = buy_in
        self.connect_timeout = connect_timeout
        self.step_timeout = step_timeout

        self._ws: Optional[websocket.WebSocket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._messages: "queue.Queue[dict]" = queue.Queue()
        self._closed = threading.Event()

        self._last_obs: Optional[Observation] = None
        self._prev_chips: Optional[int] = None

    def _ws_url(self) -> str:
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        host = self.base_url.split("://", 1)[1]
        return f"{scheme}://{host}/api/agent/table/{self.tier_id}/connect?key={self.api_key}"

    def _recv_loop(self, ws: websocket.WebSocket) -> None:
        while not self._closed.is_set():
            try:
                raw = ws.recv()
            except Exception:
                self._closed.set()
                return
            if not raw:
                self._closed.set()
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self._messages.put(msg)

    def _send(self, payload: dict) -> None:
        if not self._ws:
            raise BluffedError("not connected — call reset() first")
        self._ws.send(json.dumps(payload))

    def _next_message(self, timeout: float) -> dict:
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty:
            raise BluffedError("timed out waiting for the table")

    def _handle_message(self, msg: dict) -> Optional[Observation]:
        if msg.get("type") == "error":
            raise TableError(msg.get("error", "unknown_error"))
        if msg.get("type") == "state":
            obs = parse_observation(msg["state"])
            self._last_obs = obs
            return obs
        return None

    def _await_turn_or_terminal(self, timeout: float) -> Observation:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BluffedError("timed out waiting for the table")
            msg = self._next_message(remaining)
            obs = self._handle_message(msg)
            if obs is None:
                continue
            if obs.hand_over or obs.my_turn:
                return obs

    def _chips_now(self, obs: Optional[Observation] = None) -> Optional[int]:
        obs = obs or self._last_obs
        if obs is None:
            return self._prev_chips
        me = obs.me
        return me.chips if me else self._prev_chips

    def reset(self) -> Tuple[Observation, dict]:
        self.close()
        self._closed.clear()
        self._messages = queue.Queue()

        ws = websocket.create_connection(self._ws_url(), timeout=self.connect_timeout)
        self._ws = ws
        self._recv_thread = threading.Thread(target=self._recv_loop, args=(ws,), daemon=True)
        self._recv_thread.start()

        self._send({"type": "sit", "buyIn": self.buy_in})
        obs = self._await_turn_or_terminal(timeout=self.step_timeout)
        self._prev_chips = self._chips_now(obs)
        me = obs.me
        return obs, {"my_id": me.id if me else None}

    def step(self, action: Action) -> Tuple[Observation, float, bool, bool, dict]:
        if self._last_obs is None or not self._last_obs.my_turn:
            raise BluffedError("step() called when it isn't this agent's turn — check obs.my_turn")

        chips_before = self._chips_now()
        self._send({"type": "action", "action": action.to_wire()})

        try:
            obs = self._await_turn_or_terminal(timeout=self.step_timeout)
        except BluffedError:
            return self._last_obs, 0.0, False, True, {"reason": "connection_lost"}

        chips_after = self._chips_now(obs)
        reward = float((chips_after or 0) - (chips_before or 0))
        terminated = obs.hand_over
        info: dict[str, Any] = {"phase": obs.phase}
        return obs, reward, terminated, False, info

    def leave(self) -> None:
        if self._ws:
            self._send({"type": "leave"})

    def close(self) -> None:
        self._closed.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._recv_thread:
            self._recv_thread.join(timeout=1)
            self._recv_thread = None

    def __enter__(self) -> "BluffedTableEnv":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
