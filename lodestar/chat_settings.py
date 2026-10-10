"""Small, reusable per-process chat controls; never rewrite .env or credentials."""
from dataclasses import replace


PROFILES = {
    'fast': dict(max_tokens=2048, llm_thinking=False, dialogue_max_operations=4,
                 llm_reasoning_effort='low'),
    'balanced': dict(max_tokens=8192, llm_thinking=False, dialogue_max_operations=8,
                     llm_reasoning_effort='medium'),
    'deep': dict(max_tokens=16384, llm_thinking=True, llm_thinking_budget=8192,
                 dialogue_max_operations=12, llm_reasoning_effort='high'),
}


def configure(config, *, profile=None, model=None, thinking=None,
              thinking_budget=None, max_tokens=None, operations=None, effort=None):
    """Return a validated copy. A profile without a model mapping keeps the model."""
    updates = {}
    if profile is not None:
        if profile not in PROFILES:
            raise ValueError('档位应为 fast / balanced / deep')
        updates.update(PROFILES[profile], dialogue_profile=profile)
        if config.model_profiles.get(profile):
            updates['model'] = config.model_profiles[profile]
    if model is not None:
        updates['model'] = config.model_profiles.get(model, model)
    if thinking is not None:
        if thinking not in ('on', 'off', 'effort', 'adaptive'):
            raise ValueError('思考模式应为 on / off / effort / adaptive')
        updates['llm_thinking'] = thinking != 'off'
        if thinking != 'off':
            updates['llm_thinking_mode'] = thinking if thinking in ('effort', 'adaptive') else 'enabled'
    for attr, value in (('llm_thinking_budget', thinking_budget), ('max_tokens', max_tokens),
                        ('dialogue_max_operations', operations), ('llm_reasoning_effort', effort)):
        if value is not None:
            updates[attr] = value
    cfg = replace(config, **updates)
    if not cfg.model or len(cfg.model) > 200 or any(c.isspace() for c in cfg.model):
        raise ValueError('请填写有效的模型 ID，不能包含空白')
    if not 1024 <= cfg.llm_thinking_budget <= 32768:
        raise ValueError('思考预算应在 1024–32768 tokens 之间')
    if not 256 <= cfg.max_tokens <= 65536:
        raise ValueError('输出上限应在 256–65536 tokens 之间')
    if not 1 <= cfg.dialogue_max_operations <= 20:
        raise ValueError('工具预算应在 1–20 次之间')
    if cfg.llm_thinking_mode not in ('enabled', 'effort', 'adaptive'):
        raise ValueError('LODESTAR_LLM_THINKING_MODE 应为 enabled / effort / adaptive')
    if cfg.llm_reasoning_effort not in ('low', 'medium', 'high', 'max'):
        raise ValueError('思考强度应为 low / medium / high / max')
    if effort is not None and cfg.llm_thinking_mode == 'enabled':
        raise ValueError('强度参数请搭配 --thinking effort/adaptive；固定模式请使用思考预算')
    if cfg.llm_thinking:
        if cfg.llm_provider == 'dashscope' and cfg.llm_thinking_mode != 'enabled':
            raise ValueError('DashScope 请用 /thinking on；effort/adaptive 仅用于支持它的 Anthropic 兼容服务')
        if cfg.llm_thinking_mode == 'enabled' and cfg.max_tokens < cfg.llm_thinking_budget + 1024:
            if max_tokens is not None:
                raise ValueError('输出上限必须为思考预算之外至少留出 1024 tokens 的回答空间')
            cfg = replace(cfg, max_tokens=cfg.llm_thinking_budget + 2048)
    return cfg


def describe(config):
    thinking = (config.llm_thinking_mode + '/' + config.llm_reasoning_effort
                if config.llm_thinking_mode in ('effort', 'adaptive') else f'on/{config.llm_thinking_budget}')
    return (f'模型={config.model} | 档位={config.dialogue_profile} | '
            f'请求思考={thinking if config.llm_thinking else "off"} | '
            f'输出上限={config.max_tokens} | 工具预算={config.dialogue_max_operations}')


def snapshot(config):
    """Persist effective request settings, not credentials or unused controls."""
    mode = config.llm_thinking_mode if config.llm_thinking else 'disabled'
    return {'model': config.model, 'provider': config.llm_provider,
            'profile': config.dialogue_profile, 'thinking_requested': config.llm_thinking,
            'thinking_mode': mode,
            'thinking_budget': config.llm_thinking_budget if mode == 'enabled' else None,
            'reasoning_effort': config.llm_reasoning_effort if mode in ('effort', 'adaptive') else None,
            'max_tokens': config.max_tokens, 'operation_budget': config.dialogue_max_operations}


def command_settings(config, message):
    """Parse configuration commands only; normal questions never go through this."""
    parts = message.split()
    command, args = parts[0], parts[1:]
    if command == '/model' and len(args) == 1:
        return configure(config, model=args[0])
    if command == '/profile' and len(args) == 1:
        return configure(config, profile=args[0])
    if command == '/thinking' and 1 <= len(args) <= 2:
        if args[0] == 'off' and len(args) != 1:
            raise ValueError('/thinking off 不接受额外参数')
        if args[0] in ('effort', 'adaptive'):
            return configure(config, thinking=args[0], effort=args[1] if len(args) == 2 else None)
        return configure(config, thinking=args[0],
                         thinking_budget=int(args[1]) if len(args) == 2 else None)
    raise ValueError('用法：/model 模型ID、/profile fast|balanced|deep、/thinking on [预算]|off|effort [强度]|adaptive [强度]')
