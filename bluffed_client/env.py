import json
import queue
import threading
import time
from typing import Any, Callable, Optional, Tuple

import websocket

from .actions import Action
from .defaults import DEFAULT_BASE_URL
from .errors import BluffedError, TableError
from .money import fmt_usdc
from .observation import Observation, parse_observation
from .tiers import DEFAULT_TIER_ID, get_tier

OnEvent = Callable[[str, dict], None]


def default_log(kind: str, data: dict) -> None:
    """Plain-text fallback for `on_event` — used whenever a caller (a
    hand-rolled script, `run_forever`, whichever) doesn't wire up its own
    handler, so connecting/playing is never silent by default. A caller
    that wants quiet can still pass `on_event=lambda *a: None` explicitly."""
    if kind == "connecting":
        print("Connecting...")
    elif kind == "connected":
        print("Connected.")
    elif kind == "waiting_for_players":
        print(f"Waiting for other players ({data['seats']}/{data['max_seats']} seated)...")
    elif kind == "hand_complete":
        delta = data.get("chips_delta", 0)
        outcome = "won" if delta > 0 else "lost" if delta < 0 else "pushed"
        print(f"Hand #{data.get('hands')}: {outcome} {fmt_usdc(abs(delta))}")
    elif kind == "funded":
        print(f"Funded agent with {fmt_usdc(data['micros'])}")
    elif kind == "swept":
        print(f"Swept {fmt_usdc(data.get('micros') or 0)} back to owner")
    elif kind == "tier_changed":
        print(f"Moved from tier {data['from']} to {data['to']}")
    elif kind == "error":
        print(f"Error: {data.get('error')}")
    else:
        print(f"{kind}: {data}")


def _default_buy_in(tier_id: str) -> int:
    tier = get_tier(tier_id)
    if tier is None:
        raise BluffedError(f"unknown tier {tier_id!r} — pass buy_in explicitly")
    return tier.min_buy_in


class BluffedTableEnv:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        tier_id: str = DEFAULT_TIER_ID,
        buy_in: Optional[int] = None,
        connect_timeout: float = 10.0,
        step_timeout: float = 30.0,
        on_event: Optional[OnEvent] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tier_id = tier_id
        self.buy_in = buy_in if buy_in is not None else _default_buy_in(tier_id)
        self.connect_timeout = connect_timeout
        self.step_timeout = step_timeout
        self.on_event = on_event

        self._ws: Optional[websocket.WebSocket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._messages: "queue.Queue[dict]" = queue.Queue()
        self._closed = threading.Event()

        self._last_obs: Optional[Observation] = None
        self._prev_chips: Optional[int] = None
        self._seated = False

    @property
    def last_observation(self) -> Optional[Observation]:
        return self._last_obs

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

    def _emit(self, kind: str, data: dict) -> None:
        if self.on_event is not None:
            self.on_event(kind, data)
        else:
            default_log(kind, data)

    def _await_turn_or_terminal(self, timeout: float) -> Observation:
        deadline = time.monotonic() + timeout
        announced_waiting = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BluffedError("timed out waiting for the table")
            msg = self._next_message(remaining)
            obs = self._handle_message(msg)
            if obs is None:
                continue
            # Not a bug, just nobody else at the table yet — surface it once so
            # a caller waiting on step_timeout can tell "stuck" from "normal".
            if obs.phase == "waiting" and not announced_waiting:
                announced_waiting = True
                self._emit("waiting_for_players", {"seats": len(obs.players), "max_seats": obs.max_seats})
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

        self._emit("connecting", {"tier_id": self.tier_id})
        ws = websocket.create_connection(self._ws_url(), timeout=self.connect_timeout)
        self._ws = ws
        self._emit("connected", {})
        self._recv_thread = threading.Thread(target=self._recv_loop, args=(ws,), daemon=True)
        self._recv_thread.start()

        self._send({"type": "sit", "buyIn": self.buy_in})
        obs = self._await_turn_or_terminal(timeout=self.step_timeout)
        self._seated = True
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
        except TableError as exc:
            # A real error the server sent back (not_your_turn,
            # same_owner_already_seated, ...) — TableError is a BluffedError
            # subclass, so this used to get swallowed by the branch below
            # and reported as an opaque "connection_lost" with no way to
            # tell a table-rules violation from an actual dropped socket.
            return self._last_obs, 0.0, False, True, {"reason": "table_error", "error": exc.code}
        except BluffedError as exc:
            return self._last_obs, 0.0, False, True, {"reason": "connection_lost", "error": str(exc)}

        chips_after = self._chips_now(obs)
        reward = float((chips_after or 0) - (chips_before or 0))
        terminated = obs.hand_over
        info: dict[str, Any] = {"phase": obs.phase}
        return obs, reward, terminated, False, info

    def leave(self) -> None:
        if self._ws:
            self._send({"type": "leave"})
        self._seated = False

    def close(self) -> None:
        # The server never drops a merely-disconnected player from their
        # seat (only an explicit "leave" does) — closing the socket while
        # still seated without this would leave a permanent zombie seat,
        # and the *next* reset() on this env would come back
        # already_seated since the old one was never actually stood up.
        if self._seated and self._ws:
            try:
                self._ws.send(json.dumps({"type": "leave"}))
                time.sleep(0.2)
            except Exception:
                pass
            self._seated = False

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
