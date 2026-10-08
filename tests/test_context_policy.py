import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from lodestar.eval.context_policy import TemporaryResearchContext, run, run_case, validate_protocol


class ContextPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.protocol = {'version': 1, 'dataset_kind': 'functional_fixture', 'context_budget': 100,
            'cases': [{'id': 'early-fact', 'question': 'What is the code?',
                'packets': [{'id': 'p1', 'text': 'The code is BLUE.'},
                            {'id': 'p2', 'text': 'Irrelevant material. ' * 30},
                            {'id': 'p3', 'text': 'No update. ' * 30}],
                'expected': {'answer': 'BLUE', 'citations': ['p1']}}]}

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_policy_inputs_are_distinct_and_labels_not_sent(self):
        def complete(role, system, user, **kwargs):
            data = json.loads(user)
            self.assertNotIn('expected', data)
            found = 'BLUE' in data['context'] + data['packet']['text']
            return {'context': 'The code is BLUE. [p1]' if found else '',
                    'answer': 'BLUE' if found else 'UNKNOWN', 'citations': ['p1'] if found else []}
        llm = Mock(); llm.mode = 'test'; llm.complete_json.side_effect = complete
        report = run(llm, self.protocol, self.root / 'ab')
        self.assertEqual(report['rates'], {'append_only': 0, 'model_edit': 1})
        self.assertEqual(report['verdict'], 'measured')
        self.assertEqual(llm.complete_json.call_count, 6)
        self.assertEqual(json.loads((self.root / 'ab/model_edit/0/sources.json').read_text()), self.protocol['cases'][0]['packets'])
        with self.assertRaises(FileExistsError):
            run(llm, self.protocol, self.root / 'ab')

    def test_oversized_edit_stops_without_retry_or_source_mutation(self):
        llm = Mock(); llm.mode = 'test'
        llm.complete_json.return_value = {'context': 'x' * 101, 'answer': 'UNKNOWN', 'citations': []}
        report = run(llm, self.protocol, self.root / 'ab')
        self.assertEqual(report['verdict'], 'inconclusive')
        self.assertEqual(llm.complete_json.call_count, 4)
        self.assertEqual((self.root / 'ab/model_edit/0/context.txt').read_text(), '')
        record = json.loads((self.root / 'ab/model_edit/0/record.json').read_text())
        self.assertEqual(record['revisions'], [])
        self.assertEqual(record['events'][0]['error'], 'ValueError')

    def test_future_packet_citations_and_provider_failure_stop(self):
        llm = Mock(); llm.mode = 'test'
        llm.complete_json.return_value = {'context': '', 'answer': 'BLUE', 'citations': ['p3']}
        report = run(llm, self.protocol, self.root / 'future')
        self.assertEqual(report['verdict'], 'inconclusive')
        self.assertEqual(llm.complete_json.call_count, 1)
        llm.reset_mock(); llm.complete_json.side_effect = RuntimeError('provider unavailable')
        report = run(llm, self.protocol, self.root / 'provider')
        self.assertEqual(report['verdict'], 'inconclusive')
        self.assertEqual(llm.complete_json.call_count, 1)

    def test_protocol_budget_unique_ids_and_failed_replacement(self):
        self.protocol['cases'].append(self.protocol['cases'][0])
        with self.assertRaises(ValueError): validate_protocol(self.protocol)
        state = TemporaryResearchContext(100); state.replace('valid state')
        with self.assertRaises(ValueError): state.replace('x' * 101)
        self.assertEqual(state.text, 'valid state')
        self.assertEqual(len(state.revisions), 1)

    def test_bound_policy_preserves_source_quote_and_rejects_relabelled_quote(self):
        case = self.protocol['cases'][0]
        llm = Mock(); llm.mode = 'test'
        response = {'context': 'Code BLUE, from p1.', 'answer': 'BLUE', 'citations': ['p1'],
                    'bindings': [{'id': 'p1', 'quote': case['packets'][0]['text']}]}
        llm.complete_json.return_value = response
        record = run_case(llm, case, 'source_bound', 200, self.root/'bound')
        self.assertEqual(record['status'], 'complete')
        self.assertEqual(json.loads(record['revisions'][-1]['text'])['bindings'], response['bindings'])
        wrong = {**response, 'bindings': [{'id': 'p2', 'quote': case['packets'][0]['text']}], 'citations': ['p2']}
        llm.complete_json.side_effect = [response, wrong]
        record = run_case(llm, case, 'source_bound', 200, self.root/'wrong')
        self.assertEqual(record['status'], 'error')
        self.assertEqual(len(record['revisions']), 1)
        self.assertEqual(record['events'][1]['error'], 'ValueError')

    def test_bound_policy_rejects_citations_without_retained_sources(self):
        llm = Mock(); llm.mode = 'test'
        llm.complete_json.return_value = {'context': 'BLUE', 'answer': 'BLUE', 'citations': ['p1'], 'bindings': []}
        record = run_case(llm, self.protocol['cases'][0], 'source_bound', 200, self.root/'unbound')
        self.assertEqual(record['status'], 'error')
        self.protocol['modes'] = ['append_only','append_only']
        with self.assertRaises(ValueError): validate_protocol(self.protocol)

    def test_environment_tampering_is_rejected_by_context_audit(self):
        from lodestar.eval.context_audit import verify
        llm = Mock(); llm.mode = 'test'
        llm.complete_json.return_value = {'context': '', 'answer': 'UNKNOWN', 'citations': []}
        directory = self.root / 'receipt'
        run(llm, self.protocol, directory)
        self.assertEqual(verify(directory)['status'], 'consistent')
        (directory / 'requirements-frozen.txt').write_text('changed')
        self.assertNotEqual(verify(directory)['status'], 'consistent')
