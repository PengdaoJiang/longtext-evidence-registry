"""Apply explicit source-reviewed decisions with revision fencing; no auto-approval."""
import argparse
import json
from pathlib import Path
from registry import Registry, DEFAULT_DB, local_path


def apply(reg, document):
    results = []
    for item in document["batches"]:
        payload = json.loads(local_path(item["payload"]).read_text(encoding="utf-8"))
        receipt = json.loads(local_path(item["receipt"]).read_text(encoding="utf-8"))
        receipt = receipt.get("receipt", receipt)
        refs = {f["ref"] for f in payload["facts"]}
        if refs != set(item["decisions"]):
            raise ValueError("Every fact needs an explicit decision; no implicit acceptance")
        with reg.connection() as con:
            stored = con.execute("SELECT payload_json FROM submissions WHERE submission_id=?", (receipt["submission_id"],)).fetchone()
            committed = json.loads(stored[0]) if stored else None
            if committed and "payload" in committed:
                committed = committed["payload"]
            if committed != payload:
                raise ValueError("Review input differs from committed payload")
        for ref, decision in item["decisions"].items():
            if decision["state"] == "pending":
                continue
            results.append(reg.review(receipt["fact_ids"][ref], decision["state"],
                                      document["reviewer"], decision["reason"], decision["expected_revision"]))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = apply(Registry(args.db), json.loads(local_path(args.file).read_text(encoding="utf-8")))
    with local_path(args.output).open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({"decisions_applied": len(result)}))
