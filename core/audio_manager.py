import sounddevice as sd
import threading
import numpy as np
from core.message_types import UIMessageType, UIMessageMixin
from queue import Queue


class AudioStreamManager(UIMessageMixin):
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AudioStreamManager, cls).__new__(cls)
                UIMessageMixin.__init__(cls._instance)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return  # Avoid running initialization twice

        self.stream = None
        self.device_index = None
        self.samplerate = 16000
        self.blocksize = 16000 * 100 // 1000
        self.callback = self.default_audio_callback
        self.enabled = False
        self.min_volume_for_speech = 5
        self.audio_queue = Queue()
        self.noise_floor = None

        self._initialized = True
        self._noise_reduction_enabled = True

    def enable_noise_reduction(self):
        self._noise_reduction_enabled = True
        self.ui_msg(UIMessageType.SYS_LOG, "🔉 Noise Reduction: ON")

    def disable_noise_reduction(self):
        self._noise_reduction_enabled = False
        self.ui_msg(UIMessageType.SYS_LOG, "🔇 Noise Reduction: OFF")

    def toggle_noise_reduction(self):
        self._noise_reduction_enabled = not self._noise_reduction_enabled
        state = "ON" if self._noise_reduction_enabled else "OFF"
        self.ui_msg(
            UIMessageType.SYS_LOG,
            f"🎛 Noise Reduction: {state}",
        )

    def is_noise_reduction_enabled(self):
        return self._noise_reduction_enabled

    # ------------------------------------------------

    def set_device(self, device_index):
        restart = self.enabled

        if self.enabled:
            self.stop()

        self.device_index = device_index
        self.ui_msg(UIMessageType.SYS_LOG, f"🎧 Device = {device_index}")

        if restart:
            self.start()

    # ------------------------------------------------

    def start(self):
        if self.enabled:
            return

        try:
            self.stream = sd.InputStream(
                channels=1,
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                dtype="float32",
                device=self.device_index,
                callback=self.callback,
            )
            self.stream.start()
            self.enabled = True

            self.ui_msg(UIMessageType.SYS_LOG, "🎤 Mic ON")

        except Exception as e:
            self.ui_msg(UIMessageType.SYS_LOG, f"❌ Mic start error: {e}")
            self.enabled = False
            self.stream = None

    # ------------------------------------------------

    def stop(self):
        if not self.enabled:
            return

        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass

        self.stream = None
        self.enabled = False

        self.ui_msg(UIMessageType.SYS_LOG, "🛑 Mic OFF")

    # ------------------------------------------------

    def toggle(self):
        if self.enabled:
            self.stop()
        else:
            self.start()

    def is_on(self):
        return self.enabled

    def noise_reduction(self, pcm):
        abs_pcm = np.abs(pcm)

        if self.noise_floor is None:
            self.noise_floor = abs_pcm.mean()

        # Update the noise floor slowly using a 0.95 smoothing factor.
        self.noise_floor = 0.95 * self.noise_floor + 0.05 * abs_pcm.mean()
        self.noise_floor = max(self.noise_floor, 1e-6)
        # Suppress sounds below the noise floor.
        scale = np.clip(abs_pcm / (self.noise_floor * 1.5), 0, 1)
        return (pcm * scale).astype(np.int16)

    def get_input_devices(self):
        devices = sd.query_devices()
        input_devices = []
        for idx, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                input_devices.append((idx, d["name"]))

        return [f"{idx}: {name}" for idx, name in input_devices]

    def default_audio_callback(self, indata, frames, time, status):
        if not self.enabled:
            return
        pcm = (indata[:, 0] * 32767).astype(np.int16)
        if self._noise_reduction_enabled:
            pcm = self.noise_reduction(pcm)
        volume = np.abs(pcm).mean()

        try:
            level = volume
            self.ui_msg(UIMessageType.VOLUME, str(level))
        except Exception:
            pass

        if volume >= self.min_volume_for_speech:
            self.audio_queue.put((pcm.tobytes(), volume))
