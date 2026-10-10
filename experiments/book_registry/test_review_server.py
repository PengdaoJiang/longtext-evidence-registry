"""HTTP read/review contract tests use synthetic data, never the book registry."""
import json
import threading
import unittest
import urllib.request
import urllib.error
import uuid
from test_registry import fixture,batch,RUN_DIR
from review_server import ReviewData,server
from admission_scores import start_run,save_batch,finish_run

class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.reg=fixture(RUN_DIR/uuid.uuid4().hex/'web.sqlite3')
        self.reg.enqueue(); self.lease=self.reg.claim('synthetic-web')[0]
        self.receipt=self.reg.submit(self.lease['task_id'],self.lease['lease_token'],batch(self.lease['unit_id']))
        self.app=server(self.reg,0); self.port=self.app.server_port
        self.thread=threading.Thread(target=self.app.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.port}'
    def tearDown(self):
        self.app.shutdown();self.app.server_close();self.thread.join()
    def request(self,path,data=None,token=None):
        headers={'Content-Type':'application/json'}
        if token: headers['X-Review-Token']=token
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as exc:return exc.code,json.load(exc)
    def test_reads_expose_real_evidence_and_filtered_paragraphs(self):
        eid=self.receipt['entity_ids']['a'];fid=self.receipt['fact_ids']['transfer']
        _,entity=self.request('/api/entity?id='+eid)
        self.assertEqual(entity['label'],'甲');self.assertTrue(entity['evidence'][0]['paragraph_text'])
        _,graph=self.request('/api/graph?id='+eid)
        self.assertEqual(len(graph['edges']),1)
        _,facts=self.request('/api/facts?paragraph=SU-000001-P0002')
        self.assertEqual(facts['total'],0)
        _,fact=self.request('/api/fact?id='+fid)
        self.assertEqual(fact['subject_label'],'甲');self.assertEqual(fact['value'],None)
        self.assertTrue(fact['provenance'])
        _,chapter=self.request('/api/chapter?id=SU-000001')
        self.assertTrue(chapter['paragraphs'][0]['covered'])
    def test_browser_cannot_change_fact_decisions(self):
        fid=self.receipt['fact_ids']['transfer']
        self.assertEqual(self.request('/api/review',{'type':'fact','id':fid,'state':'accepted','revision':0,'reason':'合成测试'})[0],405)
        _,detail=self.request('/api/fact?id='+fid)
        self.assertEqual(detail['review_state'],'pending')
        self.assertEqual(detail['reviews'],[])

    def test_scores_appear_without_server_restart_and_refresh_generation(self):
        _,before=self.request('/api/status')
        self.assertEqual(self.request('/api/scores')[1]['total'],0)
        eid=self.receipt['entity_ids']['a']
        _,entity=self.request('/api/entity?id='+eid)
        start_run(self.reg,'synthetic-score-run',1,RUN_DIR)
        packet={'targets':[{'handle':entity['handle'],'entity_id':eid}],
            'entities':[{'handle':entity['handle'],'entity_id':eid}],
            'source_sha256':before['source_sha256'],'source':[{'paragraph_id':'SU-000001-P0001'}]}
        result={'scores':[{'handle':entity['handle'],'p_need':0.0,'recommendation':'source_only',
            'reason':'合成评分，不是事实判假','merge_target':None,'preserve_note':'',
            'evidence_paragraph_ids':['SU-000001-P0001'],'context_gap':None}]}
        save_batch(self.reg,'synthetic-score-run','batch',packet,result,'hash')
        finish_run(self.reg,'synthetic-score-run')
        _,after=self.request('/api/status')
        self.assertNotEqual(after['generation'],before['generation'])
        _,scores=self.request('/api/scores')
        self.assertEqual(scores['items'][0]['handle'],entity['handle'])
        self.assertEqual(scores['items'][0]['p_need'],0)
        self.assertFalse(scores['run']['threshold_enabled'])
        _,detail=self.request('/api/entity?id='+eid)
        self.assertEqual(detail['admission_score']['reason'],result['scores'][0]['reason'])
        self.assertEqual(self.request('/api/facts')[1]['total'],1)

    def test_references_survive_append_review_and_server_restart(self):
        eid=self.receipt['entity_ids']['a'];fid=self.receipt['fact_ids']['transfer']
        _,entity=self.request('/api/entity?id='+eid)
        _,fact=self.request('/api/fact?id='+fid)
        entity_code,fact_code=entity['handle'],fact['handle']
        mention_code=entity['mentions'][0]['handle']
        self.assertRegex(entity_code,r'^E\d{6}$');self.assertRegex(fact_code,r'^F\d{6}$')
        self.assertEqual(fact['subject_handle'],entity_code)
        _,target=self.request('/api/resolve?code='+fact_code.lower())
        self.assertEqual(target['record_id'],fid)
        self.assertEqual(target['handle'],fact_code)
        _,mention=self.request('/api/resolve?code='+mention_code)
        self.assertEqual(mention['entity_id'],eid)
        _,paragraph=self.request('/api/resolve?code=P1-1')
        self.assertEqual(paragraph['paragraph_id'],'SU-000001-P0001')
        next_lease=self.reg.claim('append-web-reference')[0]
        self.reg.submit(next_lease['task_id'],next_lease['lease_token'],batch(next_lease['unit_id'],'new-batch'))
        self.reg.review(fid,'rejected','synthetic-test','编号在否决后仍应可追溯',0)
        restarted=ReviewData(self.reg)
        self.assertEqual(restarted.handles.resolve(entity_code)['record_id'],eid)
        self.assertEqual(restarted.handles.resolve(fact_code)['record_id'],fid)
        _,after=self.request('/api/fact?id='+fid)
        self.assertEqual(after['handle'],fact_code)
        self.assertEqual(after['review_state'],'rejected')
        _,hits=self.request('/api/entities?q='+entity_code)
        self.assertEqual([r['entity_id'] for r in hits['items']],[eid])
        self.assertEqual(self.request('/api/resolve?code=P999-999')[0],400)

if __name__=='__main__':unittest.main()
