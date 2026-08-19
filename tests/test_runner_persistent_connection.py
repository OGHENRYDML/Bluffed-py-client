import bluffed_client.runner as runner_module
from bluffed_client import run_forever


class FakeObs:
    def __init__(self, hand_over):
        self.hand_over = hand_over
        self.phase = "handComplete" if hand_over else "preflop"


class FakeEnv:
    """Counts reset()/leave()/close() calls across several hands, the way a
    real BluffedTableEnv's would if run_forever kept reconnecting on every
    hand instead of reusing one connection."""

    def __init__(self):
        self.tier_id = "t_low"
        self.base_url = "https://bluffed.online"
        self.api_key = "key"
        self.reset_calls = 0
        self.leave_calls = 0
        self.close_calls = 0

    def reset(self):
        self.reset_calls += 1
        return FakeObs(False), {}

    def step(self, action):
        return FakeObs(True), 1_000.0, True, False, {}

    def leave(self):
        self.leave_calls += 1

    def close(self):
        self.close_calls += 1


class FakeAccount:
    def fund(self, agent_id, micros):
        pass

    def sweep(self, agent_id, micros=None):
        pass


def test_run_forever_stays_connected_across_hands(monkeypatch):
    # reset() gets called once per hand (it's the one that waits for the
    # next hand to deal), but leave()/close() must only happen once, after
    # the whole run — not after every single hand. Reconnecting per hand
    # was the old behavior and is what raced the anti-collusion check when
    # several of one owner's agents reconnected at the same time.
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 5_000_000})

    env = FakeEnv()
    run_forever(env, FakeAccount(), "agent_1", lambda obs: None, max_hands=3)

    assert env.reset_calls == 3
    assert env.leave_calls == 1
    assert env.close_calls == 1


class FailingThenFakeEnv(FakeEnv):
    def __init__(self):
        super().__init__()
        self._first = True

    def reset(self):
        if self._first:
            self._first = False
            raise RuntimeError("table did not respond in time")
        return super().reset()


def test_run_forever_closes_the_connection_on_error_to_force_a_real_reconnect(monkeypatch):
    # A failure mid-run (dead socket, table error) has to close the
    # connection so the *next* reset() reconnects from scratch instead of
    # waiting forever on a queue nothing will ever feed again.
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 5_000_000})
    monkeypatch.setattr(runner_module, "time", type("T", (), {"sleep": staticmethod(lambda *_: None)}))

    env = FailingThenFakeEnv()
    events = []
    run_forever(
        env, FakeAccount(), "agent_1", lambda obs: None, max_hands=1, on_event=lambda k, d: events.append((k, d))
    )

    assert [k for k, _d in events] == ["error", "hand_complete"]
    assert env.close_calls == 2  # once right after the error, once at the very end
