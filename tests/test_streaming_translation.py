"""Streaming regressions. In-memory transports only; never contact a service."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from queue import Queue
from threading import Event
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
import numpy as np
from support import APP, wait_for
from config import AppConfig
from gemini_translation import GeminiTextStream, StreamError, StreamCancelled, thinking_config
from streaming_translation import RecordingStream, DictationStreamBus, PhraseBook, StreamingTranslationWorker
from pipeline import Mode, Source, PipelineState, TranslationUpdate, SpeechResult, TextRequest
from test_native_and_ui import FakeGuard
from ui_overlay import Overlay
from gemini_live import LivePartial


def config(**kwargs):
    values = dict(backend='gemini', model='gemini-3.5-transcribe-live', dictation_backend='gemini',
                  dictation_model='gemini-3.5-transcribe', text_translator='gemini',
                  gemini_model='gemini-3.5-flash', gemini_api_key='test-secret', cloud_enabled=True)
    values.update(kwargs)
    return AppConfig(**values)


def event(text='', finish=None, thought=False):
    candidate = {'content': {'parts': [{'text': text, 'thought': thought}]}}
    if finish:
        candidate['finishReason'] = finish
    return ('data: '+json.dumps({'candidates': [candidate]}, ensure_ascii=False)+'\n\n').encode()

class Body(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0, stall=False):
        self.chunks, self.delay, self.stall, self.closed = chunks, delay, stall, False
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
            await asyncio.sleep(self.delay)
        if self.stall:
            await asyncio.sleep(60)
    async def aclose(self):
        self.closed = True

class SSETests(unittest.IsolatedAsyncioTestCase):
    async def make(self, body, handler=None, **kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200, stream=body))))
        self.addAsyncCleanup(client.aclose)
        return GeminiTextStream(config(), client=client, **kwargs)

    async def test_first_text_is_emitted_before_completion_and_thoughts_hidden(self):
        seen = []
        body = Body([event('private thought', thought=True), event('Hello '), event('world.', 'STOP')], .02)
        translator = await self.make(body)
        result = await translator.translate('source', lambda: False, lambda text: seen.append((text, body.closed)))
        self.assertEqual(result, 'Hello world.')
        self.assertEqual([x[0] for x in seen], ['Hello ', 'Hello world.'])
        self.assertFalse(seen[0][1])
        self.assertTrue(body.closed)

    async def test_cancel_interrupts_stalled_body(self):
        body = Body([], stall=True)
        translator = await self.make(body)
        cancel = Event()
        asyncio.get_running_loop().call_later(.06, cancel.set)
        started = time.monotonic()
        with self.assertRaises(StreamCancelled):
            await translator.translate('source', cancel.is_set)
        self.assertLess(time.monotonic()-started, .5)
        self.assertTrue(body.closed)

    async def test_first_text_deadline_includes_wait_for_headers(self):
        async def handler(request):
            await asyncio.sleep(60)
        translator = await self.make(None, handler, first_timeout=.06)
        with self.assertRaisesRegex(StreamError, 'No English'):
            await translator.translate('source', lambda: False)

    async def test_total_deadline_after_partial_keeps_draft(self):
        seen = []
        translator = await self.make(Body([event('Hello')], stall=True), total_timeout=.09)
        with self.assertRaisesRegex(StreamError, 'timed out'):
            await translator.translate('source', lambda: False, seen.append)
        self.assertEqual(seen, ['Hello'])

    async def test_cutoff_retains_partial_but_never_succeeds(self):
        seen = []
        translator = await self.make(Body([event('Partial', 'MAX_TOKENS')]))
        with self.assertRaisesRegex(StreamError, 'incomplete'):
            await translator.translate('source', lambda: False, seen.append)
        self.assertEqual(seen, ['Partial'])

    async def test_missing_final_is_not_success(self):
        translator = await self.make(Body([event('Draft')]))
        with self.assertRaises(StreamError):
            await translator.translate('source', lambda: False)

    async def test_split_utf8_and_events(self):
        raw = event('Café déjà vu.', 'STOP')
        translator = await self.make(Body([raw[i:i+1] for i in range(len(raw))]))
        self.assertEqual(await translator.translate('source', lambda: False), 'Café déjà vu.')

    async def test_unterminated_oversized_event_is_bounded(self):
        translator = await self.make(Body([b'data: '+b'x'*262144]))
        with self.assertRaisesRegex(StreamError, 'oversized'):
            await translator.translate('source', lambda: False)

    async def test_retry_is_bounded_and_does_not_expose_error_body(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(429, text='test-secret private source', headers={'retry-after': '0'})
        translator = await self.make(None, handler)
        with self.assertRaises(StreamError) as failure:
            await translator.translate('source', lambda: False)
        self.assertEqual(len(requests), 2)
        self.assertNotIn('test-secret', str(failure.exception))
        self.assertNotIn('private source', str(failure.exception))
        requests.clear()
        def long_wait(request):
            requests.append(request)
            return httpx.Response(429, headers={'retry-after': '60'})
        translator = await self.make(None, long_wait)
        started = time.monotonic()
        with self.assertRaises(StreamError):
            await translator.translate('source', lambda: False)
        self.assertEqual(len(requests), 1)
        self.assertLess(time.monotonic()-started, .5)

    async def test_precancel_never_opens_network(self):
        translator = await self.make(None, lambda r: self.fail('network escaped'))
        with self.assertRaises(StreamCancelled):
            await translator.translate('source', lambda: True)

    def test_model_specific_thinking(self):
        self.assertEqual(thinking_config('gemini-2.5-flash'), {'thinkingBudget': 0})
        self.assertEqual(thinking_config('gemini-2.5-pro'), {'thinkingBudget': 128})
        self.assertEqual(thinking_config('gemini-3.5-flash'), {'thinkingLevel': 'minimal'})
        self.assertEqual(thinking_config('gemini-3.8-flash'), {'thinkingLevel': 'low'})
        self.assertEqual(thinking_config('gemini-3.1-pro'), {'thinkingLevel': 'low'})
        self.assertIsNone(thinking_config('gemini-example-flash'))

class BufferAndPhraseTests(unittest.TestCase):
    def test_pcm_tail_is_preserved(self):
        session = RecordingStream(1)
        session.publish(np.linspace(-.5, .5, 1650, dtype=np.float32), time.monotonic())
        session.finish(1.)
        self.assertEqual([len(session.frames.get_nowait()), len(session.frames.get_nowait())], [3200, 100])
        self.assertTrue(session.ended.is_set())

    def test_backpressure_is_explicit_not_silent_audio_drop(self):
        session = RecordingStream(1, frames=Queue(1))
        with self.assertRaises(RuntimeError):
            session.publish(np.zeros(3200, dtype=np.float32), 1.)
        session.finish()
        self.assertTrue(session.error)
        self.assertTrue(session.ended.is_set())

    def test_phrase_order_and_debounce(self):
        book = PhraseBook()
        book.update('Please send the report', False, 10.)
        self.assertIsNone(book.next(10.1))
        book.update('Please send the report tomorrow', False, 10.2)
        self.assertEqual(book.next(10.25)[2], 'Please send the report')
        book.update('Please send the report tomorrow.', True, 10.3)
        first = book.phrases[0]
        book.update('Do not send it today.', True, 10.4)
        self.assertEqual(book.next(10.5)[0], 0)
        first.translated = first.revision
        first.english = first.text
        self.assertEqual(book.next(10.5)[0], 1)
        self.assertEqual(book.source(), 'Please send the report tomorrow. Do not send it today.')

class FakeWS:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.queue.put_nowait(json.dumps({'setupComplete': {}}))
        self.sent = []
    async def send(self, data):
        self.sent.append(json.loads(data))
    async def recv(self):
        return await self.queue.get()
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def source(self, text, final=False):
        self.queue.put_nowait(json.dumps({'serverContent': {'inputTranscription' if final else 'interimInputTranscription': {'text': text}}}))

class FakeTranslator:
    def __init__(self, config):
        self.requests = []
    async def translate(self, text, cancelled, on_partial, **kwargs):
        self.requests.append(text)
        await asyncio.sleep(.015)
        if cancelled():
            raise StreamCancelled()
        on_partial('English: '+text)
        return 'English: '+text
    async def close(self):
        pass

class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def setup_worker(self):
        state, bus, ws = PipelineState(), DictationStreamBus(), FakeWS()
        state.update(mode=Mode.TRANSLATION, ready=True)
        epoch = state.begin_recording().epoch
        session = bus.begin(epoch)
        worker = StreamingTranslationWorker(config(), state, bus, connector=lambda *a, **k: ws,
                                             translator_factory=FakeTranslator)
        return state, bus, ws, session, worker

    async def test_english_arrives_while_recording_and_final_waits_for_stop(self):
        state, bus, ws, session, worker = await self.setup_worker()
        outputs = []
        worker.partial.connect(outputs.append)
        translator = FakeTranslator(config())
        task = asyncio.create_task(worker._session(session, translator))
        try:
            await asyncio.sleep(.02)
            self.assertTrue(session.ready.is_set())
            ws.source('Please send the report')
            await asyncio.sleep(.4)
            self.assertTrue(outputs)
            self.assertTrue(state.snapshot().recording)
            self.assertFalse(any(o.final for o in outputs))
            session.finish(time.monotonic())
            state.finish_recording(session.epoch)
            ws.queue.put_nowait(json.dumps({'serverContent': {'turnComplete': True}}))
            await asyncio.sleep(.1)
            self.assertFalse(task.done())
            ws.source('Please send the report.', True)
            await asyncio.wait_for(task, 2)
            self.assertTrue(outputs[-1].final)
            self.assertIn('audioStreamEnd', ws.sent[-1]['realtimeInput'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_epoch_cancellation_drops_old_completion(self):
        state, bus, ws, session, worker = await self.setup_worker()
        completed, outputs = [], []
        worker.completed.connect(lambda e,m: completed.append(e))
        worker.partial.connect(outputs.append)
        task = asyncio.create_task(worker._run_async())
        try:
            await asyncio.sleep(.04)
            state.invalidate()
            ws.source('Late old phrase.', True)
            await asyncio.sleep(.1)
            self.assertEqual(completed, [])
            self.assertEqual(outputs, [])
        finally:
            worker.request_stop()
            await asyncio.wait_for(task, 1)

    async def test_setup_failure_releases_capture_without_record_permission(self):
        state, bus, ws, session, worker = await self.setup_worker()
        ws.queue.get_nowait()
        ws.queue.put_nowait(json.dumps({'error': {'code': 403}}))
        failures = []
        worker.error.connect(lambda e,m: failures.append(m))
        task = asyncio.create_task(worker._run_async())
        try:
            await asyncio.sleep(.1)
            self.assertTrue(session.ready.is_set())
            self.assertTrue(session.error)
            self.assertFalse(state.snapshot().recording)
            self.assertTrue(failures)
        finally:
            worker.request_stop()
            await asyncio.wait_for(task, 1)

class UITests(unittest.TestCase):
    def setUp(self):
        self.window = Overlay(FakeGuard())
        self.addCleanup(self.window.prepare_shutdown)
        self.window.show()
        APP.processEvents()

    def test_same_font_geometry_for_interim_and_final(self):
        w = self.window
        w.set_partial(LivePartial(1, Source.MIC, 'A stable caption.'))
        before = w.content.geometry(), w.size(), w.content.font().pointSize()
        w.add_result(SpeechResult('A stable caption.', Source.MIC, Mode.TRANSCRIPT, 1, 0, 0, 0))
        APP.processEvents()
        self.assertEqual(before, (w.content.geometry(), w.size(), w.content.font().pointSize()))
        self.assertEqual(w.content.toPlainText(), 'A stable caption.')
        self.assertFalse(w.partial_caption.isVisible())

    def test_hover_does_not_resize_or_move_caption(self):
        w = self.window
        before = w.content.geometry(), w.size()
        for hovered in (True, False, True):
            w._hovered = hovered
            w._keyboard_controls = False
            w._controls()
            APP.processEvents()
            self.assertEqual(before, (w.content.geometry(), w.size()))
            self.assertEqual(w.toolbar.isVisible(), hovered)

    def test_source_drawer_is_optional_and_programmatic_source_preserves_english(self):
        w = self.window
        w.select_mode(Mode.TRANSLATION)
        self.assertFalse(w.source_panel.isVisible())
        w.set_translation_partial(TranslationUpdate('Draft English', 1))
        w.set_recognized('New recognized words')
        self.assertEqual(w.content.toPlainText(), 'Draft English')
        size = w.size()
        w.toggle_source()
        self.assertTrue(w.source_panel.isVisible())
        w.toggle_source()
        self.assertEqual(w.size(), size)

    def test_error_persists_and_draft_is_not_copyable(self):
        w = self.window
        w.select_mode(Mode.TRANSLATION)
        w.set_translation_partial(TranslationUpdate('Partial', 1))
        self.assertFalse(w.copy_button.isEnabled())
        w.set_error('Incomplete · review source and retry')
        w.set_notice('Ready')
        self.assertTrue(w.notice.isVisible())
        self.assertIn('Incomplete', w.notice.text())
        w.set_translation_partial(TranslationUpdate('Complete.', 1, True))
        self.assertTrue(w.copy_button.isEnabled())

    def test_settings_roundtrip_fields_and_bounds(self):
        from settings_service import save_settings
        from ui_settings import SettingsPanel
        panel = SettingsPanel(AppConfig())
        saved = []
        panel.saved.connect(saved.append)
        panel.font_size.setValue(18)
        panel.caption_lines.setValue(3)
        panel.save()
        self.assertEqual(saved[0]['preferences']['caption_font_pt'], '18')
        with patch('settings_service.write_preferences'), patch('settings_service.settings_path', return_value=Path('/nonexistent/settings.json')):
            updated = save_settings(AppConfig(), saved[0])
        self.assertEqual(updated.caption_lines, 3)
        with self.assertRaises(ValueError):
            AppConfig(caption_font_pt=5)

class MetricsTests(unittest.TestCase):
    def test_summary_quantiles_failure_counts_and_duplicate_files(self):
        from summarize_latency import summarize
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'latency.jsonl'
            rows = [{'event': 'translation_first_text', 'run_id': 'run', 'request_id': str(i),
                     'monotonic': i, 'elapsed_ms': ms, 'final': True} for i,ms in enumerate([100,200,300,400])]
            rows.append({'event': 'translation_failed', 'monotonic': 5})
            path.write_text('\n'.join(json.dumps(row) for row in rows)+'\nbad-json\n')
            report = summarize([path, path])
            timing = report['timings_ms']['translation_first_text.elapsed_ms.final']
            self.assertEqual(timing['count'], 4)
            self.assertEqual(timing['p50'], 250)
            self.assertEqual(timing['p95'], 385)
            self.assertEqual(report['event_counts']['translation_failed'], 1)

class ThreadIntegrationTests(unittest.TestCase):
    def test_capture_waits_for_connection_and_streams_tail_without_batch_job(self):
        from types import SimpleNamespace
        from PyQt6.QtTest import QTest
        from dictation_capture import DictationCaptureWorker
        from test_capture import FakePortAudio
        from test_dsp import tone
        FakePortAudio.streams, FakePortAudio.instances = [], []
        module = SimpleNamespace(PyAudio=FakePortAudio, paWASAPI=13, paFloat32=1, paContinue=0)
        state, jobs, bus = PipelineState(), Queue(4), DictationStreamBus()
        state.update(mode=Mode.TRANSLATION, ready=True)
        session = bus.begin(state.begin_recording().epoch)
        worker = DictationCaptureWorker(config(), state, jobs, bus)
        with patch.dict('sys.modules', {'pyaudiowpatch': module}):
            worker.start()
            try:
                worker.request_start()
                QTest.qWait(80)
                self.assertFalse(FakePortAudio.instances)
                self.assertFalse(session.capturing.is_set())
                session.ready.set()
                wait_for(lambda: bool(FakePortAudio.streams))
                stream = FakePortAudio.streams[0]
                samples = tone(.413, 44100)
                for offset in range(0, len(samples), 882):
                    frame = samples[offset:offset+882]
                    stream.settings['stream_callback'](frame.tobytes(), len(frame), {}, 0)
                QTest.qWait(80)
                self.assertFalse(session.frames.empty())
                self.assertTrue(session.capturing.is_set())
                state.finish_recording(session.epoch)
                wait_for(session.ended.is_set)
                self.assertFalse(session.error)
                self.assertFalse(session.capturing.is_set())
                self.assertTrue(jobs.empty())
                total = 0
                while not session.frames.empty():
                    total += len(session.frames.get_nowait())//2
                self.assertLessEqual(abs(total-len(samples)/44100*16000), 1)
                self.assertTrue(stream.closed)
            finally:
                worker.request_stop()
                self.assertTrue(worker.wait(3000))

    def test_worker_reuses_loop_and_accepts_new_job_after_stalled_cancel(self):
        from types import SimpleNamespace
        from speech_engine import SpeechWorker
        from text_translation import TextTranslator
        state, jobs = PipelineState(), Queue(4)
        state.update(mode=Mode.TRANSLATION)
        body = Body([], stall=True)
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, stream=body)
            return httpx.Response(200, stream=Body([event('New English.', 'STOP')]))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        translator = TextTranslator(config(), Event(), state, stream_client=client)
        backend = SimpleNamespace(load=lambda: 'Mock engine', close=lambda: None)
        worker = SpeechWorker(config(), state, jobs, backend, translator)
        results, done = [], []
        worker.result.connect(results.append)
        worker.dictation_done.connect(lambda *args: done.append(args))
        worker.start()
        try:
            wait_for(lambda: state.snapshot().ready)
            jobs.put(TextRequest('old', state.snapshot().epoch, time.monotonic()))
            wait_for(lambda: bool(requests))
            epoch = state.invalidate().epoch
            jobs.put(TextRequest('new', epoch, time.monotonic()))
            wait_for(lambda: bool(results), 2)
            self.assertTrue(body.closed)
            self.assertEqual([(r.text, r.epoch) for r in results], [('New English.', epoch)])
            self.assertEqual(len(requests), 2)
        finally:
            worker.request_stop()
            self.assertTrue(worker.wait(3000))
            asyncio.run(client.aclose())
