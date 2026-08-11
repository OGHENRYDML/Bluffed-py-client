import random

from .actions import Action, call, check, fold
from .observation import Observation


def call_or_fold(obs: Observation) -> Action:
    legal = {a.type for a in obs.legal_actions()}
    if "call" in legal:
        return call()
    if "check" in legal:
        return check()
    return fold()


def random_legal(obs: Observation) -> Action:
    legal = obs.legal_actions()
    return random.choice(legal) if legal else fold()


def always_fold(obs: Observation) -> Action:
    return fold()


STRATEGIES = {"call": call_or_fold, "random": random_legal, "fold": always_fold}
