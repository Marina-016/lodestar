"""Alibaba OpenAI-compatible adapter with bounded requests and usage records."""
import os
import requests
from urllib.parse import urlsplit
from lodestar.providers.reasoning import dashscope_options
from lodestar.providers.errors import raise_for_status

class DashScopeClient:
    """Own an injected requests-compatible transport; never close shared requests.

    An injected transport (including one assigned to ``http`` after construction)
    belongs to this adapter. Use a separate transport for each active client.
    """
    def __init__(self, config, *, http=None):
        self.config = config
        self.http = requests if http is None else http
        self._closed = False
        self.key = os.getenv('DASHSCOPE_API_KEY')
        self.base_url = config.llm_base_url.rstrip('/')
        parts = urlsplit(self.base_url)
        if not self.key:
            raise ValueError('Missing DASHSCOPE_API_KEY')
        if parts.scheme != 'https' or not parts.hostname or not (parts.hostname == 'dashscope.aliyuncs.com' or parts.hostname.endswith('.maas.aliyuncs.com')) or parts.username or parts.password or parts.query:
            raise ValueError('DashScope requires an official HTTPS endpoint')
        self.usage = []

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.http is not requests:
            close = getattr(self.http, 'close', None)
            if close is not None:
                close()

    def list_models(self):
        """Read the official catalog, without inference or search charges."""
        parts = urlsplit(self.base_url)
        url = parts.scheme + '://' + parts.netloc + '/api/v1/models'
        models = []
        for page in (1, 2, 3):
            response = self.http.get(url, headers={'Authorization': 'Bearer ' + self.key},
                params={'capabilities': 'TG', 'page_size': 100, 'page_no': page},
                timeout=min(10, self.config.llm_timeout_s))
            if not response.ok:
                response.raise_for_status()
            data = response.json()
            if data.get('success') is False or not isinstance(data.get('output'), dict):
                raise ValueError('Invalid model catalog response')
            output = data['output']
            batch = output.get('models', [])
            models.extend(m['model'] for m in batch if isinstance(m, dict) and isinstance(m.get('model'), str))
            if not batch or page * 100 >= output.get('total', page * 100):
                break
        if not models:
            raise ValueError('Model catalog returned no valid model IDs')
        return list(dict.fromkeys(models))

    def complete(self, role, model, system, user, max_tokens, *, json_mode=False):
        payload = {'model': model, 'messages': [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': user}], 'max_tokens': max_tokens,
                    'temperature': self.config.temperature,
                    **dashscope_options(self.config, structured=json_mode)}
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        response = self.http.post(self.base_url + '/chat/completions',
            headers={'Authorization': 'Bearer ' + self.key}, json=payload,
            timeout=self.config.llm_timeout_s)
        if not response.ok:
            raise_for_status(response)
        data = response.json()
        choice = data['choices'][0]
        self.usage.append({'role': role, 'model': data.get('model', model),
                           'finish_reason': choice.get('finish_reason'), **data.get('usage', {})})
        if choice.get('finish_reason') == 'length':
            raise ValueError('DashScope output truncated; increase the explicit output budget')
        text = choice.get('message', {}).get('content')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('DashScope returned empty text')
        return text
