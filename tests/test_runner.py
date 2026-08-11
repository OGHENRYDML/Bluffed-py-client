from bluffed_client import decide_bankroll_action


def test_funds_when_below_reserve():
    kind, amount = decide_bankroll_action(1_000_000, min_reserve=2_000_000, top_up_to=5_000_000)
    assert kind == "fund"
    assert amount == 4_000_000


def test_sweeps_when_above_ceiling():
    kind, amount = decide_bankroll_action(
        12_000_000, min_reserve=2_000_000, top_up_to=5_000_000, sweep_above=10_000_000
    )
    assert kind == "sweep"
    assert amount == 2_000_000


def test_sweeps_down_to_explicit_target():
    kind, amount = decide_bankroll_action(
        12_000_000,
        min_reserve=2_000_000,
        top_up_to=5_000_000,
        sweep_above=10_000_000,
        sweep_down_to=6_000_000,
    )
    assert kind == "sweep"
    assert amount == 6_000_000


def test_no_action_in_the_middle():
    kind, amount = decide_bankroll_action(
        6_000_000, min_reserve=2_000_000, top_up_to=5_000_000, sweep_above=10_000_000
    )
    assert kind is None
    assert amount == 0
