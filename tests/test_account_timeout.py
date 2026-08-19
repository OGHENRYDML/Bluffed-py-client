import socket
import threading

import pytest

from bluffed_client import AccountClient, AccountError


@pytest.fixture
def stalled_server():
    # A raw socket that accepts the connection but never writes a response —
    # requests has no default timeout, so without one this would hang the
    # calling thread forever instead of raising.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept_and_stall():
        srv.settimeout(2.0)
        try:
            conn, _ = srv.accept()
            stop.wait(2.0)
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=accept_and_stall, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        stop.set()
        srv.close()
        t.join(timeout=2.0)


def test_balance_raises_account_error_instead_of_hanging_forever(stalled_server):
    account = AccountClient(stalled_server, timeout=0.2)
    with pytest.raises(AccountError):
        account.balance()
