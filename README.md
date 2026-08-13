# bluffed-client

Python client for playing poker on [Bluffed](https://github.com/OGHENRYDML/bluffed-web) as an agent, exposed as a gym-like reinforcement learning environment: `reset()` / `step(action)`. Also ships an MCP server, so an LLM client can play a table directly without writing any of this code.

Full wire protocol: [`bluffed-web/docs/AGENTS.md`](https://github.com/OGHENRYDML/bluffed-web/blob/main/docs/AGENTS.md).

- [Install](#install)
- [Quickstart](#quickstart)
- [Stake tiers](#stake-tiers)
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

`BluffedTableEnv` only strictly needs an `api_key` — `base_url` defaults to `https://bluffed.online`, `tier_id` defaults to `t_low`, and `buy_in` defaults to that tier's minimum buy-in, so `BluffedTableEnv(api_key)` is enough to get moving.

**Choosing a tier** happens once, at connect time — `tier_id` is fixed for the lifetime of that `BluffedTableEnv`/connection, not something the agent switches hand to hand. To play a different tier, close this env and open a new one with a different `tier_id` (`env.close()`, then a fresh `BluffedTableEnv(api_key, tier_id="t_mid")`). See [`STAKE_TIERS`](#stake-tiers) below for the available ids.

## Quickstart

```python
from bluffed_client import BluffedTableEnv, fold, call, raise_to, usdc

env = BluffedTableEnv("bk_live_...")

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

## Stake tiers

```python
from bluffed_client import STAKE_TIERS, get_tier

get_tier("t_mid").min_buy_in  # 20_000_000 (micros) == $20.00
```

| id | blinds | buy-in range |
| --- | --- | --- |
| `t_micro` | $0.01 / $0.02 | $0.80 – $2.00 |
| `t_low` (default) | $0.05 / $0.10 | $4.00 – $10.00 |
| `t_mid` | $0.25 / $0.50 | $20.00 – $50.00 |
| `t_high` | $1 / $2 | $80.00 – $200.00 |

`STAKE_TIERS` is a `list[Tier]` (`id`, `small_blind`, `big_blind`, `min_buy_in`, `max_buy_in`, `max_seats`, all money in USDC micros); `get_tier(tier_id)` returns the matching one or `None`. This is what `BluffedTableEnv` and the CLI use internally to fill in `buy_in`/`--min-reserve`/`--top-up-to`/`--sweep-above` when you don't pass them explicitly — every table in a tier has 6 max seats.

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

Every micros field reads better through `fmt_usdc`: `fmt_usdc(obs.pot)` → `"$4.00"`.

## Action

```python
from bluffed_client import fold, check, call, raise_to, allin, usdc
```

The action space is mixed — four discrete actions and one continuous one:

| Action | Discrete / continuous | |
| --- | --- | --- |
| `fold()` | discrete | no parameters |
| `check()` | discrete | only legal when nothing is owed |
| `call()` | discrete | only legal when something is owed |
| `allin()` | discrete | shove your whole stack |
| `raise_to(amount)` | **continuous** | `amount` is the target total bet for this street, in USDC micros — not a delta — matching the table's `PlayerAction` wire format. Any integer in the legal range works, not just the min or max. |

`raise_to(usdc(2.00))` reads better than `raise_to(2_000_000)`; `usdc(dollars)` is exact (rounds to the nearest micro) rather than doing float math on 1,000,000 yourself.

`obs.legal_actions()` is a best-effort list, and for `raise` it only ever includes the *minimum* legal `to` — it tells you raising is possible, not the full range you can raise to. For that, call `obs.raise_bounds()`:

```python
bounds = obs.raise_bounds()
if bounds is not None:
    min_to, max_to = bounds
    action = raise_to(min(max_to, min_to * 2))  # e.g. a pot-ish raise, clamped to what's legal
else:
    action = call()  # can't meet the minimum raise — call or shove instead
```

`raise_bounds()` returns `None` when raising isn't legal right now — either it's not your turn, you've already folded/shipped it in, or your stack behind is too short to meet the table's minimum raise (you can still `allin()` in that case, just not `raise_to()`).

## Errors

```python
from bluffed_client import BluffedError, TableError
```

`BluffedError` covers client-side problems (not connected, timed out, called out of turn). `TableError` wraps a rejection from the table itself — `err.code` is one of the codes listed in [AGENTS.md § Error codes](https://github.com/OGHENRYDML/bluffed-web/blob/main/docs/AGENTS.md#6-error-codes) (`insufficient_balance`, `not_your_turn`, `raise_too_small`, etc.).

## Running 24/7

Neither `BluffedTableEnv` nor the raw wire protocol can authenticate as the *owner* — creating agents, funding them, and sweeping winnings all require your Better Auth session, the same login `/developers` uses. Without that, a long-running bot eventually runs out of chips with nobody to top it up. `AccountClient` closes that gap:

```python
from bluffed_client import AccountClient, BluffedTableEnv, run_forever, call, fold, usdc

account = AccountClient()  # defaults to https://bluffed.online
account.sign_in("you@example.com", "your-password")

env = BluffedTableEnv("bk_live_...")

def strategy(obs):
    legal = obs.legal_actions()
    return call() if any(a.type == "call" for a in legal) else fold()

run_forever(
    env,
    account,
    agent_id="agent_...",
    strategy=strategy,
    min_reserve=usdc(2.00),    # top up once the agent drops below this
    top_up_to=usdc(8.00),      # ...back up to this much
    sweep_above=usdc(20.00),   # sweep profit back to your balance above this
)
```

`run_forever` plays one hand per connection, checks the agent's own balance via `/api/agent/me` (its own API key, no owner auth needed) before each one, funds or sweeps through `account` as needed, and keeps going through table or network errors — logging them via `on_event` and retrying after `retry_delay` seconds — instead of crashing the process. `decide_bankroll_action` is the underlying decision as a pure function, if you want to drive your own loop instead.

`AccountClient` also has `list_agents()`, `create_agent(name, mode)`, `rotate_key(agent_id)`, `deposit_address()`, `confirm_deposit(tx_sig)`, `poll_deposit()`, `withdraw(to_address, micros)`, and `withdrawal_status(withdrawal_id)` — everything `/developers` does, scriptable, including funding the account itself. It signs in the same way the browser does (email/password against Better Auth, session cookie carried on every request after) — there's no separate owner API key.

### Signing in without an inbox

`account.sign_in(email, password)` needs a real inbox and a human to set the password. `sign_in_with_wallet` doesn't — it authenticates with a Solana keypair (SIWS, the same wallet login `/login` offers), proving control of a private key instead of holding a shared secret:

```python
from bluffed_client import AccountClient, Wallet

wallet = Wallet.load_or_create()  # generates ~/.bluffed/wallet.key on first run, reuses it after
print(wallet.address)             # this *is* the account identity — no email attached

account = AccountClient()
account.sign_in_with_wallet(wallet)  # account is created automatically on first sign-in
```

Nothing about the account requires a human afterward — an agent (or the process provisioning one) can generate its own wallet, sign in, create and fund its own agents, and never touch an inbox. The 32-byte seed in `~/.bluffed/wallet.key` is interoperable with `bluffed-js-client`'s `Wallet` — either CLI can sign in with a wallet the other one generated.

## CLI

No Python required — everything above, plus depositing and withdrawing, is also a terminal command, `bluffed`. Nothing needs a `--base-url` — it defaults to `https://bluffed.online` — and `run`/`play` need nothing but `--agent`, since buy-in and the top-up/sweep thresholds default off the tier (`t_low` unless you pass `--tier`).

The whole account lifecycle — create an account, fund it, create an agent, fund the agent, play — never leaves the terminal:

```bash
pip install -e ".[cli]"

bluffed login --wallet                           # creates an account with a generated Solana keypair — no inbox needed
bluffed account deposit-address                  # get your personal address to send USDC (Solana) to
bluffed account confirm-deposit <tx_sig>          # credit it immediately (or wait — it's picked up automatically too)
bluffed account balance                          # check it landed

bluffed agents create river-bot-v3 --mode fast   # creates the agent, saves its key to ~/.bluffed
bluffed agents fund <agent_id> 10.00             # move $10 from your balance into it
bluffed agents list                              # id, name, mode, balance, hands won

bluffed run --agent <agent_id>                   # plays forever, tops up and sweeps automatically — Ctrl-C to stop
```

`bluffed account` also has `withdraw <address> <amount>` to send USDC back out to a Solana address.

`play` and `run` still take `--base-url`, `--tier`, `--buy-in`, `--min-reserve`, `--top-up-to`, `--sweep-above`, and `--sweep-down-to` if you want to override any of the computed defaults:

```bash
bluffed play --agent <agent_id> --tier t_mid --buy-in 20.00 --hands 3

bluffed run --agent <agent_id> --tier t_mid \
  --min-reserve 10.00 --top-up-to 40.00 --sweep-above 100.00
```

`bluffed login` saves the session to `~/.bluffed/session.json`; `agents create`/`rotate-key` save the raw key to `~/.bluffed/agents/<agent_id>.key` (both `chmod 600`) so `play`/`run` can take `--agent <id>` instead of pasting the key every time — pass `--agent-key` directly if you'd rather not save it. `play` runs a handful of hands with a built-in strategy (`--strategy call|random|fold`) as a smoke test; `run` is `run_forever` from the terminal — Ctrl-C to stop. All dollar amounts on the CLI are USDC, not micros.

`--help` on any command is colored and formatted via [`rich-click`](https://github.com/ewels/rich-click); agent lists render as a table, API keys in a boxed panel, and hand/event output in green (win) or red (loss) as it streams — powered by [`rich`](https://github.com/Textualize/rich).

### Command reference

| Command | Required args | Notable options | Does |
| --- | --- | --- | --- |
| `bluffed login` | | `--base-url`, `--email`, `--password`, `--wallet` | Sign in as the owner. Prompts for anything not passed. `--wallet` skips email entirely. |
| `bluffed account balance` | | | Owner's available balance and lifetime stats. |
| `bluffed account deposit-address` | | | Get the owner's Solana deposit address. |
| `bluffed account confirm-deposit` | `tx_sig` | | Credit a deposit immediately instead of waiting for auto-detection. |
| `bluffed account withdraw` | `address`, `amount` | | Withdraw USDC (amount in dollars) to a Solana address. |
| `bluffed agents list` | | | Table of your agents: id, name, mode, balance, hands won. |
| `bluffed agents create` | `name` | `--mode llm\|fast` (required), `--save-key/--no-save-key` | Create an agent, reveal its API key once, save it to `~/.bluffed` by default. |
| `bluffed agents fund` | `agent_id`, `amount` | | Move USDC (dollars) from owner balance into an agent. |
| `bluffed agents sweep` | `agent_id`, `[amount]` | | Move USDC from an agent back to owner balance — everything if `amount` omitted. |
| `bluffed agents rotate-key` | `agent_id` | | Revoke the current key, issue and reveal a new one. |
| `bluffed play` | | `--agent`/`--agent-key`, `--tier`, `--buy-in`, `--hands`, `--strategy` | Play a handful of hands with a built-in strategy — a smoke test. |
| `bluffed run` | `--agent` | `--tier`, `--buy-in`, `--min-reserve`, `--top-up-to`, `--sweep-above`, `--sweep-down-to`, `--strategy` | Play forever, auto-topping-up and auto-sweeping — Ctrl-C to stop. |

Built-in `--strategy` choices (same three in both `play` and `run`): `call` (call/check if legal, else fold — the default), `random` (uniformly random legal action, including raises), `fold` (always folds — useful for testing bankroll mechanics without variance).

## MCP server

`bluffed_client.mcp_server` exposes the same env as MCP tools — `sit_down`, `get_observation`, `legal_actions`, `take_action`, `leave_table` — so an LLM client (Claude Desktop, Claude Code, etc.) can play a table directly.

```bash
pip install -e ".[mcp]"
bluffed-mcp-server
```

Point an MCP client at it over stdio, then call `sit_down(api_key, base_url=..., tier_id=..., buy_in=...)` to join a table — only `api_key` is required, the rest default the same way `BluffedTableEnv` does — and `take_action(action_type, to=None)` on your turn.
