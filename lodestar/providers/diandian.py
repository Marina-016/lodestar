"""DianDian's documented OpenAI-compatible chat adapter.

The service uses an ``api-key`` header rather than a Bearer authorization
header.  Keeping that distinction in this small adapter prevents a gateway
credential from leaking into Anthropic or DashScope request paths.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import requests

from lodestar.providers.errors import raise_for_status
from lodestar.providers.reasoning import diandian_options


class DiandianClient:
    """Minimal, documented OpenAI-compatible transport for ``dots3-note-prev``."""

    def __init__(self, config, *, http=None):
        self.config = config
        self.http = requests if http is None else http
        self._closed = False
        self.key = os.getenv("DIANDIAN_API_KEY")
        self.base_url = config.llm_base_url.rstrip("/")
        parts = urlsplit(self.base_url)
        if not self.key:
            raise ValueError("Missing DIANDIAN_API_KEY")
        if (parts.scheme != "https" or parts.hostname != "note3-prev-api.askdiandian.com"
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path.rstrip("/") != "/v1"):
            raise ValueError("DianDian requires its documented HTTPS /v1 endpoint")
        self.usage = []

    @property
    def headers(self):
        return {"api-key": self.key}

    def openai_options(self, *, structured=False):
        return diandian_options(self.config, structured=structured)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.http is not requests:
            close = getattr(self.http, "close", None)
            if close is not None:
                close()

    def list_models(self):
        # The supplied service documentation only guarantees the configured
        # model endpoint.  Do not invent an undocumented catalog request.
        raise ValueError("DianDian does not expose a documented model catalog")

    def complete(self, role, model, system, user, max_tokens, *, json_mode=False):
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": self.config.temperature,
            **self.openai_options(structured=json_mode),
        }
        # JSON mode deliberately relies on the prompt contract instead of an
        # undocumented response_format flag.  The service supports standard
        # chat completions; unsupported optional parameters are not guessed.
        response = self.http.post(self.base_url + "/chat/completions", headers=self.headers,
                                  json=payload, timeout=self.config.llm_timeout_s)
        if not response.ok:
            raise_for_status(response)
        data = response.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("DianDian returned an invalid completion") from error
        self.usage.append({"role": role, "model": data.get("model", model),
                           "finish_reason": choice.get("finish_reason"),
                           **(data.get("usage") or {})})
        if choice.get("finish_reason") == "length":
            raise ValueError("DianDian output truncated; increase the explicit output budget")
        text = message.get("content") if isinstance(message, dict) else None
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        if not isinstance(text, str) or not text.strip():
            raise ValueError("DianDian returned empty text")
        return text
