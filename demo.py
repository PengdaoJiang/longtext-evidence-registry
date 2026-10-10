"""Offline, original synthetic corpus; no external model invocation."""
from pathlib import Path
import json,sys,uuid
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'experiments/book_registry'))
from test_registry import fixture,batch
from review_server import ReviewData
from admission_scores import upgrade

def main():
    path=ROOT/'data/demo'/('registry-'+uuid.uuid4().hex+'.sqlite3')
    reg=fixture(path,3);reg.enqueue();reg.upgrade_record_reviews();upgrade(reg)
    for task in reg.claim('offline-demonstration',3):
        reg.submit(task['task_id'],task['lease_token'],batch(task['unit_id']))
    print(json.dumps({'database':str(path),'status':ReviewData(reg).status()},ensure_ascii=False,indent=2))
    print('Open review: python experiments/book_registry/review_server.py --db "'+str(path)+'"')
if __name__=='__main__':main()
