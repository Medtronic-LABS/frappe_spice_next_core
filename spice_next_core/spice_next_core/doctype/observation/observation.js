// Copyright (c) 2026, Medtronic Labs and contributors
// For license information, please see license.txt

// Clinical thresholds matching risk/score.py _RISK_THRESHOLDS
const _OBS_THRESHOLDS = {
	"LOINC|8480-6": { moderate: 140.0, high: 160.0 },  // Systolic BP
	"LOINC|8462-4": { moderate: 90.0,  high: 100.0 },  // Diastolic BP
	"LOINC|2339-0": { moderate: 7.0,   high: 11.1  },  // Blood glucose
	"LOINC|4548-4": { moderate: 7.0,   high: 9.0   },  // HbA1c
};

frappe.listview_settings["Observation"] = {
	get_indicator(doc) {
		const thr = _OBS_THRESHOLDS[doc.concept];
		if (!thr) return ["Normal", "green", "concept,=," + doc.concept];

		const val = parseFloat(doc.value);
		if (isNaN(val)) return ["—", "gray"];

		if (val >= thr.high)     return ["⚠ High",     "red",    "concept,=," + doc.concept];
		if (val >= thr.moderate) return ["! Elevated",  "orange", "concept,=," + doc.concept];
		return ["✓ Normal", "green", "concept,=," + doc.concept];
	},
	add_fields: ["concept", "value"],
};
