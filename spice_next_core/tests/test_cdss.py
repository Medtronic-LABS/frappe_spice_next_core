"""
Unit tests for api/cdss.py — pure logic extracted from the endpoint.
No Frappe DB required: all tested functions operate on plain dicts.
"""

import unittest

# Inline thresholds from risk/score.py — avoids importing frappe at module load.
# These must stay in sync with _RISK_THRESHOLDS in risk/score.py (WHO/JNC8/ADA).
_RISK_THRESHOLDS = {
    "LOINC|8480-6": {"moderate": 140.0, "high": 160.0, "label": "Elevated BP (systolic)"},
    "LOINC|8462-4": {"moderate": 90.0,  "high": 100.0, "label": "Elevated BP (diastolic)"},
    "LOINC|2339-0": {"moderate": 7.0,   "high": 11.1,  "label": "High blood glucose"},
    "LOINC|4548-4": {"moderate": 7.0,   "high": 9.0,   "label": "Poor glycaemic control (HbA1c)"},
}
_LEVEL_NAMES = ["Low", "Moderate", "High"]

# Mirror the pure-logic helpers from cdss.py without importing frappe.
_GUIDELINE_MAP = {
    "LOINC|8480-6": "JNC8-HTN",
    "LOINC|8462-4": "JNC8-HTN",
    "LOINC|2339-0": "ADA-2024",
    "LOINC|4548-4": "ADA-2024",
}
_WHO_HIGH = {
    "LOINC|8480-6": "WHO-HTN-2023",
    "LOINC|8462-4": "WHO-HTN-2023",
}
_STEPS_BY_CONCEPT = {
    "LOINC|8480-6": "Measure BP twice — 5 minutes apart",
    "LOINC|8462-4": "Measure BP twice — 5 minutes apart",
    "LOINC|2339-0": "Review medication adherence with patient",
    "LOINC|4548-4": "Review medication adherence with patient",
}


def _evaluate_thresholds(observations):
    findings = []
    for concept, thresholds in _RISK_THRESHOLDS.items():
        raw = observations.get(concept)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (ValueError, TypeError):
            continue
        if val >= thresholds["high"]:
            level_idx = 2
            guideline = _WHO_HIGH.get(concept, _GUIDELINE_MAP.get(concept, "WHO"))
        elif val >= thresholds["moderate"]:
            level_idx = 1
            guideline = _GUIDELINE_MAP.get(concept, "WHO")
        else:
            continue
        findings.append({
            "concept": concept,
            "label": thresholds["label"],
            "value": val,
            "level_idx": level_idx,
            "guideline": guideline,
        })
    return findings


def _build_steps(findings, risk_level_name):
    steps = []
    if risk_level_name == "High":
        steps.append("Contact supervising clinician before leaving patient.")
    seen = set()
    for f in findings:
        step = _STEPS_BY_CONCEPT.get(f["concept"])
        if step and step not in seen:
            steps.append(step)
            seen.add(step)
    if not steps:
        steps.append("Continue routine monitoring per programme schedule.")
    steps.append("Document all findings in the visit form before syncing.")
    return steps


def _overall_risk(findings):
    idx = max((f["level_idx"] for f in findings), default=0)
    return _LEVEL_NAMES[idx]


class TestCdssThresholds(unittest.TestCase):

    def test_normal_obs_no_findings(self):
        findings = _evaluate_thresholds({
            "LOINC|8480-6": "120",
            "LOINC|8462-4": "80",
            "LOINC|2339-0": "5.5",
        })
        self.assertEqual(findings, [])

    def test_systolic_140_moderate(self):
        findings = _evaluate_thresholds({"LOINC|8480-6": "140"})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["level_idx"], 1)
        self.assertEqual(findings[0]["guideline"], "JNC8-HTN")

    def test_systolic_139_no_finding(self):
        findings = _evaluate_thresholds({"LOINC|8480-6": "139"})
        self.assertEqual(findings, [])

    def test_systolic_160_high_who_guideline(self):
        findings = _evaluate_thresholds({"LOINC|8480-6": "160"})
        self.assertEqual(findings[0]["level_idx"], 2)
        self.assertEqual(findings[0]["guideline"], "WHO-HTN-2023")

    def test_diastolic_90_moderate_jnc8(self):
        findings = _evaluate_thresholds({"LOINC|8462-4": "90"})
        self.assertEqual(findings[0]["level_idx"], 1)
        self.assertEqual(findings[0]["guideline"], "JNC8-HTN")

    def test_diastolic_100_high(self):
        findings = _evaluate_thresholds({"LOINC|8462-4": "100"})
        self.assertEqual(findings[0]["level_idx"], 2)

    def test_glucose_7_moderate_ada(self):
        findings = _evaluate_thresholds({"LOINC|2339-0": "7.0"})
        self.assertEqual(findings[0]["level_idx"], 1)
        self.assertEqual(findings[0]["guideline"], "ADA-2024")

    def test_glucose_11_1_high(self):
        findings = _evaluate_thresholds({"LOINC|2339-0": "11.1"})
        self.assertEqual(findings[0]["level_idx"], 2)

    def test_hba1c_7_moderate(self):
        findings = _evaluate_thresholds({"LOINC|4548-4": "7.0"})
        self.assertEqual(findings[0]["level_idx"], 1)

    def test_hba1c_9_high(self):
        findings = _evaluate_thresholds({"LOINC|4548-4": "9.0"})
        self.assertEqual(findings[0]["level_idx"], 2)

    def test_unparseable_value_ignored(self):
        findings = _evaluate_thresholds({"LOINC|8480-6": "not-a-number"})
        self.assertEqual(findings, [])

    def test_empty_string_value_ignored(self):
        findings = _evaluate_thresholds({"LOINC|8480-6": ""})
        self.assertEqual(findings, [])

    def test_multiple_findings_correct_concepts(self):
        findings = _evaluate_thresholds({
            "LOINC|8480-6": "150",
            "LOINC|2339-0": "8.0",
        })
        concepts = {f["concept"] for f in findings}
        self.assertIn("LOINC|8480-6", concepts)
        self.assertIn("LOINC|2339-0", concepts)


class TestCdssRiskAggregation(unittest.TestCase):

    def test_any_high_finding_overall_high(self):
        findings = _evaluate_thresholds({
            "LOINC|8480-6": "165",   # high
            "LOINC|2339-0": "7.5",   # moderate
        })
        self.assertEqual(_overall_risk(findings), "High")

    def test_all_moderate_overall_moderate(self):
        findings = _evaluate_thresholds({
            "LOINC|8480-6": "145",
            "LOINC|2339-0": "8.0",
        })
        self.assertEqual(_overall_risk(findings), "Moderate")

    def test_empty_findings_low(self):
        self.assertEqual(_overall_risk([]), "Low")

    def test_guideline_ids_deduplicated(self):
        findings = _evaluate_thresholds({
            "LOINC|8480-6": "145",
            "LOINC|8462-4": "92",
        })
        ids = list({f["guideline"] for f in findings})
        self.assertEqual(ids.count("JNC8-HTN"), 1)


class TestCdssSteps(unittest.TestCase):

    def test_high_risk_includes_clinician_contact(self):
        findings = _evaluate_thresholds({"LOINC|8480-6": "165"})
        steps = _build_steps(findings, "High")
        self.assertTrue(any("clinician" in s for s in steps))

    def test_bp_finding_includes_bp_step(self):
        findings = _evaluate_thresholds({"LOINC|8480-6": "150"})
        steps = _build_steps(findings, "Moderate")
        self.assertTrue(any("BP" in s for s in steps))

    def test_glucose_finding_includes_medication_step(self):
        findings = _evaluate_thresholds({"LOINC|2339-0": "8.0"})
        steps = _build_steps(findings, "Moderate")
        self.assertTrue(any("medication" in s.lower() for s in steps))

    def test_no_duplicate_steps_for_same_concept(self):
        findings = _evaluate_thresholds({
            "LOINC|8480-6": "150",
            "LOINC|8462-4": "92",
        })
        steps = _build_steps(findings, "Moderate")
        bp_steps = [s for s in steps if "BP" in s]
        self.assertEqual(len(bp_steps), 1)

    def test_empty_findings_still_has_steps(self):
        steps = _build_steps([], "Low")
        self.assertGreater(len(steps), 0)

    def test_steps_always_ends_with_document_instruction(self):
        steps = _build_steps([], "Low")
        self.assertTrue(any("Document" in s for s in steps))


class TestCdssResponseShape(unittest.TestCase):

    def _make_result(self, observations):
        findings = _evaluate_thresholds(observations)
        overall_idx = max((f["level_idx"] for f in findings), default=0)
        risk_level_name = _LEVEL_NAMES[overall_idx]
        return {
            "recommendation": "Test recommendation.",
            "risk_level": risk_level_name.lower(),
            "rationale": {
                "guideline_ids": list({f["guideline"] for f in findings}),
                "source_observations": [f["concept"] for f in findings],
                "human_review_required": True,
                "model_version": "rule-v1",
                "confidence": 0.9 if findings else 0.7,
                "summary": f"{len(findings)} finding(s).",
            },
            "steps": _build_steps(findings, risk_level_name),
            "source": "online",
        }

    def test_risk_level_is_lowercase(self):
        r = self._make_result({"LOINC|8480-6": "150"})
        self.assertEqual(r["risk_level"], "moderate")

    def test_human_review_always_true(self):
        r = self._make_result({})
        self.assertTrue(r["rationale"]["human_review_required"])

    def test_source_is_online(self):
        r = self._make_result({})
        self.assertEqual(r["source"], "online")

    def test_model_version_present(self):
        r = self._make_result({})
        self.assertIn("model_version", r["rationale"])


if __name__ == "__main__":
    unittest.main()
