class StorageBackend:
    def load(self) -> dict:
        raise NotImplementedError

    def save(self, data: dict) -> None:
        raise NotImplementedError

    def get_secret(self, key: str):
        return None

    def set_secret(self, key: str, value: str):
        raise RuntimeError("Secrets not supported")
