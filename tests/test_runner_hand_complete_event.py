import bluffed_client.runner as runner_module
from bluffed_client import run_forever


class FakeObs:
    def __init__(self, hand_over):
        self.hand_over = hand_over
        self.phase = "handComplete" if hand_over else "preflop"


class FakeEnv:
    def __init__(self):
        self.tier_id = "t_low"
        self.base_url = "https://bluffed.online"
        self.api_key = "key"
        self.closed = False

    def reset(self):
        return FakeObs(False), {}

    def step(self, action):
        return FakeObs(True), 42_000.0, True, False, {}

    def leave(self):
        pass

    def close(self):
        self.closed = True


class FakeAccount:
    def fund(self, agent_id, micros):
        pass

    def sweep(self, agent_id, micros=None):
        pass


def test_hand_complete_carries_real_chip_outcome_instead_of_just_a_count(monkeypatch):
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 5_000_000})

    events = []
    run_forever(
        FakeEnv(),
        FakeAccount(),
        "agent_1",
        lambda obs: None,
        max_hands=1,
        on_event=lambda kind, data: events.append((kind, data)),
    )

    hand_complete = next(data for kind, data in events if kind == "hand_complete")
    assert hand_complete == {"hands": 1, "chips_delta": 42_000.0, "won": True}
