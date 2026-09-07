import pytest

from bluffed_client.env import BluffedTableEnv
from bluffed_client.errors import BluffedError


class DeadWs:
    def send(self, data):
        raise OSError("socket closed by peer")


def test_send_wraps_a_raw_socket_error_in_bluffed_error():
    # A dropped socket raises a raw OSError/websocket exception here, not a
    # BluffedError — left unwrapped, this bypassed every BluffedError/
    # TableError handler downstream (step()'s connection_lost fallback,
    # reset()'s sit-retry loop, the MCP server's error translation, the
    # CLI's ClickException wrapping) and surfaced as an unhandled traceback.
    env = BluffedTableEnv("key")
    env._ws = DeadWs()
    with pytest.raises(BluffedError):
        env._send({"type": "action", "action": {"type": "fold"}})


def test_send_without_a_connection_still_raises_bluffed_error():
    env = BluffedTableEnv("key")
    with pytest.raises(BluffedError, match="not connected"):
        env._send({"type": "action", "action": {"type": "fold"}})
