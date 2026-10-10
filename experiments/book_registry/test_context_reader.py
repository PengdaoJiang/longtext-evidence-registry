"""Support reading widens, registered body coverage does not; synthetic tests."""
from copy import deepcopy
import unittest
import uuid
from context_reader import initial_context, extend_context, search_source
from prepare_submission import prepare_context_support
from test_registry import fixture, batch, span, RUN_DIR


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.reg = fixture(RUN_DIR / uuid.uuid4().hex / "test.sqlite3", 4)
        self.reg.enqueue()
        self.lease = self.reg.claim("test")[0]

    def test_full_chapter_and_neighbour_support_exclude_assigned_body(self):
        body = self.reg.task(self.lease["task_id"], self.lease["lease_token"])["body"][:1]
        context, units = initial_context(self.reg, self.lease["unit_id"], body)
        self.assertEqual({u["unit_id"] for u in units}, {"SU-000001", "SU-000002"})
        self.assertNotIn(body[0]["paragraph_id"], {p["paragraph_id"] for p in context})
        self.assertIn("SU-000001-P0002", {p["paragraph_id"] for p in context})
        packet = {"body": body, "context_only": context}
        widened = extend_context(self.reg, packet, [{"unit_id": "SU-000004", "query": "甲", "reason": "check a potentially recurring referent"}])
        self.assertIn("SU-000004-P0001", {p["paragraph_id"] for p in widened["context_only"]})
        self.assertEqual(widened["body"], body)

    def test_support_is_atomic_separate_and_can_come_from_later_chapter(self):
        value = batch(self.lease["unit_id"])
        support = [{"record_type": "fact", "ref": "transfer", "evidence": [span("SU-000004", "甲把玉简交给乙")], "note": "Synthetic later corroboration, not extra body coverage"}]
        bad = deepcopy(support)
        bad[0]["evidence"][0]["quote"] = "not present"
        with self.assertRaises(ValueError):
            self.reg.submit(self.lease["task_id"], self.lease["lease_token"], value, bad)
        self.assertEqual(self.reg.status()["counts"]["submissions"], 0)
        receipt = self.reg.submit(self.lease["task_id"], self.lease["lease_token"], value, support)
        self.assertEqual(receipt["covered_paragraphs"], 2)
        self.assertEqual(receipt["context_support_records"], 1)
        found = self.reg.timeline()["items"][0]
        self.assertEqual(found["context_support"][0]["paragraph_id"], "SU-000004-P0001")
        self.assertTrue(self.reg.submit(self.lease["task_id"], self.lease["lease_token"], value, support)["idempotent_replay"])

    def test_unseen_support_and_id_name_mismatch_are_rejected(self):
        support = [{"record_type": "fact", "ref": "transfer", "evidence": [{"paragraph_id": "SU-000004-P0001", "quote": "甲"}], "note": "test"}]
        with self.assertRaisesRegex(ValueError, "reading material"):
            prepare_context_support(self.reg, support, [])
        value = batch(self.lease["unit_id"], complete=False)
        receipt = self.reg.submit(self.lease["task_id"], self.lease["lease_token"], value)
        value["batch_id"] = "wrong-binding"
        value["entries"][0]["existing_id"] = receipt["entity_ids"]["b"]
        with self.assertRaisesRegex(ValueError, "ID/name mismatch"):
            self.reg.submit(self.lease["task_id"], self.lease["lease_token"], value)

    def test_speech_and_state_are_temporal_occurrences(self):
        value = batch(self.lease["unit_id"])
        value["entries"][0]["kind"] = "utterance"
        value["entries"][1]["kind"] = "state"
        value["facts"][0]["type"] = "temporal"
        value["facts"][0]["predicate"] = "before"
        self.reg.submit(self.lease["task_id"], self.lease["lease_token"], value)

    def test_paragraph_support_is_resolved_by_program_without_model_quotation(self):
        rows = search_source(self.reg, "玉简")["paragraphs"]
        record = [{"record_type": "fact", "ref": "f", "evidence": [{"paragraph_id": "SU-000002-P0001"}], "note": "test"}]
        compiled = prepare_context_support(self.reg, record, rows)
        self.assertEqual(compiled[0]["evidence"][0]["start"], 0)
        self.assertEqual(compiled[0]["evidence"][0]["quote"], next(p["text"] for p in rows if p["paragraph_id"] == "SU-000002-P0001"))


if __name__ == "__main__":
    unittest.main()
