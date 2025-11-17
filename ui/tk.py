import os
import tkinter as tk
from tkinter import ttk
from core.config_manager import ConfigManager
from core.audio_manager import AudioStreamManager
from core.message_types import UIMessageMixin, UIMessageType
from core.realtime_api_manager import RealtimeAPIClient
from queue import Queue, Empty
import datetime

ui_queue = Queue()
UIMessageMixin.set_ui_queue(ui_queue)


# =====================
# Action
# =====================
def action_init():
    pass


def action_toggle_nr(nr_button_var):
    AudioStreamManager().toggle_noise_reduction()
    noise_reduction_enabled = AudioStreamManager().is_noise_reduction_enabled()
    state = "ON" if noise_reduction_enabled else "OFF"
    nr_button_var.set(f"Noise Reduction Status: {state}")
    ui_queue.put(
        {
            "type": UIMessageType.SYS_LOG,
            "text": f"🔧 Noise Reduction toggled to {state}",
        }
    )


def action_change_silence_level(val):
    RealtimeAPIClient().set_commit_level(float(val))

    ui_queue.put(
        {
            "type": UIMessageType.SYS_LOG,
            "text": f"🔧 SILENCE_THRESHOLD updated to {RealtimeAPIClient().commit_level}",
        }
    )


def action_change_device(event):
    widget = event.widget  # Get the combobox
    selected = widget.get()  # Get selected text
    idx = int(selected.split(":")[0])
    ui_queue.put(
        {"type": UIMessageType.SYS_LOG, "text": f"🎤 Audio Device = {selected}"}
    )

    AudioStreamManager().set_device(idx)
    ui_queue.put(
        {
            "type": UIMessageType.SYS_LOG,
            "text": f"🔄 Restart requested for device {idx}",
        }
    )


def action_start_audio():
    ui_queue.put({"type": UIMessageType.AUDIO_STARTED})
    manager = AudioStreamManager()
    manager.start()  # ← Mic ON
    ui_queue.put(
        {"type": UIMessageType.SYS_LOG, "text": "🎤 AudioStream START requested"}
    )
    RealtimeAPIClient().start()


def action_stop_audio():
    ui_queue.put({"type": UIMessageType.AUDIO_STOPPED})
    manager = AudioStreamManager()
    manager.stop()  # ← Mic OFF
    ui_queue.put(
        {"type": UIMessageType.SYS_LOG, "text": "🛑 AudioStream STOP requested"}
    )
    RealtimeAPIClient().stop()


def action_open_apikey_dialog(root):
    key = show_apikey_dialog(root)
    action_close_apikey_dialog(key)


def action_close_apikey_dialog(key):
    if key:
        ConfigManager().set_api_key(key)
        ui_queue.put({"type": UIMessageType.SYS_LOG, "text": "🔑 API Key saved!"})
    else:
        ui_queue.put({"type": UIMessageType.SYS_LOG, "text": "🧐 No API Key provided"})


def action_open_ui():
    action_init()
    ui_queue.put(
        {
            "type": UIMessageType.SYS_LOG,
            "text": "🟢 UI Started",
        }
    )
    show_ui(with_apikey_dialog=True if ConfigManager().get_api_key() is None else False)


def action_close_ui():
    action_stop_audio()
    os._exit(0)


# =====================
# UI
# =====================
SILENCE_THRESHOLD = 10
MAX_VOLUME = 2000


def show_apikey_dialog(parent):
    dialog = tk.Toplevel(parent)
    dialog.title("API Key Required")

    dialog.transient(parent)
    dialog.grab_set()

    label = tk.Label(
        dialog, text="Please enter your OpenAI API key:", font=("Arial", 12)
    )
    label.pack(pady=10)

    # 複数行入力
    text_widget = tk.Text(dialog, height=3, font=("Arial", 12))
    text_widget.pack(fill="x", padx=10, pady=5, expand=True)
    text_widget.insert("1.0", ConfigManager().get_api_key() or "")
    text_widget.focus()

    result = {"value": None}

    def on_ok():
        key = text_widget.get("1.0", "end").strip()
        result["value"] = key
        dialog.destroy()

    def on_cancel():
        result["value"] = None
        dialog.destroy()

    button_frame = tk.Frame(dialog)
    button_frame.pack(pady=15)

    ok_btn = ttk.Button(button_frame, text="OK", command=on_ok)
    ok_btn.pack(side="left", padx=10)

    cancel_btn = ttk.Button(button_frame, text="Cancel", command=on_cancel)
    cancel_btn.pack(side="left", padx=10)

    dialog.bind("<Return>", lambda e: on_ok())

    parent.wait_window(dialog)
    return result["value"]


def show_ui(with_apikey_dialog=False):
    root = tk.Tk()
    root.title("Pepe Translator")
    root.geometry("600x800")
    root.protocol("WM_DELETE_WINDOW", action_close_ui)

    def on_resize(event):
        new_width = event.width - 40
        if new_width > 100:
            translated_label.config(wraplength=new_width)

    root.bind("<Configure>", on_resize)
    ## APIKEY DIALOG
    if with_apikey_dialog:
        root.after(500, lambda: action_open_apikey_dialog(root))

    # Original
    caption_label_title = tk.Label(
        root, text="Transcription:", font=("Arial", 11, "bold")
    )
    caption_label_title.pack(anchor="w", padx=10, pady=(10, 0))

    caption_var = tk.StringVar()
    caption_label = tk.Label(
        root,
        textvariable=caption_var,
        font=("Arial", 14),
        justify="left",
        wraplength=560,  # initial wraplength
    )
    caption_label.pack(anchor="w", padx=10, pady=5)

    # Translation
    translated_label_title = tk.Label(
        root, text="Translation:", font=("Arial", 11, "bold")
    )
    translated_label_title.pack(anchor="w", padx=10, pady=(10, 0))

    translated_var = tk.StringVar()
    translated_label = tk.Label(
        root,
        textvariable=translated_var,
        font=("Arial", 14),
        fg="white",
        justify="left",
        wraplength=560,  # initial wraplength
    )
    translated_label.pack(anchor="w", padx=10, pady=5)

    # Text for logging
    log_label = tk.Label(root, text="Logs:", font=("Arial", 11, "bold"))
    log_label.pack(anchor="w", padx=10, pady=(10, 0))

    log_text = tk.Text(root, height=10, wrap="word")
    log_text.tag_config("gray", foreground="lightgray")
    log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # Sound Level
    volume_label = tk.Label(root, text="Input level:", font=("Arial", 11))
    volume_label.pack(anchor="w", padx=10, pady=(10, 0))

    # Voice Indicator
    volume_var = tk.DoubleVar()
    volume_bar = ttk.Progressbar(root, variable=volume_var, maximum=MAX_VOLUME)
    volume_bar.pack(fill="x", padx=10, pady=5)
    volume_text_var = tk.StringVar(value="0")
    volume_text_label = tk.Label(root, textvariable=volume_text_var, font=("Arial", 10))
    volume_text_label.pack(anchor="w", padx=10)

    # Silence Threshold Slider
    threshold_label = tk.Label(
        root,
        text="Silence/Commit Threshold: Triggers transcription when the input volume falls below this level.",
        font=("Arial", 11),
    )
    threshold_label.pack(anchor="w", padx=10, pady=(10, 0))

    threshold_var = tk.DoubleVar(value=RealtimeAPIClient().commit_level)

    threshold_slider = tk.Scale(
        root,
        from_=0,
        to=3000,
        orient="horizontal",
        variable=threshold_var,
        command=action_change_silence_level,
    )
    threshold_slider.pack(fill="x", anchor="w", padx=10)

    # Noise Reduction Toggle
    nr_button_var = tk.StringVar(value="Noise Reduction Status: ON")

    nr_button = ttk.Button(
        root,
        textvariable=nr_button_var,
        command=lambda: action_toggle_nr(nr_button_var),
    )
    nr_button.pack(pady=5)

    # =========================
    # Audio Input Device Select
    # =========================
    device_names = AudioStreamManager().get_input_devices()

    selected_device_var = tk.StringVar()
    selected_device_var.set(device_names[0])  # default
    AudioStreamManager().set_device(0)
    device_label = tk.Label(root, text="Audio Input Device:", font=("Arial", 11))
    device_label.pack(anchor="w", padx=10, pady=(10, 0))

    device_combo = ttk.Combobox(
        root,
        values=device_names,
        textvariable=selected_device_var,
        state="readonly",
    )
    device_combo.pack(fill="x", padx=10, pady=5)
    device_combo.bind("<<ComboboxSelected>>", action_change_device)

    # === Button Row ===
    button_row = tk.Frame(root)
    button_row.pack(pady=10)

    # Change API Key
    change_key_button = ttk.Button(
        button_row,
        text="Change API Key",
        command=lambda: action_open_apikey_dialog(root),
    )
    change_key_button.pack(side="left", padx=10)

    # Start Audio
    start_btn = ttk.Button(button_row, text="Start Audio", command=action_start_audio)
    start_btn.pack(side="left", padx=10)

    # Stop Audio
    stop_btn = ttk.Button(button_row, text="Stop Audio", command=action_stop_audio)
    stop_btn.pack(side="left", padx=10)

    # Close App
    stop_button = ttk.Button(button_row, text="Close", command=action_close_ui)
    stop_button.pack(side="left", padx=10)

    sys_log_var = tk.StringVar()
    sys_log_label_value = tk.Label(
        root,
        textvariable=sys_log_var,
        font=("Arial", 11),
        fg="white",
        anchor="w",
    )
    sys_log_label_value.pack(fill="x", padx=10, pady=(0, 11))

    # Update UI by Queue message
    def poll_queue():
        try:
            while True:
                msg = ui_queue.get_nowait()
                mtype = msg.get("type")
                if mtype == UIMessageType.CAPTION:
                    text = msg.get("text", "")
                    caption_var.set(text)

                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    log_text.insert(
                        "1.0",
                        f"[{timestamp}]" + "👂" + text + "\n",
                    )
                    log_text.yview_moveto(0)
                elif mtype == UIMessageType.TRANSLATED:
                    text = msg.get("text", "")
                    translated_var.set(text)
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    log_text.insert("1.0", f"[{timestamp}]" + "♻️" + text + "\n", "gray")
                    log_text.yview_moveto(0)
                elif mtype == UIMessageType.VOLUME:
                    level = min(float(msg.get("text", 0)), MAX_VOLUME)
                    volume_var.set(level)
                    volume_text_var.set(f"{float(msg.get('text', 0)):.2f}")
                elif mtype == UIMessageType.LOG:
                    text = msg.get("text", "")
                    log_text.insert("end", text + "\n")
                    log_text.see("end")
                elif mtype == UIMessageType.AUDIO_STOPPED:
                    volume_var.set(0)
                    volume_text_var.set("0")
                elif mtype == UIMessageType.AUDIO_STARTED:
                    volume_bar.configure(style="TProgressbar")
                elif mtype == UIMessageType.SYS_LOG:
                    sys_log_var.set(msg.get("text", ""))
        except Empty:
            pass
        root.after(50, poll_queue)

    poll_queue()
    root.mainloop()
