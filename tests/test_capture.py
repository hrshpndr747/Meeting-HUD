from queue import Queue
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from support import wait_for
from audio_capture import AudioCaptureWorker
from config import AppConfig
from pipeline import Mode, PipelineState, Source


class FakeStream:
    def __init__(self, settings):
        self.settings = settings
        self.active = True
        self.closed = False

    def is_active(self):
        return self.active and not self.closed

    def close(self):
        self.closed = True


class FakePortAudio:
    streams = []
    instances = []
    def __init__(self):
        self.instances.append(self)
        self.terminated = False

    def get_default_wasapi_loopback(self):
        return {"index": 1, "maxInputChannels": 2, "defaultSampleRate": 48000}

    def get_host_api_info_by_type(self, kind):
        return {"defaultInputDevice": 2}

    def get_device_info_by_index(self, index):
        return {"index": index, "maxInputChannels": 1, "defaultSampleRate": 44100}

    def open(self, **settings):
        stream = FakeStream(settings)
        self.streams.append(stream)
        return stream

    def terminate(self):
        self.terminated = True


class FakeWatcher:
    def defaults(self):
        return "render-one", "capture-one"

    def close(self):
        self.closed = True


class CaptureTests(unittest.TestCase):
    def test_callback_queue_is_bounded_and_drops_obsolete_epochs(self):
        state = PipelineState()
        snapshot = state.update(ready=True)
        worker = AudioCaptureWorker(AppConfig(), state, Queue(4))
        callback = worker._callback(Source.MIC, snapshot.epoch, 1, 0)
        data = np.zeros(320, dtype="<f4").tobytes()
        for _ in range(110):
            self.assertEqual(callback(data, 320, {}, 0), (None, 0))
        self.assertEqual(worker._raw.qsize(), 100)
        self.assertEqual(worker._callback_losses, 10)
        state.update(paused=True)
        callback(data, 320, {}, 0)
        self.assertEqual(worker._callback_losses, 10)

    def start_capture(self):
        FakePortAudio.streams = []
        FakePortAudio.instances = []
        module = SimpleNamespace(PyAudio=FakePortAudio, paWASAPI=13, paFloat32=1, paContinue=0)
        patches = [patch.dict("sys.modules", {"pyaudiowpatch": module}),
                   patch("audio_capture.EndpointWatcher", FakeWatcher)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        state = PipelineState()
        state.update(ready=True)
        worker = AudioCaptureWorker(AppConfig(), state, Queue(4))
        def cleanup():
            worker.request_stop()
            self.assertTrue(worker.wait(3000))
        self.addCleanup(cleanup)
        worker.start()
        wait_for(lambda: len(FakePortAudio.streams) >= 2)
        return state, worker

    def test_mode_and_pause_close_unneeded_streams_and_stop_cleanly(self):
        state, worker = self.start_capture()
        initial = list(FakePortAudio.streams)
        state.update(mode=Mode.TRANSLATION)
        wait_for(lambda: all(stream.closed for stream in initial))
        self.assertEqual(len(FakePortAudio.streams), 2)
        state.update(paused=True)
        wait_for(lambda: all(stream.closed for stream in FakePortAudio.streams))
        worker.request_stop()
        wait_for(lambda: not worker.isRunning())
        self.assertTrue(all(instance.terminated for instance in FakePortAudio.instances))

    def test_disconnected_stream_is_reopened_without_crashing_worker(self):
        state, worker = self.start_capture()
        first = FakePortAudio.streams[0]
        first.active = False
        wait_for(lambda: first.closed and len(FakePortAudio.streams) >= 4)
        self.assertTrue(worker.isRunning())
        self.assertGreaterEqual(len(FakePortAudio.instances), 2)


if __name__ == "__main__":
    unittest.main()
