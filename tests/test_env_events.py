import json
import queue
import threading

import pytest

from bluffed_client.env import BluffedTableEnv
from bluffed_client.errors import TableError
from bluffed_client.observation import parse_observation


class FakeWs:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)

    def close(self):
        pass


class ScriptedWs:
    """Stands in for a real websocket.WebSocket: recv() hands out a fixed
    list of pre-built messages in order, then blocks (like a real idle
    socket would) until close() — used to drive reset()'s real connect
    path, background recv thread included, without a real network socket."""

    def __init__(self, messages):
        self._messages = list(messages)
        self._closed = threading.Event()
        self.sent = []

    def recv(self):
        if self._messages:
            return json.dumps(self._messages.pop(0))
        self._closed.wait()
        raise OSError("closed")

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self._closed.set()


def _player(seat=0, chips=1000, has_acted=False):
    return {
        "id": "me",
        "name": "me",
        "seat": seat,
        "chips": chips,
        "bet": 0,
        "folded": False,
        "allIn": False,
        "hasActed": has_acted,
        "sittingOut": False,
        "connected": True,
        "isYou": True,
        "holeCards": None,
    }


def _state_msg(phase, current_turn_seat=None, max_seats=6, hand_number=1, has_acted=False, me_seat=None):
    if me_seat is None:
        me_seat = current_turn_seat or 0
    return {
        "type": "state",
        "state": {
            "id": "t1",
            "phase": phase,
            "handNumber": hand_number,
            "maxSeats": max_seats,
            "dealerSeat": None,
            "currentTurnSeat": current_turn_seat,
            "currentBet": 0,
            "minRaise": 0,
            "smallBlind": 1,
            "bigBlind": 2,
            "pot": 0,
            "community": [],
            "players": [_player(seat=me_seat, has_acted=has_acted)],
            "winners": None,
            "log": [],
        },
    }


def test_await_turn_or_terminal_announces_waiting_for_players_once():
    # A lone funded agent sits in phase "waiting" until a second player
    # joins — normal, but indistinguishable from a stuck connection without
    # this event. Pushed twice to confirm it only fires once, not per message.
    events = []
    env = BluffedTableEnv("bk_live_fake", on_event=lambda kind, data: events.append((kind, data)))
    env._messages = queue.Queue()
    env._messages.put(_state_msg("waiting"))
    env._messages.put(_state_msg("waiting"))
    env._messages.put(_state_msg("handComplete"))

    obs = env._await_turn_or_terminal(timeout=1.0)

    assert obs.hand_over
    assert [kind for kind, _data in events] == ["waiting_for_players"]
    assert events[0][1] == {"seats": 1, "max_seats": 6}


def test_await_turn_or_terminal_with_no_on_event_does_not_raise():
    env = BluffedTableEnv("bk_live_fake")
    env._messages = queue.Queue()
    env._messages.put(_state_msg("handComplete"))

    obs = env._await_turn_or_terminal(timeout=1.0)

    assert obs.hand_over


def test_waiting_for_players_prints_by_default_with_no_configuration(capsys):
    # The actual ask: a script that never wires up on_event should still see
    # something, instead of looking identical to a stuck connection.
    env = BluffedTableEnv("bk_live_fake")
    env._messages = queue.Queue()
    env._messages.put(_state_msg("waiting"))
    env._messages.put(_state_msg("handComplete"))

    env._await_turn_or_terminal(timeout=1.0)

    out = capsys.readouterr().out
    assert "Waiting for other players" in out


def test_close_stands_up_a_still_seated_player():
    # The server never drops a merely-disconnected player from their seat
    # (only an explicit "leave" does) — without this, close()ing (or
    # reset()ing, which calls close() first) while still seated leaves a
    # zombie seat, and the next sit for this same env comes back
    # already_seated.
    env = BluffedTableEnv("bk_live_fake")
    fake_ws = FakeWs()
    env._ws = fake_ws
    env._seated = True

    env.close()

    assert env._seated is False
    sent = [json.loads(m) for m in fake_ws.sent]
    assert {"type": "leave"} in sent


def test_close_sends_nothing_when_never_seated():
    env = BluffedTableEnv("bk_live_fake")
    fake_ws = FakeWs()
    env._ws = fake_ws
    env._seated = False

    env.close()

    assert fake_ws.sent == []


def test_leave_marks_not_seated():
    env = BluffedTableEnv("bk_live_fake")
    env._ws = FakeWs()
    env._seated = True

    env.leave()

    assert env._seated is False


def test_reset_waits_for_the_next_hand_instead_of_reconnecting_when_already_seated():
    # The old reset() always closed and reopened the socket, which meant
    # every hand re-ran the seat/anti-collusion check from scratch. Once
    # seated on a live connection, the next hand just deals on its own —
    # reset() should wait for it in place, not leave and reconnect.
    env = BluffedTableEnv("bk_live_fake")
    fake_ws = FakeWs()
    env._ws = fake_ws
    env._seated = True
    env._last_obs = parse_observation(_state_msg("handComplete", hand_number=1)["state"])
    env._messages = queue.Queue()
    env._messages.put(_state_msg("handComplete", hand_number=1))  # stale rebroadcast of the hand that just ended
    env._messages.put(_state_msg("preflop", current_turn_seat=0, hand_number=2))

    obs, _info = env.reset()

    assert obs.hand_number == 2
    assert obs.my_turn
    assert env._ws is fake_ws  # never reconnected
    assert fake_ws.sent == []  # no fresh "sit" — still the same seat


def test_reset_reconnects_when_not_currently_seated(monkeypatch):
    # First call (nothing seated yet), or any call after leave()/close(),
    # must go through the real connect+sit path — not the "already seated,
    # just wait" branch, which would blow up reading
    # self._last_obs.hand_number on a None _last_obs.
    import bluffed_client.env as env_module

    def fake_create_connection(url, timeout):
        raise RuntimeError("sentinel: reached the real connect path")

    monkeypatch.setattr(env_module.websocket, "create_connection", fake_create_connection)

    env = BluffedTableEnv("bk_live_fake")
    env._ws = None
    env._seated = False

    try:
        env.reset()
        assert False, "expected the sentinel RuntimeError from create_connection"
    except RuntimeError as exc:
        assert "sentinel" in str(exc)


def test_reset_mid_hand_does_not_wait_for_a_new_hand_to_start():
    # reset() is also used to recover from a truncated step (e.g.
    # not_your_turn from an action based on a stale local view) — not just
    # to advance between hands. If the last obs we saw wasn't itself
    # hand_over, there's no "already-seen hand end" to skip past, and
    # filtering by hand_number would wrongly wait for the *next* hand
    # instead of noticing we're already back on the clock in this one.
    env = BluffedTableEnv("bk_live_fake", step_timeout=1.0)
    env._ws = FakeWs()
    env._seated = True
    env._last_obs = parse_observation(_state_msg("preflop", hand_number=1)["state"])  # not hand_over
    assert not env._last_obs.hand_over
    env._messages = queue.Queue()
    env._messages.put(_state_msg("preflop", current_turn_seat=0, hand_number=1))  # still hand 1, my turn now

    obs, _info = env.reset()

    assert obs.hand_number == 1
    assert obs.my_turn


def test_reset_retries_a_fresh_connect_on_a_transient_table_error(monkeypatch):
    import bluffed_client.env as env_module

    sockets = [
        ScriptedWs([{"type": "error", "error": "table_full"}]),
        ScriptedWs([_state_msg("preflop", current_turn_seat=0, hand_number=1)]),
    ]
    monkeypatch.setattr(env_module.websocket, "create_connection", lambda url, timeout: sockets.pop(0))
    monkeypatch.setattr(env_module.time, "sleep", lambda *_: None)  # skip the real backoff

    env = BluffedTableEnv("bk_live_fake", step_timeout=2.0, connect_timeout=1.0)
    events = []
    env.on_event = lambda kind, data: events.append((kind, data))

    obs, _info = env.reset()

    assert obs.hand_number == 1
    assert env._seated is True
    assert [k for k, _d in events] == ["connecting", "connected", "retrying_seat", "connecting", "connected"]


def test_reset_gives_up_after_exhausting_retries(monkeypatch):
    import bluffed_client.env as env_module

    monkeypatch.setattr(
        env_module.websocket,
        "create_connection",
        lambda url, timeout: ScriptedWs([{"type": "error", "error": "table_full"}]),
    )
    monkeypatch.setattr(env_module.time, "sleep", lambda *_: None)

    env = BluffedTableEnv("bk_live_fake", step_timeout=2.0, connect_timeout=1.0)

    with pytest.raises(TableError):
        env.reset()


def test_reset_does_not_retry_a_non_transient_table_error(monkeypatch):
    import bluffed_client.env as env_module

    attempts = []

    def fake_create_connection(url, timeout):
        attempts.append(1)
        return ScriptedWs([{"type": "error", "error": "buyin_out_of_range"}])

    monkeypatch.setattr(env_module.websocket, "create_connection", fake_create_connection)

    env = BluffedTableEnv("bk_live_fake", step_timeout=2.0, connect_timeout=1.0)

    with pytest.raises(TableError):
        env.reset()
    assert len(attempts) == 1


def test_require_acted_skips_a_stale_rebroadcast_of_the_turn_just_acted_on():
    # The exact live bug: another player sitting down elsewhere broadcasts
    # to everyone, including a socket mid-turn waiting on its own
    # already-sent action. That rebroadcast still shows my_turn=True with
    # hasActed still false, because the action hasn't landed yet — it must
    # not be mistaken for a fresh decision point.
    env = BluffedTableEnv("bk_live_fake")
    env._messages = queue.Queue()
    env._messages.put(_state_msg("preflop", current_turn_seat=0, has_acted=False))  # stale: pre-action
    # real: turn moved to the opponent (seat 1) — "me" stays at seat 0
    env._messages.put(_state_msg("preflop", current_turn_seat=1, has_acted=False, me_seat=0))

    obs = env._await_turn_or_terminal(timeout=1.0, require_acted=True)

    assert obs.current_turn_seat == 1
    assert obs.my_turn is False  # confirms the action was processed; nothing to act on


def test_require_acted_accepts_hasActed_true_on_an_immediate_next_turn():
    # Rare heads-up edge case: every other player is folded/all-in and it's
    # immediately this seat's turn again — hasActed already true is just as
    # valid a "my action landed" signal as the turn moving away.
    env = BluffedTableEnv("bk_live_fake")
    env._messages = queue.Queue()
    env._messages.put(_state_msg("preflop", current_turn_seat=0, has_acted=False))  # stale: pre-action
    env._messages.put(_state_msg("preflop", current_turn_seat=0, has_acted=True))  # real: acted, my turn again

    obs = env._await_turn_or_terminal(timeout=1.0, require_acted=True)

    assert obs.my_turn is True
    assert obs.me.has_acted is True


def test_require_acted_still_returns_immediately_on_hand_over():
    env = BluffedTableEnv("bk_live_fake")
    env._messages = queue.Queue()
    env._messages.put(_state_msg("preflop", current_turn_seat=0, has_acted=False))  # stale: pre-action
    env._messages.put(_state_msg("handComplete"))

    obs = env._await_turn_or_terminal(timeout=1.0, require_acted=True)

    assert obs.hand_over is True


def test_require_acted_false_is_unaffected_by_hasActed():
    # The default (reset()'s normal "wait for my turn") behavior must stay
    # exactly as it was — the very first my_turn=True, unacted state is
    # legitimately what it's waiting for, not something to skip past.
    env = BluffedTableEnv("bk_live_fake")
    env._messages = queue.Queue()
    env._messages.put(_state_msg("preflop", current_turn_seat=0, has_acted=False))

    obs = env._await_turn_or_terminal(timeout=1.0)

    assert obs.my_turn is True


def test_reset_reconnects_when_marked_seated_but_the_connection_died(monkeypatch):
    # _seated only ever flips to False via leave()/close() — a socket that
    # died on its own (recv loop hit an exception and set _closed) leaves
    # _seated True with nothing left to actually wait on. reset() must
    # still detect that and reconnect instead of blocking for a full
    # step_timeout on a queue nothing will ever feed again.
    import bluffed_client.env as env_module

    def fake_create_connection(url, timeout):
        raise RuntimeError("sentinel: reached the real connect path")

    monkeypatch.setattr(env_module.websocket, "create_connection", fake_create_connection)

    env = BluffedTableEnv("bk_live_fake")
    env._ws = FakeWs()
    env._seated = True
    env._closed.set()

    try:
        env.reset()
        assert False, "expected the sentinel RuntimeError from create_connection"
    except RuntimeError as exc:
        assert "sentinel" in str(exc)

