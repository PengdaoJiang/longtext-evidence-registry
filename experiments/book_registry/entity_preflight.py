"""Global lookup before creation; identity decisions remain explicit and auditable."""
from copy import deepcopy
import hashlib
import json
import re

VERSION = "global-before-create-v1"
TRANSIENT = {"event", "scene", "utterance", "proposition", "state", "text_feature"}
PRONOUNS = {"他", "她", "它", "我", "你", "他们", "她们", "我们", "你们", "自己", "对方", "此人", "众人"}


class IdentityLookupChanged(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def lookup_proposals(reg, payload, con=None, evidence=True, limit=6):
    if con is None:
        with reg.connection() as connection:
            return lookup_proposals(reg, payload, connection, evidence, limit)
    entries = {e["ref"]: e for e in payload["entries"]}
    checks = []
    for entry in payload["entries"]:
        if entry["existing_id"]:
            continue
        terms = {entry["name"].strip(), re.sub(r"[（(].*?[）)]", "", entry["name"]).strip()}
        terms.update(m["span"]["quote"].strip() for m in payload["mentions"] if m["entity"] == entry["ref"])
        terms = sorted(t for t in terms if t and t not in PRONOUNS and len(t) <= 120)
        related = set()
        for f in payload["facts"]:
            if f["type"] in ("relation", "identity"):
                other = f["object"] if f["subject"] == entry["ref"] else f["subject"] if f["object"] == entry["ref"] else None
                if other in entries and entries[other]["existing_id"]:
                    related.add(entries[other]["existing_id"])
        # Exact names, normalized descriptive labels, registered aliases and
        # actual prior mention surfaces all search the whole registry.
        kinds = [entry["kind"]]
        if entry["kind"] in ("group", "person_group", "family_unit"):
            kinds = ["group", "person_group", "family_unit"]
        units = sorted({s["paragraph_id"].split("-P")[0] for s in entry["evidence"]})
        rows = [dict(r) for r in con.execute("WITH " + reg.name_surfaces() + """,
          terms AS (SELECT value AS term FROM json_each(?)),
          name_hits AS (
            SELECT n.entity_id,max(CASE WHEN n.surface=t.term THEN 150 ELSE 60 END) AS score
            FROM name_surfaces n JOIN terms t ON n.surface=t.term OR
              (length(t.term)>=2 AND length(n.surface)>=2 AND
               (instr(n.surface,t.term)>0 OR instr(t.term,n.surface)>0)) GROUP BY n.entity_id
          ), mention_hits AS (
            SELECT m.entity_id,80 AS score FROM mentions m JOIN evidence ev USING(evidence_id)
            WHERE ev.quote IN (SELECT term FROM terms)
              AND NOT EXISTS (SELECT 1 FROM record_decisions rd WHERE rd.record_type='mention' AND rd.record_id=m.mention_id AND rd.state='rejected')
            GROUP BY m.entity_id
          ), neighbours AS (
            SELECT f.object_id AS entity_id FROM facts f WHERE f.subject_id IN (SELECT value FROM json_each(?))
              AND f.review_state='accepted' AND f.polarity='positive' AND f.modality='actual'
              AND f.fact_type IN ('relation','identity') AND f.object_id IS NOT NULL
            UNION
            SELECT f.subject_id FROM facts f WHERE f.object_id IN (SELECT value FROM json_each(?))
              AND f.review_state='accepted' AND f.polarity='positive' AND f.modality='actual'
              AND f.fact_type IN ('relation','identity')
          ), local_entities AS (
            SELECT DISTINCT ee.entity_id FROM entity_evidence ee JOIN evidence ev USING(evidence_id)
            JOIN paragraphs p USING(paragraph_id) WHERE p.unit_id IN (SELECT value FROM json_each(?))
          )
          SELECT e.entity_id,e.kind,e.label,e.status,
            coalesce(n.score,0)+coalesce(m.score,0)+
            100*(e.entity_id IN (SELECT entity_id FROM neighbours) AND e.kind NOT IN ('event','scene','utterance','proposition','state','text_feature'))+
            10*(e.entity_id IN (SELECT entity_id FROM local_entities)) AS score
          FROM entities e LEFT JOIN name_hits n USING(entity_id) LEFT JOIN mention_hits m USING(entity_id)
          WHERE (e.kind IN (SELECT value FROM json_each(?)) OR coalesce(n.score,0)>=150)
            AND (n.entity_id IS NOT NULL OR m.entity_id IS NOT NULL OR
              (e.entity_id IN (SELECT entity_id FROM neighbours) AND e.kind NOT IN ('event','scene','utterance','proposition','state','text_feature')))
            AND NOT EXISTS (SELECT 1 FROM record_decisions rd WHERE rd.record_type='entity' AND rd.record_id=e.entity_id AND rd.state='rejected')
          ORDER BY score DESC,e.entity_id
          """, (json.dumps(terms), json.dumps(sorted(related)), json.dumps(sorted(related)), json.dumps(units), json.dumps(kinds)))]
        all_ids = sorted(r["entity_id"] for r in rows)
        candidates = rows[:limit]
        if evidence:
            for c in candidates:
                c["names"] = sorted({n["surface"] for n in reg.entity_names(con, c["entity_id"])})[:12]
                c["evidence"] = [dict(r) for r in con.execute("""SELECT ev.paragraph_id,ev.quote,p.text AS paragraph_text
                    FROM entity_evidence ee JOIN evidence ev USING(evidence_id) JOIN paragraphs p USING(paragraph_id)
                    WHERE ee.entity_id=? ORDER BY (p.unit_id IN (SELECT value FROM json_each(?))) DESC,p.char_start LIMIT 2""",
                    (c["entity_id"], json.dumps(units)))]
                c["relations_to_known_entries"] = [dict(r) for r in con.execute("""SELECT subject_id,predicate,object_id,review_state
                    FROM facts WHERE review_state='accepted' AND polarity='positive' AND modality='actual' AND
                    ((subject_id=? AND object_id IN (SELECT value FROM json_each(?))) OR
                     (object_id=? AND subject_id IN (SELECT value FROM json_each(?)))) LIMIT 8""",
                    (c["entity_id"], json.dumps(sorted(related)), c["entity_id"], json.dumps(sorted(related))))]
        checks.append({"ref": entry["ref"], "proposed_kind": entry["kind"], "proposed_name": entry["name"],
            "terms": terms, "match_count": len(rows), "candidate_fingerprint": digest(all_ids),
            "truncated": len(rows)>limit, "candidates": candidates})
    return checks


def decision_schema(checks):
    ids = sorted({c["entity_id"] for x in checks for c in x["candidates"]})
    return {"type": "object", "properties": {"decisions": {"type": "array", "items": {
        "type": "object", "properties": {"ref": {"type": "string", "enum": [x["ref"] for x in checks]},
        "action": {"type": "string", "enum": ["reuse", "new_distinct", "unresolved"]},
        "existing_id": {"anyOf": [{"type": "string", "enum": ids}, {"type": "null"}]} if ids else {"type": "null"},
        "reason": {"type": "string"}}, "required": ["ref", "action", "existing_id", "reason"], "additionalProperties": False}}},
        "required": ["decisions"], "additionalProperties": False}


def apply_decisions(payload, checks, document):
    matched = {c["ref"]: c for c in checks if c["match_count"]}
    decisions = document.get("decisions", [])
    if len(decisions) != len(matched) or {d["ref"] for d in decisions} != set(matched):
        raise ValueError("Every matched new proposal requires one explicit identity decision")
    output = deepcopy(payload)
    entries = {e["ref"]: e for e in output["entries"]}
    for d in decisions:
        if not d["reason"].strip():
            raise ValueError("Identity decision requires source-based reasoning")
        c = matched[d["ref"]]
        if d["action"] == "reuse":
            target = next((x for x in c["candidates"] if x["entity_id"] == d["existing_id"]), None)
            if target is None:
                raise ValueError("Identity reuse must select a returned candidate for this proposal")
            entries[d["ref"]].update(existing_id=target["entity_id"], name=target["label"], kind=target["kind"])
        elif d["action"] in ("new_distinct", "unresolved") and d["existing_id"] is None:
            if c["truncated"] and d["action"] != "unresolved":
                raise ValueError("Truncated candidate lookup cannot establish a distinct new entity")
            if d["action"] == "unresolved":
                output["unresolved"].append({"paragraph_id": entries[d["ref"]]["evidence"][0]["paragraph_id"],
                    "description": "身份候选未决；已全库查询，仍新建临时条目：" + d["reason"]})
        else:
            raise ValueError("Invalid identity action or unexpected existing_id")
    return output


def make_proof(reg, payload, decisions, reviewed_checks, con=None):
    checks = lookup_proposals(reg, payload, con=con, evidence=False)
    reviewed = {c["ref"]: c for c in reviewed_checks}
    by_ref = {d["ref"]: d for d in decisions.get("decisions", [])}
    for c in checks:
        if c["ref"] not in reviewed or c["candidate_fingerprint"] != reviewed[c["ref"]]["candidate_fingerprint"]:
            raise IdentityLookupChanged("Identity candidates changed after review; repeat lookup")
        c["decision"] = by_ref.get(c["ref"], {"ref": c["ref"], "action": "no_candidate_found", "existing_id": None,
            "reason": "全库已查询名称、别名、提及及与已知端点的确认关系；本次检索没有匹配候选，不承诺绝对不存在。"})
        if c["match_count"] and c["decision"]["action"] not in ("new_distinct", "unresolved"):
            raise IdentityLookupChanged("New candidate appeared before registration; repeat identity review")
    return {"version": VERSION, "payload_sha256": digest(payload), "checks": checks}


def verify_proof(reg, payload, proof, con):
    if not proof or proof.get("version") != VERSION or proof.get("payload_sha256") != digest(payload):
        raise ValueError("New entities require a global pre-registration lookup bound to this exact payload")
    live = lookup_proposals(reg, payload, con=con, evidence=False)
    old = {c["ref"]: c for c in proof["checks"]}
    if len(old) != len(proof["checks"]) or set(old) != {c["ref"] for c in live}:
        raise ValueError("Pre-registration lookup omitted a proposed new entry")
    for c in live:
        checked = old[c["ref"]]
        if c["candidate_fingerprint"] != checked["candidate_fingerprint"]:
            raise IdentityLookupChanged("Another commit changed identity candidates; repeat identity review")
        decision = checked["decision"]
        if (decision.get("ref") != c["ref"] or decision.get("existing_id") is not None
                or not str(decision.get("reason", "")).strip()
                or decision.get("action") not in ("no_candidate_found", "new_distinct", "unresolved")):
            raise ValueError("Invalid or unexplained identity decision")
        if c["match_count"] and decision["action"] not in ("new_distinct", "unresolved"):
            raise ValueError("Matched entity creation requires an explicit identity decision")
        if c["truncated"] and decision["action"] != "unresolved":
            raise ValueError("Truncated identity lookup must remain unresolved")


def main():
    """Read-only manual/recovery path; never infer identity or submit records."""
    import argparse
    from pathlib import Path
    from registry import Registry, DEFAULT_DB, local_path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--file", type=Path, required=True, help="Prepared payload with exact spans")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checks", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--proof", type=Path)
    args = parser.parse_args()
    load = lambda path: json.loads(local_path(path).read_text(encoding="utf-8-sig"))
    def save(path, value):
        destination = local_path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
    reg, payload = Registry(args.db), load(args.file)
    if args.decisions:
        if not args.checks or not args.proof:
            parser.error("Explicit decisions require their saved --checks and an output --proof")
        checks, decisions = load(args.checks), load(args.decisions)
        result = apply_decisions(payload, checks, decisions)
        proof = make_proof(reg, result, decisions, checks)
        save(args.output, result)
        save(args.proof, proof)
        print(json.dumps({"prepared_identity": str(args.output), "proof": str(args.proof), "submitted": False}))
    else:
        save(args.output, lookup_proposals(reg, payload))
        print(json.dumps({"lookup": str(args.output), "submitted": False}))


if __name__ == "__main__":
    main()
