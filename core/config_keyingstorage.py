# core/config_keyringstorage.py
import keyring
from .config_storage import StorageBackend


class KeyringStorage(StorageBackend):
    SERVICE = "PepeTranslator"

    def load(self) -> dict:
        return {}

    def save(self, data: dict) -> None:
        pass

    def get(self, key, default=None):
        value = keyring.get_password(self.SERVICE, key)
        return value if value is not None else default

    def set(self, key, value):
        keyring.set_password(self.SERVICE, key, str(value))

    def get_secret(self, key):
        return self.get(key)

    def set_secret(self, key, value):
        self.set(key, value)
