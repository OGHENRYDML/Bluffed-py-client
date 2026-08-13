import bluffed_client.runner as runner_module
from bluffed_client import run_forever


class FakeObs:
    hand_over = True
    phase = "handComplete"


class FakeEnv:
    def __init__(self, api_key, base_url="https://bluffed.online", tier_id="t_low"):
        self.api_key = api_key
        self.base_url = base_url
        self.tier_id = tier_id
        self.closed = False

    def reset(self):
        return FakeObs(), {}

    def step(self, action):
        return FakeObs(), 0.0, True, False, {}

    def leave(self):
        pass

    def close(self):
        self.closed = True


class FakeAccount:
    def fund(self, agent_id, micros):
        pass

    def sweep(self, agent_id, micros=None):
        pass


def test_pick_tier_for_balance_picks_richest_affordable():
    tier = runner_module._pick_tier_for_balance(5_000_000)  # $5 — between t_micro and t_low
    assert tier.id == "t_low"


def test_pick_tier_for_balance_falls_back_to_smallest_when_broke():
    tier = runner_module._pick_tier_for_balance(1)  # can't even cover t_pico's minimum
    assert tier.id == "t_pico"


def test_pick_tier_for_balance_picks_top_tier_when_rich():
    tier = runner_module._pick_tier_for_balance(1_000_000_000)  # $1,000
    assert tier.id == "t_ultra"


def test_auto_tier_off_by_default_never_switches(monkeypatch):
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 1_000_000_000})
    monkeypatch.setattr(runner_module, "BluffedTableEnv", FakeEnv)

    events = []
    env = FakeEnv("key", tier_id="t_low")
    run_forever(env, FakeAccount(), "agent_1", lambda obs: None, max_hands=1, on_event=lambda k, d: events.append((k, d)))

    assert [k for k, _d in events] == ["hand_complete"]


def test_auto_tier_moves_up_when_balance_supports_a_richer_tier(monkeypatch):
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 25_000_000})  # $25
    monkeypatch.setattr(runner_module, "BluffedTableEnv", FakeEnv)

    events = []
    env = FakeEnv("key", tier_id="t_low")
    run_forever(
        env,
        FakeAccount(),
        "agent_1",
        lambda obs: None,
        auto_tier=True,
        max_hands=2,
        on_event=lambda k, d: events.append((k, d)),
    )

    tier_changes = [d for k, d in events if k == "tier_changed"]
    assert tier_changes == [{"from": "t_low", "to": "t_mid"}]


def test_auto_tier_moves_down_when_balance_shrinks(monkeypatch):
    balances = iter([25_000_000, 300_000])  # $25 (t_mid) then $0.30 (t_pico)
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": next(balances)})
    monkeypatch.setattr(runner_module, "BluffedTableEnv", FakeEnv)

    events = []
    env = FakeEnv("key", tier_id="t_mid")
    run_forever(
        env,
        FakeAccount(),
        "agent_1",
        lambda obs: None,
        auto_tier=True,
        max_hands=2,
        on_event=lambda k, d: events.append((k, d)),
    )

    tier_changes = [d for k, d in events if k == "tier_changed"]
    assert tier_changes == [{"from": "t_mid", "to": "t_pico"}]
