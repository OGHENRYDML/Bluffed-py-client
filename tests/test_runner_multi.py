from dataclasses import dataclass, field
from typing import Optional

import bluffed_client.runner as runner_module
from bluffed_client import TableConfig, run_forever_multi


@dataclass
class FakeObs:
    hand_over: bool = True
    phase: str = "handComplete"


class FakeEnv:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.reset_calls = 0
        self.closed = False

    def reset(self):
        self.reset_calls += 1
        return FakeObs(), {}

    def step(self, action):
        return FakeObs(), 0.0, True, False, {}

    def leave(self):
        pass

    def close(self):
        self.closed = True


class FakeAccount:
    def __init__(self):
        self.funded = []
        self.swept = []

    def fund(self, agent_id, micros):
        self.funded.append((agent_id, micros))

    def sweep(self, agent_id, micros=None):
        self.swept.append((agent_id, micros))


def test_run_forever_multi_runs_each_table_independently(monkeypatch):
    monkeypatch.setattr(
        runner_module, "get_agent_status", lambda base_url, api_key: {"availableMicros": 5_000_000}
    )

    events = []
    configs = [
        TableConfig(
            env=FakeEnv("https://bluffed.online", "key_a"),
            account=FakeAccount(),
            agent_id="agent_a",
            strategy=lambda obs: None,
            min_reserve=1,
            top_up_to=2,
            max_hands=1,
        ),
        TableConfig(
            env=FakeEnv("https://bluffed.online", "key_b"),
            account=FakeAccount(),
            agent_id="agent_b",
            strategy=lambda obs: None,
            min_reserve=1,
            top_up_to=2,
            max_hands=1,
        ),
    ]

    run_forever_multi(configs, on_event=lambda kind, data: events.append((kind, data)))

    assert configs[0].env.reset_calls == 1
    assert configs[1].env.reset_calls == 1
    assert configs[0].env.closed and configs[1].env.closed

    tagged_agent_ids = {data["agent_id"] for _kind, data in events}
    assert tagged_agent_ids == {"agent_a", "agent_b"}
