"""Provider-specific reasoning parameters, independent from answer style."""


def anthropic_options(config, *, structured=False, max_tokens=None):
    from lodestar.llm import LLMError
    if not structured and config.llm_thinking_mode == 'default':
        return {}
    if structured or not config.llm_thinking:
        return {'thinking': {'type': 'disabled'}, 'extra_body': {'temperature': config.temperature}}
    if config.llm_thinking_mode == 'adaptive':
        return {'thinking': {'type': 'adaptive'},
                'output_config': {'effort': config.llm_reasoning_effort}}
    if config.llm_thinking_mode == 'effort':
        # Explicit opt-in for compatible backends (e.g. DeepSeek) that use effort
        # instead of enforcing Anthropic's fixed thinking budget.
        return {'thinking': {'type': 'enabled'},
                'output_config': {'effort': config.llm_reasoning_effort}}
    if config.llm_thinking_mode != 'enabled':
        raise LLMError('Unsupported thinking mode', code='configuration')
    ceiling = max_tokens if max_tokens is not None else config.max_tokens
    if not 1024 <= config.llm_thinking_budget < ceiling:
        raise LLMError('Thinking budget must be at least 1024 and below max_tokens', code='configuration')
    # Sampling controls conflict with extended/adaptive thinking on some models.
    return {'thinking': {'type': 'enabled', 'budget_tokens': config.llm_thinking_budget}}


def dashscope_options(config, *, structured=False):
    from lodestar.llm import LLMError
    if not structured and config.llm_thinking_mode == 'default':
        return {}
    if structured or not config.llm_thinking:
        return {'enable_thinking': False}
    if config.llm_thinking_mode == 'effort':
        from lodestar.providers.capabilities import reasoning_choices
        if config.llm_reasoning_effort not in reasoning_choices(config)['efforts']:
            raise LLMError('Unsupported reasoning effort for this model', code='configuration')
        return {'enable_thinking': True, 'reasoning_effort': config.llm_reasoning_effort}
    if config.llm_thinking_mode != 'enabled':
        raise LLMError('Unsupported DashScope thinking mode', code='configuration')
    return {'enable_thinking': True, 'thinking_budget': config.llm_thinking_budget}
