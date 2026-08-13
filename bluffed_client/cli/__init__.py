import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Callable, Optional

import rich_click as click

from ..account import AccountClient, AccountError
from ..actions import Action
from ..defaults import DEFAULT_BASE_URL
from ..env import BluffedTableEnv
from ..errors import BluffedError
from ..observation import Observation
from ..runner import run_forever
from ..tiers import DEFAULT_TIER_ID, get_tier
from ..wallet import Wallet
from . import config, ui
from .format import to_micros

click.rich_click.USE_RICH_MARKUP = True
click.rich_click.STYLE_OPTION = "bold cyan"
click.rich_click.STYLE_SWITCH = "bold green"
click.rich_click.STYLE_METAVAR = "bold yellow"
click.rich_click.STYLE_HEADER_TEXT = "bold green"
click.rich_click.STYLE_ERRORS_SUGGESTION = "dim"
click.rich_click.MAX_WIDTH = 100


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


def _require_tier(tier_id: str):
    tier = get_tier(tier_id)
    if tier is None:
        raise click.ClickException(f"unknown tier {tier_id!r}")
    return tier


def _load_strategy_module(spec: str) -> Callable[[Observation], Action]:
    """Load a strategy function from MODULE:FUNCTION — MODULE is either an
    importable dotted module name or a path to a .py file. Lets `run`/`play`
    drive a model of your own (XGBoost, an RL policy, whatever) while still
    getting the CLI's auto-topup/sweep/reconnect for free."""
    if ":" not in spec:
        raise click.ClickException("--strategy-module must be MODULE:FUNCTION, e.g. mybot:decide or mybot.py:decide")
    mod_part, func_name = spec.rsplit(":", 1)

    if mod_part.endswith(".py"):
        path = Path(mod_part)
        if not path.exists():
            raise click.ClickException(f"no such file: {mod_part}")
        module_name = path.stem
        module_spec = importlib.util.spec_from_file_location(module_name, path)
        if module_spec is None or module_spec.loader is None:
            raise click.ClickException(f"could not load {mod_part}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        module_spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(mod_part)
        except ImportError as e:
            raise click.ClickException(f"could not import {mod_part!r}: {e}")

    try:
        strategy = getattr(module, func_name)
    except AttributeError:
        raise click.ClickException(f"{mod_part!r} has no attribute {func_name!r}")
    if not callable(strategy):
        raise click.ClickException(f"{spec} is not callable")
    return strategy


@click.group()
def main():
    """[bold green]♠ Bluffed[/bold green] — play and manage poker agents from the command line."""


@main.command()
@click.option("--base-url", prompt="Bluffed URL", default=DEFAULT_BASE_URL)
@click.option("--email", help="sign in with email/password (default)")
@click.option("--password", help="required with --email")
@click.option("--wallet", is_flag=True, help="sign in with a Solana keypair instead — no email needed. Generates one at ~/.bluffed/wallet.key on first use.")
def login(base_url: str, email: Optional[str], password: Optional[str], wallet: bool):
    """Sign in as the account owner — same login as the website, or a wallet."""
    account = AccountClient(base_url)
    try:
        if wallet:
            keypair = Wallet.load_or_create()
            account.sign_in_with_wallet(keypair)
            ui.signed_in(base_url, wallet_address=keypair.address)
        else:
            email = email or click.prompt("Email")
            password = password or click.prompt("Password", hide_input=True)
            account.sign_in(email, password)
            ui.signed_in(base_url)
    except AccountError as e:
        raise click.ClickException(str(e))
    config.save_session(base_url, account.export_cookies())


@main.group()
def account():
    """Check your balance, deposit, and withdraw — the owner account, not an agent."""


@account.command("balance")
def account_balance():
    """Show your available balance and lifetime stats."""
    acct = _account_from_session()
    ui.account_balance(acct.balance())


@account.command("deposit-address")
def account_deposit_address():
    """Get your personal Solana address for depositing USDC."""
    acct = _account_from_session()
    ui.deposit_address(acct.deposit_address())


@account.command("confirm-deposit")
@click.argument("tx_sig")
def account_confirm_deposit(tx_sig: str):
    """Credit a deposit immediately instead of waiting for it to be picked up automatically."""
    acct = _account_from_session()
    ui.deposit_confirmed(acct.confirm_deposit(tx_sig))


@account.command("withdraw")
@click.argument("address")
@click.argument("amount", type=float)
def account_withdraw(address: str, amount: float):
    """Withdraw USDC to a Solana address."""
    acct = _account_from_session()
    ui.withdraw_queued(acct.withdraw(address, to_micros(amount)))


@main.group()
def agents():
    """Create, fund, sweep, and list your agents."""


@agents.command("list")
def agents_list():
    """List your agents with their mode and balance."""
    acct = _account_from_session()
    ui.agents_table(acct.list_agents())


@agents.command("create")
@click.argument("name")
@click.option("--mode", type=click.Choice(["llm", "fast"]), required=True)
@click.option("--save-key/--no-save-key", default=True, help="save the API key to ~/.bluffed so play/run can use --agent instead of --agent-key")
def agents_create(name: str, mode: str, save_key: bool):
    """Create a new agent."""
    acct = _account_from_session()
    result = acct.create_agent(name, mode)
    ui.agent_created(result["agentId"], mode)
    path = config.save_agent_key(result["agentId"], result["apiKey"]) if save_key else None
    ui.key_reveal(result["apiKey"], path)


@agents.command("fund")
@click.argument("agent_id")
@click.argument("amount", type=float)
def agents_fund(agent_id: str, amount: float):
    """Move USDC from your balance into an agent."""
    acct = _account_from_session()
    micros = to_micros(amount)
    acct.fund(agent_id, micros)
    ui.fund_result(agent_id, micros)


@agents.command("sweep")
@click.argument("agent_id")
@click.argument("amount", type=float, required=False)
def agents_sweep(agent_id: str, amount: Optional[float]):
    """Move USDC from an agent back to your balance. Sweeps everything if amount is omitted."""
    acct = _account_from_session()
    micros = to_micros(amount) if amount is not None else None
    acct.sweep(agent_id, micros)
    ui.sweep_result(agent_id, micros)


@agents.command("rotate-key")
@click.argument("agent_id")
def agents_rotate_key(agent_id: str):
    """Revoke an agent's current key and issue a new one."""
    acct = _account_from_session()
    result = acct.rotate_key(agent_id)
    config.save_agent_key(agent_id, result["apiKey"])
    ui.key_reveal(result["apiKey"], saved_path=None)


@main.command()
@click.option("--base-url", default=DEFAULT_BASE_URL, show_default=True)
@click.option("--agent", "agent_id", help="agent id, to use a key saved by `agents create`")
@click.option("--agent-key", help="raw API key, if not using a saved one")
@click.option("--tier", default=DEFAULT_TIER_ID, show_default=True)
@click.option("--buy-in", type=float, default=None, help="buy-in, in USDC — defaults to the tier's minimum")
@click.option("--hands", type=int, default=1, show_default=True)
@click.option("--strategy-module", required=True, help="MODULE:FUNCTION or path/to/file.py:FUNCTION — your strategy, receives an Observation and returns an Action")
def play(
    base_url: str,
    agent_id: Optional[str],
    agent_key: Optional[str],
    tier: str,
    buy_in: Optional[float],
    hands: int,
    strategy_module: str,
):
    """Play a handful of hands with your strategy — a quick smoke test."""
    key = _resolve_key(agent_id, agent_key)
    _require_tier(tier)
    buy_in_micros = to_micros(buy_in) if buy_in is not None else None
    env = BluffedTableEnv(key, base_url=base_url, tier_id=tier, buy_in=buy_in_micros)
    strat = _load_strategy_module(strategy_module)
    try:
        for i in range(hands):
            obs, _info = env.reset()
            hand_reward = 0.0
            while not obs.hand_over:
                obs, reward, terminated, truncated, _info = env.step(strat(obs))
                hand_reward += reward
                if terminated or truncated:
                    break
            ui.hand_result(i + 1, hands, obs.phase, hand_reward)
            env.leave()
    except BluffedError as e:
        raise click.ClickException(str(e))
    finally:
        env.close()


@main.command()
@click.option("--base-url", default=DEFAULT_BASE_URL, show_default=True)
@click.option("--agent", "agent_id", required=True, help="agent id — also used to load a saved key")
@click.option("--agent-key", help="raw API key, if not using a saved one")
@click.option("--tier", default=DEFAULT_TIER_ID, show_default=True)
@click.option("--buy-in", type=float, default=None, help="buy-in, in USDC — defaults to the tier's minimum")
@click.option("--min-reserve", type=float, default=None, help="top up once the agent's balance drops below this, in USDC — defaults to the tier's minimum buy-in")
@click.option("--top-up-to", type=float, default=None, help="...back up to this much, in USDC — defaults to 2x the tier's minimum buy-in")
@click.option("--sweep-above", type=float, default=None, help="sweep profit back to your balance above this, in USDC — defaults to 2x the tier's maximum buy-in")
@click.option("--sweep-down-to", type=float, default=None, help="...down to this much, defaults to --top-up-to")
@click.option("--strategy-module", required=True, help="MODULE:FUNCTION or path/to/file.py:FUNCTION — your strategy, receives an Observation and returns an Action")
def run(
    base_url: str,
    agent_id: str,
    agent_key: Optional[str],
    tier: str,
    buy_in: Optional[float],
    min_reserve: Optional[float],
    top_up_to: Optional[float],
    sweep_above: Optional[float],
    sweep_down_to: Optional[float],
    strategy_module: str,
):
    """Play forever, topping up and sweeping the agent's balance automatically. Ctrl-C to stop."""
    key = _resolve_key(agent_id, agent_key)
    tier_info = _require_tier(tier)
    strat = _load_strategy_module(strategy_module)
    acct = _account_from_session()

    buy_in_micros = to_micros(buy_in) if buy_in is not None else tier_info.min_buy_in
    min_reserve_micros = to_micros(min_reserve) if min_reserve is not None else tier_info.min_buy_in
    top_up_to_micros = to_micros(top_up_to) if top_up_to is not None else tier_info.min_buy_in * 2
    sweep_above_micros = to_micros(sweep_above) if sweep_above is not None else tier_info.max_buy_in * 2
    sweep_down_to_micros = to_micros(sweep_down_to) if sweep_down_to is not None else top_up_to_micros

    env = BluffedTableEnv(key, base_url=base_url, tier_id=tier, buy_in=buy_in_micros)

    try:
        run_forever(
            env,
            acct,
            agent_id,
            strat,
            min_reserve=min_reserve_micros,
            top_up_to=top_up_to_micros,
            sweep_above=sweep_above_micros,
            sweep_down_to=sweep_down_to_micros,
            on_event=ui.event,
        )
    except KeyboardInterrupt:
        ui.stopped()
    finally:
        env.close()
