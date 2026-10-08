import json
import tempfile
import unittest
from pathlib import Path
from lodestar.eval.paired import run,validate_protocol


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.protocol={'version':1,'hypothesis':'bounded exact transformation','dataset_kind':'functional_fixture',
                       'grader':'exact_output','cases':[{'id':'one','input':2,'expected':4},{'id':'two','input':3,'expected':6}],
                       'seed':7,'min_delta':None}
        self.path=self.root/'protocol.json';self.path.write_text(json.dumps(self.protocol),encoding='utf-8')
        self.base=self.root/'baseline.py';self.candidate=self.root/'candidate.py'
        template="import json,sys\ndata=json.load(sys.stdin)\nassert all('expected' not in c for c in data['cases'])\nprint(json.dumps({'status':'complete','results':[{'case_id':c['id'],'output':EXPRESSION} for c in data['cases']]}))\n"
        self.base.write_text(template.replace('EXPRESSION',"c['input']"),encoding='utf-8')
        self.candidate.write_text(template.replace('EXPRESSION',"c['input']*2"),encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_outputs_are_scored_and_frozen_not_arm_supplied_scores(self):
        report=run(self.path,self.base,self.candidate,self.root/'result')
        self.assertEqual(report['verdict'],'measured')
        self.assertEqual(report['delta'],1)
        self.assertEqual(report['adoption_status'],'not_decided')
        self.assertEqual(report['improved_cases'],['one','two'])
        self.assertEqual((self.root/'result/baseline/arm.py').read_bytes(),self.base.read_bytes())
        self.assertTrue((self.root/'result/grader.py').exists())
        with self.assertRaises(FileExistsError):
            run(self.path,self.base,self.candidate,self.root/'result')

    def test_incomplete_or_duplicate_result_cannot_win(self):
        self.candidate.write_text("import json;print(json.dumps({'status':'complete','results':[{'case_id':'one','output':4},{'case_id':'one','output':4}]}))",encoding='utf-8')
        report=run(self.path,self.base,self.candidate,self.root/'result')
        self.assertEqual(report['verdict'],'inconclusive')
        self.assertIn('duplicate',report['arms']['candidate']['error'])

    def test_boolean_is_not_numeric_ground_truth(self):
        self.protocol['cases']=[{'id':'case','input':1,'expected':1}]
        self.path.write_text(json.dumps(self.protocol),encoding='utf-8')
        self.candidate.write_text("import json;print(json.dumps({'status':'complete','results':[{'case_id':'case','output':True}]}))",encoding='utf-8')
        report=run(self.path,self.candidate,self.candidate,self.root/'result')
        self.assertEqual(report['arms']['candidate']['exact_output_rate'],0)

    def test_timeout_nonfinite_and_duplicate_protocol_rejected(self):
        self.candidate.write_text('import time;time.sleep(5)',encoding='utf-8')
        report=run(self.path,self.base,self.candidate,self.root/'result',timeout=1)
        self.assertEqual(report['verdict'],'inconclusive')
        self.assertEqual(report['arms']['candidate']['error'],'timeout')
        self.protocol['cases'].append(self.protocol['cases'][0])
        with self.assertRaises(ValueError):validate_protocol(self.protocol)
        self.protocol['cases']=self.protocol['cases'][:1];self.protocol['min_delta']=float('nan')
        with self.assertRaises(ValueError):validate_protocol(self.protocol)

    def test_predeclared_threshold_and_regression_gate(self):
        self.protocol['min_delta']=0.1
        self.path.write_text(json.dumps(self.protocol),encoding='utf-8')
        report=run(self.path,self.candidate,self.base,self.root/'result')
        self.assertEqual(report['verdict'],'fail')
        self.assertEqual(report['regressed_cases'],['one','two'])

    def test_mutating_arm_source_invalidates_record(self):
        self.candidate.write_text("from pathlib import Path\nPath(__file__).write_text('changed')\nprint('{}')",encoding='utf-8')
        report=run(self.path,self.base,self.candidate,self.root/'result')
        self.assertEqual(report['verdict'],'inconclusive')
        self.assertEqual(report['arms']['candidate']['error'],'arm_source_changed_during_run')
