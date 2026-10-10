"""Append-only admission scores. Scores never change facts or enforce a threshold."""
import json
import math
import time
from pathlib import Path

METHOD = 'independent-retention-score-v1'
MODEL = 'gpt-6-luna'
ACTIONS = ('keep', 'merge', 'source_only', 'review')


def available(con):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='admission_score_runs'").fetchone())


def upgrade(reg):
    with reg.connection(True) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS admission_score_runs(
          run_id TEXT PRIMARY KEY, method TEXT NOT NULL, model TEXT NOT NULL,
          reasoning_effort TEXT NOT NULL, source_sha256 TEXT NOT NULL,
          expected INTEGER NOT NULL, status TEXT NOT NULL,
          artifact_dir TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL)''')
        con.execute('''CREATE TABLE IF NOT EXISTS admission_scores(
          score_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES admission_score_runs(run_id),
          entity_id TEXT NOT NULL REFERENCES entities(entity_id), batch_id TEXT NOT NULL,
          p_need REAL CHECK(p_need IS NULL OR (p_need>=0 AND p_need<=1)),
          recommendation TEXT NOT NULL CHECK(recommendation IN ('keep','merge','source_only','review')),
          reason TEXT NOT NULL, merge_target_id TEXT REFERENCES entities(entity_id),
          preserve_note TEXT NOT NULL, evidence_json TEXT NOT NULL, context_gap TEXT,
          input_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
          UNIQUE(run_id,entity_id))''')
        con.execute('CREATE INDEX IF NOT EXISTS admission_scores_entity ON admission_scores(entity_id,score_id)')


def start_run(reg, run_id, expected, artifact_dir):
    upgrade(reg)
    with reg.connection(True) as con:
        source=con.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0]
        con.execute('INSERT INTO admission_score_runs VALUES (?,?,?,?,?,?,?,?,?,NULL)',
            (run_id,METHOD,MODEL,'max',source,expected,'running',str(Path(artifact_dir).resolve()),time.time()))


def validate_result(packet, result):
    items=result.get('scores',[])
    expected={x['handle']:x['entity_id'] for x in packet['targets']}
    handles=[x.get('handle') for x in items]
    if len(handles)!=len(set(handles)) or set(handles)!=set(expected):
        raise ValueError('Score output must contain each target exactly once')
    known={x['handle']:x['entity_id'] for x in packet['entities']}
    paragraphs={x['paragraph_id'] for x in packet['source']}
    for item in items:
        p=item['p_need']
        if p is not None and (isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1):
            raise ValueError('p_need must be finite and between zero and one, or null')
        if item['recommendation'] not in ACTIONS or (p is None and item['recommendation']!='review'):
            raise ValueError('Invalid score recommendation')
        target=item['merge_target']
        if target is not None and (target not in known or target==item['handle']):
            raise ValueError('Merge target must be a different supplied entity')
        if item['recommendation']!='merge' and target is not None:
            raise ValueError('Only merge recommendations can name a merge target')
        if item['recommendation']=='merge' and not item['preserve_note'].strip():
            raise ValueError('Merge must state what information needs to survive')
        if not item['reason'].strip() or not item['evidence_paragraph_ids'] or not set(item['evidence_paragraph_ids'])<=paragraphs:
            raise ValueError('Score requires a reason and supplied source anchors')
    return items,expected,known


def save_batch(reg, run_id, batch_id, packet, result, input_sha256):
    items,expected,known=validate_result(packet,result)
    with reg.connection(True) as con:
        run=con.execute('SELECT * FROM admission_score_runs WHERE run_id=?',(run_id,)).fetchone()
        if run is None or run['status']!='running' or run['source_sha256']!=packet['source_sha256']:
            raise ValueError('Run/source mismatch or finished run')
        for item in items:
            con.execute('''INSERT INTO admission_scores(run_id,entity_id,batch_id,p_need,recommendation,
              reason,merge_target_id,preserve_note,evidence_json,context_gap,input_sha256,created_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
              (run_id,expected[item['handle']],batch_id,item['p_need'],item['recommendation'],item['reason'],
               known.get(item['merge_target']),item['preserve_note'],json.dumps(item['evidence_paragraph_ids']),
               item['context_gap'],input_sha256,time.time()))


def finish_run(reg, run_id):
    with reg.connection(True) as con:
        count=con.execute('SELECT count(*) FROM admission_scores WHERE run_id=?',(run_id,)).fetchone()[0]
        expected=con.execute('SELECT expected FROM admission_score_runs WHERE run_id=?',(run_id,)).fetchone()[0]
        status='complete' if count==expected else 'partial'
        con.execute('UPDATE admission_score_runs SET status=?,finished_at=? WHERE run_id=?',(status,time.time(),run_id))
    return {'status':status,'scored':count,'expected':expected}


def generation(con):
    if not available(con): return '0:0:0'
    return ':'.join(str(x) for x in con.execute('''SELECT
      (SELECT count(*) FROM admission_score_runs),coalesce(max(score_id),0),
      (SELECT count(*) FROM admission_score_runs WHERE finished_at IS NOT NULL) FROM admission_scores''').fetchone())


SELECT='''SELECT a.*,r.method,r.model,r.reasoning_effort,r.source_sha256,
  e.label,e.kind,m.label AS merge_target_label,h.handle AS merge_target_handle
  FROM admission_scores a JOIN admission_score_runs r USING(run_id)
  JOIN entities e USING(entity_id) LEFT JOIN entities m ON m.entity_id=a.merge_target_id
  LEFT JOIN review_handles h ON h.record_type='entity' AND h.record_id=a.merge_target_id'''


def unpack(row):
    value=dict(row)
    value['evidence_paragraph_ids']=json.loads(value.pop('evidence_json'))
    value['calibrated']=False
    value['threshold_enabled']=False
    return value


def latest_for_entity(con,eid):
    if not available(con): return None
    row=con.execute(SELECT+' WHERE a.entity_id=? ORDER BY a.score_id DESC LIMIT 1',(eid,)).fetchone()
    return unpack(row) if row else None


def list_scores(con,run_id='',order='asc',offset=0,limit=60):
    if not available(con): return {'run':None,'items':[],'total':0,'offset':offset}
    run=con.execute('SELECT * FROM admission_score_runs '+('WHERE run_id=?' if run_id else 'ORDER BY created_at DESC LIMIT 1'),(run_id,) if run_id else ()).fetchone()
    if not run: return {'run':None,'items':[],'total':0,'offset':offset}
    run=dict(run)
    run['calibrated']=False;run['threshold_enabled']=False
    total=con.execute('SELECT count(*) FROM admission_scores WHERE run_id=?',(run['run_id'],)).fetchone()[0]
    direction='DESC' if order=='desc' else 'ASC'
    rows=con.execute(SELECT+f' WHERE a.run_id=? ORDER BY (a.p_need IS NULL),a.p_need {direction},a.entity_id LIMIT ? OFFSET ?',
        (run['run_id'],limit,offset))
    return {'run':run,'items':[unpack(r) for r in rows],'total':total,'offset':offset}
