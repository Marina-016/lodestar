"""Documented provider reasoning controls; unknown models keep server defaults."""
from dataclasses import replace

DOC = 'https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/'


def reasoning_choices(config, *, include_current=False):
    model = config.model.lower()
    levels, toggle = (), False
    if config.llm_provider == 'dashscope':
        if model in {'qwen3.8-max', 'qwen3.8-max-0902', 'qwen3.8-flash',
                     'qwen3.8-2.4t-a95b', 'qwen3.8-27b', 'qwen3.8-omni-flash'}:
            levels = ('low', 'medium', 'xhigh')
            toggle = model not in {'qwen3.8-2.4t-a95b', 'qwen3.8-27b'}
        elif model in {'deepseek-v4-flash-0731', 'deepseek-v4-pro-0813', 'deepseek-v4.1-flash', 'kimi-k3'}:
            levels, toggle = ('low', 'high', 'max'), True
        elif model in {'deepseek-v4-flash', 'deepseek-v4-pro', 'glm-5.1', 'glm-5.2'}:
            levels, toggle = ('high', 'max'), True
        elif model == 'glm-5.3':
            levels = ('low', 'high', 'max')
        elif model == 'kimi/kimi-k3':
            levels = ('max',)
        elif model.startswith(('qwen3.7-', 'qwen3.6-', 'qwen3.5-', 'qwen3-')) or model == 'qwen-flash':
            toggle = True
    options = [{'value': 'default', 'label': '服务默认'}]
    if toggle:
        options.append({'value': 'off', 'label': '关闭思考'})
    options.extend({'value': level, 'label': level} for level in levels)
    if toggle and not levels:
        options.append({'value': 'on', 'label': '开启思考（固定预算）'})
    value = 'default'
    if config.llm_thinking_mode != 'default':
        if not config.llm_thinking:
            value = 'off'
        elif config.llm_thinking_mode == 'effort':
            value = config.llm_reasoning_effort
        elif not levels and toggle and config.llm_thinking_mode == 'enabled':
            value = 'on'
        else:
            value = 'current'
        if value not in {o['value'] for o in options}:
            value = 'current'
    if include_current and value == 'current':
        label = ('当前思考预算 · ' + str(config.llm_thinking_budget) + ' tokens'
                 if config.llm_thinking and config.llm_thinking_mode == 'enabled'
                 else '保留当前配置')
        options.append({'value': 'current', 'label': label})
    return {'options': options, 'value': value, 'efforts': list(levels),
            'source': DOC if config.llm_provider == 'dashscope' else None,
            'note': '档位由所选模型决定，不改变工具调用次数。' if levels else
                    '该模型未确认支持原生强度档位；可使用服务默认。'}


def apply_reasoning(config, value):
    from lodestar.chat_settings import configure
    choices = reasoning_choices(config, include_current=True)
    if value not in {o['value'] for o in choices['options']}:
        raise ValueError('此模型不支持所选推理强度')
    if value == 'current':
        return config
    if value == 'default':
        return replace(config, llm_thinking=False, llm_thinking_mode='default')
    if value in ('off', 'on'):
        return configure(config, thinking=value)
    return configure(config, thinking='effort', effort=value)
