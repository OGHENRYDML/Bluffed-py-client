def to_micros(amount: float) -> int:
    return round(amount * 1_000_000)


def fmt_usdc(micros: int) -> str:
    return f"${micros / 1_000_000:,.2f}"
