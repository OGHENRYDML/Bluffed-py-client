import nacl.exceptions
import nacl.signing
import base58
import pytest

from bluffed_client import Wallet


def test_generate_gives_a_valid_solana_address():
    wallet = Wallet.generate()
    decoded = base58.b58decode(wallet.address)
    assert len(decoded) == 32


def test_sign_produces_a_verifiable_signature():
    wallet = Wallet.generate()
    message = "Sign in to Bluffed\nNonce: abc123"
    signature = wallet.sign(message)

    verify_key = nacl.signing.VerifyKey(base58.b58decode(wallet.address))
    verify_key.verify(message.encode(), base58.b58decode(signature))


def test_sign_rejects_tampered_message():
    wallet = Wallet.generate()
    signature = wallet.sign("Sign in to Bluffed\nNonce: abc123")

    verify_key = nacl.signing.VerifyKey(base58.b58decode(wallet.address))
    with pytest.raises(nacl.exceptions.BadSignatureError):
        verify_key.verify(b"Sign in to Bluffed\nNonce: tampered", base58.b58decode(signature))


def test_save_and_load_round_trip(tmp_path):
    wallet = Wallet.generate()
    path = wallet.save(tmp_path / "wallet.key")

    loaded = Wallet.load(path)
    assert loaded.address == wallet.address


def test_load_or_create_reuses_existing_wallet(tmp_path):
    path = tmp_path / "wallet.key"
    first = Wallet.load_or_create(path)
    second = Wallet.load_or_create(path)
    assert first.address == second.address
