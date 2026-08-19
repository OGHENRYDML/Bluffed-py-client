import json
import queue

from bluffed_client.env import BluffedTableEnv


class FakeWs:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)

    def close(self):
        pass


def _player(seat=0, chips=1000):
    return {
        "id": "me",
        "name": "me",
        "seat": seat,
        "chips": chips,
        "bet": 0,
        "folded": False,
        "allIn": False,
        "sittingOut": False,
        "connected": True,
        "isYou": True,
        "holeCards": None,
    }


def _state_msg(phase, current_turn_seat=None, max_seats=6):
    return {
        "type": "state",
        "state": {
            "id": "t1",
            "phase": phase,
            "handNumber": 1,
            "maxSeats": max_seats,
            "dealerSeat": None,
            "currentTurnSeat": current_turn_seat,
            "currentBet": 0,
            "minRaise": 0,
            "smallBlind": 1,
            "bigBlind": 2,
            "pot": 0,
            "community": [],
            "players": [_player()],
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
