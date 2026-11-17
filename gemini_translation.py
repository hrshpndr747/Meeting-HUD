"""Cancellable Gemini SSE translation with first-text and overall deadlines."""
from __future__ import annotations
import asyncio
import codecs
import json
import math
from email.utils import parsedate_to_datetime
import re
import time
import uuid
import httpx
from latency_metrics import metric
from network_io import provider_error

class StreamError(RuntimeError):
    pass

class StreamCancelled(RuntimeError):
    pass


def thinking_config(model):
    """Only set controls known for this model family; unknown models keep defaults."""
    if re.match(r'^gemini-2\.5-flash(?:-|$)', model):
        return {'thinkingBudget': 0}
    if re.match(r'^gemini-2\.5-pro(?:-|$)', model):
        return {'thinkingBudget': 128}
    if re.match(r'^gemini-(?:3|3\.1|3\.5|3\.6)-flash(?:-|$)', model):
        return {'thinkingLevel': 'minimal'}
    if re.match(r'^gemini-3(?:\.\d+)?-(?:flash|pro)(?:-|$)', model):
        return {'thinkingLevel': 'low'}
    return None


class GeminiTextStream:
    def __init__(self, config, client=None, first_timeout=10., total_timeout=30.):
        self.config, self.client = config, client
        self.owns_client = client is None
        self.first_timeout, self.total_timeout = first_timeout, total_timeout

    async def close(self):
        if self.owns_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    async def translate(self, text, cancelled, on_partial=None, *, epoch=0, segment=0,
                        revision=0, final=True, context='', source_at=None):
        from text_translation import SYSTEM_PROMPT
        text = text.strip()
        if not text or len(text) > 8000:
            raise StreamError('Use between 1 and 8,000 characters of recognized text.')
        if cancelled():
            raise StreamCancelled()
        if not self.config.cloud_enabled:
            raise StreamError('Cloud services are disabled. Enable them in Settings or select Local Ollama.')
        if not self.config.gemini_api_key:
            raise StreamError('Add your Gemini key in Settings.')
        model = self.config.gemini_model
        if not model or not re.fullmatch(r'[A-Za-z0-9._-]+', model):
            raise StreamError('Load and select a Gemini text model in Settings first.')
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5, write=10, pool=2),
                                            follow_redirects=False, trust_env=False)
        contents = []
        if context:
            contents = [{'role': 'user', 'parts': [{'text': 'Previous source for context only: ' + context[-800:]}]},
                        {'role': 'model', 'parts': [{'text': 'I will translate only the next message, using this context.'}]}]
        contents.append({'role': 'user', 'parts': [{'text': text}]})
        generation = {'temperature': 0, 'maxOutputTokens': 4096}
        thinking = thinking_config(model) if self.config.translation_fast else None
        if thinking:
            generation['thinkingConfig'] = thinking
        body = {'systemInstruction': {'parts': [{'text': SYSTEM_PROMPT}]},
                'contents': contents, 'generationConfig': generation}
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent'
        started = time.monotonic()
        info = dict(epoch=epoch, segment=segment, revision=revision, final=final,
                    model=model, request_id=uuid.uuid4().hex)
        metric('translation_requested', **info, characters=len(text))
        saw_text = False

        def publish(value):
            nonlocal saw_text
            if cancelled():
                raise StreamCancelled()
            if value.strip() and not saw_text:
                saw_text = True
                metric('translation_first_text', **info, elapsed_ms=(time.monotonic()-started)*1000,
                       source_to_first_ms=(time.monotonic()-source_at)*1000 if source_at else None)
            if on_partial:
                on_partial(value)

        async def request():
            for attempt in range(2):
                async with self.client.stream('POST', url, params={'alt': 'sse'},
                        headers={'x-goog-api-key': self.config.gemini_api_key}, json=body) as response:
                    status = response.status_code
                    if status == 429 or status >= 500:
                        if attempt == 0:
                            hint = response.headers.get('retry-after', '.5')
                            try:
                                delay = float(hint)
                            except ValueError:
                                try:
                                    delay = parsedate_to_datetime(hint).timestamp() - time.time()
                                except (ValueError, TypeError, OverflowError):
                                    delay = .5
                            if not math.isfinite(delay):
                                delay = .5
                            delay = max(.3, delay)
                            remaining = min(self.first_timeout, self.total_timeout) - (time.monotonic()-started)
                            # Never retry earlier than Retry-After. A long rate-limit
                            # wait cannot meet this interaction's bounded budget.
                            if delay > 3. or delay >= remaining:
                                raise StreamError(provider_error('Gemini', status))
                            metric('translation_retry', **info, status=status, attempt=1)
                        else:
                            raise StreamError(provider_error('Gemini', status))
                    elif status != 200:
                        raise StreamError(provider_error('Gemini', status))
                    else:
                        return await self._consume(response, publish)
                await asyncio.sleep(delay)
            raise StreamError('Translation retry exhausted.')

        task = asyncio.create_task(request())
        try:
            while not task.done():
                if cancelled():
                    raise StreamCancelled()
                elapsed = time.monotonic()-started
                if elapsed >= self.total_timeout:
                    raise StreamError('Translation timed out. Your source and any partial English are preserved; retry from Review source.')
                if not saw_text and elapsed >= self.first_timeout:
                    raise StreamError('No English arrived in time. Check the connection/model and retry from Review source.')
                await asyncio.wait({task}, timeout=.03)
            if cancelled():
                raise StreamCancelled()
            result = task.result()
            metric('translation_completed', **info, elapsed_ms=(time.monotonic()-started)*1000, characters=len(result))
            return result
        except StreamCancelled:
            metric('translation_cancelled', **info, elapsed_ms=(time.monotonic()-started)*1000)
            raise
        except (httpx.TransportError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            metric('translation_failed', **info, error_type=type(exc).__name__)
            raise StreamError('Translation connection or response failed. Your recognized text is preserved; retry from Review source.') from None
        except StreamError:
            metric('translation_failed', **info, error_type='StreamError')
            raise
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _consume(self, response, publish):
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        pending, result, finish = '', '', None
        data_lines = []
        total = 0

        def event():
            nonlocal result, finish
            if not data_lines:
                return
            raw = '\n'.join(data_lines)
            data_lines.clear()
            if raw == '[DONE]':
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Invalid event')
            if data.get('error') or data.get('promptFeedback', {}).get('blockReason'):
                raise StreamError('Gemini could not translate this text. Review the source and retry.')
            candidates = data.get('candidates', [])
            if not candidates:
                return  # Usage-only events are valid.
            choice = candidates[0]
            for part in choice.get('content', {}).get('parts', []):
                if isinstance(part, dict) and isinstance(part.get('text'), str) and not part.get('thought'):
                    result += part['text']
                    if len(result) > 12000:
                        raise StreamError('Translation exceeded the text limit. Translate a shorter passage.')
                    publish(result)
            if choice.get('finishReason'):
                finish = choice['finishReason']

        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > 262144:
                raise StreamError('Translator returned an oversized response. Your source is preserved.')
            pending += decoder.decode(chunk)
            while '\n' in pending:
                line, pending = pending.split('\n', 1)
                line = line.rstrip('\r')
                if not line:
                    event()
                elif line.startswith('data:'):
                    data_lines.append(line[5:].lstrip(' '))
        pending += decoder.decode(b'', final=True)
        if pending.startswith('data:'):
            data_lines.append(pending[5:].lstrip(' ').rstrip('\r'))
        event()
        if finish != 'STOP' or not result.strip():
            raise StreamError('Translation is incomplete. Any partial English is a draft; retry from Review source.')
        return result.strip()
