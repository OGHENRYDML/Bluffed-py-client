from bluffed_client import Action, parse_observation

RAW = {
    "id": "agent:t_low:3",
    "phase": "flop",
    "ranked": True,
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
    "rakeCollected": 0,
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


def test_parses_me_and_turn():
    obs = parse_observation(RAW)
    assert obs.me.id == "agent_me"
    assert obs.my_turn is True
    assert obs.hand_over is False


def test_parses_max_seats():
    obs = parse_observation(RAW)
    assert obs.max_seats == 6


def test_legal_actions_includes_call_and_raise():
    obs = parse_observation(RAW)
    kinds = {a.type for a in obs.legal_actions()}
    assert kinds == {"fold", "call", "allin", "raise"}


def test_legal_actions_empty_when_folded():
    raw = {**RAW, "players": [{**RAW["players"][0], "folded": True}, RAW["players"][1]]}
    obs = parse_observation(raw)
    assert obs.legal_actions() == []


def test_action_to_wire():
    assert Action("raise", to=500_000).to_wire() == {"type": "raise", "to": 500_000}
    assert Action("fold").to_wire() == {"type": "fold"}


def test_raise_bounds_gives_full_range_not_just_minimum():
    obs = parse_observation(RAW)
    # owed = 200_000 - 100_000 = 100_000; min_to = 200_000 + max(100_000, 100_000) = 300_000;
    # max_to = bet(100_000) + chips(3_000_000) = 3_100_000
    assert obs.raise_bounds() == (300_000, 3_100_000)


def test_raise_bounds_none_when_short_stacked_below_minimum_raise():
    # 150_000 behind is enough to call/allin (owed=100_000) but a shove only
    # reaches bet+chips = 250_000, short of the 300_000 minimum raise.
    raw = {**RAW, "players": [{**RAW["players"][0], "chips": 150_000}, RAW["players"][1]]}
    obs = parse_observation(raw)
    assert obs.raise_bounds() is None
    assert {a.type for a in obs.legal_actions()} == {"fold", "call", "allin"}
