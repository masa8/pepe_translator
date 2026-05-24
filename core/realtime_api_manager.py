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

DEFAULT_TRANSLATION_INSTRUCTIONS = (
    "You are a Japanese translator. Translate the latest user message into natural Japanese, and output only the Japanese translation. "
    "Do not answer, paraphrase, summarize, comment, or add any extra text. Do not include English, punctuation notes, explanation, or metadata. "
    "If the message is already in English, still translate it into Japanese. Always output only the translated Japanese sentence."
)

DEFAULT_TRANSCRIPTION_INSTRUCTIONS = (
    "Transcribe the latest committed user audio verbatim in the original spoken language. "
    "Do not translate, summarize, answer, continue the conversation, or add any commentary. "
    "Output only the raw transcript text."
)


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
        self.translation_lock = asyncio.Lock()
        self.response_lock = asyncio.Lock()
        self.buffered_audio_bytes = 0
        self.pending_response_requests = []
        self.response_in_flight = False
        self.translation_prompt = ConfigManager().get_prompt(
            default=DEFAULT_TRANSLATION_INSTRUCTIONS
        )

    # ==========================================================
    # Public API
    # ==========================================================

    def set_commit_level(self, level):
        self.commit_level = level
        self.ui_msg(UIMessageType.SYS_LOG, f"🔈 Silence level updated: {level}")

    def set_translation_prompt(self, prompt):
        self.translation_prompt = prompt or DEFAULT_TRANSLATION_INSTRUCTIONS
        self.ui_msg(UIMessageType.SYS_LOG, "📝 Translation prompt updated.")

    def start(self):
        """Start realtime thread"""
        if self.main_task and not self.main_task.done():
            self.ui_msg(UIMessageType.SYS_LOG, "⚠️ Already running")
            return

        api_key = ConfigManager().get_api_key()
        if not api_key:
            self.ui_msg(
                UIMessageType.SYS_LOG,
                "⚠️ API Key is not set. Please set it before starting audio.",
            )
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

        if self.ws and self.loop and self.loop.is_running():
            try:
                # Ask for a graceful close first so we do not emit noisy close-frame errors.
                asyncio.run_coroutine_threadsafe(self.ws.close(code=1000), self.loop)
            except Exception:
                try:
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
                if self.stop_flag:
                    break
                self.ui_msg(UIMessageType.SYS_LOG, f"❌ Error: {e}")
                logging.error(f"❌ Error: {e}")
                if isinstance(e, ValueError):
                    # Session payload/config errors are deterministic; avoid reconnect storm.
                    self.stop_flag = True
                    break
                await asyncio.sleep(1)

        self.ui_msg(UIMessageType.SYS_LOG, "🛑 Realtime STOPPED")

    async def _connect_and_run(self):
        """Connect → configure session → run sender + receiver"""
        headers = [
            (
                "Authorization",
                f"Bearer {ConfigManager().get_api_key()}",
            ),
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
                        "type": "realtime",
                        "output_modalities": ["text"],
                        "audio": {
                            "input": {
                                "turn_detection": None,
                                "transcription": {"model": "gpt-4o-mini-transcribe"},
                            }
                        },
                        "instructions": (
                            "Your only task is transcription. Output must contain only the raw spoken text with no explanations, no comments, no punctuation suggestions, and no metadata. Do not add anything else. "
                            "Output only plain text. Do not use JSON, quotes, code blocks, or any formatting."
                        ),
                    },
                }
            )
        )

        # Trigger subsequent events only after confirming session.updated
        await self._wait_for_session_updated(timeout_sec=10)

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
        except Exception:
            pass

    async def _wait_for_session_updated(self, timeout_sec=10):
        deadline = time.time() + timeout_sec
        while not self.stop_flag and time.time() < deadline:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            msg = json.loads(raw)
            t = msg.get("type")

            if t == "session.updated":
                logging.info(f"✅ SESSION UPDATED:{msg.get('session')}")
                return

            if t == "error":
                err = msg.get("error", msg)
                code = ""
                if isinstance(err, dict):
                    code = err.get("code", "")
                if code in {"unknown_parameter", "missing_required_parameter"}:
                    raise ValueError(f"session.update failed: {err}")
                raise RuntimeError(f"session.update failed: {err}")

            logging.info(f"↪ pre-ready event: {t}")

        raise TimeoutError("Timeout waiting for session.updated")

    # ==========================================================
    # Sender
    # ==========================================================

    async def _sender(self):
        audio_q = AudioStreamManager().audio_queue
        last_commit = time.time()
        last_audio_activity = time.time()
        max_buffer_seconds = 4.0
        last_status_log = 0.0
        status_log_interval = 2.0

        while not self.stop_flag:
            try:
                pcm_bytes, volume = audio_q.get_nowait()
            except Empty:
                now = time.time()
                if self.buffered_audio_bytes > 0:
                    total_bytes = self.buffered_audio_bytes
                    enough_audio = total_bytes >= 16000 * 0.5 * 2
                    idle_long_enough = now - last_audio_activity > 0.8
                    commit_gap_ok = now - last_commit > 0.8
                    if enough_audio and idle_long_enough and commit_gap_ok:
                        try:
                            await self.ws.send(
                                json.dumps({"type": "input_audio_buffer.commit"})
                            )
                            self.ui_msg(
                                UIMessageType.SYS_LOG,
                                "🎯 Commit volume:0 (inactivity)",
                            )
                            self.ui_msg(
                                UIMessageType.LOG,
                                "🎯 Commit volume:0 (inactivity)",
                            )
                            last_commit = now
                            self.buffered_audio_bytes = 0
                        except Exception as e:
                            self.ui_msg(UIMessageType.SYS_LOG, f"❌ Send error:{e}")
                            self.ui_msg(UIMessageType.LOG, f"❌ Send error:{e}")
                    elif now - last_status_log > status_log_interval:
                        buffered_seconds = total_bytes / (16000 * 2)
                        self.ui_msg(
                            UIMessageType.LOG,
                            "⌛ Commit待機: "
                            f"buffer={buffered_seconds:.2f}s, "
                            f"idle={now - last_audio_activity:.2f}s, "
                            f"since_commit={now - last_commit:.2f}s",
                        )
                        last_status_log = now
                elif now - last_status_log > status_log_interval:
                    self.ui_msg(
                        UIMessageType.LOG,
                        "⌛ Commit待機: 音声フレームなし "
                        f"(min_volume_for_speech={AudioStreamManager().min_volume_for_speech})",
                    )
                    last_status_log = now
                await asyncio.sleep(0.05)
                continue

            last_audio_activity = time.time()

            enc = base64.b64encode(pcm_bytes).decode()

            # When you send an input_audio_buffer.append event,
            # the server does not send a confirmation response to this event.
            # The only time you will receive a related server event is when speech is detected and committed (if VAD is enabled),
            # at which point you may receive events like input_audio_buffer.committed,
            # but not as a direct response to each append.
            try:
                await self.ws.send(
                    json.dumps({"type": "input_audio_buffer.append", "audio": enc})
                )
            except Exception as e:
                self.ui_msg(UIMessageType.SYS_LOG, f"❌ Send error:{e}")
                continue

            self.buffered_audio_bytes += len(pcm_bytes)

            total_bytes = self.buffered_audio_bytes
            buffered_seconds = total_bytes / (16000 * 2)

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
                try:
                    await self.ws.send(
                        json.dumps({"type": "input_audio_buffer.commit"})
                    )
                    self.ui_msg(UIMessageType.SYS_LOG, f"🎯 Commit volume:{volume}")
                    self.ui_msg(UIMessageType.LOG, f"🎯 Commit volume:{volume}")
                    last_commit = now
                    self.buffered_audio_bytes = 0
                except Exception as e:
                    self.ui_msg(UIMessageType.SYS_LOG, f"❌ Send error:{e}")
                    self.ui_msg(UIMessageType.LOG, f"❌ Send error:{e}")
                    continue

                await asyncio.sleep(0.05)

                # Sending translation event
                await self._flush_translation_queue()

            # Fallback commit: if continuous input never drops below threshold,
            # flush periodically to avoid getting stuck behind noise floor.
            elif (
                total_bytes >= 16000 * 0.5 * 2
                and now - last_commit > 1.2
                and buffered_seconds >= max_buffer_seconds
            ):
                try:
                    await self.ws.send(
                        json.dumps({"type": "input_audio_buffer.commit"})
                    )
                    self.ui_msg(
                        UIMessageType.SYS_LOG,
                        f"🎯 Commit volume:{volume} (max_buffer)",
                    )
                    self.ui_msg(
                        UIMessageType.LOG,
                        f"🎯 Commit volume:{volume} (max_buffer={buffered_seconds:.2f}s)",
                    )
                    last_commit = now
                    self.buffered_audio_bytes = 0
                except Exception as e:
                    self.ui_msg(UIMessageType.SYS_LOG, f"❌ Send error:{e}")
                    self.ui_msg(UIMessageType.LOG, f"❌ Send error:{e}")
                    continue

                await asyncio.sleep(0.05)
                await self._flush_translation_queue()

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
            # logging.info(f"📩 Realtime event type: {t}")
            if t == "rate_limits.updated":
                pass
            elif t == "input_audio_buffer.committed":
                self.buffered_audio_bytes = 0
            elif t == "conversation.item.created":
                item = msg.get("item", {})
                if item.get("role") != "user":
                    continue
                for c in item.get("content", []):
                    text = (c.get("transcript") or c.get("text") or "").strip()
                    if text:
                        self.ui_msg(UIMessageType.CAPTION, text)
                        break
            elif t == "conversation.item.input_audio_transcription.delta":
                pass
            elif t == "conversation.item.input_audio_transcription.completed":
                text = msg.get("transcript", "").strip()
                if text:
                    self.ui_msg(UIMessageType.CAPTION, text)
                    self.translation_queue.append(text)
                    await self._flush_translation_queue()
            elif t == "response.output_text.delta":
                pass
            elif t == "response.output_text.done":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                self.ui_msg(UIMessageType.TRANSLATED, text)
            elif t == "response.output_audio_transcript.done":
                # We request translation as text-only responses.
                pass
            elif t == "response.output_item.added":
                pass
            elif t == "response.created":
                pass
            elif t == "response.content_part.added":
                pass
            elif t == "response.text.delta":
                pass
            elif t == "response.text.done":
                pass
            elif t == "response.content_part.done":
                pass
            elif t == "response.output_item.done":
                # handled by response.output_text.done / response.output_audio_transcript.done
                pass

            elif t == "response.done":
                self.response_in_flight = False
                await self._dispatch_next_response_request()

            elif t == "error":
                err = msg.get("error", {})
                code = err.get("code") if isinstance(err, dict) else None

                if code == "conversation_already_has_active_response":
                    # Keep the socket alive and wait for response.done.
                    self.response_in_flight = True
                    self.ui_msg(
                        UIMessageType.SYS_LOG,
                        "⏳ Response in progress, waiting before next request.",
                    )
                    continue

                if code == "input_audio_buffer_commit_empty":
                    # Server-side VAD may have already committed and cleared the buffer.
                    self.buffered_audio_bytes = 0
                    self.ui_msg(
                        UIMessageType.LOG,
                        "ℹ️ Skip empty commit (buffer already cleared).",
                    )
                    continue

                self.ui_msg(UIMessageType.SYS_LOG, f"❌ ERROR: {msg}")
                logging.error(f"❌ OpenAI error received: {msg}")
                break

        raise asyncio.CancelledError()

    async def send_translation(self, text):
        await self._queue_response_request(
            mode="translation",
            instructions=self.translation_prompt,
            user_text=text,
        )

    async def _queue_response_request(self, mode, instructions, user_text=None):
        async with self.response_lock:
            request = {
                "mode": mode,
                "instructions": instructions,
                "user_text": user_text,
            }

            if self.response_in_flight:
                self.pending_response_requests.append(request)
                return

            await self._send_response_request(request)

    async def _dispatch_next_response_request(self):
        async with self.response_lock:
            if self.response_in_flight or not self.pending_response_requests:
                return

            request = self.pending_response_requests.pop(0)
            await self._send_response_request(request)

    async def _send_response_request(self, request):
        user_text = request.get("user_text")
        instructions = request["instructions"]

        response_body = {
            "output_modalities": ["text"],
            "instructions": instructions,
        }

        # Keep translation isolated from default conversation so prior turns do not
        # leak style/language back into translated output.
        if user_text:
            response_body["conversation"] = "none"
            response_body["input"] = [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_text,
                        }
                    ],
                }
            ]

        await self.ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": response_body,
                }
            )
        )
        self.response_in_flight = True

    async def _flush_translation_queue(self):
        async with self.translation_lock:
            while self.translation_queue and not self.stop_flag:
                next_text = self.translation_queue.pop(0)
                try:
                    await self.send_translation(next_text)
                except Exception as e:
                    # Restore order on transient send failures.
                    self.translation_queue.insert(0, next_text)
                    self.ui_msg(UIMessageType.SYS_LOG, f"❌ Send error:{e}")
                    break
