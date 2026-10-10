"""Alibaba OpenAI-compatible adapter with bounded requests and usage records."""
import os
import requests
from urllib.parse import urlsplit
from lodestar.providers.reasoning import dashscope_options

class DashScopeClient:
    def __init__(self, config):
        self.config = config
        self.key = os.getenv('DASHSCOPE_API_KEY')
        self.base_url = config.llm_base_url.rstrip('/')
        parts = urlsplit(self.base_url)
        if not self.key:
            raise ValueError('Missing DASHSCOPE_API_KEY')
        if parts.scheme != 'https' or not parts.hostname or not (parts.hostname == 'dashscope.aliyuncs.com' or parts.hostname.endswith('.maas.aliyuncs.com')) or parts.username or parts.password or parts.query:
            raise ValueError('DashScope requires an official HTTPS endpoint')
        self.usage = []

    def complete(self, role, model, system, user, max_tokens, *, json_mode=False):
        payload = {'model': model, 'messages': [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': user}], 'max_tokens': max_tokens,
                    'temperature': self.config.temperature,
                    **dashscope_options(self.config, structured=json_mode)}
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        response = requests.post(self.base_url + '/chat/completions',
            headers={'Authorization': 'Bearer ' + self.key}, json=payload,
            timeout=self.config.llm_timeout_s)
        if not response.ok:
            raise ValueError(f'DashScope HTTP {response.status_code}')
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
