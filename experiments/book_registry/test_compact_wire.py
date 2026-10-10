"""Synthetic-only tests for the compact wire compiler."""
import copy
import json
import unittest

from compact_wire import WIRE_SCHEMA, expand
from prepare_submission import prepare


def wire_value():
    return {
        "batch_id": "synthetic/part-1",
        "complete": False,
        "model": "synthetic-test-no-model-call",
        "reasoning_effort": "none",
        "anchors": [
            {"ref": "a_name", "scope": "body", "paragraph_id": "SU-SYN-P0001", "quote": "甲", "occurrence": None},
            {"ref": "a_item", "scope": "body", "paragraph_id": "SU-SYN-P0001", "quote": "玉简", "occurrence": 0},
            {"ref": "a_event", "scope": "body", "paragraph_id": "SU-SYN-P0001", "quote": "甲把玉简交给乙", "occurrence": None},
            {"ref": "a_recipient", "scope": "body", "paragraph_id": "SU-SYN-P0001", "quote": "乙", "occurrence": 0},
        ],
        "qualifiers": [
            {"ref": "q_reported", "polarity": "positive", "modality": "reported",
             "attribution": "character", "speaker": "e_a", "frame": "quoted-speech",
             "story_time": None},
        ],
        "entries": [
            {"ref": "e_a", "existing_id": None, "kind": "person", "name": "甲", "evidence": ["a_name"]},
            {"ref": "e_b", "existing_id": None, "kind": "item", "name": "玉简", "evidence": ["a_item"]},
        ],
        "mentions": [
            {"entity": "e_a", "span": "a_name", "note": "synthetic explicit name"},
            {"entity": "e_b", "span": "a_item", "note": "synthetic item mention"},
        ],
        "facts": [
            {"ref": "f_claim", "type": "relation", "subject": "e_a", "predicate": "交付",
             "object": "e_b", "value": None, "qualifier": "q_reported",
             "note": "synthetic relation claim", "evidence": ["a_event"]},
        ],
        "coverage": ["SU-SYN-P0001"],
        "unresolved": [],
        "context_requests": [],
        "context_support": [],
    }


def assert_strict_objects(test, schema):
    if schema.get("type") == "object":
        test.assertFalse(schema["additionalProperties"])
        test.assertEqual(schema["required"], list(schema["properties"]))
        for child in schema["properties"].values():
            assert_strict_objects(test, child)
    if schema.get("type") == "array":
        assert_strict_objects(test, schema["items"])
    for branch in schema.get("anyOf", []):
        assert_strict_objects(test, branch)


class CompactWireTests(unittest.TestCase):
    def test_expands_refs_and_copies_explicit_qualifier_fields(self):
        raw = wire_value()
        expanded = expand(raw)

        self.assertNotIn("anchors", expanded)
        self.assertNotIn("qualifiers", expanded)
        self.assertNotIn("schema_version", expanded)  # bookdb prepare adds it
        self.assertEqual(expanded["entries"][0]["evidence"], [
            {"paragraph_id": "SU-SYN-P0001", "quote": "甲"}
        ])
        self.assertEqual(expanded["mentions"][1]["span"], {
            "paragraph_id": "SU-SYN-P0001", "quote": "玉简", "occurrence": 0
        })
        fact = expanded["facts"][0]
        self.assertEqual(fact["evidence"], [{
            "paragraph_id": "SU-SYN-P0001", "quote": "甲把玉简交给乙"
        }])
        self.assertEqual((fact["polarity"], fact["modality"], fact["attribution"]),
                         ("positive", "reported", "character"))
        self.assertEqual((fact["speaker"], fact["frame"], fact["story_time"]),
                         ("e_a", "quoted-speech", None))
        self.assertEqual(fact["note"], "synthetic relation claim")
        self.assertEqual(raw["facts"][0]["evidence"], ["a_event"])  # no input mutation

    def test_passes_explicit_occurrence_through_for_prepare(self):
        expanded = expand(wire_value())
        self.assertEqual(expanded["mentions"][1]["span"]["occurrence"], 0)

    def test_compiled_payload_is_located_by_existing_prepare(self):
        class SyntheticRegistry:
            def task(self, task_id, token):
                self.asserted = (task_id, token)
                return {"body": [{
                    "paragraph_id": "SU-SYN-P0001",
                    "text": "甲把玉简交给乙",
                }]}

        registry = SyntheticRegistry()
        prepared = prepare(registry, "synthetic-task", "synthetic-lease", expand(wire_value()))
        self.assertEqual(registry.asserted, ("synthetic-task", "synthetic-lease"))
        self.assertEqual(prepared["schema_version"], "book-registry-v1")
        self.assertEqual(prepared["facts"][0]["evidence"][0], {
            "paragraph_id": "SU-SYN-P0001", "start": 0, "end": len("甲把玉简交给乙"),
            "quote": "甲把玉简交给乙",
        })

    def test_context_support_expands_separately_and_requests_stay_raw(self):
        from compact_wire import expand_support

        value = wire_value()
        value["anchors"].append({
            "ref": "a_context", "scope": "context", "paragraph_id": "SU-OTHER-P0002",
            "quote": "此前借得玉简", "occurrence": None,
        })
        value["context_requests"] = [{
            "query": "甲乙此前是否借过玉简",
            "unit_id": "SU-OTHER",
            "reason": "核对当前段落提到的借用状态",
        }]
        value["context_support"] = [{
            "record_type": "fact", "ref": "f_claim",
            "evidence": ["a_context"], "note": "合成上下文支持记录",
        }]

        body = expand(value)
        support = expand_support(value)
        self.assertNotIn("context_requests", body)
        self.assertNotIn("context_support", body)
        self.assertEqual(body["facts"][0]["evidence"][0]["paragraph_id"], "SU-SYN-P0001")
        self.assertEqual(support, [{
            "record_type": "fact", "ref": "f_claim",
            "evidence": [{"paragraph_id": "SU-OTHER-P0002", "quote": "此前借得玉简"}],
            "note": "合成上下文支持记录",
        }])
        self.assertEqual(value["context_requests"][0]["unit_id"], "SU-OTHER")

    def test_context_and_body_evidence_cannot_cross_scopes(self):
        value = wire_value()
        value["anchors"].append({
            "ref": "a_context", "scope": "context", "paragraph_id": "SU-OTHER-P0002",
            "quote": "此前借得玉简", "occurrence": None,
        })
        value["facts"][0]["evidence"] = ["a_context"]
        with self.assertRaisesRegex(ValueError, "expected 'body'"):
            expand(value)

        value = wire_value()
        value["context_support"] = [{
            "record_type": "entity", "ref": "e_a",
            "evidence": ["a_name"], "note": "不可用正文锚点充当上下文证据",
        }]
        with self.assertRaisesRegex(ValueError, "expected 'context'"):
            expand(value)

    def test_nullable_context_fields_and_empty_support(self):
        from compact_wire import expand_support

        value = wire_value()
        value["context_requests"] = None
        value["context_support"] = None
        self.assertIsNone(expand_support(value))
        self.assertNotIn("context_requests", expand(value))

    def test_context_support_requires_a_local_target(self):
        value = wire_value()
        value["anchors"].append({"ref": "outside", "scope": "context", "paragraph_id": "SU-OTHER-P0001", "quote": "synthetic source", "occurrence": None})
        value["context_support"] = [{"record_type": "fact", "ref": "unknown", "evidence": ["outside"], "note": "test"}]
        with self.assertRaisesRegex(ValueError, "missing target reference"):
            expand(value)

    def test_paragraph_evidence_is_allowed_but_mentions_stay_precise(self):
        raw = wire_value()
        raw["anchors"][2]["quote"] = None
        expanded = expand(raw)
        self.assertEqual(expanded["facts"][0]["evidence"], [{"paragraph_id": "SU-SYN-P0001"}])
        raw["mentions"][0]["span"] = "a_event"
        with self.assertRaisesRegex(ValueError, "explicit surface"):
            expand(raw)

    def test_rejects_unknown_anchor_qualifier_and_entity_references(self):
        cases = [
            ("facts[0].evidence", lambda x: x["facts"][0].update(evidence=["missing_anchor"])),
            ("qualifier reference", lambda x: x["facts"][0].update(qualifier="missing_qualifier")),
            ("subject reference", lambda x: x["facts"][0].update(subject="missing_entity")),
            ("speaker reference", lambda x: x["qualifiers"][0].update(speaker="missing_speaker")),
        ]
        for message, mutate in cases:
            with self.subTest(message=message):
                value = wire_value()
                mutate(value)
                with self.assertRaises(ValueError):
                    expand(value)

    def test_rejects_duplicate_definition_and_evidence_refs(self):
        value = wire_value()
        value["anchors"].append(copy.deepcopy(value["anchors"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate ref"):
            expand(value)

        value = wire_value()
        value["qualifiers"][0]["ref"] = "a_event"
        with self.assertRaisesRegex(ValueError, "duplicate ref"):
            expand(value)

        value = wire_value()
        value["facts"][0]["evidence"] = ["a_event", "a_event"]
        with self.assertRaisesRegex(ValueError, "duplicate anchor reference"):
            expand(value)

    def test_identical_fact_repetition_deduplicates_but_conflicting_ref_fails(self):
        value = wire_value()
        expected = expand(value)
        value["facts"].append(copy.deepcopy(value["facts"][0]))
        self.assertEqual(expand(value), expected)
        self.assertEqual(len(value["facts"]), len(expected["facts"])+1)
        value["facts"][-1]["note"] = "Different assertion explanation"
        with self.assertRaisesRegex(ValueError, "duplicate ref"):
            expand(value)

    def test_rejects_missing_qualifier_fields_instead_of_defaulting(self):
        value = wire_value()
        del value["qualifiers"][0]["polarity"]
        with self.assertRaisesRegex(ValueError, "missing=.*polarity"):
            expand(value)

        value = wire_value()
        del value["facts"][0]["qualifier"]
        with self.assertRaisesRegex(ValueError, "missing=.*qualifier"):
            expand(value)

    def test_rejects_duplicate_mentions_and_coverage(self):
        value = wire_value()
        value["mentions"].append(copy.deepcopy(value["mentions"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate entity/span"):
            expand(value)

        value = wire_value()
        value["coverage"].append(value["coverage"][0])
        with self.assertRaisesRegex(ValueError, "coverage contains duplicate"):
            expand(value)

    def test_schema_uses_only_strict_objects(self):
        assert_strict_objects(self, WIRE_SCHEMA)
        self.assertEqual(WIRE_SCHEMA["required"], list(WIRE_SCHEMA["properties"]))

    def test_accepts_json_text_and_rejects_extra_properties(self):
        self.assertEqual(expand(json.dumps(wire_value(), ensure_ascii=False)), expand(wire_value()))
        value = wire_value()
        value["unexpected"] = "not allowed"
        with self.assertRaisesRegex(ValueError, "extra=.*unexpected"):
            expand(value)


if __name__ == "__main__":
    unittest.main()
