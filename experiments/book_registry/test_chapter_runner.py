"""Synthetic execution, recovery and representation tests; no model calls."""
from argparse import Namespace
from copy import deepcopy
from pathlib import Path
import json
import unittest
import uuid

from chapter_runner import Runner, compact_schema, dump, make_packet, refresh_candidates, verify_assignment
from semantic_checks import inspect, require_valid
from test_registry import fixture, batch, fact, span, RUN_DIR
from test_compact_wire import wire_value


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = RUN_DIR / uuid.uuid4().hex
        self.reg = fixture(self.root / "test.sqlite3", count=3)
        self.reg.enqueue()

    def args(self, **changes):
        data = dict(exe=Path("unused.exe"), db=self.reg.path, output=self.root / "run",
                    workers=1, max_tasks=1, max_batches=1, max_minutes=1,
                    request_timeout=30, paragraphs=2, characters=1400, lease=[])
        data.update(changes)
        return Namespace(**data)

    def test_next_packet_skips_submitted_coverage_and_preserves_context(self):
        lease = self.reg.claim("first")[0]
        value = batch(lease["unit_id"], complete=False)
        value["coverage"] = value["coverage"][:1]
        receipt = self.reg.submit(lease["task_id"], lease["lease_token"], value)
        packet = make_packet(self.reg, lease)
        self.assertEqual([p["ordinal"] for p in packet["body"]], [2])
        self.assertTrue(any(p["ordinal"] == 1 for p in packet["context_only"]))
        self.assertIn(receipt["entity_ids"]["a"], [c["entity_id"] for c in packet["candidates"]])

    def test_supplied_leases_are_reserved_before_fast_worker_claims(self):
        leases = self.reg.claim("existing", 2)
        files = []
        for i, lease in enumerate(leases):
            path = self.root / f"lease{i}.json"
            dump(path, lease)
            files.append(path)
        runner = Runner(self.args(workers=2, max_tasks=2, lease=files))
        self.assertIsNotNone(runner.allocate("one", leases[0]))
        self.assertIsNone(runner.allocate("one"))
        self.assertIsNotNone(runner.allocate("two", leases[1]))

    def test_existing_ids_are_constrained_to_the_actual_packet(self):
        from compact_wire import _validate
        field = compact_schema([{"entity_id": "known-id"}])["properties"]["entries"]["items"]["properties"]["existing_id"]
        _validate("known-id", field, "existing_id")
        _validate(None, field, "existing_id")
        with self.assertRaises(ValueError):
            _validate("invented-or-mistyped", field, "existing_id")

    def test_runner_reconciles_new_proposals_against_global_registry(self):
        from entity_preflight import VERSION
        first, second = self.reg.claim("preflight", 2)
        original = self.reg.submit(first["task_id"], first["lease_token"], batch(first["unit_id"]))
        with self.reg.connection(True) as con:
            con.execute("INSERT INTO metadata VALUES ('entity_preflight_policy',?)", (VERSION,))
        runner = Runner(self.args())
        packet = make_packet(self.reg, second)
        packet["candidates"] = []  # Deliberately emulate the initial retrieval miss.
        value = batch(second["unit_id"], complete=False)
        batch_dir = runner.root / "synthetic-batch"
        batch_dir.mkdir()
        calls = []
        def fake(directory, input_packet, batch_id, lease, purpose="extraction"):
            calls.append(purpose)
            self.assertEqual(purpose, "identity")
            return {"decisions": [{"ref": c["ref"], "action": "reuse",
                "existing_id": original["entity_ids"][c["ref"]], "reason": "合成证据确认同一对象"}
                for c in input_packet["checks"]]}
        runner.invoke = fake
        receipt, final = runner.reconcile_and_submit(batch_dir, packet, value, [], second)
        self.assertEqual(calls, ["identity"])
        self.assertEqual(receipt["entity_ids"], original["entity_ids"])
        self.assertTrue(all(e["existing_id"] for e in final["entries"]))

    def test_body_names_survive_saturated_context_and_duplicate_alias_rows(self):
        previous, current = self.reg.claim("lookup-pressure", 2)
        value = batch(previous["unit_id"])
        target = self.reg.submit(previous["task_id"], previous["lease_token"], value)["entity_ids"]["a"]
        noisy_recent = {}
        with self.reg.connection(True) as con:
            # Context-only records would otherwise outrank a body name on ties.
            evidence = con.execute("SELECT evidence_id FROM entity_evidence WHERE entity_id=?", (target,)).fetchone()[0]
            for i in range(85):
                eid = f"noise-{i:03d}"
                con.execute("INSERT INTO entities VALUES (?, 'event', ?, 'candidate', '{}', 0)", (eid, f"旧事件{i}"))
                self.reg._name(con, eid, "此前", "label", previous["unit_id"], None, "test")
                con.execute("INSERT INTO entity_evidence VALUES (?,?)", (eid, evidence))
                con.execute("INSERT INTO mentions VALUES (?,?,?,?)", (f"mention-{i}", eid, evidence, "synthetic noisy historic mention"))
                noisy_recent[str(i)] = eid
            for i in range(8):
                self.reg._name(con, target, "甲", "label", f"SU-{i+20:06d}", None, "test")
            self.reg._name(con, target, "城门", "alias", previous["unit_id"], None, "test")
        packet = make_packet(self.reg, current)
        packet["context_only"] += [{**p, "unit_id": current["unit_id"]} for p in packet["body"][:1]]
        packet["body"] = packet["body"][1:]
        packet = refresh_candidates(self.reg, current, packet,
            {"prior_submissions": [{"receipt": {"entity_ids": noisy_recent}}]})
        found = next(c for c in packet["candidates"] if c["entity_id"] == target)
        self.assertEqual(found["rank"], max(c["rank"] for c in packet["candidates"]))
        surfaces = [n["surface"] for n in found["names"]]
        self.assertEqual(surfaces.count("甲"), 1)
        self.assertIn("城门", surfaces)
        self.assertTrue(packet["candidate_list_truncated"])

    def test_context_candidates_include_descriptive_names_from_both_directions(self):
        previous, current, later = self.reg.claim("parallel", 3)
        expected = []
        for lease, label in ((previous, "前章未具名长辈"), (later, "后章确认的长辈")):
            value = batch(lease["unit_id"])
            value["entries"][0]["name"] = label  # This display name never occurs in the source.
            receipt = self.reg.submit(lease["task_id"], lease["lease_token"], value)
            expected.append(receipt["entity_ids"]["a"])
        packet = make_packet(self.reg, current)
        supplied = {e["entity_id"] for e in packet["candidates"]}
        self.assertTrue(set(expected) <= supplied)
        self.assertEqual(packet["reading_mode"], "whole_book_retrospective")

    def test_assignment_disallows_context_evidence_and_hidden_reused_id(self):
        lease = self.reg.claim("worker")[0]
        packet = make_packet(self.reg, lease, paragraph_limit=1)
        value = batch(lease["unit_id"], "one", False)
        value["coverage"] = [packet["body"][0]["paragraph_id"]]
        verify_assignment(value, packet, "one")
        value["entries"][0]["existing_id"] = "secret-not-in-lookup"
        with self.assertRaisesRegex(ValueError, "candidate lookup"):
            verify_assignment(value, packet, "one")
        value["entries"][0]["existing_id"] = None
        value["facts"][0]["evidence"] = [span(lease["unit_id"], "甲", 2)]
        with self.assertRaisesRegex(ValueError, "context"):
            verify_assignment(value, packet, "one")

    def test_failure_releases_lease_without_coverage_or_silent_retry(self):
        runner = Runner(self.args())
        def fail(*args):
            raise RuntimeError("synthetic model error")
        runner.invoke = fail
        self.assertEqual(runner.run(), 2)
        self.assertEqual(self.reg.status()["counts"]["submissions"], 0)
        self.assertNotIn("leased", self.reg.status()["tasks"])
        with self.reg.connection() as con:
            self.assertEqual(con.execute("SELECT sum(attempts) FROM tasks").fetchone()[0], 1)

    def test_bounded_success_and_completion_are_separate(self):
        runner = Runner(self.args())
        def fake(directory, packet, batch_id, lease):
            directory.mkdir(parents=True)
            return batch(lease["unit_id"], batch_id, False)
        runner.invoke = fake
        self.assertEqual(runner.run(), 0)
        self.assertEqual(self.reg.status()["counts"]["submissions"], 2)
        self.assertEqual(self.reg.status()["tasks"]["complete"], 1)
        self.assertEqual(self.reg.status()["semantic_review"]["pending"], 1)

    def test_missing_wire_quote_uses_bounded_same_paragraph_repair_and_preserves_raw(self):
        runner = Runner(self.args(paragraphs=1))
        calls = []

        def fake(directory, packet, batch_id, lease, purpose="extraction"):
            directory.mkdir(parents=True)
            calls.append((purpose, packet.get("attempt")))
            if purpose == "extraction":
                raw = wire_value()
                raw["batch_id"] = batch_id
                paragraph_id = packet["body"][0]["paragraph_id"]
                raw["coverage"] = [paragraph_id]
                for anchor in raw["anchors"]:
                    anchor["paragraph_id"] = paragraph_id
                next(a for a in raw["anchors"] if a["ref"] == "a_item")["quote"] = "玉简盒"
                dump(directory / "compact.json", raw)
                return raw

            corrections = []
            for issue in packet["issues"]:
                corrections.append({
                    "anchor_ref": issue["anchor_ref"],
                    "occurrences": [0],
                    "replacement_quote": ("not in the paragraph" if packet["attempt"] == 1 else "玉简")
                        if issue["repair_type"] == "quote_missing" else None,
                    "reason": "Synthetic bounded-repair test; copied only from supplied source on retry.",
                })
            decision = {"corrections": corrections}
            dump(directory / "compact.json", decision)
            return decision

        runner.invoke = fake
        self.assertEqual(runner.run(), 0)
        self.assertEqual([purpose for purpose, _ in calls], ["extraction", "alignment", "alignment"])

        batch_dir = runner.root / "chapter-worker-01" / "SU-000001-p0001"
        original = json.loads((batch_dir / "compact.json").read_text(encoding="utf-8"))
        aligned = json.loads((batch_dir / "aligned-compact.json").read_text(encoding="utf-8"))
        record = json.loads((batch_dir / "alignment-repair-record.json").read_text(encoding="utf-8"))
        self.assertEqual(next(a for a in original["anchors"] if a["ref"] == "a_item")["quote"], "玉简盒")
        self.assertEqual(next(a for a in aligned["anchors"] if a["ref"] == "a_item")["quote"], "玉简")
        self.assertEqual(original["facts"], aligned["facts"])
        self.assertEqual(original["entries"], aligned["entries"])
        self.assertEqual(original["qualifiers"], aligned["qualifiers"])
        self.assertEqual(original["coverage"], aligned["coverage"])
        self.assertEqual(original["unresolved"], aligned["unresolved"])
        self.assertEqual([(m["entity"], m["note"]) for m in original["mentions"]],
                         [(m["entity"], m["note"]) for m in aligned["mentions"]])
        self.assertEqual(record["status"], "applied")
        self.assertEqual(record["attempt_count"], 2)
        self.assertIn("exact substring", record["attempts"][0]["validation_error"])
        self.assertIsNone(record["attempts"][1]["validation_error"])
        item_change = next(change for change in record["changes"] if change["anchor_ref"] == "a_item")
        self.assertEqual(item_change["before_quote"], "玉简盒")
        self.assertEqual(item_change["after_quote"], "玉简")
        self.assertTrue(item_change["verbatim_same_paragraph_verified"])

    def test_repair_that_never_returns_a_literal_quote_stops_without_submission(self):
        runner = Runner(self.args(paragraphs=1))
        calls = []

        def fake(directory, packet, batch_id, lease, purpose="extraction"):
            directory.mkdir(parents=True)
            calls.append(purpose)
            if purpose == "extraction":
                raw = wire_value()
                raw["batch_id"] = batch_id
                paragraph_id = packet["body"][0]["paragraph_id"]
                raw["coverage"] = [paragraph_id]
                for anchor in raw["anchors"]:
                    anchor["paragraph_id"] = paragraph_id
                next(a for a in raw["anchors"] if a["ref"] == "a_item")["quote"] = "玉简盒"
                dump(directory / "compact.json", raw)
                return raw

            return {"corrections": [{
                "anchor_ref": issue["anchor_ref"], "occurrences": [0],
                "replacement_quote": "never present" if issue["repair_type"] == "quote_missing" else None,
                "reason": "Synthetic invalid correction must fail closed.",
            } for issue in packet["issues"]]}

        runner.invoke = fake
        self.assertEqual(runner.run(), 2)
        self.assertEqual(calls, ["extraction", "alignment", "alignment"])
        batch_dir = runner.root / "chapter-worker-01" / "SU-000001-p0001"
        original = json.loads((batch_dir / "compact.json").read_text(encoding="utf-8"))
        record = json.loads((batch_dir / "alignment-repair-record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertTrue(all("exact substring" in x["validation_error"] for x in record["attempts"]))
        self.assertEqual(next(a for a in original["anchors"] if a["ref"] == "a_item")["quote"], "玉简盒")
        self.assertFalse((batch_dir / "payload.json").exists())
        self.assertEqual(self.reg.status()["counts"]["submissions"], 0)

    def test_representation_errors_rollback_and_open_roles_are_allowed(self):
        lease = self.reg.claim("worker")[0]
        value = batch(lease["unit_id"])
        value["facts"][0]["story_time"] = lease["unit_id"] + "-P0001"
        with self.assertRaisesRegex(ValueError, "narrative_position"):
            self.reg.submit(lease["task_id"], lease["lease_token"], value)
        self.assertEqual(self.reg.status()["counts"]["entities"], 0)
        value["facts"][0]["story_time"] = None
        value["entries"][1]["kind"] = "scene"
        value["facts"][0]["predicate"] = "location"
        with self.assertRaisesRegex(ValueError, "scene_is_not_place"):
            require_valid(value)
        value["facts"][0]["predicate"] = "in_scene"
        require_valid(value)
        value["facts"][0]["predicate"] = "new_supported_role"
        require_valid(value)

    def test_warning_does_not_rewrite_semantic_assertion(self):
        value = batch("SU-000001")
        value["entries"][1]["kind"] = "event"
        value["facts"][0]["predicate"] = "patient"
        original = deepcopy(value)
        self.assertEqual(inspect(value)["warnings"], 1)
        require_valid(value)
        self.assertEqual(original, value)

    def test_context_name_is_searchable_and_rejection_survives_later_reuse(self):
        one, two = self.reg.claim("names", 2)
        value = batch(one["unit_id"])
        naming = fact("new_name", "a", None, span(one["unit_id"], "甲"), "name", "attribute")
        naming.update(value="小甲", attribution="inference")
        value["facts"].append(naming)
        first = self.reg.submit(one["task_id"], one["lease_token"], value)
        eid, fid = first["entity_ids"]["a"], first["fact_ids"]["new_name"]
        self.assertEqual(self.reg.lookup("小甲")["items"][0]["entity_id"], eid)
        self.assertTrue(any(n["surface"] == "小甲" for n in self.reg.entry(eid)["names"]))
        second = batch(two["unit_id"])
        second["entries"][0].update(existing_id=eid, name="小甲")
        self.reg.submit(two["task_id"], two["lease_token"], second)
        self.reg.review(fid, "rejected", "test", "synthetic incorrect naming", 0)
        self.assertEqual(self.reg.lookup("小甲")["total"], 0)
        self.assertFalse(any(n["surface"] == "小甲" for n in self.reg.entry(eid)["names"]))
        self.assertTrue(any(n["surface"] == "小甲" for n in self.reg.entry(eid, review_state="all")["names"]))

    def test_name_fact_can_retrieve_an_entity_outside_neighbour_chapters(self):
        first, middle, last = self.reg.claim("name-index", 3)
        value = batch(first["unit_id"])
        value["entries"][0]["name"] = "未具名者的显示标签"
        naming = fact("resolved_name", "a", None, span(first["unit_id"], "甲"), "name", "attribute")
        naming["value"] = "甲"
        value["facts"].append(naming)
        receipt = self.reg.submit(first["task_id"], first["lease_token"], value)
        packet = make_packet(self.reg, last)
        self.assertNotIn(first["unit_id"], {p["unit_id"] for p in packet["context_only"]})
        self.assertIn(receipt["entity_ids"]["a"], {c["entity_id"] for c in packet["candidates"]})

    def test_accepted_relation_recalls_an_unnamed_neighbour_outside_reading_units(self):
        first, middle, last = self.reg.claim("graph-recall", 3)
        value = batch(first["unit_id"])
        value["entries"][1]["name"] = "只知关系的未具名长辈"
        value["facts"][0].update(predicate="parent_of", subject="b", object="a")
        receipt = self.reg.submit(first["task_id"], first["lease_token"], value)
        eid = receipt["entity_ids"]["b"]
        self.assertNotIn(eid, {c["entity_id"] for c in make_packet(self.reg, last)["candidates"]})
        self.reg.review(receipt["fact_ids"]["transfer"], "accepted", "test", "synthetic source-reviewed relation", 0)
        self.assertIn(eid, {c["entity_id"] for c in make_packet(self.reg, last)["candidates"]})

    def test_literal_story_time_roundtrips_without_weakening_order_edges(self):
        lease = self.reg.claim("time-worker")[0]
        value = batch(lease["unit_id"])
        value["entries"][0]["kind"] = "event"
        value["facts"][0].update(type="temporal", predicate="occurs_at", object=None,
                                  value="次日", modality="planned", story_time="次日")
        for predicate, target, literal, subject_kind in (
                ("before", None, "次日", "event"),
                ("occurs_at", "b", None, "event"),
                ("occurs_at", None, "次日", "person")):
            invalid = deepcopy(value)
            invalid["facts"][0].update(predicate=predicate, object=target, value=literal)
            invalid["entries"][0]["kind"] = subject_kind
            with self.assertRaises(ValueError):
                self.reg.submit(lease["task_id"], lease["lease_token"], invalid)
            self.assertEqual(self.reg.status()["counts"]["facts"], 0)
        self.reg.submit(lease["task_id"], lease["lease_token"], value)
        saved = self.reg.timeline()["items"][0]
        self.assertIsNone(saved["object_id"])
        self.assertEqual(saved["value"], "次日")
        self.assertEqual(saved["modality"], "planned")
        evidence = saved["evidence"][0]
        self.assertIn(evidence["quote"], evidence["paragraph_text"])
        self.assertGreater(len(evidence["paragraph_text"]), len(evidence["quote"]))

    def test_rejected_entity_cannot_propagate_through_lookup_or_packets(self):
        lease = self.reg.claim("first")[0]
        value = batch(lease["unit_id"], complete=False)
        value["coverage"] = value["coverage"][:1]
        receipt = self.reg.submit(lease["task_id"], lease["lease_token"], value)
        eid = receipt["entity_ids"]["a"]
        self.reg.review_record("entity", eid, "rejected", "synthetic-review", "wrong actor label", 0)
        self.assertEqual(self.reg.lookup("甲")["total"], 0)
        self.assertEqual(self.reg.timeline()["total"], 0)
        self.assertEqual(self.reg.timeline(review_state="all")["total"], 1)
        self.assertNotIn(eid, [c["entity_id"] for c in make_packet(self.reg, lease)["candidates"]])
        with self.assertRaisesRegex(ValueError, "rejected"):
            self.reg.entry(eid)
        self.assertEqual(self.reg.entry(eid, review_state="all")["record_review"]["state"], "rejected")
        value["batch_id"] = "bad-reuse"
        value["entries"][0]["existing_id"] = eid
        with self.assertRaisesRegex(ValueError, "rejected"):
            self.reg.submit(lease["task_id"], lease["lease_token"], value)

    def test_name_and_mention_reviews_retain_history_without_reexposure(self):
        lease = self.reg.claim("first")[0]
        receipt = self.reg.submit(lease["task_id"], lease["lease_token"], batch(lease["unit_id"]))
        eid = receipt["entity_ids"]["a"]
        with self.reg.connection() as con:
            nid = con.execute("SELECT name_id FROM names WHERE entity_id=?", (eid,)).fetchone()[0]
            mid = con.execute("SELECT mention_id FROM mentions WHERE entity_id=?", (eid,)).fetchone()[0]
        self.reg.review_record("name", nid, "rejected", "test", "incorrect alias", 0)
        self.reg.review_record("mention", mid, "rejected", "test", "incorrect coreference", 0)
        self.assertEqual(self.reg.lookup("甲")["total"], 0)
        self.assertEqual(self.reg.entry(eid)["mention_count"], 0)
        self.assertEqual(self.reg.entry(eid, review_state="all")["mention_count"], 1)
        with self.assertRaisesRegex(ValueError, "revision"):
            self.reg.review_record("mention", mid, "accepted", "test", "stale reviewer", 0)

    def test_wire_context_request_round_commits_only_assigned_body(self):
        with self.reg.connection() as con:
            remote_task = con.execute("SELECT task_id FROM tasks WHERE unit_id='SU-000003'").fetchone()[0]
        remote_lease = self.reg.claim("remote-context", task_id=remote_task)[0]
        remote_value = batch("SU-000003")
        remote_value["entries"][0]["name"] = "仅在远章登记的特指对象"
        remote_id = self.reg.submit(remote_task, remote_lease["lease_token"], remote_value)["entity_ids"]["a"]
        runner = Runner(self.args(paragraphs=1))
        seen = []
        def fake(directory, packet, batch_id, lease):
            directory.mkdir(parents=True)
            seen.append(packet)
            if len(seen) == 1:
                return {"context_requests": [{"unit_id": "SU-000003", "query": "甲", "reason": "Confirm a recurring referent"}]}
            raw = wire_value()
            raw["batch_id"] = batch_id
            for anchor in raw["anchors"]:
                anchor["paragraph_id"] = packet["body"][0]["paragraph_id"]
                if anchor["quote"] == "甲":
                    anchor["occurrence"] = 0
            raw["coverage"] = [p["paragraph_id"] for p in packet["body"]]
            raw["anchors"].append({"ref": "context_fact", "scope": "context", "paragraph_id": "SU-000003-P0001", "quote": "甲把玉简交给乙", "occurrence": None})
            raw["context_support"] = [{"record_type": "fact", "ref": "f_claim", "evidence": ["context_fact"], "note": "Synthetic support only"}]
            return raw
        runner.invoke = fake
        self.assertEqual(runner.run(), 0)
        self.assertEqual(len(seen), 2)
        self.assertNotIn(remote_id, {e["entity_id"] for e in seen[0]["candidates"]})
        self.assertIn(remote_id, {e["entity_id"] for e in seen[1]["candidates"]})
        self.assertIn("SU-000003-P0001", {p["paragraph_id"] for p in seen[1]["context_only"]})
        with self.reg.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM coverage c JOIN tasks t USING(task_id) WHERE t.unit_id='SU-000001'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM context_support").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
