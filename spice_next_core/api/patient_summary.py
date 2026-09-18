import frappe


@frappe.whitelist(methods=["POST"])
def get_health_summary(patient):
	doc = frappe.get_doc("Patient", patient)

	cases = frappe.get_all(
		"Case",
		filters={"patient": patient},
		fields=["name", "programme", "status", "opened_on"],
		order_by="opened_on asc",
	)
	case_names = [c.name for c in cases]

	latest_vitals = {}
	last_encounter = None
	encounter_count = 0

	if case_names:
		for row in frappe.get_all(
			"Observation",
			filters={"case": ["in", case_names]},
			fields=["concept", "value", "observed_dt"],
			order_by="observed_dt desc",
		):
			if row.concept not in latest_vitals:
				latest_vitals[row.concept] = {
					"value": row.value,
					"date": str(row.observed_dt)[:10],
				}

		enc_rows = frappe.get_all(
			"Encounter",
			filters={"case": ["in", case_names]},
			fields=["encounter_type", "encounter_dt", "provider", "facility"],
			order_by="encounter_dt desc",
			limit=1,
		)
		if enc_rows:
			e = enc_rows[0]
			last_encounter = {
				"encounter_type": e.encounter_type,
				"encounter_dt": str(e.encounter_dt)[:10],
				"provider": e.provider,
				"facility": e.facility,
			}

		encounter_count = frappe.db.count("Encounter", {"case": ["in", case_names]})

	cfg = frappe.get_single("UHIS Settings")

	return {
		"dob": str(doc.dob) if doc.dob else None,
		"gender": doc.gender,
		"phone": doc.phone,
		"primary_household": doc.primary_household,
		"cases": [{"programme": c.programme, "status": c.status} for c in cases],
		"latest_vitals": latest_vitals,
		"last_encounter": last_encounter,
		"encounter_count": encounter_count,
		"ai_summary": doc.ai_summary,
		"ai_summary_generated_at": doc.ai_summary_generated_at,
		"ai_insights_enabled": bool(cfg.ai_insights_enabled),
	}
