import json
import queue
import threading
import time
from typing import Any, Callable, Optional, Tuple

import websocket

from .actions import Action
from .defaults import DEFAULT_BASE_URL
from .errors import BluffedError, StillWaitingAlone, TableError
from .money import fmt_usdc
from .observation import Observation, parse_observation
from .tiers import DEFAULT_TIER_ID, get_tier

OnEvent = Callable[[str, dict], None]

# Errors a fresh connect+sit is worth retrying for — both are inherent to
# table assignment being a soft, racy index rather than an outright "you
# can never do this" rejection. See reset()'s retry loop.
_TRANSIENT_SIT_ERRORS = {"table_full", "same_owner_already_seated"}
_MAX_SIT_ATTEMPTS = 4

# How long a *fresh* connect+sit will sit in phase "waiting" with no second
# player before giving up on this specific table and trying assignTable
# again. assignTable's atomic seat reservation guarantees no table is ever
# oversold — several near-simultaneous connects can still land on different
# tables instead of converging on one, though (a burst of joins racing each
# other, not a correctness bug — see assignTable's own notes). Without this,
# the unlucky one just sits alone for the full step_timeout before failing
# outright; this retries well before that, same as a table_full/
# same_owner_already_seated rejection already does.
_LONELY_WAIT_TIMEOUT = 12.0


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
    elif kind == "retrying_seat":
        print(f"Table was full or already occupied (attempt {data.get('attempt')}) — trying a different one...")
    elif kind == "busted_out":
        print(f"Busted out at {data.get('table_id')} — the table removed you (0 chips, no rebuy).")
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
    elif kind == "table_hopped":
        print(f"Left the table after {data.get('after_losses')} losing hands in a row — finding new opponents...")
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
            if msg.get("type") == "ping":
                # Answered right here in the recv thread, not queued for the
                # caller to pick up — the server is probing whether this
                # connection is still alive at all (see PING_CHECK_INTERVAL_MS
                # server-side), and the caller's main thread could be off
                # doing something slow (an LLM call, a long dumb_strategy)
                # that has nothing to do with whether the socket itself is
                # still good. Replying from the queue would tie "are you
                # alive" to "are you currently free", which defeats the point.
                try:
                    ws.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass
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
            if self._seated and obs.me is None:
                # The server removed us from the table without an explicit
                # leave() on our end — busting out (0 chips, no rebuy) is the
                # only way that happens today. Raise instead of handing back
                # an observation with me=None: a caller reading obs.me.chips
                # would crash on it anyway, and reset()'s "already seated"
                # branch would otherwise wait forever on a turn that can
                # never come for a seat we no longer have. Clearing _seated
                # first means a caller that reacts by calling reset() again
                # gets a real fresh connect+sit, not another wait.
                self._seated = False
                self._emit("busted_out", {"table_id": obs.table_id})
                raise TableError("removed_from_table")
            return obs
        return None

    def _emit(self, kind: str, data: dict) -> None:
        if self.on_event is not None:
            self.on_event(kind, data)
        else:
            default_log(kind, data)

    def _await_turn_or_terminal(
        self,
        timeout: float,
        after_hand_number: Optional[int] = None,
        require_acted: bool = False,
        lonely_wait_timeout: Optional[float] = None,
    ) -> Observation:
        # `after_hand_number` is set when we're already seated and waiting
        # for the *next* hand on the same connection (see reset()) — without
        # it, a late/duplicate broadcast of the hand that just ended would
        # satisfy `obs.hand_over` immediately and we'd report a hand as
        # played without ever having seen a card of it.
        #
        # `require_acted` is set by step(), after it's already sent an
        # action — every mutation broadcasts to every socket, including
        # ones that don't touch the current hand at all (another player
        # sitting down elsewhere, a disconnect tick), and a broadcast that
        # landed before the server got around to processing *our* action
        # looks identical to a genuine "it's your turn" state: my_turn,
        # phase, pot, current_bet all still read exactly as they did before
        # we acted. hasActed is the one field that actually flips once the
        # action is processed, so once we're waiting on our own action's
        # result, "still my turn, still haven't acted" can only mean this
        # broadcast predates it — keep waiting instead of resending.
        deadline = time.monotonic() + timeout
        announced_waiting = False
        waiting_since: Optional[float] = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BluffedError("timed out waiting for the table")
            # Whichever deadline is sooner wins the wait — but *which one*
            # matters too: if nothing else ever broadcasts again (the
            # realistic case for a table that's genuinely alone, not just a
            # late message), _next_message() below times out and raises the
            # generic BluffedError on its own, never reaching a fresh loop
            # iteration where the plain `remaining <= 0` check above could
            # tell the two deadlines apart. lonely_limited records which one
            # this particular wait was actually bounded by, so a timeout can
            # still be attributed correctly even when it's discovered inside
            # _next_message() instead of at the top of this loop.
            lonely_limited = False
            if lonely_wait_timeout is not None and waiting_since is not None:
                lonely_remaining = waiting_since + lonely_wait_timeout - time.monotonic()
                if lonely_remaining <= 0:
                    raise StillWaitingAlone(f"still alone after {lonely_wait_timeout}s — likely landed on a fragmented table")
                if lonely_remaining < remaining:
                    remaining = lonely_remaining
                    lonely_limited = True
            try:
                msg = self._next_message(remaining)
            except BluffedError:
                if lonely_limited:
                    raise StillWaitingAlone(f"still alone after {lonely_wait_timeout}s — likely landed on a fragmented table")
                raise
            obs = self._handle_message(msg)
            if obs is None:
                continue
            # Not a bug, just nobody else at the table yet — surface it once so
            # a caller waiting on step_timeout can tell "stuck" from "normal".
            if obs.phase == "waiting" and not announced_waiting:
                announced_waiting = True
                self._emit("waiting_for_players", {"seats": len(obs.players), "max_seats": obs.max_seats})
            if obs.phase == "waiting":
                if waiting_since is None:
                    waiting_since = time.monotonic()
            else:
                waiting_since = None
            if after_hand_number is not None and obs.hand_number == after_hand_number:
                continue
            if obs.hand_over:
                return obs
            if require_acted:
                # Confirmation our action was processed is the turn moving
                # away from us, or (rare — every other player folded/all-in
                # and it's immediately our turn again) hasActed already
                # showing true. "Still my turn, still haven't acted" is the
                # one state that can only be a stale pre-action rebroadcast.
                me = obs.me
                if not obs.my_turn or (me is not None and me.has_acted):
                    return obs
                continue
            if obs.my_turn:
                return obs

    def _chips_now(self, obs: Optional[Observation] = None) -> Optional[int]:
        obs = obs or self._last_obs
        if obs is None:
            return self._prev_chips
        me = obs.me
        return me.chips if me else self._prev_chips

    def reset(self) -> Tuple[Observation, dict]:
        # Already sitting at a live table on a live connection — the next
        # hand deals on its own; just wait for it instead of leaving and
        # reconnecting. Reconnecting on every reset() (the old behavior)
        # meant every hand re-ran the seat/anti-collusion check from
        # scratch, which is slow on its own and, when several of one
        # owner's agents reconnect in the same instant, races that check:
        # it reads the seats currently at the table before any of the
        # other in-flight reconnects have actually landed, so more than
        # one of them can slip past a guard that's only ever supposed to
        # allow one seat per owner per table.
        if self._ws is not None and self._seated and not self._closed.is_set():
            # Only filter out a stale rebroadcast of the hand we already
            # know ended — if the last obs we saw wasn't itself hand_over
            # (e.g. reset() got called to recover from a mid-hand truncated
            # step, not because the hand actually finished), there's no
            # "already seen this hand end" state to skip past, and filtering
            # by hand_number here would wait for the *next* hand to start
            # even if the current one is still going and about to need us.
            after_hand_number = self._last_obs.hand_number if self._last_obs and self._last_obs.hand_over else None
            obs = self._await_turn_or_terminal(timeout=self.step_timeout, after_hand_number=after_hand_number)
            self._prev_chips = self._chips_now(obs)
            me = obs.me
            return obs, {"my_id": me.id if me else None}

        for attempt in range(1, _MAX_SIT_ATTEMPTS + 1):
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
            try:
                obs = self._await_turn_or_terminal(timeout=self.step_timeout, lonely_wait_timeout=_LONELY_WAIT_TIMEOUT)
            except TableError as exc:
                if exc.code in _TRANSIENT_SIT_ERRORS and attempt < _MAX_SIT_ATTEMPTS:
                    # Table assignment is a soft, best-effort index (see
                    # assignTable in apps/web) — several connects landing in
                    # the same instant can all get routed to the same table
                    # before any of their seats actually commit, so the
                    # losers see table_full (or, rarely, the anti-collusion
                    # check) even though a different table has room. A fresh
                    # connect re-runs assignment from scratch, which is
                    # usually enough to land somewhere that actually fits.
                    self._emit("retrying_seat", {"attempt": attempt, "error": exc.code})
                    time.sleep(0.5)
                    continue
                raise
            except StillWaitingAlone:
                if attempt < _MAX_SIT_ATTEMPTS:
                    # Same underlying cause as table_full above (a burst of
                    # near-simultaneous connects fragmenting across tables),
                    # just discovered by timing out alone instead of an
                    # outright rejection — assignTable's atomic reservation
                    # never oversells a seat, but it can't stop several
                    # connects from landing on different tables when they
                    # race close enough together. A fresh connect re-runs
                    # assignment from scratch, same fix as table_full.
                    self._emit("retrying_seat", {"attempt": attempt, "error": "still_waiting_alone"})
                    continue
                raise
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
            obs = self._await_turn_or_terminal(timeout=self.step_timeout, require_acted=True)
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
