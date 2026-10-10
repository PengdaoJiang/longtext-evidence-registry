"""Loopback-only live registry browser. The UI displays stored data; feedback is handled in the chat."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlparse, parse_qs

from registry import Registry, DEFAULT_DB, ROOT
from review_handles import ReviewHandles
import admission_scores

UI = Path(__file__).with_name("ui") / "dist"
TRANSIENT = ("event", "scene", "utterance", "proposition", "state", "text_feature")
FACT_SELECT = """SELECT f.*,s.label AS subject_label,s.kind AS subject_kind,
 o.label AS object_label,o.kind AS object_kind,sp.label AS speaker_label,
 (SELECT p.unit_id FROM fact_evidence fe JOIN evidence ev USING(evidence_id)
  JOIN paragraphs p USING(paragraph_id) WHERE fe.fact_id=f.fact_id ORDER BY ev.global_start LIMIT 1) AS unit_id
 FROM facts f JOIN entities s ON s.entity_id=f.subject_id
 LEFT JOIN entities o ON o.entity_id=f.object_id LEFT JOIN entities sp ON sp.entity_id=f.speaker_id"""


def unpack(row):
    value = dict(row)
    value["value"] = json.loads(value.pop("value_json")) if value.get("value_json") is not None else None
    value.pop("value_json", None)
    return value


class ReviewData:
    def __init__(self, registry):
        self.reg = registry
        self.handles = ReviewHandles(registry)

    def fact_where(self, state="active", entity="", unit="", query=""):
        clauses, params = [], []
        if state == "active":
            clauses += ["f.review_state<>'rejected'", self.reg.visible("entity", "f.subject_id"), self.reg.visible("entity", "f.object_id")]
        elif state in ("accepted", "pending", "rejected"):
            clauses.append("f.review_state=?"); params.append(state)
        elif state != "all":
            raise ValueError("Unknown review filter")
        if entity:
            clauses.append("(f.subject_id=? OR f.object_id=? OR f.speaker_id=?)"); params.extend([entity]*3)
        if unit:
            clauses.append("EXISTS (SELECT 1 FROM fact_evidence fe JOIN evidence ev USING(evidence_id) JOIN paragraphs p USING(paragraph_id) WHERE fe.fact_id=f.fact_id AND p.unit_id=?)")
            params.append(unit)
        if query:
            clauses.append("(instr(s.label,?)>0 OR instr(coalesce(o.label,''),?)>0 OR instr(f.predicate,?)>0 OR instr(coalesce(f.value_json,''),?)>0)")
            params.extend([query]*4)
        return " AND ".join(clauses) or "1", params

    def status(self):
        with self.reg.connection() as con:
            counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("entities", "facts", "submissions", "paragraphs", "units")}
            counts["covered"] = con.execute("SELECT count(DISTINCT paragraph_id) FROM coverage").fetchone()[0]
            states = dict(con.execute("SELECT review_state,count(*) FROM facts GROUP BY review_state"))
            task_states = dict(con.execute("SELECT state,count(*) FROM tasks GROUP BY state"))
            jobs = [dict(r) for r in con.execute("""SELECT t.unit_id,u.heading,t.owner,t.state,
             t.first_paragraph,t.last_paragraph,t.lease_until,
             (SELECT count(DISTINCT c.paragraph_id) FROM coverage c WHERE c.task_id=t.task_id) AS covered
             FROM tasks t JOIN units u USING(unit_id) WHERE t.state='leased' AND t.lease_until>? ORDER BY u.ordinal""",(time.time(),))]
            revision = con.execute("SELECT coalesce(max(review_id),0) FROM reviews").fetchone()[0]
            record_revision = con.execute("SELECT count(*) FROM record_review_log").fetchone()[0]
            score_generation = admission_scores.generation(con)
            return {"counts": counts, "review": states, "jobs": jobs, "task_states": task_states,
                "generation": f"{counts['submissions']}:{revision}:{record_revision}:{score_generation}",
                "source_sha256": con.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0], "now": time.time()}

    def entities(self, query="", kind="", limit=80, offset=0):
        with self.reg.connection() as con:
            where = self.reg.visible("entity", "e.entity_id") + " AND (?='' OR e.kind=?)"
            params = [kind, kind]
            prefix = "WITH " + self.reg.name_surfaces() + " "
            if query:
                where += " AND (EXISTS (SELECT 1 FROM name_surfaces n WHERE n.entity_id=e.entity_id AND instr(n.surface,?)>0) OR EXISTS (SELECT 1 FROM review_handles h WHERE h.record_type='entity' AND h.record_id=e.entity_id AND h.handle=upper(?)))"
                params.extend([query,query])
            total = con.execute(prefix+"SELECT count(*) FROM entities e WHERE "+where, params).fetchone()[0]
            sql = prefix+"""SELECT e.entity_id,e.label,e.kind,e.status,
              (SELECT count(*) FROM mentions m WHERE m.entity_id=e.entity_id) AS mentions,
              (SELECT count(*) FROM facts f WHERE (f.subject_id=e.entity_id OR f.object_id=e.entity_id OR f.speaker_id=e.entity_id) AND f.review_state<>'rejected') AS links,
              EXISTS(SELECT 1 FROM name_surfaces n WHERE n.entity_id=e.entity_id AND n.surface=?) AS exact
              FROM entities e WHERE """+where+" ORDER BY exact DESC,links DESC,e.label LIMIT ? OFFSET ?"
            return {"total": total, "items": [dict(r) for r in con.execute(sql,[query]+params+[limit,offset])], "offset": offset}

    def entity(self, eid):
        value = self.reg.entry(eid, limit=60, review_state="all")
        value.pop("facts", None)
        with self.reg.connection() as con:
            value["evidence"] = [dict(r) for r in con.execute("""SELECT ev.*,p.text AS paragraph_text FROM entity_evidence ee JOIN evidence ev USING(evidence_id)
                JOIN paragraphs p USING(paragraph_id) WHERE ee.entity_id=? ORDER BY ev.global_start LIMIT 12""", (eid,))]
            value["mentions"] = [dict(r) for r in con.execute("""SELECT m.*,ev.paragraph_id,ev.quote,ev.start,ev.end,p.text AS paragraph_text,
                coalesce(rd.state,'pending') AS review_state FROM mentions m JOIN evidence ev USING(evidence_id) JOIN paragraphs p USING(paragraph_id)
                LEFT JOIN record_decisions rd ON rd.record_type='mention' AND rd.record_id=m.mention_id
                WHERE m.entity_id=? ORDER BY ev.global_start LIMIT 40""",(eid,))]
            value["reviews"] = [dict(r) for r in con.execute("SELECT * FROM record_review_log WHERE record_type='entity' AND record_id=? ORDER BY revision DESC",(eid,))]
            value["identity_links"] = [unpack(r) for r in con.execute(FACT_SELECT+" WHERE f.fact_type='identity' AND (f.subject_id=? OR f.object_id=?) ORDER BY f.narrative_start",(eid,eid))]
            value["admission_score"] = admission_scores.latest_for_entity(con,eid)
        return value

    def scores(self, run_id='', order='asc', offset=0, limit=60):
        with self.reg.connection() as con:
            return admission_scores.list_scores(con,run_id,order,offset,limit)

    def facts(self, state="active", entity="", unit="", query="", offset=0, limit=60, paragraph=""):
        where, params = self.fact_where(state, entity, unit, query)
        if paragraph:
            where += " AND EXISTS (SELECT 1 FROM fact_evidence fe JOIN evidence ev USING(evidence_id) WHERE fe.fact_id=f.fact_id AND ev.paragraph_id=?)"
            params.append(paragraph)
        with self.reg.connection() as con:
            total = con.execute("SELECT count(*) FROM ("+FACT_SELECT+" WHERE "+where+")",params).fetchone()[0]
            rows = [unpack(r) for r in con.execute(FACT_SELECT+" WHERE "+where+" ORDER BY f.narrative_start,f.narrative_end,f.fact_id LIMIT ? OFFSET ?",params+[limit,offset])]
        return {"total": total, "offset": offset, "items": rows}

    def fact(self, fid):
        with self.reg.connection() as con:
            row = con.execute(FACT_SELECT+" WHERE f.fact_id=?",(fid,)).fetchone()
            if not row: raise ValueError("Fact not found")
            value = unpack(row)
            value["evidence"] = [dict(r) for r in con.execute("""SELECT ev.*,p.text AS paragraph_text FROM fact_evidence fe JOIN evidence ev USING(evidence_id)
              JOIN paragraphs p USING(paragraph_id) WHERE fe.fact_id=? ORDER BY ev.global_start""",(fid,))]
            value["context_support"] = [dict(r) for r in con.execute("""SELECT DISTINCT ev.*,p.text AS paragraph_text,cs.note FROM context_support cs JOIN evidence ev USING(evidence_id)
              JOIN paragraphs p USING(paragraph_id) WHERE cs.record_type='fact' AND cs.record_id=? ORDER BY ev.global_start""",(fid,))]
            value["reviews"] = [dict(r) for r in con.execute("SELECT * FROM reviews WHERE fact_id=? ORDER BY revision DESC",(fid,))]
            value["provenance"] = [dict(r) for r in con.execute("""SELECT s.submission_id,s.batch_id,s.created_at,t.owner,t.unit_id,
              coalesce(json_extract(s.payload_json,'$.payload.model'),json_extract(s.payload_json,'$.model')) AS model
              FROM provenance pr JOIN submissions s USING(submission_id) JOIN tasks t USING(task_id)
              WHERE pr.record_type='fact' AND pr.record_id=? ORDER BY s.created_at""",(fid,))]
            return value

    def graph(self, eid, state="active", events=False):
        where, params = self.fact_where(state, eid)
        where += " AND f.object_id IS NOT NULL"
        if not events:
            kind_params = ",".join("?" for _ in TRANSIENT)
            where += f" AND s.kind NOT IN ({kind_params}) AND o.kind NOT IN ({kind_params})"
            params.extend(TRANSIENT*2)
        with self.reg.connection() as con:
            all_rows = [unpack(r) for r in con.execute(FACT_SELECT+" WHERE "+where+" ORDER BY (f.fact_type='identity') DESC,(f.review_state='accepted') DESC,f.narrative_start",params)]
            selected, ids = [], {eid}
            for f in all_rows:
                next_ids = ids | {f['subject_id'], f['object_id']}
                if len(next_ids)>28 or len(selected)>=120: continue
                ids=next_ids; selected.append(f)
            identity_where, identity_params = self.fact_where(state)
            placeholders=','.join('?' for _ in ids)
            seen={f['fact_id'] for f in selected}
            for row in con.execute(FACT_SELECT+f" WHERE {identity_where} AND f.fact_type='identity' AND f.subject_id IN ({placeholders}) AND f.object_id IN ({placeholders})",identity_params+sorted(ids)+sorted(ids)):
                if row['fact_id'] not in seen:
                    selected.append(unpack(row)); seen.add(row['fact_id'])
            nodes = [dict(r) for r in con.execute("SELECT entity_id,label,kind,status FROM entities WHERE entity_id IN ("+','.join('?' for _ in ids)+")",sorted(ids))]
            return {"nodes": nodes, "edges": selected, "total_edges": max(len(all_rows),len(selected)), "focus": eid}

    def chapters(self, offset=0, limit=32):
        with self.reg.connection() as con:
            rows = [dict(r) for r in con.execute("""SELECT u.*,
              (SELECT count(*) FROM paragraphs p WHERE p.unit_id=u.unit_id) AS paragraphs,
              (SELECT count(DISTINCT c.paragraph_id) FROM coverage c JOIN paragraphs p USING(paragraph_id) WHERE p.unit_id=u.unit_id) AS covered,
              (SELECT count(DISTINCT fe.fact_id) FROM fact_evidence fe JOIN evidence ev USING(evidence_id) JOIN paragraphs p USING(paragraph_id) WHERE p.unit_id=u.unit_id) AS facts
              FROM units u ORDER BY u.ordinal LIMIT ? OFFSET ?""",(limit,offset))]
            return {"items":rows,"offset":offset,"total":con.execute("SELECT count(*) FROM units").fetchone()[0]}

    def chapter(self, uid):
        with self.reg.connection() as con:
            row=con.execute("SELECT * FROM units WHERE unit_id=?",(uid,)).fetchone()
            if not row: raise ValueError("Chapter not found")
            value=dict(row)
            value['paragraphs']=[dict(r) for r in con.execute("""SELECT p.*,
              EXISTS(SELECT 1 FROM coverage c WHERE c.paragraph_id=p.paragraph_id) AS covered,
              (SELECT count(DISTINCT fe.fact_id) FROM evidence ev JOIN fact_evidence fe USING(evidence_id) WHERE ev.paragraph_id=p.paragraph_id) AS facts
              FROM paragraphs p WHERE p.unit_id=? ORDER BY p.ordinal""",(uid,))]
            return value


def server(reg, port=8766):
    dataset = ReviewData(reg)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if args and str(args[1] if len(args)>1 else '') not in ('200','304'): super().log_message(fmt,*args)
        def send(self, code, body, mime='application/json; charset=utf-8'):
            if code==200 and isinstance(body,dict): dataset.handles.decorate(body)
            content=json.dumps(body,ensure_ascii=False).encode() if not isinstance(body,bytes) else body
            self.send_response(code); self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(content))); self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(content)
        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def do_GET(self):
            if not self.valid_host(): return self.send(403,{'error':'Loopback host required'})
            parsed=urlparse(self.path); q={k:v[0] for k,v in parse_qs(parsed.query).items()}
            try:
                route=parsed.path
                if route.startswith('/api/'): dataset.handles.sync()
                if route=='/api/resolve': return self.send(200,dataset.handles.resolve(q.get('code','')))
                if route=='/api/status': return self.send(200,dataset.status())
                if route=='/api/entities': return self.send(200,dataset.entities(q.get('q',''),q.get('kind',''),min(100,max(1,int(q.get('limit',80)))),max(0,int(q.get('offset',0)))))
                if route=='/api/entity': return self.send(200,dataset.entity(q['id']))
                if route=='/api/scores': return self.send(200,dataset.scores(q.get('run',''),q.get('order','asc'),max(0,int(q.get('offset',0))),min(100,max(1,int(q.get('limit',60))))))
                if route=='/api/facts': return self.send(200,dataset.facts(q.get('state','active'),q.get('entity',''),q.get('unit',''),q.get('q',''),max(0,int(q.get('offset',0))),min(100,max(1,int(q.get('limit',60)))),q.get('paragraph','')))
                if route=='/api/fact': return self.send(200,dataset.fact(q['id']))
                if route=='/api/graph': return self.send(200,dataset.graph(q.get('id','seed:ENT-0001'),q.get('state','active'),q.get('events')=='1'))
                if route=='/api/chapters': return self.send(200,dataset.chapters(max(0,int(q.get('offset',0))),min(100,max(1,int(q.get('limit',32))))))
                if route=='/api/chapter': return self.send(200,dataset.chapter(q.get('id','SU-000001')))
                target=(UI/('index.html' if route=='/' else route.lstrip('/'))).resolve()
                if not target.is_relative_to(UI.resolve()) or not target.is_file(): return self.send(404,{'error':'Not found'})
                return self.send(200,target.read_bytes(),mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            except (ValueError,KeyError) as exc: self.send(400,{'error':str(exc)})
            except sqlite3.Error as exc: self.send(503,{'error':str(exc)})
        def do_POST(self):
            self.send(405,{'error':'网页仅浏览；请在聊天中引用编号反馈。'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB); p.add_argument('--port',type=int,default=8766)
    p.add_argument('--resolve',help='Look up the stable review code and its evidence without starting a server')
    args=p.parse_args()
    if args.resolve:
        dataset=ReviewData(Registry(args.db)); target=dataset.handles.resolve(args.resolve)
        if target['record_type']=='fact': detail=dataset.fact(target['record_id'])
        elif target['record_type']=='entity': detail=dataset.entity(target['record_id'])
        elif target['record_type']=='paragraph':
            detail=next(p for p in dataset.chapter(target['unit_id'])['paragraphs'] if p['paragraph_id']==target['record_id'])
        else: detail=dataset.entity(target['entity_id'])
        print(json.dumps(dataset.handles.decorate({'target':target,'detail':detail}),ensure_ascii=False,indent=2))
        raise SystemExit(0)
    app=server(Registry(args.db),args.port)
    print(f'Registry review: http://127.0.0.1:{app.server_port}',flush=True)
    app.serve_forever()
