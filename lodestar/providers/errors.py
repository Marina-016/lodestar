"""Allowlisted service error categories, never provider messages in diagnostics."""
import json
import re

import requests


def classify(payload):
    if not isinstance(payload, dict):
        return None
    error = payload.get('error', payload)
    if not isinstance(error, dict):
        return None
    values = [error.get('code'), error.get('type'), payload.get('code')]
    codes = {re.sub(r'[^a-z0-9]', '', v.lower()) for v in values if isinstance(v, str) and len(v) < 200}
    # Alibaba documents insufficient_quota / Throttling.AllocationQuota as
    # TPM/TPS limits. They must not hide a model as permanently exhausted.
    if codes & {'allocationquotafreetieronly', 'freequotaexhausted', 'freetierexhausted'}:
        return 'quota_exhausted'
    if codes & {'modelnotfound', 'modelnotexist', 'modelunavailable', 'modeldisabled'}:
        return 'model_unavailable'
    message = error.get('message')
    if isinstance(message, str) and len(message) <= 500 and re.fullmatch(
            r'(?:the )?model(?: [\w./-]+)? (?:not exist|does not exist|can not be found)\.?',
            message.strip(), re.IGNORECASE):
        return 'model_unavailable'
    return None


def from_exception(error):
    if error is None:
        return None
    found = classify(getattr(error, 'body', None))
    if found:
        return found
    response = getattr(error, 'response', None)
    body = getattr(response, '_content', None)
    if isinstance(body, bytes) and len(body) <= 65536:
        try:
            return classify(json.loads(body))
        except (ValueError, UnicodeError):
            pass
    return None


def raise_for_status(response):
    """Read at most 64 KiB of an error stream before its response is closed."""
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        from lodestar.llm import LLMError
        code = from_exception(error)
        if not code and getattr(response, '_content', None) is False:
            body = bytearray()
            try:
                for chunk in response.iter_content(chunk_size=4096):
                    body.extend(chunk)
                    if len(body) > 65536:
                        break
                if len(body) <= 65536:
                    code = classify(json.loads(body))
            except (ValueError, UnicodeError, requests.RequestException):
                pass
        raise LLMError('Model service rejected the request', code=code or 'http_error') from error
