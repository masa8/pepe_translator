import asyncio
import json
import base64
import websockets
import time
import threading
import logging
from queue import Empty
from core.config_manager import ConfigManager
from core.audio_manager import AudioStreamManager
from core.message_types import UIMessageType, UIMessageMixin


WS_URL = "wss://api.openai.com/v1/realtime?model=gpt-realtime-mini"


class RealtimeAPIClient(UIMessageMixin):
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            UIMessageMixin.__init__(cls._instance)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self.ws = None
        self.loop = None
        self.main_task = None
        self.stop_flag = False
        self._initialized = True
        self.commit_level = 10

        # translation_queue:
        # -------------------------------------------------------------
        # A FIFO queue that stores pending texts that still need to be translated.
        # Whisper may produce multiple transcripts (delta or completed) in rapid
        # succession, even while a translation is still in progress.
        # Instead of dropping them, we store them here and process them one-by-one
        # after the current translation finishes.
        # This guarantees that *no translation is ever lost*.
        self.translation_queue = []

    # ==========================================================
    # Public API
    # ==========================================================

    def set_commit_level(self, level):
        self.commit_level = level
        self.ui_msg(UIMessageType.SYS_LOG, f"🔈 Silence level updated: {level}")

    def start(self):
        """Start realtime thread"""
        if self.main_task and not self.main_task.done():
            self.ui_msg(UIMessageType.SYS_LOG, "⚠️ Already running")
            return

        self.stop_flag = False
        self.loop = asyncio.new_event_loop()
        self.main_task = asyncio.ensure_future(self._runner(), loop=self.loop)

        threading.Thread(
            target=self.loop.run_until_complete, args=(self.main_task,), daemon=True
        ).start()

        self.ui_msg(UIMessageType.SYS_LOG, "🚀 Realtime STARTED")

    def stop(self):
        """Stop realtime safely"""
        self.ui_msg(UIMessageType.SYS_LOG, "🛑 Realtime STOP requested")
        self.stop_flag = True

        if self.ws:
            try:
                # Immediately break recv()
                self.ws.fail_connection()
            except Exception:
                pass

    # ==========================================================
    # Internals
    # ==========================================================

    async def _runner(self):
        """Main lifecycle"""
        while not self.stop_flag:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.ui_msg(UIMessageType.SYS_LOG, f"❌ Error: {e}")
                logging.error(f"❌ Error: {e}")
                await asyncio.sleep(1)

        self.ui_msg(UIMessageType.SYS_LOG, "🛑 Realtime STOPPED")

    async def _connect_and_run(self):
        """Connect → configure session → run sender + receiver"""
        headers = [
            ("Authorization", f"Bearer {ConfigManager().get_api_key()}"),
            ("OpenAI-Beta", "realtime=v1"),
        ]

        self.ws = await websockets.connect(WS_URL, extra_headers=headers)
        self.ui_msg(UIMessageType.SYS_LOG, "🔗 WS Connected")

        # It is not explicitly stated whether you must wait for session.created before sending session.update
        # so, just ignore session.created

        # sending session update
        await self.ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],
                        "input_audio_transcription": {
                            "model": "whisper-1",
                        },
                        "turn_detection": None,
                        "instructions": (
                            "Your only task is transcription. Output must contain only the raw spoken text with no explanations, no comments, no punctuation suggestions, and no metadata. Do not add anything else. "
                            "Output only plain text. Do not use JSON, quotes, code blocks, or any formatting."
                        ),
                    },
                }
            )
        )

        # Trigger subsequent events only after confirming session.updated
        while True:
            raw = await self.ws.recv()
            msg = json.loads(raw)
            if msg["type"] == "session.updated":
                logging.info(f"✅ SESSION UPDATED:{msg['session']}")
                break

        self.ui_msg(UIMessageType.SYS_LOG, "🎤 Whisper READY")
        sender_task = asyncio.create_task(self._sender())
        receiver_task = asyncio.create_task(self._receiver())

        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
        )

        # cancel all
        for t in pending:
            t.cancel()

        try:
            await self.ws.close()
        except:
            pass

    # ==========================================================
    # Sender
    # ==========================================================

    async def _sender(self):
        pending_pcm = []
        audio_q = AudioStreamManager().audio_queue
        last_commit = time.time()

        while not self.stop_flag:
            try:
                pcm_bytes, volume = audio_q.get_nowait()
            except Empty:
                await asyncio.sleep(0.05)
                continue

            enc = base64.b64encode(pcm_bytes).decode()
            pending_pcm.append(pcm_bytes)

            # When you send an input_audio_buffer.append event,
            # the server does not send a confirmation response to this event.
            # The only time you will receive a related server event is when speech is detected and committed (if VAD is enabled),
            # at which point you may receive events like input_audio_buffer.committed,
            # but not as a direct response to each append.
            await self.ws.send(
                json.dumps({"type": "input_audio_buffer.append", "audio": enc})
            )
            total_bytes = sum(len(x) for x in pending_pcm)

            if total_bytes < (
                16000 * 0.5 * 2
            ):  # 500ms = 16000 sample/s * 0.5, 16-bit = x2
                await asyncio.sleep(0.05)
                continue

            # Send it when all three conditions are met.
            now = time.time()
            if (
                total_bytes >= 16000 * 0.5 * 2  # at least 500ms of audio
                and now - last_commit > 3  # at least 3 seconds since last commit
                and volume < self.commit_level  # audio level is low
            ):
                # WebSocket sending is asynchronous, so even if you call send(append) and then send(commit) in that order,
                # the server is not guaranteed to receive them in the same order. When the sender runs fast,
                # the messages can arrive as commit first, causing Whisper to think the audio buffer is incomplete.
                await asyncio.sleep(0.05)

                # When you send an input_audio_buffer.commit event,
                # the server will create a new user message item in the conversation from the current audio buffer.
                # This will trigger input audio transcription (if enabled in the session configuration),
                # but it will not automatically create a response from the model.
                # The server will respond with an input_audio_buffer.committed event, which includes the ID of the new user message item.
                # If the input audio buffer is empty, the server will return an error
                # However, the specific error message or code is not provided in the available knowledge
                #
                # When you call input_audio_buffer.commit,
                # all audio in the buffer up to that point is used to create a new user message item in the conversation,
                # and the buffer is cleared. This is stated in the documentation
                #
                # If there is audio in the buffer but it is only noise (not silence),
                # the knowledge sources do not state that this will cause an error. Instead,
                # the model may attempt to transcribe or interpret the noisy audio.
                # In cases of unintelligible or unclear audio, the model might respond with a clarification request or similar behavior,
                # but this is not treated as an error by the API itself
                #
                # when you send an input_audio_buffer.commit event,
                # the server will respond with an input_audio_buffer.committed event,
                # which includes the ID of the new user message item that will be created.
                # After this, you may also receive additional events related to the processing of the committed audio,
                # such as transcription events (conversation.item.input_audio_transcription.delta and
                # conversation.item.input_audio_transcription.completed)
                # if transcription is enabled in your session
                await self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                self.ui_msg(UIMessageType.SYS_LOG, f"🎯 Commit volume:{volume}")
                last_commit = now
                pending_pcm.clear()
                await asyncio.sleep(0.05)

                # Sending translation event
                if self.translation_queue:
                    next_text = self.translation_queue.pop(0)
                    await self.send_translation(next_text)

        raise asyncio.CancelledError()

    # ==========================================================
    # Receiver
    # ==========================================================

    async def _receiver(self):
        while not self.stop_flag:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                await asyncio.sleep(0.005)
                continue
            except Exception:
                break

            msg = json.loads(raw)
            t = msg.get("type")
            if t == "rate_limits.updated":
                pass
            elif t == "input_audio_buffer.committed":
                pass
            elif t == "conversation.item.created":
                pass
            elif t == "conversation.item.input_audio_transcription.delta":
                pass
            elif t == "conversation.item.input_audio_transcription.completed":
                text = msg.get("transcript", "").strip()
                if text:
                    self.ui_msg(UIMessageType.CAPTION, text)
                    self.translation_queue.append(text)
            elif t == "response.output_item.added":
                pass
            elif t == "response.created":
                pass
            elif t == "response.content_part.added":
                pass
            elif t == "response.text.delta":
                pass
            elif t == "esponse.text.done":
                pass
            elif t == "esponse.content_part.done":
                pass
            elif t == "response.output_item.done":
                item = msg.get("item", {})
                content = item.get("content", [])
                for c in content:
                    if c.get("type") == "text":
                        text = c.get("text", "").strip()
                        if text:
                            # remove json style
                            if text.startswith("{") and text.endswith("}"):
                                try:
                                    # get text from {"something": "..."}
                                    inner = json.loads(text)
                                    text = " ".join(inner.values())
                                except Exception:
                                    pass
                            if text.strip():
                                self.ui_msg(UIMessageType.TRANSLATED, text)

            elif t == "response.done":
                pass

            elif t == "error":
                self.ui_msg(UIMessageType.SYS_LOG, f"❌ ERROR: {msg}")
                logging.error(f"❌ OpenAI error received: {msg}")
                break

        raise asyncio.CancelledError()

    async def send_translation(self, text):
        await self.ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": text,
                            }
                        ],
                    },
                }
            )
        )
        await asyncio.sleep(0.03)
        await self.ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "modalities": ["text"],
                        "instructions": (
                            "Translate the latest user message into natural Japanese. Output must contain only the translated sentence. Do not add explanations, comments, notes, brackets, quotes, metadata, or any other text."
                        ),
                    },
                }
            )
        )
