from typing import Optional

import click

from ..account import AccountClient, AccountError
from ..env import BluffedTableEnv
from ..errors import BluffedError
from ..runner import run_forever
from ..strategies import STRATEGIES
from . import config


def to_micros(amount: float) -> int:
    return round(amount * 1_000_000)


def fmt_usdc(micros: int) -> str:
    return f"${micros / 1_000_000:,.2f}"


def _account_from_session() -> AccountClient:
    session = config.load_session()
    if not session:
        raise click.ClickException("not logged in — run `bluffed login` first")
    account = AccountClient(session["base_url"])
    account.import_cookies(session["cookies"])
    return account


def _resolve_key(agent_id: Optional[str], agent_key: Optional[str]) -> str:
    if agent_key:
        return agent_key
    if agent_id:
        key = config.load_agent_key(agent_id)
        if key:
            return key
    raise click.ClickException("no API key — pass --agent-key, or --agent ID for a key saved by `agents create`")


@click.group()
def main():
    """Bluffed — play and manage poker agents from the command line."""


@main.command()
@click.option("--base-url", prompt="Bluffed URL", default="https://bluffed.example.com")
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True)
def login(base_url: str, email: str, password: str):
    """Sign in as the account owner — same login as the website."""
    account = AccountClient(base_url)
    try:
        account.sign_in(email, password)
    except AccountError as e:
        raise click.ClickException(str(e))
    config.save_session(base_url, account.export_cookies())
    click.echo(f"Signed in to {base_url}.")


@main.group()
def agents():
    """Create, fund, sweep, and list your agents."""


@agents.command("list")
def agents_list():
    """List your agents with their mode and balance."""
    account = _account_from_session()
    for a in account.list_agents():
        click.echo(f"{a['id']}  {a['name']:<20} {a['mode']:<5} {fmt_usdc(a['availableMicros']):>10}  {a['handsWon']} hands")


@agents.command("create")
@click.argument("name")
@click.option("--mode", type=click.Choice(["llm", "fast"]), required=True)
@click.option("--save-key/--no-save-key", default=True, help="save the API key to ~/.bluffed so play/run can use --agent instead of --agent-key")
def agents_create(name: str, mode: str, save_key: bool):
    """Create a new agent."""
    account = _account_from_session()
    result = account.create_agent(name, mode)
    click.echo(f"Created agent {result['agentId']}")
    click.echo(f"API key (shown once): {result['apiKey']}")
    if save_key:
        path = config.save_agent_key(result["agentId"], result["apiKey"])
        click.echo(f"Saved to {path}")


@agents.command("fund")
@click.argument("agent_id")
@click.argument("amount", type=float)
def agents_fund(agent_id: str, amount: float):
    """Move USDC from your balance into an agent."""
    account = _account_from_session()
    account.fund(agent_id, to_micros(amount))
    click.echo(f"Funded {agent_id} with {fmt_usdc(to_micros(amount))}.")


@agents.command("sweep")
@click.argument("agent_id")
@click.argument("amount", type=float, required=False)
def agents_sweep(agent_id: str, amount: Optional[float]):
    """Move USDC from an agent back to your balance. Sweeps everything if amount is omitted."""
    account = _account_from_session()
    micros = to_micros(amount) if amount is not None else None
    account.sweep(agent_id, micros)
    click.echo(f"Swept {agent_id}" + (f" for {fmt_usdc(micros)}." if micros is not None else " (all)."))


@agents.command("rotate-key")
@click.argument("agent_id")
def agents_rotate_key(agent_id: str):
    """Revoke an agent's current key and issue a new one."""
    account = _account_from_session()
    result = account.rotate_key(agent_id)
    click.echo(f"New API key (shown once): {result['apiKey']}")
    config.save_agent_key(agent_id, result["apiKey"])


@main.command()
@click.option("--base-url", required=True)
@click.option("--agent", "agent_id", help="agent id, to use a key saved by `agents create`")
@click.option("--agent-key", help="raw API key, if not using a saved one")
@click.option("--tier", default="t_low", show_default=True)
@click.option("--buy-in", type=float, required=True, help="buy-in, in USDC")
@click.option("--hands", type=int, default=1, show_default=True)
@click.option("--strategy", type=click.Choice(list(STRATEGIES)), default="call", show_default=True)
def play(base_url: str, agent_id: Optional[str], agent_key: Optional[str], tier: str, buy_in: float, hands: int, strategy: str):
    """Play a handful of hands with a built-in strategy — a quick smoke test."""
    key = _resolve_key(agent_id, agent_key)
    env = BluffedTableEnv(base_url=base_url, api_key=key, tier_id=tier, buy_in=to_micros(buy_in))
    strat = STRATEGIES[strategy]
    try:
        for i in range(hands):
            obs, _info = env.reset()
            hand_reward = 0.0
            while not obs.hand_over:
                obs, reward, terminated, truncated, _info = env.step(strat(obs))
                hand_reward += reward
                if terminated or truncated:
                    break
            click.echo(f"hand {i + 1}/{hands}: {obs.phase}  reward={hand_reward:+.0f} micros")
            env.leave()
    except BluffedError as e:
        raise click.ClickException(str(e))
    finally:
        env.close()


@main.command()
@click.option("--base-url", required=True)
@click.option("--agent", "agent_id", required=True, help="agent id — also used to load a saved key")
@click.option("--agent-key", help="raw API key, if not using a saved one")
@click.option("--tier", default="t_low", show_default=True)
@click.option("--buy-in", type=float, required=True, help="buy-in, in USDC")
@click.option("--min-reserve", type=float, required=True, help="top up once the agent's balance drops below this, in USDC")
@click.option("--top-up-to", type=float, required=True, help="...back up to this much, in USDC")
@click.option("--sweep-above", type=float, default=None, help="sweep profit back to your balance above this, in USDC")
@click.option("--sweep-down-to", type=float, default=None, help="...down to this much, defaults to --sweep-above")
@click.option("--strategy", type=click.Choice(list(STRATEGIES)), default="call", show_default=True)
def run(
    base_url: str,
    agent_id: str,
    agent_key: Optional[str],
    tier: str,
    buy_in: float,
    min_reserve: float,
    top_up_to: float,
    sweep_above: Optional[float],
    sweep_down_to: Optional[float],
    strategy: str,
):
    """Play forever, topping up and sweeping the agent's balance automatically. Ctrl-C to stop."""
    key = _resolve_key(agent_id, agent_key)
    account = _account_from_session()
    env = BluffedTableEnv(base_url=base_url, api_key=key, tier_id=tier, buy_in=to_micros(buy_in))

    def on_event(kind: str, data: dict):
        click.echo(f"[{kind}] {data}")

    try:
        run_forever(
            env,
            account,
            agent_id,
            STRATEGIES[strategy],
            min_reserve=to_micros(min_reserve),
            top_up_to=to_micros(top_up_to),
            sweep_above=to_micros(sweep_above) if sweep_above is not None else None,
            sweep_down_to=to_micros(sweep_down_to) if sweep_down_to is not None else None,
            on_event=on_event,
        )
    except KeyboardInterrupt:
        click.echo("stopped.")
    finally:
        env.close()
