from typing import NamedTuple, Optional


class Tier(NamedTuple):
    id: str
    small_blind: int
    big_blind: int
    min_buy_in: int
    max_buy_in: int
    max_seats: int


# Mirrors bluffed-web's STAKE_TIERS (apps/web/src/lib/stakes.ts). All amounts
# in USDC micros.
STAKE_TIERS = [
    Tier("t_micro", 10_000, 20_000, 800_000, 2_000_000, 6),
    Tier("t_low", 50_000, 100_000, 4_000_000, 10_000_000, 6),
    Tier("t_mid", 250_000, 500_000, 20_000_000, 50_000_000, 6),
    Tier("t_high", 1_000_000, 2_000_000, 80_000_000, 200_000_000, 6),
]

DEFAULT_TIER_ID = "t_low"


def get_tier(tier_id: str) -> Optional[Tier]:
    return next((t for t in STAKE_TIERS if t.id == tier_id), None)
