import bluffed_client.runner as runner_module
from bluffed_client import run_forever


class FakeObs:
    hand_over = True
    phase = "handComplete"


class FakeEnv:
    def __init__(self, api_key, base_url="https://bluffed.online", tier_id="t_low", on_event=None):
        self.api_key = api_key
        self.base_url = base_url
        self.tier_id = tier_id
        self.on_event = on_event
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


class LossyObs:
    def __init__(self, hand_over):
        self.hand_over = hand_over


def _make_lossy_env_class(rewards):
    """Like FakeEnv, but reset() starts a hand (hand_over=False) and step()
    ends it with the next reward off a shared, scripted queue — needed to
    exercise consecutive-loss tracking, which FakeEnv can't (its reset()
    returns hand_over=True immediately, so step() is never even called).
    The queue is shared across every instance this factory produces, since
    run_forever constructs a brand new env on every reconnect/hop."""
    shared_rewards = iter(rewards)

    class LossyEnv:
        def __init__(self, api_key, base_url="https://bluffed.online", tier_id="t_low", on_event=None):
            self.api_key = api_key
            self.base_url = base_url
            self.tier_id = tier_id
            self.on_event = on_event
            self.closed = False

        def reset(self):
            return LossyObs(hand_over=False), {}

        def step(self, action):
            reward = next(shared_rewards, 0.0)
            return LossyObs(hand_over=True), reward, True, False, {}

        def leave(self):
            pass

        def close(self):
            self.closed = True

    return LossyEnv


def test_pick_tier_for_balance_picks_richest_affordable():
    tier = runner_module._pick_tier_for_balance(5_000_000)  # $5 — between t_micro and t_low
    assert tier.id == "t_low"


def test_pick_tier_for_balance_falls_back_to_smallest_when_broke():
    tier = runner_module._pick_tier_for_balance(1)  # can't even cover t_pico's minimum
    assert tier.id == "t_pico"


def test_pick_tier_for_balance_picks_top_tier_when_rich():
    tier = runner_module._pick_tier_for_balance(1_000_000_000)  # $1,000
    assert tier.id == "t_ultra"


def test_auto_tier_is_on_by_default(monkeypatch):
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 1_000_000_000})
    monkeypatch.setattr(runner_module, "BluffedTableEnv", FakeEnv)

    events = []
    env = FakeEnv("key", tier_id="t_low")
    run_forever(env, FakeAccount(), "agent_1", lambda obs: None, max_hands=1, on_event=lambda k, d: events.append((k, d)))

    tier_changes = [d for k, d in events if k == "tier_changed"]
    assert tier_changes == [{"from": "t_low", "to": "t_ultra"}]


def test_auto_tier_disabled_explicitly_never_switches(monkeypatch):
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 1_000_000_000})
    monkeypatch.setattr(runner_module, "BluffedTableEnv", FakeEnv)

    events = []
    env = FakeEnv("key", tier_id="t_low")
    run_forever(
        env,
        FakeAccount(),
        "agent_1",
        lambda obs: None,
        auto_tier=False,
        hop_after_losses=None,
        max_hands=1,
        on_event=lambda k, d: events.append((k, d)),
    )

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


def test_hop_after_losses_switches_tables_after_the_streak(monkeypatch):
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 1_000_000_000})
    EnvClass = _make_lossy_env_class([-1, -1, -1])  # three losses in a row
    monkeypatch.setattr(runner_module, "BluffedTableEnv", EnvClass)

    events = []
    env = EnvClass("key", tier_id="t_low")
    run_forever(
        env,
        FakeAccount(),
        "agent_1",
        lambda obs: None,
        auto_tier=False,  # isolate from tier switching
        hop_after_losses=3,
        max_hands=4,  # one extra iteration so the post-streak check actually runs
        on_event=lambda k, d: events.append((k, d)),
    )

    hops = [d for k, d in events if k == "table_hopped"]
    assert hops == [{"tier": "t_low", "after_losses": 3}]


def test_hop_after_losses_streak_resets_on_a_win(monkeypatch):
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": 1_000_000_000})
    # Same shape as the test above (5 hands, threshold 3) but the third hand
    # wins, so the streak that would otherwise trigger a hop never rebuilds.
    EnvClass = _make_lossy_env_class([-1, -1, 1, -1, -1])
    monkeypatch.setattr(runner_module, "BluffedTableEnv", EnvClass)

    events = []
    env = EnvClass("key", tier_id="t_low")
    run_forever(
        env,
        FakeAccount(),
        "agent_1",
        lambda obs: None,
        auto_tier=False,
        hop_after_losses=3,
        max_hands=5,
        on_event=lambda k, d: events.append((k, d)),
    )

    assert [d for k, d in events if k == "table_hopped"] == []


def test_hop_after_losses_is_skipped_on_a_hand_auto_tier_already_reconnected_on(monkeypatch):
    # $25 (t_mid) for two hands, then drops to $0.30 (t_pico) right as the
    # loss streak also crosses the hop threshold — only the tier change
    # should fire, not also a same-iteration table hop.
    balances = iter([25_000_000, 25_000_000, 300_000])
    monkeypatch.setattr(runner_module, "get_agent_status", lambda *a, **k: {"availableMicros": next(balances)})
    EnvClass = _make_lossy_env_class([-1, -1, -1])
    monkeypatch.setattr(runner_module, "BluffedTableEnv", EnvClass)

    events = []
    env = EnvClass("key", tier_id="t_mid")
    run_forever(
        env,
        FakeAccount(),
        "agent_1",
        lambda obs: None,
        auto_tier=True,
        hop_after_losses=2,
        max_hands=3,
        on_event=lambda k, d: events.append((k, d)),
    )

    kinds = [k for k, _d in events if k in ("tier_changed", "table_hopped")]
    assert kinds == ["tier_changed"]
