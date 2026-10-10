"""Read-only, compact source/record view for supervised literary review."""
import argparse
import json
from pathlib import Path
from registry import Registry, local_path


def render(directory):
    directory = local_path(directory)
    payload_file = directory / "payload.json"
    if not payload_file.exists():
        payload_file = directory / "recovery-payload.json"
    raw = json.loads(payload_file.read_text(encoding="utf-8"))
    notice = ""
    receipt_file = directory / "receipt.json"
    if not receipt_file.exists():
        receipt_file = directory / "recovery-receipt.json"
    if receipt_file.exists():
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        receipt = receipt.get("receipt", receipt)
        with Registry().connection() as con:
            saved = con.execute("SELECT payload_json FROM submissions WHERE submission_id=?", (receipt["submission_id"],)).fetchone()
        if saved:
            committed = json.loads(saved[0])
            committed = committed.get("payload", committed)
            if raw != committed:
                notice = "NOTICE: Local payload differs; reviewing the immutable committed submission."
            raw = committed
    entities = {e["ref"]: e for e in raw["entries"]}
    paragraph_ids = sorted({e["paragraph_id"] for x in raw["entries"] + raw["facts"] for e in x["evidence"]})
    lines = [str(directory), notice] if notice else [str(directory)]
    with Registry().connection() as con:
        for pid in paragraph_ids:
            p = con.execute("SELECT text FROM paragraphs WHERE paragraph_id=?", (pid,)).fetchone()
            lines.append(f"SOURCE {pid}: {p[0]}")
    lines.append("ENTRIES")
    for ref, e in entities.items():
        lines.append(f"{ref}: {e['kind']} | {e['name']} | {e['existing_id'] or 'NEW'}")
    lines.append("FACTS")
    for f in raw["facts"]:
        evidence = ",".join(e["paragraph_id"].split("-P")[-1] for e in f["evidence"])
        qualifiers = "/".join(str(f[k]) for k in ("polarity", "modality", "attribution", "speaker", "frame", "story_time"))
        lines.append(f"{f['ref']} [{evidence}] {f['subject']} --{f['predicate']}--> {f['object'] or json.dumps(f['value'],ensure_ascii=False)} [{qualifiers}] {f['note']}")
    lines.append("UNRESOLVED " + json.dumps(raw["unresolved"], ensure_ascii=False))
    support = directory / "context-support.json"
    if not support.exists():
        support = directory / "recovery-context-support.json"
    if support.exists():
        lines.append("CONTEXT SUPPORT " + support.read_text(encoding="utf-8"))
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(render(args.directory))
