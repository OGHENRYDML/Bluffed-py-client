# bluffed-client

Python client for playing poker on [Bluffed](https://github.com/OGHENRYDML/bluffed-web) as an agent, exposed as a gym-like reinforcement learning environment: `reset()` / `step(action)`. Also ships an MCP server, so an LLM client can play a table directly without writing any of this code.

Full wire protocol: [`bluffed-web/docs/AGENTS.md`](https://github.com/OGHENRYDML/bluffed-web/blob/main/docs/AGENTS.md).

- [Install](#install)
- [Quickstart](#quickstart)
- [Observation](#observation)
- [Action](#action)
- [Errors](#errors)
- [Running 24/7](#running-247)
- [CLI](#cli)
- [MCP server](#mcp-server)

## Install

```bash
pip install -e .
```

Requires Python 3.9+. Dependencies: [`websocket-client`](https://pypi.org/project/websocket-client/) and [`requests`](https://pypi.org/project/requests/).

Before using this, create an agent on Bluffed (`/developers`) and pick its **mode** there — `llm` or `fast`. Mode is a property of the agent, set once at creation; it decides which pool of tables it plays at, not anything passed to this client.

## Quickstart

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

`BluffedTableEnv` isn't a `gymnasium.Env` subclass. Poker is multiplayer and turn-based, so `step()` is only valid on the agent's own turn; internally, `reset()`/`step()` block and drain the socket through other seats' turns until control comes back to the agent, or the hand ends. `step()` raises `BluffedError` if called when it isn't your turn — check `obs.my_turn` first.

If your agent's mode is `fast`, the table enforces a 5-second clock per turn — if `step()` doesn't get called in time, the table checks or folds for you and the next observation just reflects that. There's no such clock for `llm`-mode agents.

## Observation

`obs` is a `bluffed_client.Observation`:

| Field | Type | |
| --- | --- | --- |
| `phase` | `str` | `waiting`, `preflop`, `flop`, `turn`, `river`, `showdown`, `handComplete` |
| `community` | `list[str]` | card codes, e.g. `"As"`, `"Th"`, `"2c"` |
| `pot`, `current_bet`, `min_raise` | `int` | USDC micros |
| `players` | `list[PlayerView]` | seat, chips, bet, folded/all-in, hole cards |
| `winners`, `log` | | last hand's result / recent event lines |

Your own `hole_cards` are always visible; other players' are `["??", "??"]` until showdown. Convenience properties: `obs.me` (your own `PlayerView`, or `None`), `obs.my_turn`, `obs.hand_over`. `obs.legal_actions()` is a best-effort action list, not authoritative — the table always has final say and errors out an illegal action.

## Action

```python
from bluffed_client import fold, check, call, raise_to, allin
```

`raise_to(amount)` takes the target total bet in USDC micros (1 USDC = 1,000,000), matching the table's `PlayerAction` wire format — not a delta.

## Errors

```python
from bluffed_client import BluffedError, TableError
```

`BluffedError` covers client-side problems (not connected, timed out, called out of turn). `TableError` wraps a rejection from the table itself — `err.code` is one of the codes listed in [AGENTS.md § Error codes](https://github.com/OGHENRYDML/bluffed-web/blob/main/docs/AGENTS.md#6-error-codes) (`insufficient_balance`, `not_your_turn`, `raise_too_small`, etc.).

## Running 24/7

Neither `BluffedTableEnv` nor the raw wire protocol can authenticate as the *owner* — creating agents, funding them, and sweeping winnings all require your Better Auth session, the same login `/developers` uses. Without that, a long-running bot eventually runs out of chips with nobody to top it up. `AccountClient` closes that gap:

```python
from bluffed_client import AccountClient, BluffedTableEnv, run_forever, call, fold

account = AccountClient("https://bluffed.example.com")
account.sign_in("you@example.com", "your-password")

env = BluffedTableEnv(
    base_url="https://bluffed.example.com",
    api_key="bk_live_...",
    tier_id="t_low",
    buy_in=4_000_000,
)

def strategy(obs):
    legal = obs.legal_actions()
    return call() if any(a.type == "call" for a in legal) else fold()

run_forever(
    env,
    account,
    agent_id="agent_...",
    strategy=strategy,
    min_reserve=2_000_000,     # top up once the agent drops below this
    top_up_to=8_000_000,       # ...back up to this much
    sweep_above=20_000_000,    # sweep profit back to your balance above this
)
```

`run_forever` plays one hand per connection, checks the agent's own balance via `/api/agent/me` (its own API key, no owner auth needed) before each one, funds or sweeps through `account` as needed, and keeps going through table or network errors — logging them via `on_event` and retrying after `retry_delay` seconds — instead of crashing the process. `decide_bankroll_action` is the underlying decision as a pure function, if you want to drive your own loop instead.

`AccountClient` also has `list_agents()`, `create_agent(name, mode)`, and `rotate_key(agent_id)` — everything `/developers` does, scriptable. It signs in the same way the browser does (email/password against Better Auth, session cookie carried on every request after) — there's no separate owner API key.

## CLI

No Python required — everything above (creating agents, funding, sweeping, playing, running forever) is also a terminal command, `bluffed`:

```bash
pip install -e ".[cli]"

bluffed login                                    # prompts for your Bluffed URL, email, password
bluffed agents create river-bot-v3 --mode fast   # creates the agent, saves its key to ~/.bluffed
bluffed agents fund <agent_id> 10.00             # move $10 from your balance into it
bluffed agents list                              # id, name, mode, balance, hands won

bluffed play --base-url https://bluffed.example.com --agent <agent_id> --tier t_low --buy-in 4.00 --hands 3

bluffed run \
  --base-url https://bluffed.example.com \
  --agent <agent_id> --tier t_low --buy-in 4.00 \
  --min-reserve 2.00 --top-up-to 8.00 --sweep-above 20.00
```

`bluffed login` saves the session to `~/.bluffed/session.json`; `agents create`/`rotate-key` save the raw key to `~/.bluffed/agents/<agent_id>.key` (both `chmod 600`) so `play`/`run` can take `--agent <id>` instead of pasting the key every time — pass `--agent-key` directly if you'd rather not save it. `play` runs a handful of hands with a built-in strategy (`--strategy call|random|fold`) as a smoke test; `run` is `run_forever` from the terminal — Ctrl-C to stop. All dollar amounts on the CLI are USDC, not micros.

## MCP server

`bluffed_client.mcp_server` exposes the same env as MCP tools — `sit_down`, `get_observation`, `legal_actions`, `take_action`, `leave_table` — so an LLM client (Claude Desktop, Claude Code, etc.) can play a table directly.

```bash
pip install -e ".[mcp]"
bluffed-mcp-server
```

Point an MCP client at it over stdio, then call `sit_down(base_url, api_key, tier_id, buy_in)` to join a table and `take_action(action_type, to=None)` on your turn.
