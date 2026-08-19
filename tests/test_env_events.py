import queue

from bluffed_client.env import BluffedTableEnv


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
