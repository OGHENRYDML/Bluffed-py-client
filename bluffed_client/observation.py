from dataclasses import dataclass
from typing import List, Optional, Tuple

from .actions import Action


@dataclass(frozen=True)
class PlayerView:
    id: str
    name: str
    seat: int
    chips: int
    bet: int
    folded: bool
    all_in: bool
    sitting_out: bool
    connected: bool
    is_you: bool
    hole_cards: Optional[List[str]]


@dataclass(frozen=True)
class Observation:
    table_id: str
    phase: str
    hand_number: int
    max_seats: int
    dealer_seat: Optional[int]
    current_turn_seat: Optional[int]
    current_bet: int
    min_raise: int
    small_blind: int
    big_blind: int
    pot: int
    community: List[str]
    players: List[PlayerView]
    winners: Optional[list]
    log: List[str]

    @property
    def me(self) -> Optional[PlayerView]:
        return next((p for p in self.players if p.is_you), None)

    @property
    def my_turn(self) -> bool:
        me = self.me
        return me is not None and self.current_turn_seat == me.seat

    @property
    def hand_over(self) -> bool:
        return self.phase == "handComplete"

    def raise_bounds(self) -> Optional[Tuple[int, int]]:
        """(min_to, max_to) — the range of legal target total bets for a
        raise this turn, in USDC micros. Both ends are legal, and so is
        everything between them. None if raising isn't legal right now
        (not enough chips behind to meet the table minimum — shoving is
        still allowed via `allin`, just not as a `raise`)."""
        me = self.me
        if me is None or me.folded or me.all_in:
            return None
        owed = self.current_bet - me.bet
        stack_behind = me.chips
        if stack_behind <= max(owed, 0):
            return None
        min_to = self.current_bet + max(self.min_raise, self.big_blind)
        max_to = me.bet + stack_behind
        if max_to < min_to:
            return None
        return (min_to, max_to)

    def legal_actions(self) -> List[Action]:
        me = self.me
        if me is None or me.folded or me.all_in:
            return []

        actions = [Action("fold")]
        owed = self.current_bet - me.bet
        if owed <= 0:
            actions.append(Action("check"))
        else:
            actions.append(Action("call"))

        stack_behind = me.chips
        if stack_behind > max(owed, 0):
            actions.append(Action("allin"))
            bounds = self.raise_bounds()
            if bounds is not None:
                actions.append(Action("raise", to=bounds[0]))

        return actions


def _parse_player(raw: dict) -> PlayerView:
    return PlayerView(
        id=raw["id"],
        name=raw["name"],
        seat=raw["seat"],
        chips=raw["chips"],
        bet=raw["bet"],
        folded=raw["folded"],
        all_in=raw["allIn"],
        sitting_out=raw["sittingOut"],
        connected=raw["connected"],
        is_you=raw["isYou"],
        hole_cards=raw["holeCards"],
    )


def parse_observation(raw: dict) -> Observation:
    return Observation(
        table_id=raw["id"],
        phase=raw["phase"],
        hand_number=raw["handNumber"],
        max_seats=raw["maxSeats"],
        dealer_seat=raw["dealerSeat"],
        current_turn_seat=raw["currentTurnSeat"],
        current_bet=raw["currentBet"],
        min_raise=raw["minRaise"],
        small_blind=raw["smallBlind"],
        big_blind=raw["bigBlind"],
        pot=raw["pot"],
        community=raw["community"],
        players=[_parse_player(p) for p in raw["players"]],
        winners=raw["winners"],
        log=raw["log"],
    )
