"""
Rule-based clinical risk scoring for the Case DocType.

Thresholds from WHO/JNC8 hypertension guidelines and ADA diabetes standards.
LOINC concept keys must stay in sync with _VITALS_META in patient.js.
"""

import frappe

# concept → {"moderate": threshold, "high": threshold, "label": human-readable}
_RISK_THRESHOLDS = {
	"LOINC|8480-6": {"moderate": 140.0, "high": 160.0, "label": "Elevated BP (systolic)"},
	"LOINC|8462-4": {"moderate": 90.0, "high": 100.0, "label": "Elevated BP (diastolic)"},
	"LOINC|2339-0": {"moderate": 7.0, "high": 11.1, "label": "High blood glucose"},
	"LOINC|4548-4": {"moderate": 7.0, "high": 9.0, "label": "Poor glycaemic control (HbA1c)"},
}

_LEVELS = {"Low": 0, "Moderate": 1, "High": 2}
_LEVEL_NAMES = ["Low", "Moderate", "High"]


def compute_risk(doc, event):
	"""doc_events hook — Observation.after_insert.
	Fetches the latest value per scored concept for this case and writes
	risk_level + risk_factors back to the Case record.
	"""
	case_name = getattr(doc, "case", None)
	if not case_name:
		return

	concepts = list(_RISK_THRESHOLDS.keys())
	rows = frappe.get_all(
		"Observation",
		filters={"case": case_name, "concept": ["in", concepts]},
		fields=["concept", "value", "observed_dt"],
		order_by="observed_dt desc",
	)

	latest = {}
	for row in rows:
		if row.concept not in latest:
			try:
				latest[row.concept] = float(row.value)
			except (ValueError, TypeError):
				pass

	level_idx = 0
	factors = []
	for concept, thresholds in _RISK_THRESHOLDS.items():
		val = latest.get(concept)
		if val is None:
			continue
		if val >= thresholds["high"]:
			level_idx = max(level_idx, 2)
			factors.append(thresholds["label"])
		elif val >= thresholds["moderate"]:
			level_idx = max(level_idx, 1)
			factors.append(thresholds["label"])

	frappe.db.set_value(
		"Case",
		case_name,
		{
			"risk_level": _LEVEL_NAMES[level_idx],
			"risk_factors": " · ".join(factors) if factors else "",
		},
		update_modified=False,
	)


def update_last_encounter_dt(doc, event):
	"""doc_events hook — Encounter.after_insert.
	Keeps Case.last_encounter_dt current so the Overdue Follow-ups
	number card filter stays accurate.
	"""
	case_name = getattr(doc, "case", None)
	if not case_name or not doc.encounter_dt:
		return
	frappe.db.set_value("Case", case_name, "last_encounter_dt", doc.encounter_dt, update_modified=False)
