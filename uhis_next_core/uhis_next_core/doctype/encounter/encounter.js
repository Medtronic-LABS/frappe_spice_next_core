// Copyright (c) 2026, Medtronic Labs and contributors
// For license information, please see license.txt

frappe.listview_settings["Encounter"] = {
	get_indicator(doc) {
		const map = {
			"Emergency": ["Emergency", "red",    "encounter_type,=,Emergency"],
			"Followup":  ["Follow-up", "orange", "encounter_type,=,Followup"],
			"Routine":   ["Routine",   "green",  "encounter_type,=,Routine"],
			"Referral":  ["Referral",  "blue",   "encounter_type,=,Referral"],
		};
		return map[doc.encounter_type] || ["—", "gray"];
	},
};
