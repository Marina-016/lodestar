import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from lodestar.eval.context_policy import run
from lodestar.eval.context_audit import verify


class ContextAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.protocol = {'version': 1, 'dataset_kind': 'functional_fixture', 'context_budget': 200,
            'cases': [{'id':'one','question':'What code?', 'packets':[{'id':'p1','text':'The code is BLUE.'}],
                      'expected':{'answer':'BLUE','citations':['p1']}}]}
        self.llm = Mock(); self.llm.mode='test'
        self.llm.complete_json.return_value={'context':'The code is BLUE.','answer':'BLUE','citations':['p1']}
        run(self.llm,self.protocol,self.root/'ab')
    def tearDown(self): self.temp.cleanup()
    def test_intact_record_is_consistent_without_execution(self):
        self.assertEqual(verify(self.root/'ab')['status'],'consistent')
        self.assertEqual(self.llm.complete_json.call_count,2)
    def test_edited_scores_and_event_sources_are_rejected(self):
        p=self.root/'ab/result.json';data=json.loads(p.read_text());data['rates']['model_edit']=0
        p.write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(verify(self.root/'ab')['status'],'invalid')
        data['rates']['model_edit']=1;p.write_text(json.dumps(data),encoding='utf-8')
        p=self.root/'ab/model_edit/0/record.json';data=json.loads(p.read_text());data['events'][0]['input']['packet']['text']='Altered'
        p.write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(verify(self.root/'ab')['status'],'invalid')
    def test_changed_implementation_or_missing_files_rejected(self):
        (self.root/'ab/implementation.py').write_text('changed',encoding='utf-8')
        self.assertEqual(verify(self.root/'ab')['status'],'invalid')
        self.assertEqual(verify(self.root/'missing')['status'],'invalid')
    def test_failure_records_remain_inconclusive_not_success(self):
        self.protocol['cases'].append({**self.protocol['cases'][0], 'id':'two'})
        self.llm.complete_json.side_effect=RuntimeError('provider failure')
        run(self.llm,self.protocol,self.root/'failed')
        self.assertEqual(verify(self.root/'failed')['status'],'consistent')
