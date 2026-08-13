import textwrap

import pytest
from click import ClickException

from bluffed_client.cli import _load_strategy_module


def test_loads_strategy_from_a_file(tmp_path):
    module_file = tmp_path / "mystrat.py"
    module_file.write_text(
        textwrap.dedent(
            """
            def decide(obs):
                return "fake-action"
            """
        )
    )
    strategy = _load_strategy_module(f"{module_file}:decide")
    assert strategy(None) == "fake-action"


def test_loads_strategy_from_an_importable_module():
    strategy = _load_strategy_module("bluffed_client.strategies:always_fold")
    action = strategy(None)
    assert action.type == "fold"


def test_missing_colon_raises():
    with pytest.raises(ClickException):
        _load_strategy_module("no_colon_here")


def test_missing_file_raises():
    with pytest.raises(ClickException):
        _load_strategy_module("/no/such/file.py:decide")


def test_missing_attribute_raises():
    with pytest.raises(ClickException):
        _load_strategy_module("bluffed_client.strategies:does_not_exist")
