"""Meaningful arithmetic, provenance and failure-boundary checks."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plan_checks", ROOT / "scripts/plan_checks.py")
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)


def fixture(name):
    return json.loads((ROOT / "assets" / name).read_text(encoding="utf-8"))


class ChecksTests(unittest.TestCase):
    def test_known_overload_and_missing_assessment(self):
        result = CHECKS.check_plan(fixture("example-plan.json"))
        self.assertEqual(result["time"]["net_periods"], 10)
        self.assertEqual(result["time"]["overload_periods"], 2)
        self.assertEqual(result["links"]["structural_link_percent"], 50)
        self.assertIn(("no_assessment", "O2"), [(w["code"], w["id"]) for w in result["warnings"]])

    def test_net_and_gross_agree_when_losses_accounted_once(self):
        gross = fixture("example-plan.json")
        net = copy.deepcopy(gross)
        net["time"] = {"basis": "net", "net_periods": 10, "reserve_periods": 1}
        a, b = CHECKS.check_plan(gross), CHECKS.check_plan(net)
        for key in ["capacity_periods", "planned_periods", "overload_periods"]:
            self.assertEqual(a["time"][key], b["time"][key])

    def test_double_deduction_rejected(self):
        data = fixture("example-plan.json")
        data["time"] = {"basis": "net", "net_periods": 10,
                        "losses": [{"reason": "عطلة", "periods": 1, "source": "إفادة"}]}
        with self.assertRaises(CHECKS.DataError):
            CHECKS.check_plan(data)

    def test_duplicate_and_unknown_ids_rejected(self):
        data = fixture("example-plan.json")
        data["activities"][0]["outcome_ids"] = ["not-real"]
        with self.assertRaises(CHECKS.DataError):
            CHECKS.check_plan(data)
        data = fixture("example-plan.json")
        data["outcomes"].append(copy.deepcopy(data["outcomes"][0]))
        with self.assertRaises(CHECKS.DataError):
            CHECKS.check_plan(data)

    def test_boolean_periods_and_impossible_reserve_rejected(self):
        data = fixture("example-plan.json")
        data["units"][0]["periods"] = True
        with self.assertRaises(CHECKS.DataError):
            CHECKS.check_plan(data)
        data = fixture("example-plan.json")
        data["time"]["reserve_periods"] = 100
        with self.assertRaises(CHECKS.DataError):
            CHECKS.check_plan(data)

    def test_indexed_source_cannot_verify_outcome(self):
        data = fixture("example-plan.json")
        data["sources"] = [{"id": "S1", "title": "فهرسة", "locator": "رابط", "status": "indexed_only"}]
        data["outcomes"][0].update(provenance="verified", source_id="S1", locator="ص1")
        with self.assertRaises(CHECKS.DataError):
            CHECKS.check_plan(data)

    def test_all_evidence_missing_has_no_numeric_score(self):
        data = fixture("example-ratings.json")
        for row in data["ratings"]:
            row.update(status="insufficient", score=None, reason="مرجع غير متاح")
        result = CHECKS.score_rubric(data)
        self.assertIsNone(result["score_on_available_evidence"])
        self.assertEqual(result["evidence_coverage_percent"], 0)
        self.assertFalse(result["eligible_for_global_professional_judgment"])

    def test_missing_reference_not_zero_or_full_judgment(self):
        result = CHECKS.score_rubric(fixture("example-ratings.json"))
        self.assertEqual(result["score_on_available_evidence"], 66.7)
        self.assertEqual(result["evidence_coverage_percent"], 80)
        self.assertIn("R1", result["unresolved_critical_axes"])
        self.assertFalse(result["eligible_for_global_professional_judgment"])

    def test_not_applicable_excluded_from_coverage_denominator(self):
        data = fixture("example-ratings.json")
        data["ratings"][0].update(status="rated", score=2, evidence=["مرفق مرجعي اصطناعي، بند1"])
        data["ratings"][-1].update(status="not_applicable", score=None, reason="نطاق جزئي متفق عليه")
        result = CHECKS.score_rubric(data)
        self.assertEqual(result["applicable_weight"], 95)
        self.assertEqual(result["evidence_coverage_percent"], 100)
        self.assertEqual(result["score_on_available_evidence"], 66.7)

    def test_critical_failure_blocks_even_maximum_score(self):
        data = fixture("example-ratings.json")
        for row in data["ratings"]:
            row.update(status="rated", score=3, evidence=["دليل اصطناعي"])
        data["critical_failures"] = ["خطر مثبت في النشاط"]
        result = CHECKS.score_rubric(data)
        self.assertEqual(result["score_on_available_evidence"], 100)
        self.assertFalse(result["eligible_for_global_professional_judgment"])

    def test_invalid_score_and_malformed_state_rejected(self):
        data = fixture("example-ratings.json")
        data["ratings"][1]["score"] = 4
        with self.assertRaises(CHECKS.DataError):
            CHECKS.score_rubric(data)
        data["ratings"][1]["status"] = []
        with self.assertRaises(CHECKS.DataError):
            CHECKS.score_rubric(data)

    def test_cli_preserves_input_and_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "input.json", Path(tmp) / "output.json"
            src.write_text(json.dumps(fixture("example-plan.json"), ensure_ascii=False), encoding="utf-8")
            before = src.read_bytes()
            run = subprocess.run([sys.executable, str(ROOT / "scripts/plan_checks.py"), "check", str(src), "--output", str(dst)], capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(dst.read_text(encoding="utf-8"))["time"]["overload_periods"], 2)
            self.assertEqual(src.read_bytes(), before)
            denied = subprocess.run([sys.executable, str(ROOT / "scripts/plan_checks.py"), "check", str(src), "--output", str(src)], capture_output=True)
            self.assertEqual(denied.returncode, 2)
            self.assertEqual(src.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
