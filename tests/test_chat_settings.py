import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lodestar.chat_settings import command_settings, configure, describe, snapshot
from lodestar.config import Config, load_config
from lodestar.llm import LLMError
from lodestar.providers.reasoning import anthropic_options, dashscope_options
from scripts.chat import switch_config
from lodestar.agent.conversation import _history_context


class ChatSettingsTests(unittest.TestCase):
    def test_recent_analysis_is_not_silently_cut_at_3000_characters(self):
        history = [{'role': 'user', 'content': 'compare'},
                   {'role': 'assistant', 'content': 'evidence ' * 800 + 'FINAL_JUDGMENT'}]
        self.assertEqual(_history_context(history), history)

    def test_history_total_budget_keeps_recent_context_and_marks_truncation(self):
        history = [{'role': 'user', 'content': 'old' * 1000},
                   {'role': 'assistant', 'content': 'new judgment'}]
        bounded = _history_context(history, char_budget=200)
        self.assertEqual(bounded[-1], history[-1])
        self.assertLessEqual(sum(len(m['content']) for m in bounded), 200)
        self.assertIn('omitted', bounded[0]['content'])

    def test_audit_does_not_claim_an_unused_fixed_budget_in_effort_mode(self):
        state = snapshot(command_settings(Config(), '/thinking effort high'))
        self.assertIsNone(state['thinking_budget'])
        self.assertEqual(state['reasoning_effort'], 'high')

    def test_profile_is_copy_and_does_not_pretend_to_switch_model(self):
        original = Config(model='current')
        deep = configure(original, profile='deep')
        self.assertFalse(original.llm_thinking)
        self.assertEqual(deep.model, 'current')
        self.assertEqual(deep.dialogue_max_operations, 12)
        self.assertGreater(deep.max_tokens, deep.llm_thinking_budget)
        self.assertTrue(deep.llm_thinking)
        self.assertEqual(configure(deep, profile='fast').dialogue_max_operations, 4)

    def test_profile_model_mapping_and_explicit_override(self):
        cfg = Config(model='base', model_profiles={'deep': 'configured-deep'})
        self.assertEqual(configure(cfg, profile='deep').model, 'configured-deep')
        self.assertEqual(configure(cfg, profile='deep', model='override').model, 'override')
        self.assertEqual(command_settings(cfg, '/model deep').model, 'configured-deep')

    def test_turn_on_after_fast_reserves_room_for_answer(self):
        cfg = configure(Config(), profile='fast')
        cfg = command_settings(cfg, '/thinking on 8192')
        self.assertEqual(cfg.llm_thinking_budget, 8192)
        self.assertGreater(cfg.max_tokens, 8192)
        self.assertIn('请求思考=on/8192', describe(cfg))

    def test_invalid_settings_are_rejected_without_mutating_original(self):
        cfg = Config(model='original')
        for kwargs in ({'model': ''}, {'model': 'bad model'}, {'profile': 'unknown'},
                       {'thinking_budget': 1}, {'operations': 30}, {'max_tokens': 1},
                       {'thinking': 'on', 'max_tokens': 4096}, {'effort': 'invalid'},
                       {'thinking': 'on', 'effort': 'high'}):
            with self.assertRaises(ValueError):
                configure(cfg, **kwargs)
        self.assertEqual(cfg.model, 'original')
        self.assertFalse(cfg.llm_thinking)

    def test_unknown_or_malformed_commands_do_not_reach_model(self):
        for command in ('/unknown', '/profile', '/thinking on invalid', '/model a b', '/thinking off 4096'):
            with self.assertRaises(ValueError):
                command_settings(Config(), command)

    def test_adaptive_is_explicit_and_provider_specific(self):
        cfg = command_settings(Config(), '/thinking adaptive')
        self.assertEqual(anthropic_options(cfg)['thinking'], {'type': 'adaptive'})
        with self.assertRaises(ValueError):
            configure(Config(llm_provider='dashscope'), thinking='adaptive')
        with self.assertRaises(LLMError):
            dashscope_options(cfg)

    def test_effort_mode_has_no_fake_fixed_budget(self):
        cfg = command_settings(Config(), '/thinking effort max')
        self.assertEqual(anthropic_options(cfg), {
            'thinking': {'type': 'enabled'}, 'output_config': {'effort': 'max'}})
        self.assertIn('请求思考=effort/max', describe(cfg))
        with self.assertRaises(ValueError):
            command_settings(Config(llm_provider='dashscope'), '/thinking effort high')

    def test_adaptive_command_can_choose_effort(self):
        cfg = command_settings(Config(), '/thinking adaptive low')
        self.assertEqual(anthropic_options(cfg)['output_config'], {'effort': 'low'})

    def test_structured_extraction_remains_non_thinking(self):
        cfg = configure(Config(), profile='deep')
        self.assertEqual(anthropic_options(cfg, structured=True)['thinking'], {'type': 'disabled'})
        self.assertEqual(dashscope_options(cfg, structured=True), {'enable_thinking': False})

    def test_switch_replaces_client_and_workspace_config_without_new_session(self):
        previous, replacement = Mock(), Mock()
        agent = SimpleNamespace(llm=previous, ws=SimpleNamespace(config=Config()))
        cfg = configure(agent.ws.config, model='new-model')
        with patch('scripts.chat.LLMClient', return_value=replacement):
            switch_config(agent, cfg)
        self.assertIs(agent.llm, replacement)
        self.assertIs(agent.ws.config, cfg)
        previous.close.assert_called_once()

    def test_failed_client_initialization_keeps_previous_settings(self):
        previous = Mock()
        cfg = Config()
        agent = SimpleNamespace(llm=previous, ws=SimpleNamespace(config=cfg))
        with patch('scripts.chat.LLMClient', side_effect=LLMError('no credentials')):
            with self.assertRaises(LLMError):
                switch_config(agent, configure(cfg, model='new'))
        self.assertIs(agent.llm, previous)
        self.assertIs(agent.ws.config, cfg)
        previous.close.assert_not_called()

    def test_environment_overrides_load_without_rewriting_files(self):
        env = {'LODESTAR_MODEL': 'current', 'LODESTAR_MODEL_DEEP': 'deep-model',
               'LODESTAR_LLM_THINKING': 'true', 'LODESTAR_LLM_THINKING_BUDGET': '2048',
               'LODESTAR_MAX_TOKENS': '10000', 'LODESTAR_LLM_THINKING_MODE': 'adaptive'}
        with patch.dict(os.environ, env, clear=True), patch('lodestar.config.load_dotenv'), \
                patch.object(Config, 'ensure_dirs'):
            cfg = load_config()
        self.assertEqual(cfg.max_tokens, 10000)
        self.assertEqual(cfg.llm_thinking_budget, 2048)
        self.assertEqual(cfg.llm_thinking_mode, 'adaptive')
        self.assertEqual(cfg.model_profiles, {'deep': 'deep-model'})
