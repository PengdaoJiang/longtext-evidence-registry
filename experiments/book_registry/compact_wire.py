"""Compile compact, reference-based model output to the registry quote payload.

This module is deliberately local and dependency-free. It validates structure and
references only; quote positions remain unresolved for ``bookdb prepare`` and no
semantic value is inferred or defaulted here.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


def _object(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


def _nullable(kind: str) -> dict[str, Any]:
    return {"anyOf": [{"type": kind}, {"type": "null"}]}


_TEXT = {"type": "string", "minLength": 1}
_NOTE = {"type": "string"}
_REFS = _array(_TEXT)
_ANCHOR = _object({
    "ref": _TEXT,
    "scope": {"type": "string", "enum": ["body", "context"]},
    "paragraph_id": _TEXT,
    "quote": _nullable("string"),
    "occurrence": _nullable("integer"),
})
_QUALIFIER = _object({
    "ref": _TEXT,
    "polarity": {"type": "string", "enum": ["positive", "negative"]},
    "modality": {"type": "string", "enum": [
        "actual", "planned", "reported", "conditional", "hypothetical", "unknown"
    ]},
    "attribution": {"type": "string", "enum": [
        "narrator", "character", "inference", "unknown"
    ]},
    "speaker": _nullable("string"),
    "frame": _TEXT,
    "story_time": _nullable("string"),
})
_ENTRY = _object({
    "ref": _TEXT,
    "existing_id": _nullable("string"),
    "kind": _TEXT,
    "name": _TEXT,
    "evidence": _REFS,
})
_MENTION = _object({
    "entity": _TEXT,
    "span": _TEXT,
    "note": _NOTE,
})
_FACT = _object({
    "ref": _TEXT,
    "type": {"type": "string", "enum": ["relation", "attribute", "temporal", "identity"]},
    "subject": _TEXT,
    "predicate": {**_TEXT, "description": "Temporal: before/after/simultaneous/overlaps/unknown connect two event, scene, utterance or state entries; occurs_at instead uses a literal time value. Attributes use value; relations and identity use object."},
    "object": _nullable("string"),
    "value": _nullable("string"),
    "qualifier": _TEXT,
    "note": _NOTE,
    "evidence": _REFS,
})
_UNRESOLVED = _object({"paragraph_id": _TEXT, "description": _TEXT})
_CONTEXT_REQUEST = _object({
    "query": _TEXT,
    "unit_id": _nullable("string"),
    "reason": _TEXT,
})
_CONTEXT_SUPPORT = _object({
    "record_type": {"type": "string", "enum": ["entity", "fact"]},
    "ref": _TEXT,
    "evidence": _REFS,
    "note": _TEXT,
})

# OpenAI Structured Outputs strict-schema subset: every object field is required,
# every object rejects additional properties, and nullable values use anyOf.
WIRE_SCHEMA = _object({
    "batch_id": _TEXT,
    "complete": {"type": "boolean"},
    "model": _TEXT,
    "reasoning_effort": _TEXT,
    "anchors": _array(_ANCHOR),
    "qualifiers": _array(_QUALIFIER),
    "entries": _array(_ENTRY),
    "mentions": _array(_MENTION),
    "facts": _array(_FACT),
    "coverage": _array(_TEXT),
    "unresolved": _array(_UNRESOLVED),
    "context_requests": {"anyOf": [_array(_CONTEXT_REQUEST), {"type": "null"}]},
    "context_support": {"anyOf": [_array(_CONTEXT_SUPPORT), {"type": "null"}]},
})


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return type(value) is dict
    if expected == "array":
        return type(value) is list
    if expected == "string":
        return type(value) is str
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        return type(value) is int
    if expected == "null":
        return value is None
    return False


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    """Validate the small JSON Schema subset used by WIRE_SCHEMA."""
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate(value, branch, path)
                return
            except ValueError:
                continue
        raise ValueError(f"{path}: value does not match any allowed type")

    expected = schema.get("type")
    if not _matches_type(value, expected):
        raise ValueError(f"{path}: expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: unsupported value {value!r}")

    if type(value) is str:
        if len(value.strip()) < schema.get("minLength", 0):
            raise ValueError(f"{path}: string must not be empty")
    elif type(value) is dict:
        properties = schema["properties"]
        expected_keys = set(properties)
        actual_keys = set(value)
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        if missing or extra:
            raise ValueError(f"{path}: missing={sorted(missing)}, extra={sorted(extra)}")
        for key, child_schema in properties.items():
            _validate(value[key], child_schema, f"{path}.{key}")
    elif type(value) is list:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")


def _index_refs(rows: list[dict[str, Any]], table: str,
                seen: dict[str, str]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        ref = row["ref"]
        if ref in seen:
            raise ValueError(f"duplicate ref {ref!r} in {table}; already defined in {seen[ref]}")
        seen[ref] = table
        result[ref] = row
    return result


def _resolve_anchor(ref: str, anchors: dict[str, dict[str, Any]], where: str,
                    expected_scope: str = "body") -> dict[str, Any]:
    if ref not in anchors:
        raise ValueError(f"{where}: missing anchor reference {ref!r}")
    anchor = anchors[ref]
    if anchor["scope"] != expected_scope:
        raise ValueError(f"{where}: anchor {ref!r} has scope {anchor['scope']!r}; expected {expected_scope!r}")
    occurrence = anchor["occurrence"]
    if anchor["quote"] is None:
        if occurrence is not None:
            raise ValueError("Whole-paragraph anchors do not use occurrence")
        return {"paragraph_id": anchor["paragraph_id"]}
    if occurrence is not None and (type(occurrence) is not int or occurrence < 0):
        raise ValueError(f"anchors[{ref!r}].occurrence must be null or a non-negative integer")
    compiled = {"paragraph_id": anchor["paragraph_id"], "quote": anchor["quote"]}
    if occurrence is not None:
        compiled["occurrence"] = occurrence
    return compiled


def _resolve_evidence(refs: list[str], anchors: dict[str, dict[str, Any]], where: str,
                      expected_scope: str = "body") -> list[dict[str, Any]]:
    if not refs:
        raise ValueError(f"{where}: at least one evidence anchor is required")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{where}: duplicate anchor reference")
    return [_resolve_anchor(ref, anchors, where, expected_scope) for ref in refs]


def _decode_wire(raw: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc.msg}") from exc
    _validate(raw, WIRE_SCHEMA, "$wire")

    # An exact repeated fact definition conveys no additional assertion. Keep
    # one definition so existing support references stay unambiguous. Conflicts
    # with the same ref still fail in _index_refs below; never choose a meaning.
    raw = deepcopy(raw)
    unique_facts = []
    for row in raw["facts"]:
        if row not in unique_facts:
            unique_facts.append(row)
    raw["facts"] = unique_facts

    seen: dict[str, str] = {}
    anchors = _index_refs(raw["anchors"], "anchors", seen)
    qualifiers = _index_refs(raw["qualifiers"], "qualifiers", seen)
    entries = _index_refs(raw["entries"], "entries", seen)
    facts = _index_refs(raw["facts"], "facts", seen)
    entry_refs = set(entries)

    for entry in raw["entries"]:
        _resolve_evidence(entry["evidence"], anchors, f"entries[{entry['ref']!r}].evidence")

    mention_signatures = set()
    for mention in raw["mentions"]:
        entity = mention["entity"]
        if entity not in entry_refs:
            raise ValueError(f"mentions: missing entity reference {entity!r}")
        span = _resolve_anchor(mention["span"], anchors, f"mentions[{entity!r}].span")
        if "quote" not in span:
            raise ValueError("Mention spans need an explicit surface quote, not a whole paragraph")
        signature = (entity, span["paragraph_id"], span["quote"], span.get("occurrence"))
        if signature in mention_signatures:
            raise ValueError(f"mentions: duplicate entity/span reference {entity!r}/{mention['span']!r}")
        mention_signatures.add(signature)

    for qualifier in raw["qualifiers"]:
        speaker = qualifier["speaker"]
        if speaker is not None and speaker not in entry_refs:
            raise ValueError(f"qualifiers[{qualifier['ref']!r}]: missing speaker reference {speaker!r}")

    for fact in raw["facts"]:
        if fact["subject"] not in entry_refs:
            raise ValueError(f"facts[{fact['ref']!r}]: missing subject reference {fact['subject']!r}")
        if fact["object"] is not None and fact["object"] not in entry_refs:
            raise ValueError(f"facts[{fact['ref']!r}]: missing object reference {fact['object']!r}")
        qualifier_ref = fact["qualifier"]
        if qualifier_ref not in qualifiers:
            raise ValueError(f"facts[{fact['ref']!r}]: missing qualifier reference {qualifier_ref!r}")
        _resolve_evidence(fact["evidence"], anchors, f"facts[{fact['ref']!r}].evidence")

    for support in raw["context_support"] or []:
        targets = entries if support["record_type"] == "entity" else facts
        if support["ref"] not in targets:
            raise ValueError("context_support: missing target reference " + support["ref"])
        _resolve_evidence(support["evidence"], anchors,
                          f"context_support[{support['record_type']}:{support['ref']}].evidence",
                          expected_scope="context")

    coverage = raw["coverage"]
    if len(coverage) != len(set(coverage)):
        raise ValueError("coverage contains duplicate paragraph IDs")
    return raw, anchors, qualifiers


def expand(raw: Any) -> dict[str, Any]:
    """Expand a compact wire object to the input shape accepted by ``prepare``.

    ``raw`` may be a parsed object or its JSON string. This function does not
    locate text, select among quote occurrences, fill qualifiers, or submit data.
    """
    raw, anchors, qualifiers = _decode_wire(raw)

    mentions = []
    for mention in raw["mentions"]:
        entity = mention["entity"]
        span = _resolve_anchor(mention["span"], anchors, f"mentions[{entity!r}].span")
        mentions.append({"entity": entity, "span": span, "resolution_note": mention["note"]})

    compiled_facts = []
    for fact in raw["facts"]:
        qualifier_ref = fact["qualifier"]
        qualifier = qualifiers[qualifier_ref]
        compiled_facts.append({
            "ref": fact["ref"],
            "type": fact["type"],
            "subject": fact["subject"],
            "predicate": fact["predicate"],
            "object": fact["object"],
            "value": fact["value"],
            "polarity": qualifier["polarity"],
            "modality": qualifier["modality"],
            "attribution": qualifier["attribution"],
            "speaker": qualifier["speaker"],
            "frame": qualifier["frame"],
            "story_time": qualifier["story_time"],
            "note": fact["note"],
            "evidence": _resolve_evidence(fact["evidence"], anchors, f"facts[{fact['ref']!r}].evidence"),
        })

    compiled_entries = [{
        "ref": entry["ref"],
        "existing_id": entry["existing_id"],
        "kind": entry["kind"],
        "name": entry["name"],
        "evidence": _resolve_evidence(entry["evidence"], anchors, f"entries[{entry['ref']!r}].evidence"),
    } for entry in raw["entries"]]

    return {
        "batch_id": raw["batch_id"],
        "complete": raw["complete"],
        "model": raw["model"],
        "reasoning_effort": raw["reasoning_effort"],
        "entries": compiled_entries,
        "mentions": mentions,
        "facts": compiled_facts,
        "coverage": list(raw["coverage"]),
        "unresolved": [dict(item) for item in raw["unresolved"]],
    }


def expand_support(raw: Any) -> list[dict[str, Any]] | None:
    """Expand context support separately from body evidence.

    Context requests remain available unchanged in ``raw['context_requests']``.
    Context anchors are accepted here only when marked ``scope='context'``;
    the regular ``expand`` result never includes them.
    """
    raw, anchors, _ = _decode_wire(raw)
    if raw["context_support"] is None:
        return None
    return [{
        "record_type": support["record_type"],
        "ref": support["ref"],
        "evidence": _resolve_evidence(
            support["evidence"], anchors,
            f"context_support[{support['record_type']}:{support['ref']}].evidence",
            expected_scope="context",
        ),
        "note": support["note"],
    } for support in raw["context_support"]]
