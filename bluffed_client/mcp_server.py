import dataclasses
from typing import Optional

from mcp.server import MCPServer

from .actions import ACTION_TYPES, Action
from .defaults import DEFAULT_BASE_URL
from .env import BluffedTableEnv
from .errors import BluffedError
from .tiers import DEFAULT_TIER_ID

server = MCPServer("bluffed-poker")

_env: Optional[BluffedTableEnv] = None


def _obs_dict(obs) -> dict:
    return dataclasses.asdict(obs)


def _require_env() -> BluffedTableEnv:
    if _env is None:
        raise BluffedError("not seated — call sit_down first")
    return _env


@server.tool()
def sit_down(
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    tier_id: str = DEFAULT_TIER_ID,
    buy_in: Optional[int] = None,
) -> dict:
    """Connect to a Bluffed table and sit down. buy_in is in USDC micros;
    omit it to use the tier's minimum."""
    global _env
    if _env is not None:
        _env.close()
    _env = BluffedTableEnv(api_key, base_url=base_url, tier_id=tier_id, buy_in=buy_in)
    obs, info = _env.reset()
    return {"observation": _obs_dict(obs), "info": info}


@server.tool()
def get_observation() -> dict:
    """Return the last known table state without taking an action."""
    env = _require_env()
    obs = env.last_observation
    if obs is None:
        raise BluffedError("no table state yet")
    return _obs_dict(obs)


@server.tool()
def legal_actions() -> list:
    """List the actions currently legal for this agent."""
    env = _require_env()
    obs = env.last_observation
    if obs is None:
        raise BluffedError("no table state yet")
    return [dataclasses.asdict(a) for a in obs.legal_actions()]


@server.tool()
def take_action(action_type: str, to: Optional[int] = None) -> dict:
    """Take one action on the agent's turn: fold, check, call, raise (with `to`), or allin."""
    if action_type not in ACTION_TYPES:
        raise BluffedError(f"invalid action_type {action_type!r} — must be one of {ACTION_TYPES}")
    env = _require_env()
    obs, reward, terminated, truncated, info = env.step(Action(action_type, to))
    return {
        "observation": _obs_dict(obs),
        "reward": reward,
        "terminated": terminated,
        "truncated": truncated,
        "info": info,
    }


@server.tool()
def leave_table() -> dict:
    """Stand up from the table and close the connection."""
    global _env
    if _env is not None:
        _env.leave()
        _env.close()
        _env = None
    return {"ok": True}


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
