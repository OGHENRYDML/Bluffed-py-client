from bluffed_client import DEFAULT_BASE_URL, BluffedTableEnv, get_tier


def test_defaults_base_url_and_tier():
    env = BluffedTableEnv("bk_live_fake")
    assert env.base_url == DEFAULT_BASE_URL
    assert env.tier_id == "t_low"


def test_default_buy_in_is_tier_minimum():
    env = BluffedTableEnv("bk_live_fake", tier_id="t_mid")
    assert env.buy_in == get_tier("t_mid").min_buy_in


def test_explicit_buy_in_wins():
    env = BluffedTableEnv("bk_live_fake", tier_id="t_mid", buy_in=25_000_000)
    assert env.buy_in == 25_000_000


def test_unknown_tier_without_explicit_buy_in_raises():
    import pytest

    from bluffed_client import BluffedError

    with pytest.raises(BluffedError):
        BluffedTableEnv("bk_live_fake", tier_id="t_nope")
