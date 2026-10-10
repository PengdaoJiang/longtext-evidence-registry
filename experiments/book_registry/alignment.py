"""Resolve quote positions and repair missing quotes with same-paragraph substrings."""
from copy import deepcopy


ALIGNMENT_MAX_ATTEMPTS = 2

ALIGNMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "anchor_ref": {"type": "string"},
                    "occurrences": {"type": "array", "items": {"type": "integer"}},
                    "replacement_quote": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "reason": {"type": "string"},
                },
                "required": ["anchor_ref", "occurrences", "replacement_quote", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["corrections"],
    "additionalProperties": False,
}


def _quote_matches(text, quote):
    """Return every exact occurrence, including overlapping occurrences."""
    if not quote:
        return []
    matches = []
    start = text.find(quote)
    while start >= 0:
        matches.append({
            "occurrence": len(matches),
            "start": start,
            "end": start + len(quote),
            "window": text[max(0, start - 18):start + len(quote) + 18],
        })
        start = text.find(quote, start + 1)
    return matches


def _anchor_uses(raw, anchor_ref):
    mentions = [m for m in raw["mentions"] if m["span"] == anchor_ref]
    other = [
        {"type": kind, "ref": row["ref"]}
        for kind, rows in (
            ("entity", raw["entries"]),
            ("fact", raw["facts"]),
            ("context_support", raw.get("context_support") or []),
        )
        for row in rows
        if anchor_ref in row["evidence"]
    ]
    return mentions, other


def alignment_issues(raw, packet):
    """List ambiguous positions and absent quotes that need a focused repair.

    An absent quote is only repairable when its paragraph is present in this
    packet. The returned issue includes that exact paragraph text; callers must
    never search another paragraph or silently fuzzy-match the quote.
    """
    texts = {p["paragraph_id"]: p["text"] for p in packet["body"] + packet["context_only"]}
    issues = []
    for anchor in raw.get("anchors", []):
        if anchor["quote"] is None:
            continue
        paragraph_id = anchor["paragraph_id"]
        if paragraph_id not in texts:
            raise ValueError(f"Anchor {anchor['ref']} paragraph is absent from its supplied packet")
        text = texts[paragraph_id]
        matches = _quote_matches(text, anchor["quote"])
        occurrence = anchor["occurrence"]
        if matches and ((occurrence is None and len(matches) == 1) or
                        (type(occurrence) is int and 0 <= occurrence < len(matches))):
            continue

        mentions, other = _anchor_uses(raw, anchor["ref"])
        if not mentions and not other and not matches:
            raise ValueError(f"Anchor {anchor['ref']} quotes text absent from its supplied paragraph")
        if not mentions and not other:
            continue
        issues.append({
            "anchor_ref": anchor["ref"],
            "paragraph_id": paragraph_id,
            "quote": anchor["quote"],
            "paragraph_text": text,
            "matches": matches,
            "mentions": mentions,
            "other_uses": other,
            "multiple_allowed": bool(mentions) and not other,
            "repair_type": "quote_missing" if not matches else "occurrence_selection",
        })
    return issues


def apply_alignment(raw, packet, decision):
    issues = {i["anchor_ref"]: i for i in alignment_issues(raw, packet)}
    corrections = decision.get("corrections", [])
    if len(corrections) != len(issues) or {c["anchor_ref"] for c in corrections} != set(issues):
        raise ValueError("Alignment must decide every ambiguous anchor exactly once")
    output = deepcopy(raw)
    anchors = {a["ref"]: a for a in output["anchors"]}
    used = {r["ref"] for rows in (output["anchors"], output["entries"], output["facts"], output["qualifiers"]) for r in rows}
    for correction in corrections:
        issue = issues[correction["anchor_ref"]]
        selections = correction["occurrences"]
        reason = correction["reason"]
        replacement_quote = correction.get("replacement_quote")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Alignment requires a reason")

        if issue["repair_type"] == "quote_missing":
            if not isinstance(replacement_quote, str) or not replacement_quote or not replacement_quote.strip():
                raise ValueError("Missing-quote repair requires a nonempty replacement_quote")
            matches = _quote_matches(issue["paragraph_text"], replacement_quote)
            if not matches:
                raise ValueError("Replacement quote is not an exact substring of its supplied paragraph")
        else:
            if replacement_quote is not None:
                raise ValueError("Occurrence selection cannot alter the original quote")
            matches = issue["matches"]

        if (not selections or any(type(i) is not int or not 0 <= i < len(matches) for i in selections)):
            raise ValueError("Alignment requires a reason and valid enumerated positions")
        if len(set(selections)) != len(selections) or (len(selections) > 1 and not issue["multiple_allowed"]):
            raise ValueError("Only mention-only anchors may select several distinct occurrences")
        selections = sorted(selections)
        anchor = anchors[correction["anchor_ref"]]
        if issue["repair_type"] == "quote_missing":
            anchor["quote"] = replacement_quote
        anchor["occurrence"] = selections[0]
        for occurrence in selections[1:]:
            clone_ref = f"{anchor['ref']}__occ_{occurrence}"
            if clone_ref in used:
                raise ValueError("Alignment clone reference collision")
            used.add(clone_ref)
            output["anchors"].append({**anchor, "ref": clone_ref, "occurrence": occurrence})
            output["mentions"].extend({**m, "span": clone_ref} for m in issue["mentions"])

    # Two anchor refs can resolve to the very same mention after correction.
    # Retain both notes but only one entity/span; this never merges entities.
    aligned_anchors = {a["ref"]: a for a in output["anchors"]}
    unique = {}
    for mention in output["mentions"]:
        anchor = aligned_anchors[mention["span"]]
        key = (mention["entity"], anchor["scope"], anchor["paragraph_id"], anchor["quote"], anchor["occurrence"] or 0)
        if key not in unique:
            unique[key] = mention
        elif mention["note"] and mention["note"] != unique[key]["note"]:
            unique[key]["note"] = "\n".join(filter(None, (unique[key]["note"], mention["note"])))
    output["mentions"] = list(unique.values())
    return output


def alignment_repair_record(original, aligned, packet, decision, attempt_count):
    """Build an auditable verbatim before/after record for the accepted repairs."""
    texts = {p["paragraph_id"]: p["text"] for p in packet["body"] + packet["context_only"]}
    before = {a["ref"]: a for a in original.get("anchors", [])}
    after = {a["ref"]: a for a in aligned.get("anchors", [])}
    changes = []
    for correction in decision.get("corrections", []):
        ref = correction["anchor_ref"]
        old, new = before[ref], after[ref]
        text = texts[old["paragraph_id"]]
        quote_matches = _quote_matches(text, new["quote"])
        selected = correction["occurrences"]
        changes.append({
            "anchor_ref": ref,
            "paragraph_id": old["paragraph_id"],
            "repair_type": "quote_substitution" if old["quote"] != new["quote"] else "occurrence_selection",
            "before_quote": old["quote"],
            "after_quote": new["quote"],
            "selected_occurrences": selected,
            "selected_spans": [
                {"start": quote_matches[i]["start"], "end": quote_matches[i]["end"]}
                for i in selected
            ],
            "verbatim_same_paragraph_verified": bool(quote_matches) and old["paragraph_id"] in texts,
            "reason": correction["reason"],
        })
    return {
        "status": "applied",
        "original_compact": "compact.json",
        "attempt_count": attempt_count,
        "changes": changes,
    }
