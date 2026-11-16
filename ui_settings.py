"""Inline model settings: no dialogs, popup menus or unprotected dropdown windows."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from model_catalog import CatalogRequest, LOCAL_MODELS


class SettingsPanel(QWidget):
    saved = pyqtSignal(object)
    discover = pyqtSignal(object)
    microphone_request = pyqtSignal(object)
    microphone_cancel = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.stage = "live"
        self.providers = {"live": config.backend, "dictation": config.dictation_backend,
                          "text": config.text_translator}
        self.models = {
            ("live", "local"): config.model if config.backend == "local" else "small",
            ("live", "groq"): config.model if config.backend == "groq" else "whisper-large-v3",
            ("live", "gemini"): config.model if config.backend == "gemini" else "gemini-3.5-transcribe-live",
            ("dictation", "local"): config.dictation_model if config.dictation_backend == "local" else "large-v3",
            ("dictation", "groq"): config.dictation_model if config.dictation_backend == "groq" else "whisper-large-v3",
            ("dictation", "gemini"): config.dictation_model if config.dictation_backend == "gemini" else "gemini-3.5-transcribe",
            ("text", "ollama"): config.ollama_model, ("text", "gemini"): config.gemini_model,
            ("text", "groq"): config.text_model,
        }
        self.languages = {"live": config.transcript_language or "auto", "dictation": config.translation_language or "auto"}
        self.keys = {"gemini": config.gemini_api_key, "groq": config.groq_api_key}
        self.key_updates = {}
        self.token = 0
        self.mic_token = 0
        self.microphone = config.microphone
        self._rendering = False
        self.device = config.device
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title = QLabel("Choose a model for each step. Local processing has no API charges.")
        title.setWordWrap(True)
        layout.addWidget(title)
        stages = QHBoxLayout()
        group = QButtonGroup(self)
        self.stage_buttons = {}
        for label, stage in (("Live Transcript", "live"), ("Recognize my speech", "dictation"), ("English translation", "text")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(stage == self.stage)
            group.addButton(button)
            self.stage_buttons[stage] = button
            button.clicked.connect(lambda checked, s=stage: self.select_stage(s))
            stages.addWidget(button)
        layout.addLayout(stages)
        self.provider_row = QHBoxLayout()
        self.provider_buttons = {}
        self.provider_group = QButtonGroup(self)
        for provider, label in (("local", "Local Whisper"), ("ollama", "Local Ollama"), ("gemini", "Gemini API"), ("groq", "Groq API")):
            button = QPushButton(label)
            button.setCheckable(True)
            self.provider_group.addButton(button)
            button.clicked.connect(lambda checked, p=provider: self.select_provider(p))
            self.provider_buttons[provider] = button
            self.provider_row.addWidget(button)
        layout.addLayout(self.provider_row)
        self.key_panel = QWidget()
        key_row = QHBoxLayout(self.key_panel)
        key_row.setContentsMargins(0, 0, 0, 0)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setMaxLength(4096)
        self.key_edit.textEdited.connect(self._key_edited)
        self.forget = QPushButton("Forget saved key")
        self.forget.clicked.connect(self._forget_key)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.forget)
        layout.addWidget(self.key_panel)
        model_row = QHBoxLayout()
        self.model_edit = QLineEdit()
        self.model_edit.setMaxLength(240)
        self.model_edit.setPlaceholderText("Choose a returned model or enter its exact ID")
        self.model_edit.textEdited.connect(self._model_edited)
        self.refresh = QPushButton("Load models")
        self.refresh.clicked.connect(self.load_models)
        model_row.addWidget(self.model_edit, 1)
        model_row.addWidget(self.refresh)
        layout.addLayout(model_row)
        self.model_list = QListWidget()
        self.model_list.setFixedHeight(84)
        self.model_list.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.model_list.itemClicked.connect(self._choose_model)
        self.model_list.itemActivated.connect(self._choose_model)
        layout.addWidget(self.model_list)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(32)
        layout.addWidget(self.message)
        self.speech_options = QWidget()
        speech_row = QHBoxLayout(self.speech_options)
        speech_row.setContentsMargins(0, 0, 0, 0)
        speech_row.addWidget(QLabel("Language"))
        self.language_edit = QLineEdit()
        self.language_edit.setMaxLength(4)
        self.language_edit.setMaximumWidth(70)
        self.language_edit.setPlaceholderText("hi / auto")
        self.language_edit.textEdited.connect(lambda value: self.languages.__setitem__(self.stage, value))
        speech_row.addWidget(self.language_edit)
        speech_row.addStretch()
        self.device_group = QButtonGroup(self)
        for device, label in (("auto", "Auto"), ("cuda", "GPU"), ("cpu", "CPU")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(device == config.device)
            self.device_group.addButton(button)
            button.clicked.connect(lambda checked, d=device: setattr(self, "device", d))
            speech_row.addWidget(button)
        layout.addWidget(self.speech_options)
        self.mic_panel = QWidget()
        mic_layout = QVBoxLayout(self.mic_panel)
        mic_layout.setContentsMargins(0, 0, 0, 0)
        mic_row = QHBoxLayout()
        self.mic_label = QLabel(config.microphone.removeprefix("wasapi:") or "Windows default microphone")
        self.mic_label.setTextFormat(Qt.TextFormat.PlainText)
        self.mic_label.setWordWrap(True)
        mic_row.addWidget(self.mic_label, 1)
        refresh_mic = QPushButton("Select mic")
        refresh_mic.clicked.connect(lambda: self.request_microphone(False))
        self.test_mic = QPushButton("Test 5 sec")
        self.test_mic.clicked.connect(lambda: self.request_microphone(True))
        mic_row.addWidget(refresh_mic)
        mic_row.addWidget(self.test_mic)
        mic_layout.addLayout(mic_row)
        self.mic_list = QListWidget()
        self.mic_list.setFixedHeight(72)
        self.mic_list.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.mic_list.itemClicked.connect(self.choose_microphone)
        self.mic_list.itemActivated.connect(self.choose_microphone)
        self.mic_list.hide()
        mic_layout.addWidget(self.mic_list)
        self.mic_meter = QProgressBar()
        self.mic_meter.setRange(0, 100)
        self.mic_meter.setValue(0)
        self.mic_meter.setTextVisible(False)
        self.mic_meter.setFixedHeight(5)
        mic_layout.addWidget(self.mic_meter)
        self.noise = QCheckBox("Reduce microphone noise · optional RNNoise")
        self.noise.setChecked(config.noise_suppression)
        self.noise.toggled.connect(self.cancel_microphone)
        mic_layout.addWidget(self.noise)
        self.glossary = QLineEdit(config.initial_prompt)
        self.glossary.setMaxLength(200)
        self.glossary.setPlaceholderText("Names / technical terms, comma separated (optional)")
        self.glossary.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        mic_layout.addWidget(self.glossary)
        layout.addWidget(self.mic_panel)
        self.cloud = QCheckBox("Allow selected cloud services (provider charges/quotas may apply)")
        self.cloud.setChecked(config.cloud_enabled)
        layout.addWidget(self.cloud)
        privacy = QLabel("Cloud speech sends audio; cloud translation sends recognized text. Local has no API charges.\nKeys are encrypted for your Windows account; model access and free quota depend on the provider.")
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: #A4A7B3; font-size: 10px;")
        layout.addWidget(privacy)
        save = QPushButton("Save and restart")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        self.setStyleSheet("""
            QLineEdit, QListWidget {background: #242730; border: 1px solid #3A3E4B;
                border-radius: 5px; color: #F2F2F7; padding: 4px;}
            QListWidget::item:selected {background: #294554; color: #64D2FF;}
            QCheckBox {spacing: 6px;}
            QProgressBar {background: #303440; border: none; border-radius: 2px;}
            QProgressBar::chunk {background: #64D2FF; border-radius: 2px;}
        """)
        for editor in (self.key_edit, self.model_edit, self.language_edit):
            editor.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._render()

    def select_stage(self, stage):
        self.cancel_microphone()
        self.stage = stage
        self.stage_buttons[stage].setChecked(True)
        self._render()

    def select_provider(self, provider):
        self.providers[self.stage] = provider
        self._render()

    def _render(self):
        self.token += 1  # Pending network replies cannot populate another stage/provider.
        self._rendering = True
        provider = self.providers[self.stage]
        allowed = ("ollama", "gemini", "groq") if self.stage == "text" else ("local", "groq", "gemini")
        for name, button in self.provider_buttons.items():
            button.setVisible(name in allowed)
            button.setChecked(name == provider)
        self.model_edit.setText(self.models[(self.stage, provider)])
        self.key_panel.setVisible(provider in self.keys)
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("Key available · paste to replace" if self.keys.get(provider) else "Paste API key to load this provider's models")
        self.speech_options.setVisible(self.stage != "text")
        self.mic_panel.setVisible(self.stage != "text")
        for button in self.device_group.buttons():
            button.setVisible(provider == "local")
        self.language_edit.setText(self.languages.get(self.stage, "auto"))
        self.model_list.clear()
        self.refresh.setEnabled(True)
        if provider == "local":
            self.model_list.addItems(LOCAL_MODELS)
            self.message.setText("small: faster live captions · large-v2 / large-v3: more compute, stronger recognition. Models download on first use.")
        elif provider == "ollama":
            self.message.setText("Load models lists installed local Ollama models. Example: ollama pull qwen3:4b-instruct")
        else:
            self.message.setText("Load models checks your key. Returned IDs still depend on access, region and quota; generation may be billed.")
        self._rendering = False

    def _model_edited(self, value):
        self.models[(self.stage, self.providers[self.stage])] = value.strip()

    def _choose_model(self, item):
        self.model_edit.setText(item.text())
        self._model_edited(item.text())

    def _key_edited(self, value):
        provider = self.providers[self.stage]
        self.keys[provider] = value.strip()
        self.key_updates[provider] = value.strip()
        self.token += 1
        self.model_list.clear()
        self.refresh.setEnabled(True)

    def _forget_key(self):
        self._key_edited("")
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("Key will be removed when you Save & restart")
        self.message.setText("Saved key removal is pending. Environment keys must also be removed from Windows environment variables.")

    def load_models(self):
        provider = self.providers[self.stage]
        if provider == "local":
            self._render()
            return
        self.token += 1
        self.model_list.clear()
        self.refresh.setEnabled(False)
        self.message.setText("Loading model list… no audio or text is sent.")
        self.discover.emit(CatalogRequest(self.token, provider, self.stage, self.keys.get(provider, "")))

    def apply_catalog(self, result):
        if result.token != self.token or result.stage != self.stage or result.provider != self.providers[self.stage]:
            return
        self.refresh.setEnabled(True)
        self.model_list.clear()
        self.model_list.addItems(result.models)
        self.message.setText(result.error or (f"{len(result.models)} models returned. Click a text model to select it." if result.models and self.stage == "text" else
                                             f"{len(result.models)} speech models returned. Click to select." if result.models else
                                             "No compatible models returned. Install a local Ollama text model or check provider access."))

    def save(self):
        self.cancel_microphone()
        from credentials import validate_key
        from config import language
        try:
            for key in self.keys.values():
                validate_key(key)
            for value in self.languages.values():
                language(value)
            if any(p in ("groq", "gemini") for p in self.providers.values()):
                if not self.cloud.isChecked():
                    raise ValueError("Enable cloud services for your selected API providers, or choose Local Whisper/Ollama.")
                for provider in set(self.providers.values()) & {"groq", "gemini"}:
                    if not self.keys.get(provider):
                        raise ValueError(f"Enter a {provider} key, or select a local provider.")
                if self.providers["text"] == "gemini" and not self.models[("text", "gemini")]:
                    raise ValueError("Load and select a Gemini model first.")
            values = {"backend": self.providers["live"], "model": self.models[("live", self.providers["live"])],
                      "dictation_backend": self.providers["dictation"], "dictation_model": self.models[("dictation", self.providers["dictation"])],
                      "text_translator": self.providers["text"], "ollama_model": self.models[("text", "ollama")],
                      "text_model": self.models[("text", "groq")], "gemini_model": self.models[("text", "gemini")],
                      "device": self.device, "cloud_enabled": "1" if self.cloud.isChecked() else "0",
                      "transcript_language": self.languages["live"], "translation_language": self.languages["dictation"],
                      "microphone": self.microphone, "noise_suppression": "1" if self.noise.isChecked() else "0",
                      "initial_prompt": self.glossary.text().strip()}
            self.saved.emit({"preferences": values, "keys": dict(self.key_updates)})
        except ValueError as exc:
            self.message.setText(str(exc))

    def cancel_microphone(self, *_):
        self.mic_token += 1
        self.microphone_cancel.emit()
        self.test_mic.setEnabled(True)
        self.mic_meter.setValue(0)

    def request_microphone(self, test):
        from microphones import MicRequest
        self.cancel_microphone()
        self.test_mic.setEnabled(not test)
        self.message.setText("Speak normally for five seconds. Audio stays on this computer." if test else "Loading WASAPI microphones…")
        self.microphone_request.emit(MicRequest(self.mic_token, self.microphone, test, self.noise.isChecked()))

    def apply_microphones(self, token, rows, error):
        if token != self.mic_token:
            return
        self.mic_list.clear()
        for value, name in [("", "Follow Windows default microphone")] + list(rows):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.mic_list.addItem(item)
        self.mic_list.show()
        self.message.setText(error or "Select a microphone. Missing/duplicate devices will never silently switch to another input.")

    def choose_microphone(self, item):
        self.cancel_microphone()
        self.microphone = item.data(Qt.ItemDataRole.UserRole)
        self.mic_label.setText(item.text())
        self.mic_list.hide()

    def mic_level(self, token, level):
        if token == self.mic_token:
            self.mic_meter.setValue(round(level * 100))

    def mic_completed(self, token, message):
        if token == self.mic_token:
            self.test_mic.setEnabled(True)
            self.mic_meter.setValue(0)
            self.message.setText(message)
