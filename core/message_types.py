from enum import Enum
import logging


class UIMessageType(Enum):
    SYS_LOG = "sys_log"
    LOG = "log"
    CAPTION = "caption"
    TRANSLATED = "translated"
    VOLUME = "volume"
    AUDIO_STARTED = "audio_started"
    AUDIO_STOPPED = "audio_stopped"


class UIMessageMixin:
    ui_queue = None

    @classmethod
    def set_ui_queue(cls, queue):
        cls.ui_queue = queue

    def ui_msg(self, msg_type: UIMessageType, text: str):
        # Mirror messages to logger so status is visible outside the Tk UI.
        try:
            if msg_type != UIMessageType.VOLUME:
                logging.info("[%s] %s", msg_type.value, text)
        except Exception:
            pass

        if self.ui_queue:
            try:
                self.ui_queue.put({"type": msg_type, "text": text})
            except Exception:
                pass
        else:
            pass
