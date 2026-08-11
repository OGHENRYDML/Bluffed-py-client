import os
from pathlib import Path
from typing import Optional

import base58
import nacl.signing

from .paths import CONFIG_DIR

WALLET_FILE = CONFIG_DIR / "wallet.key"


class Wallet:
    """A Solana keypair used to sign in via SIWS instead of email/password —
    the account is authenticated by proving control of the private key, not
    by holding a shared secret. The 32-byte seed is interoperable with
    bluffed-js-client's Wallet: the same file works with either CLI."""

    def __init__(self, seed: bytes):
        self._signing_key = nacl.signing.SigningKey(seed)

    @property
    def address(self) -> str:
        return base58.b58encode(bytes(self._signing_key.verify_key)).decode()

    def sign(self, message: str) -> str:
        signature = self._signing_key.sign(message.encode()).signature
        return base58.b58encode(signature).decode()

    @classmethod
    def generate(cls) -> "Wallet":
        return cls(nacl.signing.SigningKey.generate().encode())

    def save(self, path: Path = WALLET_FILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        path.write_bytes(self._signing_key.encode())
        os.chmod(path, 0o600)
        return path

    @classmethod
    def load(cls, path: Path = WALLET_FILE) -> Optional["Wallet"]:
        if not path.exists():
            return None
        return cls(path.read_bytes())

    @classmethod
    def load_or_create(cls, path: Path = WALLET_FILE) -> "Wallet":
        wallet = cls.load(path)
        if wallet is not None:
            return wallet
        wallet = cls.generate()
        wallet.save(path)
        return wallet
