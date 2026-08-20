from bluffed_client import parse_observation
from bluffed_client.strategies import always_fold, call_or_fold, random_legal

RAW = {
    "id": "agent:t_low:3",
    "phase": "flop",
    "maxSeats": 6,
    "smallBlind": 50_000,
    "bigBlind": 100_000,
    "community": ["As", "Kd", "2c"],
    "currentBet": 200_000,
    "minRaise": 100_000,
    "dealerSeat": 0,
    "currentTurnSeat": 2,
    "handNumber": 5,
    "winners": None,
    "log": [],
    "pot": 400_000,
    "players": [
        {
            "id": "agent_me",
            "name": "Bot",
            "seat": 2,
            "chips": 3_000_000,
            "bet": 100_000,
            "folded": False,
            "allIn": False,
            "hasActed": False,
            "sittingOut": False,
            "connected": True,
            "isYou": True,
            "holeCards": ["Ah", "Qh"],
        },
        {
            "id": "agent_other",
            "name": "Other",
            "seat": 4,
            "chips": 2_000_000,
            "bet": 200_000,
            "folded": False,
            "allIn": False,
            "hasActed": False,
            "sittingOut": False,
            "connected": True,
            "isYou": False,
            "holeCards": ["??", "??"],
        },
    ],
}


def test_call_or_fold_calls_when_facing_a_bet():
    obs = parse_observation(RAW)
    assert call_or_fold(obs).type == "call"


def test_call_or_fold_checks_when_free():
    raw = {**RAW, "currentBet": RAW["players"][0]["bet"]}
    obs = parse_observation(raw)
    assert call_or_fold(obs).type == "check"


def test_call_or_fold_folds_when_no_actions():
    raw = {**RAW, "players": [{**RAW["players"][0], "folded": True}, RAW["players"][1]]}
    obs = parse_observation(raw)
    assert always_fold(obs).type == "fold"
    assert call_or_fold(obs).type == "fold"


def test_random_legal_picks_a_legal_action():
    obs = parse_observation(RAW)
    legal_types = {a.type for a in obs.legal_actions()}
    assert random_legal(obs).type in legal_types
