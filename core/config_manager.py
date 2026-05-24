import threading
from core.config_filestorage import FileStorage


class ConfigManager:
    _instance = None
    _lock = threading.Lock()
    _backend = None  # plugin for saving config

    @classmethod
    def configure(cls, backend):
        if cls._instance is not None:
            raise RuntimeError("ConfigManager is already initialized")
        cls._backend = backend

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return

        if self.__class__._backend is None:
            raise RuntimeError("ConfigManager is not configured")

        self.backend = self.__class__._backend
        self._data = self.backend.load() if hasattr(self.backend, "load") else {}
        self._initialized = True

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.backend.save(self._data)

    def get_api_key(self):
        return self.backend.get_secret("API_KEY")

    def set_api_key(self, key):
        self.backend.set_secret("API_KEY", key)

    def get_prompt(self, default=None):
        storage = FileStorage()
        data = storage.load()
        prompt = data.get("PROMPT")
        return prompt if prompt is not None else default

    def set_prompt(self, prompt):
        storage = FileStorage()
        data = storage.load()
        data["PROMPT"] = prompt
        storage.save(data)

    def all(self):
        return dict(self._data)
