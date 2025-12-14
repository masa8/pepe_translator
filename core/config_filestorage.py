import json
from pathlib import Path
from core.config_storage import StorageBackend


class FileStorage(StorageBackend):
    def __init__(self):
        self.path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "PepeTranslator"
            / "config.json"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                return {}
        return {}

    def save(self, data):
        self.path.write_text(json.dumps(data, indent=2))
        self.path.chmod(0o600)

    def get_secret(self, key):
        return self.load().get(key)

    def set_secret(self, key, value):
        data = self.load()
        data[key] = value
        self.save(data)
