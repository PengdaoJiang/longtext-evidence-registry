"""Synthetic identity retrieval, explicit disambiguation and transaction races."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from entity_preflight import (VERSION, IdentityLookupChanged, apply_decisions,
    lookup_proposals, make_proof)
from test_registry import fixture, batch, fact, span, RUN_DIR


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.reg = fixture(RUN_DIR / uuid.uuid4().hex / "preflight.sqlite3", count=3)
        self.reg.enqueue()
        self.one, self.two, self.three = self.reg.claim("lookup-test", 3)

    def enable(self):
        with self.reg.connection(True) as con:
            con.execute("INSERT INTO metadata VALUES ('entity_preflight_policy',?)", (VERSION,))

    def seed(self):
        value = batch(self.one["unit_id"])
        return self.reg.submit(self.one["task_id"], self.one["lease_token"], value)

    def decisions(self, checks, action="new_distinct"):
        return {"decisions": [{"ref": c["ref"], "action": action, "existing_id": None,
            "reason": "合成语料明确说明这次是同名的另一个人。"} for c in checks if c["match_count"]]}

    def test_policy_requires_lookup_and_exact_payload_binding(self):
        self.enable()
        value = batch(self.one["unit_id"])
        with self.assertRaisesRegex(ValueError, "global pre-registration"):
            self.reg.submit(self.one["task_id"], self.one["lease_token"], value)
        self.assertEqual(self.reg.status()["counts"]["entities"], 0)
        checks = lookup_proposals(self.reg, value)
        proof = make_proof(self.reg, value, {"decisions": []}, checks)
        tampered = deepcopy(value)
        tampered["entries"][0]["name"] = "另一个人"
        with self.assertRaisesRegex(ValueError, "exact payload"):
            self.reg.submit(self.one["task_id"], self.one["lease_token"], tampered, entity_preflight=proof)
        omitted = deepcopy(proof)
        omitted["checks"].pop()
        with self.assertRaisesRegex(ValueError, "omitted"):
            self.reg.submit(self.one["task_id"], self.one["lease_token"], value, entity_preflight=omitted)
        self.reg.submit(self.one["task_id"], self.one["lease_token"], value, entity_preflight=proof)

    def test_global_mentions_and_accepted_relations_recall_different_labels(self):
        value = batch(self.one["unit_id"])
        value["entries"][0]["name"] = "早先未具名者"
        value["entries"][1]["name"] = "早先未具名长辈"
        value["facts"][0].update(subject="b", object="a", predicate="parent_of")
        receipt = self.reg.submit(self.one["task_id"], self.one["lease_token"], value)
        self.reg.review(receipt["fact_ids"]["transfer"], "accepted", "root", "合成证据", 0)
        later = batch(self.three["unit_id"])
        later["entries"][1]["name"] = "晚章中的母亲"
        first = lookup_proposals(self.reg, later)
        self.assertIn(receipt["entity_ids"]["a"], {c["entity_id"] for c in first[0]["candidates"]})
        # Binding one known endpoint also exposes an unnamed parent globally.
        later["entries"][0].update(existing_id=receipt["entity_ids"]["a"], name="早先未具名者")
        found = lookup_proposals(self.reg, later)[0]
        self.assertIn(receipt["entity_ids"]["b"], {c["entity_id"] for c in found["candidates"]})
        parent = next(c for c in found["candidates"] if c["entity_id"] == receipt["entity_ids"]["b"])
        self.assertTrue(parent["evidence"])
        self.assertTrue(parent["relations_to_known_entries"])

    def test_derived_alias_retrieves_seed_outside_initial_packet(self):
        value = batch(self.one["unit_id"])
        value["entries"][0]["name"] = "未具名者"
        naming = fact("alias", "a", None, span(self.one["unit_id"], "甲"), "alias", "attribute")
        naming["value"] = "小甲"
        value["facts"].append(naming)
        receipt = self.reg.submit(self.one["task_id"], self.one["lease_token"], value)
        later = batch(self.three["unit_id"])
        later["entries"][0]["name"] = "小甲"
        later["mentions"] = []
        found = lookup_proposals(self.reg, later)[0]
        self.assertIn(receipt["entity_ids"]["a"], {c["entity_id"] for c in found["candidates"]})

    def test_public_lookup_keeps_exact_name_before_events_containing_it(self):
        receipt = self.seed()
        with self.reg.connection(True) as con:
            for i in range(25):
                eid, name = f"aaa-event-{i}", f"甲做某事{i}"
                con.execute("INSERT INTO entities VALUES (?, 'event', ?, 'candidate', '{}', 0)", (eid, name))
                self.reg._name(con, eid, name, "label", self.one["unit_id"], None, "test")
        result = self.reg.lookup("甲", limit=1)
        self.assertEqual(result["total"], 26)
        self.assertEqual(result["items"][0]["entity_id"], receipt["entity_ids"]["a"])
        self.assertTrue(result["items"][0]["exact_name_match"])
        self.assertTrue(result["has_more"])

    def test_explicit_reuse_and_distinct_homonym_both_supported(self):
        receipt = self.seed()
        value = batch(self.two["unit_id"])
        checks = lookup_proposals(self.reg, value)
        choices = self.decisions(checks)
        choices["decisions"][0].update(action="reuse", existing_id=receipt["entity_ids"]["a"], reason="同一叙事对象")
        revised = apply_decisions(value, checks, choices)
        self.assertIsNone(value["entries"][0]["existing_id"])
        self.assertEqual(revised["entries"][0]["existing_id"], receipt["entity_ids"]["a"])
        self.assertIsNone(revised["entries"][1]["existing_id"])
        self.enable()
        proof = make_proof(self.reg, revised, choices, checks)
        out = self.reg.submit(self.two["task_id"], self.two["lease_token"], revised, entity_preflight=proof)
        self.assertEqual(out["entity_ids"]["a"], receipt["entity_ids"]["a"])
        self.assertNotEqual(out["entity_ids"]["b"], receipt["entity_ids"]["b"])

    def test_reuse_cannot_select_another_proposals_candidate(self):
        receipt = self.seed()
        value = batch(self.two["unit_id"])
        checks = lookup_proposals(self.reg, value)
        choices = self.decisions(checks)
        choices["decisions"][0].update(action="reuse", existing_id=receipt["entity_ids"]["b"])
        with self.assertRaisesRegex(ValueError, "this proposal"):
            apply_decisions(value, checks, choices)

    def test_truncated_results_cannot_be_declared_absent(self):
        self.seed()
        with self.reg.connection(True) as con:
            for i in range(8):
                eid = f"homonym-{i}"
                con.execute("INSERT INTO entities VALUES (?, 'person', '甲', 'candidate', '{}', 0)", (eid,))
                self.reg._name(con, eid, "甲", "label", self.one["unit_id"], None, "test")
        value = batch(self.two["unit_id"])
        checks = lookup_proposals(self.reg, value)
        self.assertTrue(checks[0]["truncated"])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            apply_decisions(value, checks, self.decisions(checks))
        revised = apply_decisions(value, checks, self.decisions(checks, "unresolved"))
        self.assertTrue(revised["unresolved"])

    def test_commit_race_is_rechecked_atomically_and_can_be_reconciled(self):
        self.enable()
        a, b = batch(self.one["unit_id"]), batch(self.two["unit_id"])
        ca, cb = lookup_proposals(self.reg, a), lookup_proposals(self.reg, b)
        pa = make_proof(self.reg, a, {"decisions": []}, ca)
        pb = make_proof(self.reg, b, {"decisions": []}, cb)
        receipt = self.reg.submit(self.one["task_id"], self.one["lease_token"], a, entity_preflight=pa)
        with self.assertRaises(IdentityLookupChanged):
            self.reg.submit(self.two["task_id"], self.two["lease_token"], b, entity_preflight=pb)
        self.assertEqual(self.reg.status()["counts"]["submissions"], 1)
        self.assertEqual(self.reg.status()["counts"]["entities"], 2)
        with self.assertRaises(IdentityLookupChanged):
            make_proof(self.reg, b, {"decisions": []}, cb)
        fresh = lookup_proposals(self.reg, b)
        choices = {"decisions": [{"ref": c["ref"], "action": "reuse", "existing_id": receipt["entity_ids"][c["ref"]],
            "reason": "复查确认并行先登记的是同一对象"} for c in fresh]}
        revised = apply_decisions(b, fresh, choices)
        proof = make_proof(self.reg, revised, choices, fresh)
        second = self.reg.submit(self.two["task_id"], self.two["lease_token"], revised, entity_preflight=proof)
        self.assertEqual(receipt["entity_ids"], second["entity_ids"])

    def test_manual_cli_lookup_and_submission_use_same_policy(self):
        self.enable()
        root = self.reg.path.parent
        original, checks, decisions = (root / n for n in ("original.json", "checks.json", "decisions.json"))
        reconciled, proof = root / "reconciled.json", root / "proof.json"
        original.write_text(json.dumps(batch(self.one["unit_id"])), encoding="utf-8")
        decisions.write_text('{"decisions": []}', encoding="utf-8")
        tool = str(Path(__file__).with_name("entity_preflight.py"))
        prefix = [sys.executable, tool, "--db", str(self.reg.path), "--file", str(original)]
        subprocess.run(prefix + ["--output", str(checks)], capture_output=True, check=True)
        subprocess.run(prefix + ["--checks", str(checks), "--decisions", str(decisions),
            "--output", str(reconciled), "--proof", str(proof)], capture_output=True, check=True)
        self.assertEqual(self.reg.status()["counts"]["submissions"], 0)
        command = [sys.executable, str(Path(__file__).with_name("registry.py")), "--db", str(self.reg.path),
            "submit", "--task", self.one["task_id"], "--lease", self.one["lease_token"], "--file", str(reconciled)]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
        subprocess.run(command + ["--identity-proof", str(proof)], capture_output=True, check=True)
        self.assertEqual(self.reg.status()["counts"]["submissions"], 1)


if __name__ == "__main__":
    unittest.main()
