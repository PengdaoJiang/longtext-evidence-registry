from copy import deepcopy
import unittest
from alignment import alignment_issues, apply_alignment, alignment_repair_record
from compact_wire import expand
from test_compact_wire import wire_value


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.raw = wire_value()
        self.raw["anchors"] = [{"ref": "a", "scope": "body", "paragraph_id": "p", "quote": "甲", "occurrence": None}]
        self.raw["mentions"] = [{"entity": "x", "span": "a", "note": "first speaker"}]
        self.raw["entries"] = []
        self.raw["facts"] = []
        self.raw["context_support"] = []
        self.packet = {"body": [{"paragraph_id": "p", "text": "甲问另一个甲。甲答。"}], "context_only": []}

    def decide(self, indices):
        return {"corrections": [{"anchor_ref": "a", "occurrences": indices,
                                 "replacement_quote": None, "reason": "synthetic reviewed positions"}]}

    def test_positions_include_overlaps_and_do_not_guess_same_named_people(self):
        issues = alignment_issues(self.raw, self.packet)
        self.assertEqual([m["start"] for m in issues[0]["matches"]], [0, 5, 7])
        original = deepcopy(self.raw)
        resolved = apply_alignment(self.raw, self.packet, self.decide([0]))
        self.assertEqual(resolved["anchors"][0]["occurrence"], 0)
        self.assertEqual(len(resolved["mentions"]), 1)
        self.assertEqual(original, self.raw)

    def test_explicit_multi_selection_only_clones_mentions_and_preserves_facts(self):
        self.raw["facts"] = [{"ref": "fact", "evidence": []}]
        resolved = apply_alignment(self.raw, self.packet, self.decide([0, 2]))
        self.assertEqual(len(resolved["mentions"]), 2)
        self.assertEqual(resolved["facts"], self.raw["facts"])
        self.assertEqual(resolved["entries"], self.raw["entries"])
        self.assertFalse(alignment_issues(resolved, self.packet))

    def test_unresolved_invalid_duplicate_and_evidence_multi_selection_fail_closed(self):
        for indices in ([], [3], [-1], [True], [0, 0]):
            with self.assertRaises(ValueError):
                apply_alignment(self.raw, self.packet, self.decide(indices))
        with self.assertRaises(ValueError):
            apply_alignment(self.raw, self.packet, {"corrections": []})
        self.raw["facts"] = [{"ref": "fact", "evidence": ["a"]}]
        with self.assertRaisesRegex(ValueError, "mention-only"):
            apply_alignment(self.raw, self.packet, self.decide([0, 1]))

    def test_duplicate_resolved_mentions_preserve_all_notes_without_merging_people(self):
        self.raw["anchors"].append({**self.raw["anchors"][0], "ref": "b", "occurrence": 0})
        self.raw["mentions"] += [{"entity": "x", "span": "b", "note": "second note"},
                                 {"entity": "y", "span": "b", "note": "other identity candidate"}]
        resolved = apply_alignment(self.raw, self.packet, self.decide([0]))
        self.assertEqual(len(resolved["mentions"]), 2)
        self.assertIn("second note", resolved["mentions"][0]["note"])
        self.assertEqual(resolved["mentions"][1]["entity"], "y")

    def test_missing_quote_can_only_be_replaced_by_a_same_paragraph_substring(self):
        self.raw["anchors"][0]["quote"] = "地面"
        self.packet["body"][0]["text"] = "甲问另一个甲，三人落在地。甲答。"
        issues = alignment_issues(self.raw, self.packet)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["repair_type"], "quote_missing")
        self.assertEqual(issues[0]["paragraph_id"], "p")
        self.assertEqual(issues[0]["matches"], [])

        decision = {"corrections": [{
            "anchor_ref": "a", "occurrences": [0], "replacement_quote": "落在地",
            "reason": "The exact phrase is present in the cited paragraph.",
        }]}
        original = deepcopy(self.raw)
        aligned = apply_alignment(self.raw, self.packet, decision)
        self.assertEqual(aligned["anchors"][0]["quote"], "落在地")
        self.assertEqual(aligned["anchors"][0]["occurrence"], 0)
        self.assertEqual(self.raw, original)
        self.assertEqual(aligned["facts"], original["facts"])
        self.assertEqual(aligned["entries"], original["entries"])
        self.assertEqual(aligned["coverage"], original["coverage"])
        self.assertFalse(alignment_issues(aligned, self.packet))

        record = alignment_repair_record(original, aligned, self.packet, decision, attempt_count=1)
        change = record["changes"][0]
        self.assertEqual(change["before_quote"], "地面")
        self.assertEqual(change["after_quote"], "落在地")
        self.assertEqual(change["selected_spans"], [{"start": 9, "end": 12}])
        self.assertTrue(change["verbatim_same_paragraph_verified"])

    def test_missing_quote_rejects_generated_text_wrong_paragraph_and_multi_fact_positions(self):
        self.raw["anchors"][0]["quote"] = "地面"
        self.packet["body"][0]["text"] = "甲问另一个甲，三人落在地。甲答。"
        invalid_text = {"corrections": [{"anchor_ref": "a", "occurrences": [0],
                                         "replacement_quote": "落在地面",
                                         "reason": "This sounds right."}]}
        with self.assertRaisesRegex(ValueError, "exact substring"):
            apply_alignment(self.raw, self.packet, invalid_text)

        missing = deepcopy(self.packet)
        missing["body"][0]["paragraph_id"] = "other"
        with self.assertRaisesRegex(ValueError, "paragraph is absent"):
            apply_alignment(self.raw, missing, {"corrections": []})

        self.raw["facts"] = [{"ref": "f", "evidence": ["a"]}]
        duplicate_quote = {"corrections": [{"anchor_ref": "a", "occurrences": [0, 1],
                                            "replacement_quote": "甲",
                                            "reason": "This anchor supports a fact."}]}
        with self.assertRaisesRegex(ValueError, "mention-only"):
            apply_alignment(self.raw, self.packet, duplicate_quote)


if __name__ == "__main__":
    unittest.main()
