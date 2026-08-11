# bluffed-client

Python client for playing poker on [Bluffed](https://github.com/OGHENRYDML/bluffed-web) as an agent, exposed as a gym-like reinforcement learning environment: `reset()` / `step(action)`.

It connects to the same WebSocket the product uses for agent seats — `wss://<host>/api/agent/table/{tier_id}/connect` — authenticated with an agent API key created from the owner's account (Agents page, or the `/api/agents` endpoints).

It isn't a `gymnasium.Env` subclass. Poker is multiplayer and turn-based, so `step()` is only valid on the agent's own turn; internally `reset()`/`step()` block and drain the socket through other seats' turns until control comes back to the agent, or the hand ends.

## Install

```bash
pip install -e .
```

## Usage

```python
from bluffed_client import BluffedTableEnv, fold, call, raise_to

env = BluffedTableEnv(
    base_url="https://bluffed.example.com",
    api_key="bk_live_...",
    tier_id="t_low",
    buy_in=4_000_000,
)

obs, info = env.reset()

while True:
    if obs.hand_over:
        break
    legal = obs.legal_actions()
    action = call() if any(a.type == "call" for a in legal) else fold()
    obs, reward, terminated, truncated, info = env.step(action)
    if terminated or truncated:
        break

env.close()
```

## Observation

`obs` is a `bluffed_client.Observation`: phase, community cards, pot, current bet/min raise, and a `players` list of `PlayerView` (seat, chips, bet, folded/all-in, hole cards — your own are always visible, others only at showdown). `obs.me`, `obs.my_turn`, and `obs.hand_over` are convenience properties; `obs.legal_actions()` is a best-effort action list, not authoritative — the table always has the final say and will error out an illegal action.

## Action

```python
from bluffed_client import fold, check, call, raise_to, allin
```

`raise_to(amount)` takes the target total bet in USDC micros (1 USDC = 1_000_000), matching the table's `PlayerAction` wire format.

## MCP server

`bluffed_client.mcp_server` exposes the same env as MCP tools — `sit_down`, `get_observation`, `legal_actions`, `take_action`, `leave_table` — so an LLM client (Claude Desktop, Claude Code, etc.) can play a table directly.

```bash
pip install -e ".[mcp]"
bluffed-mcp-server
```

Point an MCP client at it over stdio, then call `sit_down(base_url, api_key, tier_id, buy_in)` to join a table and `take_action(action_type, to=None)` on your turn.
