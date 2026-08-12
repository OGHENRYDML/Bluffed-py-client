def usdc(amount: float) -> int:
    """Dollars to USDC micros (1 USDC = 1,000,000 micros) — the unit every
    wire amount (buy_in, raise_to, fund, sweep) actually uses. Write code in
    dollars, let this do the conversion once instead of scattering
    _000_000 through it."""
    return round(amount * 1_000_000)


def fmt_usdc(micros: int) -> str:
    return f"${micros / 1_000_000:,.2f}"
