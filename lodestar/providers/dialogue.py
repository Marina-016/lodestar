"""Native tool dialogue transport. Messages use Anthropic's block representation."""
import json

import requests

from lodestar.providers.reasoning import anthropic_options, dashscope_options


def step(client, system, messages, tools, on_text=None):
    from lodestar.llm import LLMError
    try:
        if client._dashscope is not None:
            return _openai_step(client, system, messages, tools, on_text)
        options = {'tools': tools} if tools else {'tool_choice': {'type': 'none'}}
        options.update(anthropic_options(client.config))
        with client._client.messages.stream(
                model=client.model, max_tokens=client.config.max_tokens,
                system=system, messages=messages,
                **options) as stream:
            for text in stream.text_stream:
                if on_text:
                    on_text(text)
            response = stream.get_final_message()
        if response.stop_reason == 'max_tokens':
            raise LLMError('Dialogue output truncated', code='truncated')
        if response.stop_reason not in ('end_turn', 'tool_use', 'stop_sequence'):
            raise LLMError('Dialogue did not finish', code='stream_interrupted')
        content = [block.model_dump(exclude_none=True) for block in response.content]
        usage = getattr(response, 'usage', None)
        client.last_dialogue_observation = {
            'requested_model': client.model,
            'returned_model': getattr(response, 'model', None),
            'thinking_requested': client.config.llm_thinking,
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


def _openai_step(client, system, messages, tools, on_text):
    from lodestar.llm import LLMError
    adapter = client._dashscope
    payload = {'model': client.model, 'messages': [{'role': 'system', 'content': system}]
               + _openai_messages(messages), 'max_tokens': client.config.max_tokens,
               'temperature': client.config.temperature, **dashscope_options(client.config),
               'stream': True, 'stream_options': {'include_usage': True}}
    if tools:
        payload['tools'] = [{'type': 'function', 'function': {
            'name': t['name'], 'description': t['description'], 'parameters': t['input_schema']}}
            for t in tools]
    else:
        payload['tool_choice'] = 'none'
    text, reasoning, calls, finished, done = [], [], {}, False, False
    usage, returned_model = {}, None
    with requests.post(adapter.base_url + '/chat/completions',
            headers={'Authorization': 'Bearer ' + adapter.key}, json=payload,
            timeout=client.config.llm_timeout_s, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
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
                raise LLMError('Provider stream error', code='stream_interrupted')
            if data.get('usage'):
                usage = data['usage']
                adapter.usage.append({'role': 'conversation', **data['usage']})
            for choice in data.get('choices', []):
                delta = choice.get('delta', {})
                if delta.get('reasoning_content'):
                    reasoning.append(delta['reasoning_content'])
                if delta.get('content'):
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
        'thinking_requested': client.config.llm_thinking,
        'thinking_observed': bool(reasoning), 'usage': usage,
    }
    return {'role': 'assistant', 'content': blocks}
