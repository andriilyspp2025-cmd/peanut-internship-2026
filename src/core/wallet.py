import os
import logging
import json
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_account.datastructures import SignedMessage, SignedTransaction
from eth_account.messages import encode_defunct, encode_typed_data

logger = logging.getLogger(__name__)


class WalletManager:
    """
    Manages wallet operations: key loading, signing, verification.
    CRITICAL: Private key must never appear in logs, errors, or string representations.
    """

    def __init__(self, private_key: str):
        if not private_key:
            raise ValueError("Private key cannot be empty.")
        # Enable unaudited hdwallet features if needed (optional, але eth_account іноді просить)
        Account.enable_unaudited_hdwallet_features()

        self._account: LocalAccount = Account.from_key(private_key)

    @classmethod
    def from_env(cls, env_var: str = "PRIVATE_KEY") -> "WalletManager":
        """Load private key from environment variable."""
        private_key = os.getenv(env_var)
        if not private_key:
            raise ValueError(
                f"Private key not found in environment variable '{env_var}'"
            )
        return cls(private_key)

    @classmethod
    def generate(cls) -> "WalletManager":
        """Generate a new random wallet. Returns manager + displays private key ONCE."""
        new_account, _ = Account.create_with_mnemonic()

        print("=" * 50)
        print("NEW WALLET GENERATED")
        print(f"Address:     {new_account.address}")
        print(f"Private Key: {new_account.key.hex()}")
        print("SAVE THIS NOW! It will not be accessible after this message.")
        print("=" * 50)

        return cls(new_account.key.hex())

    @property
    def address(self) -> str:
        """Returns checksummed address."""
        return self._account.address

    def sign_message(self, message: str) -> SignedMessage:
        """Sign an arbitrary message (with EIP-191 prefix)."""
        # Перевіряємо, чи повідомлення не порожнє і не складається лише з пробілів
        if not message or not message.strip():
            raise ValueError("Security Requirement: message cannot be empty or blank.")

        packed_message = encode_defunct(text=message)
        return self._account.sign_message(packed_message)

    def sign_typed_data(self, domain: dict, types: dict, value: dict) -> SignedMessage:
        """Sign EIP-712 typed data (used by many DeFi protocols)."""
        if not domain or not types or not value:
            raise ValueError(
                "Invalid types: domain, types, and value dicts cannot be empty."
            )

        encoded_data = encode_typed_data(domain=domain, types=types, message=value)
        return self._account.sign_message(encoded_data)

    def sign_transaction(self, tx: dict) -> SignedTransaction:
        """Sign a transaction dict."""
        return self._account.sign_transaction(tx)

    # --- ЗАХИСТ ВІД ВИТОКУ КЛЮЧА ---
    def __repr__(self) -> str:
        """MUST NOT expose private key. Used in logs and tracebacks."""
        return f"WalletManager(address={self.address})"

    def __str__(self) -> str:
        """Used when printing the object."""
        return f"WalletManager(address={self.address})"

    @classmethod
    def from_keyfile(cls, path: str, password: str) -> "WalletManager":
        """Load from encrypted JSON keyfile."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Keyfile not found at path: {path}")

        with open(path, "r", encoding="utf-8") as f:
            keyfile_data = json.load(f)

        try:
            private_key_bytes = Account.decrypt(keyfile_data, password)
            return cls(private_key_bytes.hex())
        except ValueError:
            raise ValueError(
                "Decryption failed: Invalid password or corrupted keyfile."
            )

    def to_keyfile(self, path: str, password: str) -> None:
        """Export to encrypted JSON keyfile (geth/clef format)."""
        if not password or len(password) < 4:
            raise ValueError(
                "Password is too weak or empty. Encryption requires a valid password."
            )

        try:
            encrypted_data = Account.encrypt(self._account.key, password)

            (
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.dirname(path)
                else None
            )

            with open(path, "w", encoding="utf-8") as f:
                json.dump(encrypted_data, f, indent=4)

        except Exception as e:
            raise IOError(f"Failed to write keyfile to {path}: {str(e)}")
