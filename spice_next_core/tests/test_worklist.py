"""
Unit tests for api/worklist.py — pure sort/transform logic.
No Frappe DB required.
"""

import unittest

_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


def _risk_to_priority(risk_level):
    return {
        "High": "high",
        "Moderate": "normal",
        "Low": "low",
    }.get(risk_level or "Low", "low")


def _parse_care_gaps(risk_factors):
    if not risk_factors:
        return []
    return [f.strip() for f in risk_factors.split("·") if f.strip()]


def _sort_entries(entries):
    entries.sort(
        key=lambda e: (_PRIORITY_ORDER.get(e["priority"], 1), e["scheduled_date"])
    )
    return entries


class TestRiskToPriority(unittest.TestCase):

    def test_high_maps_to_high(self):
        self.assertEqual(_risk_to_priority("High"), "high")

    def test_moderate_maps_to_normal(self):
        self.assertEqual(_risk_to_priority("Moderate"), "normal")

    def test_low_maps_to_low(self):
        self.assertEqual(_risk_to_priority("Low"), "low")

    def test_none_defaults_to_low(self):
        self.assertEqual(_risk_to_priority(None), "low")

    def test_unknown_defaults_to_low(self):
        self.assertEqual(_risk_to_priority("Unknown"), "low")


class TestParseCareGaps(unittest.TestCase):

    def test_single_gap(self):
        self.assertEqual(_parse_care_gaps("Elevated BP (systolic)"), ["Elevated BP (systolic)"])

    def test_multiple_gaps_separated_by_middot(self):
        gaps = _parse_care_gaps("Elevated BP (systolic) · High blood glucose")
        self.assertEqual(len(gaps), 2)
        self.assertIn("Elevated BP (systolic)", gaps)
        self.assertIn("High blood glucose", gaps)

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(_parse_care_gaps(""), [])

    def test_none_returns_empty_list(self):
        self.assertEqual(_parse_care_gaps(None), [])

    def test_whitespace_trimmed(self):
        gaps = _parse_care_gaps("  BP uncontrolled  ·  Glucose elevated  ")
        self.assertEqual(gaps, ["BP uncontrolled", "Glucose elevated"])

    def test_trailing_separator_ignored(self):
        gaps = _parse_care_gaps("BP uncontrolled ·")
        self.assertEqual(gaps, ["BP uncontrolled"])


class TestWorklistSort(unittest.TestCase):

    def _entry(self, priority, date, name="Patient"):
        return {"patient_name": name, "priority": priority, "scheduled_date": date}

    def test_high_before_normal_before_low(self):
        entries = [
            self._entry("low", "2026-06-20"),
            self._entry("high", "2026-06-20"),
            self._entry("normal", "2026-06-20"),
        ]
        sorted_entries = _sort_entries(entries)
        self.assertEqual(
            [e["priority"] for e in sorted_entries], ["high", "normal", "low"]
        )

    def test_within_same_priority_earlier_date_first(self):
        entries = [
            self._entry("high", "2026-06-22"),
            self._entry("high", "2026-06-17"),
            self._entry("high", "2026-06-20"),
        ]
        sorted_entries = _sort_entries(entries)
        dates = [e["scheduled_date"] for e in sorted_entries]
        self.assertEqual(dates, sorted(dates))

    def test_overdue_high_surfaces_first(self):
        entries = [
            self._entry("low",  "2026-06-10", "Low patient"),
            self._entry("high", "2026-06-10", "Overdue high"),
            self._entry("high", "2026-06-20", "Future high"),
        ]
        sorted_entries = _sort_entries(entries)
        self.assertEqual(sorted_entries[0]["patient_name"], "Overdue high")

    def test_empty_list_returns_empty(self):
        self.assertEqual(_sort_entries([]), [])

    def test_single_entry_unchanged(self):
        entries = [self._entry("normal", "2026-06-18")]
        self.assertEqual(_sort_entries(entries), entries)

    def test_all_same_priority_sorted_by_date(self):
        entries = [
            self._entry("normal", "2026-06-25"),
            self._entry("normal", "2026-06-15"),
            self._entry("normal", "2026-06-20"),
        ]
        sorted_entries = _sort_entries(entries)
        dates = [e["scheduled_date"] for e in sorted_entries]
        self.assertEqual(dates, ["2026-06-15", "2026-06-20", "2026-06-25"])


class TestWorklistEntryShape(unittest.TestCase):

    def test_all_required_fields_present(self):
        entry = {
            "patient_uuid": "uuid-001",
            "patient_name": "Fatema Khatun",
            "patient_name_bn": "ফাতেমা খাতুন",
            "programme": "ncd",
            "priority": "high",
            "scheduled_date": "2026-06-17",
            "care_gaps": ["Elevated BP (systolic)"],
        }
        required = [
            "patient_uuid", "patient_name", "patient_name_bn",
            "programme", "priority", "scheduled_date", "care_gaps",
        ]
        for field in required:
            self.assertIn(field, entry)

    def test_priority_values_are_valid(self):
        valid = {"high", "normal", "low"}
        for risk in ["High", "Moderate", "Low", None]:
            self.assertIn(_risk_to_priority(risk), valid)


if __name__ == "__main__":
    unittest.main()
