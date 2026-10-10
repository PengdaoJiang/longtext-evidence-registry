"""Bounded, read-only source access beyond the chapter being registered.

The project uses a retrospective whole-book view. Reading later chapters is
allowed; source ranges are service boundaries, not prediction cutoffs.
"""
import argparse
import json
from registry import Registry


def read_chapter(reg, unit_id, max_characters=50000):
    with reg.connection() as con:
        unit = con.execute("SELECT * FROM units WHERE unit_id=?", (unit_id,)).fetchone()
        if unit is None:
            raise ValueError("Unknown source unit")
        rows = [dict(r) for r in con.execute("SELECT paragraph_id,unit_id,ordinal,text FROM paragraphs WHERE unit_id=? ORDER BY ordinal", (unit_id,))]
    selected, size = [], 0
    for row in rows:
        if size + len(row["text"]) > max_characters:
            break
        selected.append(row)
        size += len(row["text"])
    return {"unit": dict(unit), "paragraphs": selected, "complete": len(selected) == len(rows), "characters": size}


def search_source(reg, query, max_hits=6, radius=2):
    if not query.strip() or len(query) > 100 or not 1 <= max_hits <= 10 or not 0 <= radius <= 3:
        raise ValueError("Use a short source phrase and bounded search")
    with reg.connection() as con:
        hits = [dict(r) for r in con.execute("SELECT paragraph_id,unit_id,ordinal FROM paragraphs WHERE instr(text,?)>0 ORDER BY char_start LIMIT ?", (query, max_hits+1))]
        paragraphs = {}
        for hit in hits[:max_hits]:
            for row in con.execute("SELECT paragraph_id,unit_id,ordinal,text FROM paragraphs WHERE unit_id=? AND ordinal BETWEEN ? AND ? ORDER BY ordinal", (hit["unit_id"], hit["ordinal"]-radius, hit["ordinal"]+radius)):
                paragraphs[row["paragraph_id"]] = dict(row)
    return {"query": query, "has_more": len(hits) > max_hits, "hits": hits[:max_hits], "paragraphs": list(paragraphs.values())}


def initial_context(reg, unit_id, body, max_characters=30000):
    with reg.connection() as con:
        ordinal = con.execute("SELECT ordinal FROM units WHERE unit_id=?", (unit_id,)).fetchone()[0]
        neighbours = [r[0] for r in con.execute("SELECT unit_id FROM units WHERE ordinal BETWEEN ? AND ? ORDER BY abs(ordinal-?),ordinal", (ordinal-1, ordinal+1, ordinal))]
    body_ids = {p["paragraph_id"] for p in body}
    context, inventory, size = [], [], 0
    for uid in neighbours:
        chapter = read_chapter(reg, uid, max_characters-size)
        inventory.append({"unit_id": uid, "complete": chapter["complete"]})
        for paragraph in chapter["paragraphs"]:
            if paragraph["paragraph_id"] not in body_ids:
                context.append({**paragraph, "purpose": "support_for_assigned_body"})
        size += chapter["characters"]
    return context, inventory


def extend_context(reg, packet, requests, max_characters=50000):
    if len(requests) > 4:
        raise ValueError("At most four targeted context requests per round")
    context = {p["paragraph_id"]: p for p in packet["context_only"]}
    body_ids = {p["paragraph_id"] for p in packet["body"]}
    reports = []
    size = sum(len(p["text"]) for p in context.values())
    for request in requests:
        if not request["reason"].strip():
            raise ValueError("Explain how the context serves the assigned body")
        if request["unit_id"]:
            result = read_chapter(reg, request["unit_id"], max_characters)
        else:
            result = search_source(reg, request["query"])
        omitted = 0
        for paragraph in result["paragraphs"]:
            pid = paragraph["paragraph_id"]
            if pid in body_ids or pid in context:
                continue
            if size + len(paragraph["text"]) > max_characters:
                omitted += 1
                continue
            context[pid] = {**paragraph, "purpose": "support_for_assigned_body"}
            size += len(paragraph["text"])
        reports.append({"request": request, "truncated_by_budget": omitted > 0, **{k: v for k, v in result.items() if k != "paragraphs"}})
    return {**packet, "context_only": sorted(context.values(), key=lambda p: p["paragraph_id"]),
            "context_request_results": packet.get("context_request_results", []) + reports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit")
    parser.add_argument("--query")
    args = parser.parse_args()
    if bool(args.unit) == bool(args.query):
        parser.error("Choose one chapter ID or source search phrase")
    print(json.dumps(read_chapter(Registry(), args.unit) if args.unit else search_source(Registry(), args.query), ensure_ascii=False, indent=2))
