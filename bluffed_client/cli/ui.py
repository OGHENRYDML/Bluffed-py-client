from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .format import fmt_usdc

console = Console()

MODE_STYLE = {"llm": "bold cyan", "fast": "bold yellow"}
EVENT_STYLE = {"funded": "cyan", "swept": "green", "hand_complete": "dim", "error": "bold red"}


def signed_in(base_url: str) -> None:
    console.print(f"[bold green]♠[/bold green] Signed in to [bold]{base_url}[/bold].")


def agents_table(agents: list) -> None:
    if not agents:
        console.print("[dim]No agents yet — create one with `bluffed agents create`.[/dim]")
        return
    table = Table(border_style="grey35", header_style="bold")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Mode")
    table.add_column("Balance", justify="right")
    table.add_column("Hands", justify="right")
    for a in agents:
        mode_style = MODE_STYLE.get(a["mode"], "white")
        table.add_row(
            a["id"],
            a["name"],
            f"[{mode_style}]{a['mode']}[/{mode_style}]",
            f"[green]{fmt_usdc(a['availableMicros'])}[/green]",
            str(a["handsWon"]),
        )
    console.print(table)


def agent_created(agent_id: str, mode: str) -> None:
    mode_style = MODE_STYLE.get(mode, "white")
    console.print(f"[bold green]♠[/bold green] Created agent [bold]{agent_id}[/bold] · [{mode_style}]{mode}[/{mode_style}]")


def key_reveal(api_key: str, saved_path=None) -> None:
    body = f"[bold]{api_key}[/bold]"
    if saved_path:
        body += f"\n[dim]saved to {saved_path}[/dim]"
    console.print(Panel(body, title="API key — shown once", border_style="yellow", expand=False))


def fund_result(agent_id: str, micros: int) -> None:
    console.print(f"[cyan]▸ funded[/cyan] {agent_id}  [green]+{fmt_usdc(micros)}[/green]")


def sweep_result(agent_id: str, micros) -> None:
    amount = f"[green]{fmt_usdc(micros)}[/green]" if micros is not None else "[green]everything[/green]"
    console.print(f"[cyan]▸ swept[/cyan] {agent_id}  {amount}")


def hand_result(i: int, total: int, phase: str, reward: float) -> None:
    color = "green" if reward >= 0 else "red"
    console.print(f"[dim]hand {i}/{total}[/dim]  {phase}  [{color}]{reward:+.0f}[/{color}] micros")


def event(kind: str, data: dict) -> None:
    style = EVENT_STYLE.get(kind, "white")
    console.print(f"[{style}]▸ {kind}[/{style}]  {data}")


def stopped() -> None:
    console.print("[dim]stopped.[/dim]")
