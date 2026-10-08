import unittest
from copy import deepcopy
from lodestar.agent.plan_contract import validate, canonicalize_quotes

class PlanContractTests(unittest.TestCase):
    def setUp(self):
        self.docs=[{'path':'retrieval.py','content':'Exact project source evidence with offsets.'}]
        self.papers=[{'url':'https://arxiv.org/abs/test','content':'Exact method evidence from the paper body.'}]
        self.proposal={'problem':{'description':'Bounded evidence selection','project_refs':[{'path':'retrieval.py','quote':self.docs[0]['content']}]},
            'method':{'description':'Project hypothesis','paper_refs':[{'url':self.papers[0]['url'],'quote':self.papers[0]['content']}]},
            'changes':[{'path':'retrieval.py','description':'Keep evidence offsets'}],
            'experiment':{'hypothesis':'Improved coverage','baseline':'old','candidate':'new','metrics':['coverage'],'constraints':['fixed corpus']},
            'risks':['missed terms'],'missing_evidence':['semantic review']}
    def test_valid_contract_does_not_execute(self):
        self.assertEqual(validate(self.proposal,self.docs,self.papers),[])
    def test_unknown_file_and_fabricated_quote_rejected(self):
        p=deepcopy(self.proposal);p['changes'][0]['path']='unknown.py'
        p['method']['paper_refs'][0]['quote']='Fabricated method statement not in paper'
        self.assertEqual(len(validate(p,self.docs,self.papers)),2)
    def test_short_quote_and_missing_metrics_rejected(self):
        p=deepcopy(self.proposal);p['problem']['project_refs'][0]['quote']='Exact';p['experiment']['metrics']=[]
        self.assertEqual(len(validate(p,self.docs,self.papers)),2)

    def test_pdf_whitespace_restored_with_source_offsets(self):
        paper = {'url': self.papers[0]['url'], 'content': 'Exact method evidence\nfrom the paper body.'}
        canonicalize_quotes(self.proposal, self.docs, [paper])
        ref = self.proposal['method']['paper_refs'][0]
        self.assertEqual(ref['quote'], paper['content'])
        self.assertTrue(ref['whitespace_restored'])
        self.assertEqual(ref['excerpt_span'], {'start': 0, 'end': len(paper['content'])})
        self.assertEqual(validate(self.proposal, self.docs, [paper]), [])
        self.proposal['method']['paper_refs'][0]['quote'] = 'Exact method evidence from the invented body.'
        canonicalize_quotes(self.proposal, self.docs, [paper])
        self.assertTrue(validate(self.proposal, self.docs, [paper]))
