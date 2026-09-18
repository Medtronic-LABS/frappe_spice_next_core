import frappe


@frappe.whitelist(methods=["POST"])
def get_household_summary(household):
	patients = frappe.get_all(
		"Patient",
		filters={"primary_household": household},
		fields=["name", "full_name", "dob", "gender"],
	)
	patient_names = [p.name for p in patients]

	members = []
	for pt in patients:
		cases = frappe.get_all(
			"Case",
			filters={"patient": pt.name},
			fields=["name", "programme", "status"],
		)
		case_names = [c.name for c in cases]

		vitals = {}
		if case_names:
			for row in frappe.get_all(
				"Observation",
				filters={"case": ["in", case_names]},
				fields=["concept", "value"],
				order_by="observed_dt desc",
			):
				if row.concept not in vitals:
					vitals[row.concept] = row.value

		members.append(
			{
				"name": pt.name,
				"full_name": pt.full_name,
				"dob": str(pt.dob) if pt.dob else None,
				"gender": pt.gender,
				"programmes": [{"programme": c.programme, "status": c.status} for c in cases],
				"bp_sys": vitals.get("LOINC|8480-6"),
				"glucose": vitals.get("LOINC|2339-0"),
				"hba1c": vitals.get("LOINC|4548-4"),
			}
		)

	all_cases = (
		frappe.get_all(
			"Case",
			filters={"patient": ["in", patient_names]} if patient_names else {"name": ""},
			fields=["name", "programme", "status"],
		)
		if patient_names
		else []
	)

	active_by_prog = {}
	for c in all_cases:
		if c.status == "Active":
			active_by_prog[c.programme] = active_by_prog.get(c.programme, 0) + 1

	all_case_names = [c.name for c in all_cases]
	encounter_count = 0
	last_enc_dt = None
	if all_case_names:
		encounter_count = frappe.db.count("Encounter", {"case": ["in", all_case_names]})
		last_rows = frappe.get_all(
			"Encounter",
			filters={"case": ["in", all_case_names]},
			fields=["encounter_dt"],
			order_by="encounter_dt desc",
			limit=1,
		)
		if last_rows:
			last_enc_dt = str(last_rows[0].encounter_dt)[:10]

	hh_doc = frappe.get_doc("Household", household)
	cfg = frappe.get_single("UHIS Settings")

	return {
		"members": members,
		"active_by_prog": active_by_prog,
		"total_encounters": encounter_count,
		"last_encounter_dt": last_enc_dt,
		"ai_summary": hh_doc.get("ai_summary"),
		"ai_summary_generated_at": hh_doc.get("ai_summary_generated_at"),
		"ai_insights_enabled": bool(cfg.ai_insights_enabled),
	}
