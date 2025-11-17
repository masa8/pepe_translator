import logging
from core.log_manager import setup_logging
import ui.tk

if __name__ == "__main__":
    setup_logging()
    try:
        ui.tk.action_open_ui()
    except KeyboardInterrupt:
        logging.info(
            "🛑 Stopped by user",
        )
    except Exception as e:
        logging.exception(f"😔 Unexpected error: {e}")
