"""Provider protocol, privacy, settings and recovery tests. No external requests."""
from dataclasses import replace
import json
import os
from pathlib import Path
from queue import Queue
from threading import Event
import tempfile
import unittest
from unittest.mock import patch

import httpx
from support import APP
from config import AppConfig
from credentials import CredentialError, credential_path, read_key, validate_key, write_key
from model_catalog import CatalogError, CatalogRequest, CatalogResult, LOCAL_MODELS, fetch_models
from network_io import request_json
from pipeline import Mode, PipelineState
from preferences import read_preferences, settings_path, write_preferences
from settings_service import save_settings
from speech_engine import FatalBackendError, GroqWhisperBackend
from test_speech import chunk_for
from test_native_and_ui import FakeGuard
from text_translation import TextTranslator, TranslationCancelled, TranslationError
from ui_overlay import Overlay
from ui_settings import SettingsPanel


class MockHTTPTest(unittest.TestCase):
    def client(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client


class CatalogTests(MockHTTPTest):
    def test_gemini_key_header_pagination_and_capability_filter(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.headers["x-goog-api-key"], "secret")
            self.assertNotIn("secret", str(request.url))
            if len(requests) == 1:
                return httpx.Response(200, json={"models": [
                    {"name": "models/gemini-example-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-example-tts", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]}], "nextPageToken": "page-2"})
            self.assertEqual(request.url.params["pageToken"], "page-2")
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-example-pro", "supportedGenerationMethods": ["generateContent"]}]})
        models = fetch_models(CatalogRequest(1, "gemini", "text", "secret"), self.client(handler))
        self.assertEqual(models, ("gemini-example-flash", "gemini-example-pro"))
        self.assertEqual(len(requests), 2)

    def test_groq_stage_filters_speech_and_text_models(self):
        client = self.client(lambda r: httpx.Response(200, json={"data": [
            {"id": "whisper-large-v3", "active": True}, {"id": "whisper-retired", "active": False},
            {"id": "llama-3.3-70b-versatile"}, {"id": "canopylabs/orpheus-tts"}, {"id": "llama-guard"}]}))
        self.assertEqual(fetch_models(CatalogRequest(1, "groq", "live", "key"), client), ("whisper-large-v3",))
        self.assertEqual(fetch_models(CatalogRequest(2, "groq", "text", "key"), client), ("llama-3.3-70b-versatile",))

    def test_ollama_discovery_excludes_remote_aliases(self):
        client = self.client(lambda r: httpx.Response(200, json={"models": [
            {"name": "qwen3:4b-instruct"}, {"name": "secret-alias:latest", "remote_host": "https://remote.invalid"},
            {"name": "model:cloud"}, {"name": "x-cloud:latest"}]}))
        self.assertEqual(fetch_models(CatalogRequest(1, "ollama", "text"), client), ("qwen3:4b-instruct",))

    def test_no_key_local_catalog_and_bad_key_never_make_request(self):
        client = self.client(lambda r: self.fail("Unexpected network request"))
        self.assertIn("large-v2", fetch_models(CatalogRequest(1, "local", "live"), client))
        for key in ("", "secret\nheader"):
            with self.assertRaises((CatalogError, ValueError)):
                fetch_models(CatalogRequest(1, "gemini", "text", key), client)

    def test_error_bodies_and_secrets_are_not_exposed(self):
        client = self.client(lambda r: httpx.Response(403, text="sensitive-key echoed by proxy"))
        with self.assertRaises(CatalogError) as error:
            fetch_models(CatalogRequest(1, "gemini", "text", "sensitive-key"), client)
        self.assertNotIn("sensitive-key", str(error.exception))
        self.assertNotIn("sensitive-key", repr(CatalogRequest(1, "gemini", "text", "sensitive-key")))

    def test_repeated_page_token_is_rejected(self):
        client = self.client(lambda r: httpx.Response(200, json={"models": [], "nextPageToken": "again"}))
        with self.assertRaises(CatalogError):
            fetch_models(CatalogRequest(1, "gemini", "text", "key"), client)


class TranslationProviderTests(MockHTTPTest):
    def setUp(self):
        self.state = PipelineState()
        self.epoch = self.state.update(ready=True, mode=Mode.TRANSLATION).epoch

    def gemini(self, handler, **kwargs):
        config = AppConfig(text_translator="gemini", gemini_model="gemini-example-flash",
                           gemini_api_key="private-key", cloud_enabled=True)
        return TextTranslator(replace(config, **kwargs), Event(), self.state, self.client(handler))

    def test_gemini_sends_only_exact_source_text_and_ignores_thought_parts(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.url.host, "generativelanguage.googleapis.com")
            self.assertNotIn("private-key", str(request.url))
            self.assertEqual(request.headers["x-goog-api-key"], "private-key")
            body = json.loads(request.read())
            self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "कल तीन बजे, पंद्रह नहीं।"}]}])
            self.assertNotIn("inlineData", request.read().decode())
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": "Internal reasoning", "thought": True}, {"text": "Tomorrow at three, not fifteen."}]}}]})
        self.assertEqual(self.gemini(handler).translate("कल तीन बजे, पंद्रह नहीं।", self.epoch), "Tomorrow at three, not fifteen.")
        self.assertEqual(len(requests), 1)

    def test_cloud_disabled_blocks_generation_even_with_a_key(self):
        with self.assertRaisesRegex(TranslationError, "disabled"):
            self.gemini(lambda r: self.fail("Cloud request escaped opt-in"), cloud_enabled=False).translate("hi", self.epoch)

    def test_cancelled_translation_never_contacts_provider(self):
        translator = self.gemini(lambda r: self.fail("Cancelled request escaped"))
        self.state.invalidate()
        with self.assertRaises(TranslationCancelled):
            translator.translate("cancelled", self.epoch)

    def test_gemini_blocked_truncated_and_malformed_results_are_not_displayed(self):
        for reason in ("SAFETY", "MAX_TOKENS", "RECITATION"):
            with self.subTest(reason=reason), self.assertRaises(TranslationError):
                self.gemini(lambda r: httpx.Response(200, json={"candidates": [{"finishReason": reason,
                    "content": {"parts": [{"text": "Incomplete"}]}}]})).translate("test", self.epoch)
        with self.assertRaises(TranslationError):
            self.gemini(lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})).translate("test", self.epoch)

    def test_remote_ollama_alias_is_rejected_before_text_leaves_process(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.url.path, "/api/show")
            self.assertNotIn("sensitive source", request.read().decode())
            return httpx.Response(200, json={"remote_host": "https://remote.invalid", "capabilities": ["completion"]})
        translator = TextTranslator(AppConfig(ollama_model="my-alias:latest"), Event(), self.state, self.client(handler))
        with self.assertRaisesRegex(TranslationError, "remote host"):
            translator.translate("sensitive source", self.epoch)
        self.assertEqual(len(requests), 1)

    def test_embedding_model_cannot_be_selected_as_local_translator(self):
        translator = TextTranslator(AppConfig(), Event(), self.state,
            self.client(lambda r: httpx.Response(200, json={"capabilities": ["embedding"]})))
        with self.assertRaisesRegex(TranslationError, "generate text"):
            translator.translate("test", self.epoch)

    def test_oversized_response_is_rejected(self):
        with self.assertRaises(TranslationError):
            self.gemini(lambda r: httpx.Response(200, content=b"x" * 300000)).translate("test", self.epoch)

    def test_groq_model_is_selected_independently_for_each_speech_stage(self):
        config = AppConfig(backend="groq", model="whisper-large-v3-turbo", dictation_backend="groq",
                           dictation_model="whisper-large-v3", groq_api_key="key", cloud_enabled=True)
        requests = []
        client = self.client(lambda r: requests.append(r) or httpx.Response(200, json={"text": "words"}))
        backend = GroqWhisperBackend(config, Event(), self.state, client)
        backend.recognize(chunk_for(self.state, Mode.TRANSCRIPT))
        backend.recognize(chunk_for(self.state, Mode.TRANSLATION))
        self.assertIn(b"whisper-large-v3-turbo", requests[0].read())
        self.assertNotIn(b"whisper-large-v3-turbo", requests[1].read())
        self.assertIn(b"whisper-large-v3", requests[1].read())
        backend.config = replace(config, cloud_enabled=False)
        with self.assertRaises(FatalBackendError):
            backend.recognize(chunk_for(self.state, Mode.TRANSCRIPT))
        self.assertEqual(len(requests), 2)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, {"APPDATA": folder.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_api_keys_never_enter_plain_settings_and_never_enable_cloud_implicitly(self):
        with self.assertRaises(ValueError):
            write_preferences({"gemini_api_key": "secret"})
        with patch("credentials.read_key", return_value="stored-key"):
            config = AppConfig.from_env()
        self.assertEqual((config.backend, config.text_translator, config.cloud_enabled), ("local", "ollama", False))
        self.assertNotIn("stored-key", repr(config))

    def test_failed_dpapi_read_keeps_local_ui_config_available(self):
        with patch("credentials.read_key", side_effect=CredentialError("wrong Windows account")):
            config = AppConfig.from_env()
        self.assertEqual(config.gemini_api_key, "")
        self.assertIn("Re-enter", config.credential_notice)

    def test_broken_preferences_recover_to_local_defaults_with_notice(self):
        path = settings_path()
        path.parent.mkdir(parents=True)
        path.write_text("broken json")
        config = AppConfig.from_env()
        self.assertEqual(config.backend, "local")
        self.assertIn("could not be read", config.credential_notice)

    def test_encrypted_storage_atomic_roundtrip_and_delete(self):
        with patch("credentials._crypt", side_effect=[b"encrypted-blob", b"secret"]):
            write_key("gemini", "secret")
            self.assertEqual(credential_path("gemini").read_bytes(), b"encrypted-blob")
            self.assertEqual(read_key("gemini"), "secret")
        write_key("gemini", "")
        self.assertFalse(credential_path("gemini").exists())

    def test_invalid_model_does_not_save_any_keys(self):
        with patch("settings_service.write_key") as write:
            with self.assertRaises(ValueError):
                save_settings(AppConfig(), {"preferences": {"dictation_model": "small.en"}, "keys": {"gemini": "secret"}})
            write.assert_not_called()

    def test_credential_save_rolls_back_when_preferences_write_fails(self):
        path = credential_path("gemini")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"old encrypted key")
        settings_path().write_text('{"model":"small"}')
        def key_write(provider, value):
            credential_path(provider).write_bytes(b"new encrypted key")
        with patch("settings_service.write_key", side_effect=key_write), \
                patch("settings_service.write_preferences", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                save_settings(AppConfig(), {"preferences": {"model": "large-v2"}, "keys": {"gemini": "new-key"}})
        self.assertEqual(path.read_bytes(), b"old encrypted key")
        self.assertEqual(read_preferences()["model"], "small")

    def test_environment_override_does_not_pretend_ui_save_will_apply(self):
        with patch.dict(os.environ, {"HUD_MODEL": "small"}), self.assertRaisesRegex(ValueError, "HUD_MODEL overrides"):
            save_settings(AppConfig(), {"preferences": {"model": "large-v2"}, "keys": {}})

    def test_independent_stage_selection_and_stale_discovery_results(self):
        panel = SettingsPanel(AppConfig())
        self.addCleanup(panel.close)
        panel._model_edited("large-v2")
        panel.select_stage("text")
        panel.select_provider("gemini")
        panel._key_edited("key")
        requests = []
        panel.discover.connect(requests.append)
        panel.load_models()
        token = requests[-1].token
        panel.select_provider("ollama")
        panel.apply_catalog(CatalogResult(token, "gemini", "text", ("gemini-stale",)))
        self.assertEqual(panel.model_list.count(), 0)
        self.assertEqual(panel.models[("live", "local")], "large-v2")
        panel.select_provider("gemini")
        panel.load_models()
        panel.apply_catalog(CatalogResult(requests[-1].token, "gemini", "text", ("gemini-selected",)))
        panel._choose_model(panel.model_list.item(0))
        panel.cloud.setChecked(True)
        saved = []
        panel.saved.connect(saved.append)
        panel.save()
        self.assertEqual(saved[0]["preferences"]["model"], "large-v2")
        self.assertEqual(saved[0]["preferences"]["gemini_model"], "gemini-selected")
        self.assertNotIn("key", json.dumps(saved[0]["preferences"]))

    def test_settings_are_children_and_translation_size_is_restored(self):
        window = Overlay(FakeGuard())
        self.addCleanup(window.prepare_shutdown)
        window.show()
        window.select_mode(Mode.TRANSLATION)
        before = window.size()
        window.toggle_settings()
        APP.processEvents()
        self.assertFalse(window.settings_editor.isWindow())
        self.assertFalse(window.settings_editor.model_list.isWindow())
        window.toggle_settings()
        self.assertEqual(window.size(), before)

    def test_direct_mode_switch_from_settings_does_not_keep_settings_height(self):
        window = Overlay(FakeGuard())
        self.addCleanup(window.prepare_shutdown)
        window.show()
        window.toggle_settings()
        self.assertGreaterEqual(window.height(), 500)
        window.select_mode(Mode.TRANSLATION)
        APP.processEvents()
        self.assertEqual(window.height(), 260)
        window.toggle_settings()
        window.select_mode(Mode.TRANSLATION)
        self.assertFalse(window._settings_open)
        self.assertEqual(window.height(), 260)


if __name__ == "__main__":
    unittest.main()
