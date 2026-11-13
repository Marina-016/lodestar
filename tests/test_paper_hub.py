"""Paper/source integration and human transcript retrieval regression checks."""
import json
import unittest
import threading
import requests
from lodestar.web.server import make_server
from lodestar.memory import learning, repo
from lodestar.web import paper_hub, conversation_search
from lodestar.web.tools import assistant_tools
from tests import test_web

class PaperHubTests(unittest.TestCase):
    setUp=test_web.WebTest.setUp
    tearDown=test_web.WebTest.tearDown

    def seed(self,ws):
        item=self.app.item(ws,'main');url='https://arxiv.org/abs/2601.00001v2'
        message=repo.add_message(ws.conn,item['conversation_id'],'assistant','推荐 [Test Paper]('+url+')。',metadata={'source_details':[
            {'url':url,'title':'Test Paper','source_type':'paper','keywords':['agents']},
            {'url':'https://arxiv.org/abs/2601.99999','title':'Unused Result','source_type':'paper'}]})
        record=learning.record(ws.conn,user_id='web:local',technology='KV cache',method='Selective transfer',event='explained',actor='agent',evidence='助手讲解过方法',paper_url='https://arxiv.org/pdf/2601.00001.pdf')
        return item,message,record['evidence_id']

    def test_evidence_and_actual_citation_deduplicate_without_claiming_mastery(self):
        with self.app.workspace() as ws,ws.conn:
            _,_,eid=self.seed(ws);paper_hub.sync(ws)
            result=paper_hub.catalog(ws,query='Selective transfer')
            self.assertEqual(result['total'],1)
            p=result['papers'][0];self.assertEqual(p['title'],'Test Paper');self.assertIn('agents',p['keywords'])
            self.assertEqual(p['study']['mastery'],'unknown');self.assertEqual(p['study']['status'],'inbox')
            self.assertEqual(len(paper_hub.detail(ws,p['id'])['evidence']),1)
            learning.revoke(ws.conn,eid,user_id='web:local');paper_hub.sync(ws)
            self.assertEqual(paper_hub.catalog(ws,query='Selective transfer')['total'],0)
            self.assertEqual(paper_hub.catalog(ws)['total'],1)

    def test_unbound_state_survives_sync_and_conflicting_update_is_rejected(self):
        with self.app.workspace() as ws,ws.conn:
            self.seed(ws);paper_hub.sync(ws);p=paper_hub.catalog(ws)['papers'][0]
            paper_hub.update(ws,{'id':p['id'],'revision':1,'status':'later','mastery':'understand','note':'摘要已懂，方法待学'})
            paper_hub.sync(ws)
            p=paper_hub.catalog(ws,status='later',mastery='understand')['papers'][0]
            self.assertEqual(p['study']['note'],'摘要已懂，方法待学')
            with self.assertRaises(ValueError):paper_hub.update(ws,{'id':p['id'],'revision':1,'status':'read'})
            self.assertEqual(paper_hub.detail(ws,p['id'])['paper']['study']['status'],'later')

    def test_duplicate_add_keeps_existing_study_state(self):
        first=self.app.paper_library({'title':'Paper','url':'https://arxiv.org/abs/2601.00001','status':'reading'})
        second=self.app.paper_library({'title':'Paper','url':'https://arxiv.org/pdf/2601.00001v2.pdf','status':'inbox'})
        self.assertEqual(first['paper']['id'],second['paper']['id'])
        self.assertEqual(second['paper']['study']['status'],'reading')

    def test_http_state_requires_token_and_conflicting_edit_rolls_back_metadata(self):
        first=self.app.paper_library({'title':'Original','url':'https://arxiv.org/abs/2601.00001','status':'later'})
        pid=first['paper']['id'];server=make_server(self.app,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_address[1])
        try:
            catalog=requests.get(base+'/api/papers',timeout=5).json()
            self.assertEqual(catalog['total'],1)
            payload={'id':pid,'revision':first['paper']['study']['revision'],'status':'reading'}
            self.assertEqual(requests.post(base+'/api/paper-study',json=payload,timeout=5).status_code,403)
            headers={'X-Lodestar-Token':self.app.token}
            changed=requests.post(base+'/api/paper-study',json=payload,headers=headers,timeout=5)
            self.assertEqual(changed.status_code,200)
            stale=requests.post(base+'/api/paper-library',headers=headers,json={'action':'save','id':pid,'revision':first['paper']['revision'],
                'study_revision':first['paper']['study']['revision'],'title':'Must roll back','url':first['paper']['canonical_url'],'status':'read'},timeout=5)
            self.assertEqual(stale.status_code,400)
            p=requests.get(base+'/api/papers?id='+str(pid),timeout=5).json()['paper']
            self.assertEqual(p['title'],'Original');self.assertEqual(p['study']['status'],'reading')
        finally:server.shutdown();server.server_close();thread.join(timeout=5)

    def test_code_example_and_unused_search_hit_are_not_papers(self):
        with self.app.workspace() as ws,ws.conn:
            item=self.app.item(ws,'main')
            repo.add_message(ws.conn,item['conversation_id'],'assistant','```\nhttps://arxiv.org/abs/2601.00001\n```',metadata={'source_details':[{'url':'https://arxiv.org/abs/2601.00002','title':'Unused','source_type':'paper'}]})
            paper_hub.sync(ws);self.assertEqual(paper_hub.catalog(ws)['total'],0)

    def test_dedicated_tool_saves_later_without_personal_memory_and_blocks_background_updates(self):
        with self.app.workspace() as ws,ws.conn:
            item=self.app.item(ws,'main')
            repo.add_message(ws.conn,item['conversation_id'],'user','把这篇论文加入待学。')
        tool=assistant_tools(self.app,item,'paper-tool','把这篇论文加入待学。',lambda:None)['manage_paper']['handler']
        result=tool({'action':'save','url':'https://arxiv.org/abs/2601.00001','title':'Test Paper','status':'later'})
        self.assertEqual(result['paper']['study']['status'],'later')
        with self.app.workspace() as ws:
            self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM assistant_memories').fetchone()[0],0)
        tool=assistant_tools(self.app,item,'background','',lambda:None,user_authored=False)['manage_paper']['handler']
        with self.assertRaises(ValueError):tool({'action':'update','id':result['paper']['id'],'revision':2,'mastery':'apply'})

class ConversationSearchTests(unittest.TestCase):
    setUp=test_web.WebTest.setUp
    tearDown=test_web.WebTest.tearDown

    def test_literal_search_returns_short_safe_data_and_bounded_context(self):
        with self.app.workspace() as ws,ws.conn:
            item=self.app.item(ws,'main');ids=[]
            for i in range(7):ids.append(repo.add_message(ws.conn,item['conversation_id'],'user' if i%2==0 else 'assistant',('x'*1000+'literal 100%_ token'+'y'*1000) if i==3 else 'context '+str(i))['id'])
            result=conversation_search.search(ws,query='100%_')
            self.assertEqual(result['total'],1);self.assertLess(len(result['matches'][0]['excerpt']),245)
            context=conversation_search.context(ws,'main',ids[3])
            self.assertEqual([m['id'] for m in context['messages']],ids[1:6])
            self.assertEqual(conversation_search.search(ws,query='100Xz')['total'],0)
            self.assertEqual(conversation_search.search(ws,role='assistant',query='token')['total'],1)
            self.assertEqual(conversation_search.search(ws,role='user',query='token')['total'],0)

    def test_empty_query_shows_one_recent_hit_per_owned_conversation(self):
        task=self.app.create('Second conversation')
        with self.app.workspace() as ws,ws.conn:
            for id in ('main',task):
                item=self.app.item(ws,id)
                for i in range(3):repo.add_message(ws.conn,item['conversation_id'],'user','recent '+str(i))
            recent=conversation_search.search(ws)
            self.assertEqual(recent['total'],2);self.assertTrue(recent['recent'])
            ws.conn.execute('UPDATE web_items SET owner=? WHERE id=?',('foreign',task))
            self.assertEqual(conversation_search.search(ws)['total'],1)
            with self.assertRaises(LookupError):conversation_search.context(ws,task,recent['matches'][0]['id'])
