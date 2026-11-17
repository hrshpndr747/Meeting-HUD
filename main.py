"""Windows entry point; --diagnose works without loading the GUI or a model."""
from __future__ import annotations

import argparse
import logging
import sys

from config import APP_VERSION, AppConfig


def launch(config: AppConfig, start_paused=False, smoke_ui=False) -> int:
    import signal
    import time
    from queue import Full, Queue
    from PyQt6.QtCore import QObject, QTimer, pyqtSlot
    from PyQt6.QtWidgets import QApplication
    from audio_capture import AudioCaptureWorker
    from dictation_capture import DictationCaptureWorker
    from pipeline import Mode, PipelineState, Source, TextRequest, drain
    from speech_engine import SpeechWorker
    from model_catalog import CatalogWorker
    from stealth_win32 import ProtectionError
    from ui_overlay import Overlay
    from gemini_live import GeminiLiveWorker, LiveAudioBus
    from streaming_translation import DictationStreamBus, StreamingTranslationWorker
    from latency_metrics import metric
    from microphones import MicWorker
    from instance_ipc import InstanceError, RestoreListener
    from pathlib import Path

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Meeting HUD")
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    state = PipelineState()
    if start_paused or smoke_ui:
        state.update(paused=True)
    metric("application_start", streaming=config.use_streaming_translation)
    chunks = Queue(maxsize=config.queue_size)
    live_bus = LiveAudioBus()
    stream_bus = DictationStreamBus()
    restore_listener = RestoreListener(Path(__file__).resolve().parent)
    try:
        window = Overlay(config=config)
    except ProtectionError as exc:
        restore_listener.close()
        logging.error("%s", exc)
        return 2
    logging.info("Display affinity 0x11 verified. Ctrl+Shift+H toggles local visibility.")

    class Controller(QObject):
        def __init__(self):
            super().__init__()
            self.closing = self.failed = self.processing = False
            self.exit_code = 0
            self.engine_name = "Loading speech engine"
            self.connected = {source: False for source in Source}
            self.notice_until = 0.0
            self.metrics = ""
            self.manual_pending = False
            self.manual_completed_epoch = -1
            self.paused_before_settings = False
            self.capture = AudioCaptureWorker(config, state, chunks, live_bus)
            self.dictation = DictationCaptureWorker(config, state, chunks, stream_bus if config.use_streaming_translation else None)
            self.speech = SpeechWorker(config, state, chunks)
            self.catalog = CatalogWorker()
            self.mic_check = MicWorker()
            self.live_speech = GeminiLiveWorker(config, state, live_bus)
            self.streaming = StreamingTranslationWorker(config, state, stream_bus)
            self.workers = (self.streaming, self.capture, self.dictation, self.speech, self.catalog, self.mic_check, self.live_speech)
            self.streaming.recognized.connect(self.recognized)
            self.streaming.partial.connect(self.translation_partial)
            self.streaming.completed.connect(self.dictation_done)
            self.streaming.error.connect(self.recoverable_error)
            self.streaming.status.connect(self.notice)
            self.speech.translation_partial.connect(self.translation_partial)
            self.speech.recoverable_error.connect(self.recoverable_error)
            self.live_connected = {source: False for source in Source}
            window.settings_editor.microphone_request.connect(self.mic_check.submit)
            window.settings_editor.microphone_cancel.connect(self.mic_check.cancel)
            self.mic_check.devices.connect(window.settings_editor.apply_microphones)
            self.mic_check.level.connect(window.settings_editor.mic_level)
            self.mic_check.completed.connect(window.settings_editor.mic_completed)
            self.live_speech.result.connect(self.result)
            self.live_speech.partial.connect(self.partial)
            self.live_speech.connection.connect(self.live_connection)
            window.mode_changed.connect(self.change_mode)
            window.pause_requested.connect(self.toggle_pause)
            window.shutdown_requested.connect(self.shutdown)
            window.protection_failed.connect(self.protection_failure)
            window.record_requested.connect(self.record)
            window.cancel_requested.connect(self.cancel_manual)
            window.translate_text_requested.connect(self.translate_text)
            window.settings_saved.connect(self.save_settings)
            window.settings_opened.connect(self.settings_opened)
            window.discover_models.connect(self.catalog.submit)
            self.catalog.discovered.connect(window.settings_editor.apply_catalog)
            window.local_hidden.connect(self.hidden)
            self.dictation.finished_recording.connect(self.recording_finished)
            self.dictation.level.connect(self.level)
            self.capture.source_state.connect(self.source_state)
            self.speech.ready.connect(self.engine_ready)
            self.speech.busy.connect(self.busy)
            self.speech.result.connect(self.result)
            self.speech.recognized.connect(self.recognized)
            self.speech.dictation_done.connect(self.dictation_done)
            for worker in (self.capture, self.speech, self.live_speech):
                worker.status.connect(self.notice)
                worker.fault.connect(self.fault)
            self.timer = QTimer(self)
            self.timer.setInterval(100)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

        @pyqtSlot(object)
        def change_mode(self, mode):
            self.mic_check.cancel()
            window.clear_partials()
            state.update(mode=mode, paused=False)
            drain(chunks)
            self.manual_pending = False
            self.connected = {source: False for source in Source}
            self.metrics = ""
            self.notice("Microphone off · press Record to begin" if mode == Mode.TRANSLATION
                        else "Listening to meeting output and microphone")

        @pyqtSlot(bool)
        def settings_opened(self, opened):
            self.mic_check.cancel()
            window.clear_partials()
            if not opened:
                window.settings_editor.cancel_microphone()
            if opened:
                self.paused_before_settings = state.snapshot().paused
                state.update(paused=True)
            else:
                state.update(paused=self.paused_before_settings)
            drain(chunks)
            self.manual_pending = False

        @pyqtSlot()
        def record(self):
            snapshot = state.snapshot()
            if self.failed or self.closing or snapshot.mode != Mode.TRANSLATION:
                return
            if snapshot.recording:
                state.finish_recording(snapshot.epoch)
                self.manual_pending = True
                self.notice("Finishing your recording…")
            elif snapshot.ready and not self.manual_pending and not self.processing:
                if config.dictation_backend in ("groq", "gemini") and (not getattr(config, config.dictation_backend + "_api_key") or not config.cloud_enabled):
                    self.notice("Cloud mode requires a key. Choose Local models in Settings for free processing.")
                    return
                drain(chunks)
                try:
                    snapshot = state.begin_recording()
                except ValueError:
                    self.notice("Speech engine state changed. Wait until it is ready and try again.")
                    return
                window.clear_error()
                window.clear_translation()
                if config.use_streaming_translation:
                    stream_bus.begin(snapshot.epoch)
                    self.manual_pending = True
                self.dictation.request_start()
                self.notice("Connecting… microphone off until ready" if config.use_streaming_translation else
                            "Recording · pauses are kept · press Stop when done")
            self.tick()

        @pyqtSlot()
        def cancel_manual(self):
            if state.snapshot().mode == Mode.TRANSLATION:
                state.invalidate()
                window.clear_error()
                drain(chunks)
                self.manual_pending = False
                self.notice("Cancelled · microphone off")
                self.tick()

        @pyqtSlot()
        def hidden(self):
            self.mic_check.cancel()
            window.settings_editor.cancel_microphone()
            if state.snapshot().recording:
                self.cancel_manual()

        @pyqtSlot(int, bool, str)
        def recording_finished(self, epoch, submitted, message):
            if state.snapshot().epoch == epoch and epoch != self.manual_completed_epoch:
                self.manual_pending = submitted
                self.notice(message)
                self.tick()

        @pyqtSlot(int, float)
        def level(self, epoch, value):
            if state.snapshot().epoch == epoch:
                window.waveform.add_level(value)

        @pyqtSlot(object)
        def recognized(self, item):
            if state.accepts(item.epoch, Source.MIC):
                window.set_recognized(item.text)

        @pyqtSlot(object)
        def translation_partial(self, item):
            if not self.closing and state.accepts(item.epoch, Source.MIC):
                window.set_translation_partial(item)
                metric("translation_ui_updated", epoch=item.epoch, final=item.final, characters=len(item.text))

        @pyqtSlot(int, str)
        def recoverable_error(self, epoch, message):
            if not self.closing and state.accepts(epoch, Source.MIC):
                window.set_error(message)

        @pyqtSlot(int, str)
        def dictation_done(self, epoch, message):
            if state.snapshot().epoch == epoch:
                self.manual_completed_epoch = epoch
                self.manual_pending = False
                self.notice(message or "Ready for a new recording.")
                self.tick()

        @pyqtSlot(str)
        def translate_text(self, text):
            if self.failed or self.closing or self.manual_pending or self.processing:
                return
            if not text.strip() or len(text) > 8000:
                self.notice("Enter between 1 and 8,000 characters to translate.")
                return
            snapshot = state.invalidate()
            window.clear_error()
            window.clear_translation()
            drain(chunks)
            try:
                chunks.put_nowait(TextRequest(text.strip(), snapshot.epoch, time.monotonic()))
            except Full:
                self.notice("Speech queue is busy. Retry Translate text after processing finishes.")
                return
            self.manual_pending = True
            self.tick()

        @pyqtSlot(object)
        def save_settings(self, data):
            from settings_service import save_settings
            from credentials import CredentialError
            try:
                save_settings(config, data)
            except (ValueError, CredentialError) as exc:
                window.settings_editor.message.setText(str(exc))
                return
            except (TypeError, KeyError, OSError):
                window.settings_editor.message.setText("Settings could not be saved. Check folder permissions and try again.")
                return
            self.exit_code = 75  # Quiet launcher restarts after the current workers finish.
            self.shutdown()

        @pyqtSlot()
        def toggle_pause(self):
            if self.failed or self.closing:
                return
            paused = not state.snapshot().paused
            window.clear_partials()
            state.update(paused=paused)
            drain(chunks)
            self.connected = {source: False for source in Source}
            self.notice("Capture paused" if paused else "Resuming capture…")

        @pyqtSlot(str, bool)
        def source_state(self, source, connected):
            self.connected[Source(source)] = connected

        @pyqtSlot(str, bool)
        def live_connection(self, source, connected):
            self.live_connected[Source(source)] = connected

        @pyqtSlot(object)
        def partial(self, item):
            if not self.closing and state.accepts(item.epoch, item.source):
                window.set_partial(item)

        @pyqtSlot(str)
        def engine_ready(self, description):
            self.engine_name = description
            self.notice(description)

        @pyqtSlot(bool)
        def busy(self, active):
            self.processing = active

        @pyqtSlot(str)
        def notice(self, text):
            if not self.closing and not self.failed:
                window.set_notice(text)
                self.notice_until = time.monotonic() + 5

        @pyqtSlot(object)
        def result(self, result):
            if not self.closing and state.accepts(result.epoch, result.source):
                window.add_result(result)
                self.metrics = ("Gemini streaming" if config.backend == "gemini" and result.mode == Mode.TRANSCRIPT else
                                f"{result.end_to_text_s:.1f}s end→text · {result.inference_s:.2f}s inference")

        @pyqtSlot(str)
        def fault(self, message):
            if self.closing:
                return
            self.failed = True
            self.exit_code = 1
            state.update(paused=True, ready=False)
            drain(chunks)
            window.set_error(message)
            window.pause_button.setEnabled(False)
            self.capture.request_stop()
            self.dictation.request_stop()
            self.speech.request_stop()
            self.live_speech.request_stop()
            self.streaming.request_stop()
            self.mic_check.cancel()
            logging.error("%s", message)

        @pyqtSlot(str)
        def protection_failure(self, message):
            logging.error("%s", message)
            self.exit_code = 2
            self.shutdown()

        @pyqtSlot()
        def shutdown(self):
            if self.closing:
                return
            self.closing = True
            state.update(stopped=True, ready=False, paused=True)
            drain(chunks)
            window.prepare_shutdown()
            for worker in self.workers:
                worker.request_stop()

        @pyqtSlot()
        def tick(self):
            if self.closing:
                if not any(w.isRunning() for w in self.workers):
                    self.timer.stop()
                    app.quit()
                return
            try:
                if restore_listener.poll():
                    window.restore_local()
            except InstanceError:
                # Qt slots must not leak native errors through the event loop.
                try:
                    restore_listener.close()
                except InstanceError:
                    pass
                self.notice("Shortcut restore unavailable. Use the tray icon or Ctrl+Shift+H.")
            snapshot = state.snapshot()
            working = self.processing or self.manual_pending
            window.set_record_state(snapshot.recording, working, snapshot.ready and not self.failed)
            required = (Source.MIC,) if snapshot.mode == Mode.TRANSLATION else tuple(Source)
            session = stream_bus.current() if config.use_streaming_translation else None
            connecting = bool(snapshot.recording and session and session.epoch == snapshot.epoch
                              and not session.capturing.is_set())
            status = ("ERROR" if self.failed else "PAUSED" if snapshot.paused else
                      "LOAD" if not snapshot.ready else "LINK" if connecting else "REC" if snapshot.recording else "WORK" if working else
                      "IDLE" if snapshot.mode == Mode.TRANSLATION else
                      "AUDIO" if not all(self.connected[s] for s in required) else
                      "LINK" if config.backend == "gemini" and not all(self.live_connected[s] for s in required) else "LIVE")
            window.set_status(status, snapshot.paused)
            if not self.failed and snapshot.mode == Mode.TRANSLATION and snapshot.recording:
                window.set_notice("Connecting… microphone off" if connecting else "Listening…")
            elif not self.failed and snapshot.mode == Mode.TRANSLATION and working and time.monotonic() >= self.notice_until:
                window.set_notice("Translating…")
            elif not self.failed and time.monotonic() >= self.notice_until:
                sources = " · ".join(f"{s.value} {'on' if self.connected[s] else 'off'}" for s in required)
                route = f"{config.dictation_backend} speech → {config.text_translator} English · microphone off"
                window.set_notice((route if snapshot.mode == Mode.TRANSLATION and not snapshot.recording and not working else self.engine_name) + " · " + (self.metrics or sources))

    controller = Controller()
    if config.credential_notice:
        window.toggle_settings()
        window.settings_editor.message.setText(config.credential_notice)
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: controller.shutdown())
    app.aboutToQuit.connect(controller.shutdown)
    window.show()
    if smoke_ui:
        window.set_notice("UI check · microphone and network workers are off")
    if not controller.closing and not smoke_ui:
        for worker in controller.workers:
            worker.start()
    code = app.exec()
    controller.shutdown()
    # Only after the GUI loop exits. While closing normally, tick polls without
    # blocking the UI; native inference is never force-terminated mid-call.
    for worker in controller.workers:
        worker.request_stop()
        worker.wait()
    restore_listener.close()
    return controller.exit_code or code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Windows meeting transcription / English translation HUD")
    parser.add_argument("--version", action="version", version=f"Meeting HUD {APP_VERSION}")
    parser.add_argument("--diagnose", action="store_true", help="Print a JSON setup report without recording audio")
    parser.add_argument("--report", metavar="PATH", help="Also save the --diagnose JSON report to this file")
    parser.add_argument("--check-model", action="store_true", help="With --diagnose, load/warm the local model (may download)")
    parser.add_argument("--paused", action="store_true", help="Start with capture paused")
    parser.add_argument("--smoke-ui", action="store_true", help="Open the UI without starting audio, network or model workers")
    args = parser.parse_args(argv)
    if (args.report or args.check_model) and not args.diagnose:
        parser.error("--report and --check-model require --diagnose")
    from app_logging import configure_logging
    configure_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.diagnose:
        from diagnostics import diagnose
        return diagnose(report_path=args.report, check_model=args.check_model)
    if sys.platform != "win32":
        print("The HUD requires Windows 10 build 19041+ or Windows 11 x64. Use --diagnose for setup information.", file=sys.stderr)
        return 2
    try:
        config = AppConfig.from_env()
    except (TypeError, ValueError) as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    try:
        from runtime_bootstrap import preload_onnx
        preload_onnx()
        return launch(config, start_paused=args.paused, smoke_ui=args.smoke_ui)
    except ImportError as exc:
        print(f"Missing dependency ({exc.name}); install requirements.txt in this virtual environment.", file=sys.stderr)
        return 2
    except Exception as exc:
        logging.exception("Application startup failed (%s)", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
