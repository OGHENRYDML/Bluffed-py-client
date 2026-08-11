from dataclasses import dataclass
from typing import Optional

ACTION_TYPES = ("fold", "check", "call", "raise", "allin")


@dataclass(frozen=True)
class Action:
    type: str
    to: Optional[int] = None

    def to_wire(self) -> dict:
        if self.type == "raise":
            return {"type": "raise", "to": self.to}
        return {"type": self.type}


def fold() -> Action:
    return Action("fold")


def check() -> Action:
    return Action("check")


def call() -> Action:
    return Action("call")


def raise_to(amount: int) -> Action:
    return Action("raise", to=amount)


def allin() -> Action:
    return Action("allin")
