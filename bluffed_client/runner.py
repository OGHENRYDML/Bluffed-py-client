import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .account import AccountClient
from .actions import Action
from .agent_self import get_agent_status
from .env import BluffedTableEnv
from .observation import Observation
from .tiers import STAKE_TIERS, Tier

Strategy = Callable[[Observation], Action]
OnEvent = Callable[[str, dict], None]


def _pick_tier_for_balance(available_micros: int) -> Tier:
    """The richest tier this balance can cover the minimum buy-in for —
    or, if it can't even cover the smallest tier's minimum, the smallest
    tier anyway (there's nowhere lower to go)."""
    affordable = [t for t in STAKE_TIERS if t.min_buy_in <= available_micros]
    if affordable:
        return max(affordable, key=lambda t: t.min_buy_in)
    return min(STAKE_TIERS, key=lambda t: t.min_buy_in)


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
    min_reserve: Optional[int] = None,
    top_up_to: Optional[int] = None,
    sweep_above: Optional[int] = None,
    sweep_down_to: Optional[int] = None,
    max_hands: Optional[int] = None,
    retry_delay: float = 5.0,
    on_event: Optional[OnEvent] = None,
    auto_tier: bool = False,
) -> None:
    """Play hands back to back, forever (or until `max_hands`), keeping the
    agent's own balance within [min_reserve, sweep_above] by pulling from
    and pushing to the owner's balance through `account` — skipped
    entirely if `min_reserve`/`top_up_to` are left `None`. Reconnects for
    every hand; a table or network error pauses `retry_delay` seconds and
    tries again rather than raising.

    `auto_tier=True` moves the agent to whatever stake tier its *current*
    balance actually affords before every hand — up when it's winning,
    down when it's losing — instead of playing a fixed tier until it can't
    afford the buy-in anymore. Opt-in: this changes which table the agent
    is at, out from under whatever `tier_id` `env` was built with, and it's
    a real behavior change someone should choose, not one silently applied.
    """
    hands = 0
    emit = on_event or (lambda kind, data: None)
    current_env = env

    while max_hands is None or hands < max_hands:
        try:
            status = get_agent_status(current_env.base_url, current_env.api_key)
            available = status["availableMicros"]

            if min_reserve is not None and top_up_to is not None:
                kind, amount = decide_bankroll_action(
                    available,
                    min_reserve=min_reserve,
                    top_up_to=top_up_to,
                    sweep_above=sweep_above,
                    sweep_down_to=sweep_down_to,
                )
                if kind == "fund":
                    account.fund(agent_id, amount)
                    emit("funded", {"micros": amount})
                    available += amount
                elif kind == "sweep":
                    account.sweep(agent_id, amount)
                    emit("swept", {"micros": amount})
                    available -= amount

            if auto_tier:
                target = _pick_tier_for_balance(available)
                if target.id != current_env.tier_id:
                    from_tier = current_env.tier_id
                    current_env.close()
                    current_env = BluffedTableEnv(
                        current_env.api_key, base_url=current_env.base_url, tier_id=target.id
                    )
                    emit("tier_changed", {"from": from_tier, "to": target.id})

            obs, _info = current_env.reset()
            while not obs.hand_over:
                obs, _reward, terminated, truncated, _info = current_env.step(strategy(obs))
                if terminated or truncated:
                    break
            current_env.leave()
            hands += 1
            emit("hand_complete", {"hands": hands})
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on any failure
            emit("error", {"error": str(exc)})
            time.sleep(retry_delay)
        finally:
            current_env.close()


@dataclass
class TableConfig:
    """One table's worth of run_forever() arguments — see run_forever_multi()."""

    env: BluffedTableEnv
    account: AccountClient
    agent_id: str
    strategy: Strategy
    min_reserve: Optional[int] = None
    top_up_to: Optional[int] = None
    sweep_above: Optional[int] = None
    sweep_down_to: Optional[int] = None
    max_hands: Optional[int] = None
    retry_delay: float = 5.0
    auto_tier: bool = False


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
            auto_tier=config.auto_tier,
        )

    threads = [threading.Thread(target=run_one, args=(c,), daemon=True) for c in configs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
