import json
from pathlib import Path
import threading


class ConfigManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return  # Avoid running initialization twice

        self.config_path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "PepeTranslator"
            / "config.json"
        )
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        self._data = {}
        self._load()

        self._initialized = True

    # ---------------------------------------------------------
    # Internal load/save
    # ---------------------------------------------------------

    def _load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        with open(self.config_path, "w") as f:
            json.dump(self._data, f, indent=2)

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self._save()

    def get_api_key(self):
        return self.get("API_KEY")

    def set_api_key(self, key):
        self.set("API_KEY", key)

    def all(self):
        return dict(self._data)
