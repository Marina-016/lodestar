"""Native tool dialogue transport. Messages use Anthropic's block representation."""
import importlib
import json
import re
import time

import requests

from lodestar.providers.reasoning import anthropic_options, dashscope_options
from lodestar.providers.errors import classify, raise_for_status


def _guard_sse_errors(response):
    """Surface data-only error envelopes that event-name-based SDKs ignore.

    Inspect bounded SSE event data, not model text; never change successful
    bytes, retain upstream bodies, retry, or infer a specific service cause.
    """
    content_type = response.headers.get('content-type', '') if response is not None else ''
    if not isinstance(content_type, str) or content_type.split(';')[0].strip().lower() != 'text/event-stream':
        return
    from lodestar.llm import LLMError
    http = importlib.import_module(type(response).__module__.split('.')[0])
    original = response.stream

    class GuardedStream(http.SyncByteStream):
        def __iter__(self):
            pending, data, size, oversized = b'', [], 0, False
            skip_lf = False

            def line(value):
                nonlocal data, size, oversized
                if value == b'':
                    if data and not oversized:
                        try:
                            event = json.loads(b'\n'.join(data))
                        except (ValueError, UnicodeError):
                            event = None
                        if isinstance(event, dict) and event.get('type') == 'error':
                            raise LLMError('Provider returned an error in its response stream', code=classify(event) or 'upstream_stream_error')
                    data, size, oversized = [], 0, False
                elif value.startswith(b'data:') and not oversized:
                    size += len(value)
                    if size > 65536:
                        data, oversized = [], True
                    else:
                        data.append(value[5:].lstrip(b' '))

            for chunk in original:
                # Let the SDK deliver preceding text from this same chunk first.
                yield chunk
                if not chunk:
                    continue
                if skip_lf and chunk.startswith(b'\n'):
                    chunk = chunk[1:]
                skip_lf = False
                pending += chunk
                start = 0
                # SSE permits CR, LF and CRLF; CRLF may span byte chunks.
                for ending in re.finditer(br'\r\n|\r|\n', pending):
                    line(pending[start:ending.start()])
                    start = ending.end()
                    skip_lf = ending.group() == b'\r' and start == len(pending)
                pending = pending[start:]
                if len(pending) > 65536:
                    pending, data, oversized = b'', [], True
            if pending:
                line(pending.rstrip(b'\r'))
            line(b'')  # Also handle a final valid error envelope without a blank line.

        def close(self):
            original.close()
    response.stream = GuardedStream()


def step(client, system, messages, tools, on_text=None, *, tool_choice=None):
    from lodestar.llm import LLMError
    try:
        if client._dashscope is not None or getattr(client, '_diandian', None) is not None:
            return _openai_step(client, system, messages, tools, on_text, tool_choice=tool_choice)
        options = {'tools': tools} if tools else {'tool_choice': {'type': 'none'}}
        options.update(anthropic_options(client.config))
        if tools and tool_choice == 'required':
            # Extended thinking supports auto/none, not a forced any/tool call.
            # Keep the one bounded host repair without sending an invalid mix.
            thinking = options.get('thinking', {}).get('type')
            options['tool_choice'] = {'type': 'auto' if thinking in ('enabled','adaptive') else 'any'}
        with client._client.messages.stream(
                model=client.model, max_tokens=client.config.max_tokens,
                system=system, messages=messages,
                **options) as stream:
            _guard_sse_errors(getattr(stream, 'response', None))
            for text in stream.text_stream:
                if on_text:
                    on_text(text)
            try:
                response = stream.get_final_message()
            except AssertionError as error:
                # The SDK asserts when a compatible endpoint closes without any
                # recognized message. This is an incomplete transport, not a
                # successful answer or a generic harness decision failure.
                raise LLMError('Dialogue stream ended without a final message', code='stream_interrupted') from error
        if response.stop_reason == 'max_tokens':
            raise LLMError('Dialogue output truncated', code='truncated')
        if response.stop_reason not in ('end_turn', 'tool_use', 'stop_sequence'):
            raise LLMError('Dialogue did not finish', code='stream_interrupted')
        content = [block.model_dump(exclude_none=True) for block in response.content]
        usage = getattr(response, 'usage', None)
        client.last_dialogue_observation = {
            'requested_model': client.model,
            'returned_model': getattr(response, 'model', None),
            'thinking_requested': None if client.config.llm_thinking_mode == 'default' else client.config.llm_thinking,
            'thinking_observed': any(b.get('type') in ('thinking', 'redacted_thinking') for b in content),
            'usage': usage.model_dump(exclude_none=True) if usage is not None else {},
        }
        return {'role': 'assistant', 'content': content}
    except LLMError as error:
        error.role = 'conversation'
        raise
    except Exception as error:
        raise LLMError('Native dialogue request failed', role='conversation') from error


def _openai_messages(messages):
    converted = []
    for message in messages:
        content = message['content']
        if isinstance(content, str):
            converted.append(message)
            continue
        results = [b for b in content if b['type'] == 'tool_result']
        if results:
            converted.extend({'role': 'tool', 'tool_call_id': b['tool_use_id'],
                              'content': b['content']} for b in results)
            continue
        item = {'role': message['role'], 'content': ''.join(
            b['text'] for b in content if b['type'] == 'text') or None}
        reasoning = ''.join(b.get('thinking', '') for b in content if b['type'] == 'thinking')
        if reasoning:
            item['reasoning_content'] = reasoning
        calls = [{'id': b['id'], 'type': 'function', 'function': {
            'name': b['name'], 'arguments': json.dumps(b['input'], ensure_ascii=False)}}
            for b in content if b['type'] == 'tool_use']
        if calls:
            item['tool_calls'] = calls
        converted.append(item)
    return converted


def _openai_step(client, system, messages, tools, on_text, *, tool_choice=None):
    from lodestar.llm import LLMError
    adapter = client._dashscope or client._diandian
    # Older tests inject a minimal DashScope fixture; retain the public
    # transport shape while letting real adapters own their header semantics.
    openai_options = (adapter.openai_options() if hasattr(adapter, 'openai_options')
                      else dashscope_options(client.config))
    headers = adapter.headers if hasattr(adapter, 'headers') else {'Authorization': 'Bearer ' + adapter.key}
    payload = {'model': client.model, 'messages': [{'role': 'system', 'content': system}]
               + _openai_messages(messages), 'max_tokens': client.config.max_tokens,
               'temperature': client.config.temperature, **openai_options,
               'stream': True, 'stream_options': {'include_usage': True}}
    if tools:
        if tool_choice == 'required':
            payload['tool_choice'] = 'required'
        payload['tools'] = [{'type': 'function', 'function': {
            'name': t['name'], 'description': t['description'], 'parameters': t['input_schema']}}
            for t in tools]
    else:
        payload['tool_choice'] = 'none'
    text, reasoning, calls, finished, done = [], [], {}, False, False
    usage, returned_model = {}, None
    transport = getattr(adapter, 'http', requests)
    # ``requests`` applies its timeout to an individual socket operation. An
    # upstream SSE heartbeat can therefore keep an otherwise stalled dialogue
    # alive indefinitely. Conversation timeout is an end-to-end UI budget.
    conversation_timeout_s = client.config.conversation_timeout_s
    request_start = time.monotonic()
    first_text_ms, first_reasoning_ms = None, None
    deadline = request_start + conversation_timeout_s
    with transport.post(adapter.base_url + '/chat/completions',
            headers=headers, json=payload,
            timeout=min(client.config.llm_timeout_s, conversation_timeout_s), stream=True) as response:
        headers_ms = round((time.monotonic()-request_start)*1000)
        try:
            raise_for_status(response)
        except LLMError as error:
            # A valid DianDian key may still see a 403 for one streaming
            # payload. Keep that distinct from a failed key: callers should
            # offer retry/rewording, not falsely tell the user to replace it.
            if client._diandian is not None and response.status_code == 403 and error.code == 'http_error':
                raise LLMError('DianDian rejected this dialogue payload', code='request_rejected') from error.__cause__
            raise
        # requests defaults to 512-byte chunks; small SSE deltas can sit in
        # that buffer and appear in bursts even when the provider emits promptly.
        for line in response.iter_lines(chunk_size=1):
            if time.monotonic() >= deadline:
                raise LLMError('Dialogue request exceeded configured timeout', code='timeout')
            line = line.decode('utf-8') if isinstance(line, bytes) else line
            if not line or not line.startswith('data:'):
                continue
            raw = line[5:].strip()
            if raw == '[DONE]':
                done = True
                break
            data = json.loads(raw)
            returned_model = data.get('model') or returned_model
            if data.get('error'):
                raise LLMError('Provider stream error', code=classify(data) or 'upstream_stream_error')
            if data.get('usage'):
                usage = data['usage']
                adapter.usage.append({'role': 'conversation', **data['usage']})
            for choice in data.get('choices', []):
                delta = choice.get('delta', {})
                if delta.get('reasoning_content'):
                    if first_reasoning_ms is None:
                        first_reasoning_ms = round((time.monotonic()-request_start)*1000)
                    reasoning.append(delta['reasoning_content'])
                if delta.get('content'):
                    if first_text_ms is None:
                        first_text_ms = round((time.monotonic()-request_start)*1000)
                    text.append(delta['content'])
                    if on_text:
                        on_text(delta['content'])
                for part in delta.get('tool_calls', []):
                    call = calls.setdefault(part['index'], {'id': '', 'name': '', 'arguments': ''})
                    call['id'] += part.get('id', '')
                    call['name'] += part.get('function', {}).get('name', '')
                    call['arguments'] += part.get('function', {}).get('arguments', '')
                reason = choice.get('finish_reason')
                if reason == 'length':
                    raise LLMError('Dialogue output truncated', code='truncated')
                if reason in ('stop', 'tool_calls'):
                    finished = True
    if not done or not finished:
        raise LLMError('Stream ended before completion', code='stream_interrupted')
    blocks = [{'type': 'text', 'text': ''.join(text)}] if text else []
    if reasoning:
        blocks.insert(0, {'type': 'thinking', 'thinking': ''.join(reasoning)})
    for _, call in sorted(calls.items()):
        try:
            arguments = json.loads(call['arguments'])
        except (ValueError, TypeError):
            # Preserve invalid arguments as data: the host returns a repairable tool error.
            arguments = call['arguments']
        blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['name'], 'input': arguments})
    client.last_dialogue_observation = {
        'requested_model': client.model, 'returned_model': returned_model,
        'thinking_requested': None if client.config.llm_thinking_mode == 'default' else client.config.llm_thinking,
        'thinking_observed': bool(reasoning), 'usage': usage,
        'timing': {'headers_ms':headers_ms,'first_text_ms':first_text_ms,
                   'first_reasoning_ms':first_reasoning_ms,
                   'total_ms':round((time.monotonic()-request_start)*1000),
                   'input_chars':sum(len(str(m.get('content') or '')) for m in payload['messages'])},
    }
    return {'role': 'assistant', 'content': blocks}
