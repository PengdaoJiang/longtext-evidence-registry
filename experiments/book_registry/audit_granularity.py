"""Read-only inventory of structural admission candidates, not an automatic prune."""
import argparse
import json
from pathlib import Path
import time
from datetime import datetime,timezone
from registry import Registry,DEFAULT_DB,local_path
from semantic_checks import VERSION,granularity_candidates


def audit(reg):
    started=time.monotonic()
    with reg.connection() as con:
        entries=[{'ref':r['entity_id'],'name':r['label'],'kind':r['kind']}
            for r in con.execute('SELECT * FROM entities e WHERE '+reg.visible('entity','e.entity_id'))]
        known={e['ref'] for e in entries}
        facts=[{'ref':r['fact_id'],'type':r['fact_type'],'subject':r['subject_id'],
            'predicate':r['predicate'],'object':r['object_id'],'speaker':r['speaker_id']}
            for r in con.execute("SELECT * FROM facts WHERE review_state<>'rejected'")
            if all(x is None or x in known for x in (r['subject_id'],r['object_id'],r['speaker_id']))]
        handles=dict(con.execute('SELECT record_id,handle FROM review_handles')) if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_handles'").fetchone() else {}
        findings=granularity_candidates({'entries':entries,'facts':facts})
        for item in findings:
            item['handle']=handles.get(item['ref'])
            item['fact_handles']=[handles.get(fid,fid) for fid in item['fact_refs']]
            item['paragraphs']=[r[0] for r in con.execute('''SELECT DISTINCT ev.paragraph_id FROM entity_evidence ee
                JOIN evidence ev USING(evidence_id) WHERE ee.entity_id=? ORDER BY ev.global_start''',(item['ref'],))]
        source=con.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0]
    return {'created_at':datetime.now(timezone.utc).isoformat(),'method':VERSION,'source_sha256':source,
        'entities_scanned':len(entries),'facts_scanned':len(facts),'candidates':len(findings),
        'elapsed_seconds':time.monotonic()-started,'database_modified':False,
        'interpretation':'Candidates require source review for merge, raw-text-only retention, or independent admission. Counts are not proven low-value records or recall/precision scores.',
        'items':findings}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    destination=local_path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    result=audit(Registry(args.db))
    with destination.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='items'},ensure_ascii=False))
