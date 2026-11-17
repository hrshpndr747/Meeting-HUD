"""Opt-in, bounded timing logs. Never log speech, audio, keys or provider bodies."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from threading import Lock
import time
import uuid
from config import APP_VERSION

_RUN = uuid.uuid4().hex
_LOCK = Lock()
_LOGGER = None
_FIELDS = {'epoch', 'segment', 'revision', 'final', 'model', 'request_id', 'elapsed_ms',
           'source_to_first_ms', 'characters', 'samples', 'status', 'attempt', 'streaming',
           'duration_ms', 'error_type', 'source_revision'}

def metric(event, **values):
    global _LOGGER
    if os.getenv('HUD_LATENCY_LOG') != '1':
        return
    try:
        with _LOCK:
            if _LOGGER is None:
                folder = Path(__file__).resolve().parent / 'logs'
                folder.mkdir(exist_ok=True)
                handler = RotatingFileHandler(folder / 'latency.jsonl', maxBytes=2_000_000,
                                              backupCount=2, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(message)s'))
                _LOGGER = logging.Logger('meeting-hud-timing', logging.INFO)
                _LOGGER.propagate = False
                _LOGGER.addHandler(handler)
        record = {'event': event, 'run_id': _RUN, 'version': APP_VERSION,
                  'utc': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic()}
        record.update({k: v for k, v in values.items() if k in _FIELDS and
                       isinstance(v, (str, int, float, bool, type(None)))})
        _LOGGER.info(json.dumps(record, ensure_ascii=True))
    except (OSError, ValueError):
        pass  # Diagnostics must never interrupt capture.
