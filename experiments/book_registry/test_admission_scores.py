"""Scores are persisted independently from factual decisions; no model calls."""
import copy
import unittest
import uuid
from test_registry import fixture,batch,RUN_DIR
from review_handles import ReviewHandles
from admission_scores import start_run,save_batch,finish_run,list_scores,latest_for_entity,validate_result,generation


class AdmissionScoreTests(unittest.TestCase):
    def setUp(self):
        self.reg=fixture(RUN_DIR/uuid.uuid4().hex/'scores.sqlite3')
        self.reg.enqueue();lease=self.reg.claim('synthetic-scores')[0]
        receipt=self.reg.submit(lease['task_id'],lease['lease_token'],batch(lease['unit_id']))
        self.ids=receipt['entity_ids'];handles=ReviewHandles(self.reg)
        with self.reg.connection() as con:
            codes=dict(con.execute("SELECT record_id,handle FROM review_handles WHERE record_type='entity'"))
            source=con.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0]
        entries=[{'entity_id':eid,'handle':codes[eid]} for eid in self.ids.values()]
        self.packet={'targets':entries,'entities':entries,'source_sha256':source,'source':[{'paragraph_id':'SU-000001-P0001'}]}
        self.result={'scores':[{'handle':e['handle'],'p_need':p,'recommendation':'keep','reason':'合成测试',
            'merge_target':None,'preserve_note':'','evidence_paragraph_ids':['SU-000001-P0001'],'context_gap':None}
            for e,p in zip(entries,(0.0,0.9))]}

    def test_append_preserves_facts_zero_and_history(self):
        with self.reg.connection() as con:before=[tuple(r) for r in con.execute('SELECT * FROM facts')]
        start_run(self.reg,'trial',2,RUN_DIR)
        save_batch(self.reg,'trial','batch',self.packet,self.result,'hash')
        self.assertEqual(finish_run(self.reg,'trial')['status'],'complete')
        with self.reg.connection() as con:
            data=list_scores(con)
            self.assertEqual([s['p_need'] for s in data['items']],[0.0,0.9])
            self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM facts')],before)
            self.assertFalse(data['run']['threshold_enabled'])
        start_run(self.reg,'later',2,RUN_DIR)
        result=copy.deepcopy(self.result);result['scores'][0]['p_need']=0.4
        save_batch(self.reg,'later','batch',self.packet,result,'hash2')
        with self.reg.connection() as con:
            self.assertEqual(latest_for_entity(con,self.ids['a'])['p_need'],0.4)
            self.assertEqual(list_scores(con,'trial')['items'][0]['p_need'],0.0)
        with self.assertRaises(ValueError):save_batch(self.reg,'trial','batch',self.packet,self.result,'hash')

    def test_invalid_results_never_store_partial_batch(self):
        start_run(self.reg,'trial',2,RUN_DIR)
        for bad in (float('nan'),1.1,-0.1,True):
            result=copy.deepcopy(self.result);result['scores'][1]['p_need']=bad
            with self.assertRaises(ValueError):save_batch(self.reg,'trial','batch',self.packet,result,'hash')
        result=copy.deepcopy(self.result);result['scores'][1]=result['scores'][0]
        with self.assertRaises(ValueError):validate_result(self.packet,result)
        result=copy.deepcopy(self.result);result['scores'][0]['evidence_paragraph_ids']=['invented']
        with self.assertRaises(ValueError):validate_result(self.packet,result)
        self.assertEqual(finish_run(self.reg,'trial')['scored'],0)

    def test_merge_keeps_unperformed_transfer_explicit(self):
        result=copy.deepcopy(self.result);item=result['scores'][0]
        item.update(recommendation='merge',merge_target=self.result['scores'][1]['handle'],preserve_note='保留交付内容')
        validate_result(self.packet,result)
        item['merge_target']=item['handle']
        with self.assertRaises(ValueError):validate_result(self.packet,result)

    def test_null_is_unscored_review_not_zero(self):
        start_run(self.reg,'trial',2,RUN_DIR)
        self.result['scores'][0].update(p_need=None,recommendation='review',context_gap='需语境')
        save_batch(self.reg,'trial','batch',self.packet,self.result,'hash')
        with self.reg.connection() as con:
            self.assertEqual([x['p_need'] for x in list_scores(con)['items']],[0.9,None])
            self.assertNotEqual(generation(con),'0:0:0')


if __name__=='__main__':unittest.main()
