import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .account import AccountClient
from .actions import Action
from .agent_self import get_agent_status
from .env import BluffedTableEnv
from .observation import Observation

Strategy = Callable[[Observation], Action]
OnEvent = Callable[[str, dict], None]


def decide_bankroll_action(
    available_micros: int,
    *,
    min_reserve: int,
    top_up_to: int,
    sweep_above: Optional[int] = None,
    sweep_down_to: Optional[int] = None,
) -> Tuple[Optional[str], int]:
    """Pure decision: given the agent's current balance, should it be
    topped up from the owner's balance, swept back to it, or left alone?
    Returns (None, 0) when no action is needed."""
    if available_micros < min_reserve:
        return "fund", top_up_to - available_micros
    if sweep_above is not None and available_micros > sweep_above:
        target = sweep_down_to if sweep_down_to is not None else sweep_above
        return "sweep", available_micros - target
    return None, 0


def run_forever(
    env: BluffedTableEnv,
    account: AccountClient,
    agent_id: str,
    strategy: Strategy,
    *,
    min_reserve: int,
    top_up_to: int,
    sweep_above: Optional[int] = None,
    sweep_down_to: Optional[int] = None,
    max_hands: Optional[int] = None,
    retry_delay: float = 5.0,
    on_event: Optional[OnEvent] = None,
) -> None:
    """Play hands back to back, forever (or until `max_hands`), keeping the
    agent's own balance within [min_reserve, sweep_above] by pulling from
    and pushing to the owner's balance through `account`. Reconnects for
    every hand; a table or network error pauses `retry_delay` seconds and
    tries again rather than raising."""
    hands = 0
    emit = on_event or (lambda kind, data: None)

    while max_hands is None or hands < max_hands:
        try:
            status = get_agent_status(env.base_url, env.api_key)
            kind, amount = decide_bankroll_action(
                status["availableMicros"],
                min_reserve=min_reserve,
                top_up_to=top_up_to,
                sweep_above=sweep_above,
                sweep_down_to=sweep_down_to,
            )
            if kind == "fund":
                account.fund(agent_id, amount)
                emit("funded", {"micros": amount})
            elif kind == "sweep":
                account.sweep(agent_id, amount)
                emit("swept", {"micros": amount})

            obs, _info = env.reset()
            while not obs.hand_over:
                obs, _reward, terminated, truncated, _info = env.step(strategy(obs))
                if terminated or truncated:
                    break
            env.leave()
            hands += 1
            emit("hand_complete", {"hands": hands})
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on any failure
            emit("error", {"error": str(exc)})
            time.sleep(retry_delay)
        finally:
            env.close()


@dataclass
class TableConfig:
    """One table's worth of run_forever() arguments — see run_forever_multi()."""

    env: BluffedTableEnv
    account: AccountClient
    agent_id: str
    strategy: Strategy
    min_reserve: int
    top_up_to: int
    sweep_above: Optional[int] = None
    sweep_down_to: Optional[int] = None
    max_hands: Optional[int] = None
    retry_delay: float = 5.0


def run_forever_multi(configs: List[TableConfig], on_event: Optional[OnEvent] = None) -> None:
    """Multi-table: run_forever() for each config, one per thread, blocking
    until all of them stop (which, with max_hands=None, is never — Ctrl-C
    stops the process instead).

    Give each table its own agent_id/account rather than reusing one agent
    across tables — run_forever's fund/sweep decisions read-then-write an
    agent's balance with no locking, so two tables sharing an agent can race
    each other into over-funding or duplicate sweeps. Separate agents means
    separate balances, so there's nothing to race.
    """
    emit = on_event or (lambda kind, data: None)

    def run_one(config: TableConfig) -> None:
        def tagged_emit(kind: str, data: dict) -> None:
            emit(kind, {**data, "agent_id": config.agent_id})

        run_forever(
            config.env,
            config.account,
            config.agent_id,
            config.strategy,
            min_reserve=config.min_reserve,
            top_up_to=config.top_up_to,
            sweep_above=config.sweep_above,
            sweep_down_to=config.sweep_down_to,
            max_hands=config.max_hands,
            retry_delay=config.retry_delay,
            on_event=tagged_emit,
        )

    threads = [threading.Thread(target=run_one, args=(c,), daemon=True) for c in configs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
