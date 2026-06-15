"""Seed dummy data for uhis_next_core development testing."""

import frappe


def _insert(doctype, name, **fields):
	"""Insert a doc if it doesn't already exist; return its name."""
	if frappe.db.exists(doctype, name):
		return name
	doc = frappe.new_doc(doctype)
	doc.name = name
	for k, v in fields.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True)
	return doc.name


def _insert_clinical(doctype, client_uuid, **fields):
	"""Insert a clinical doc (autoname=field:client_uuid) if not present."""
	existing = frappe.db.get_value(doctype, {"client_uuid": client_uuid}, "name")
	if existing:
		return existing
	doc = frappe.new_doc(doctype)
	doc.client_uuid = client_uuid
	for k, v in fields.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True)
	return doc.name


_CONCEPTS = {
	"bp_sys": "LOINC|8480-6",
	"bp_dia": "LOINC|8462-4",
	"weight": "LOINC|29463-7",
	"glucose": "LOINC|2339-0",
	"tobacco": "SNOMED|77176002",
	"height": "LOINC|8302-2",
	"bmi": "LOINC|39156-5",
	"hba1c": "LOINC|4548-4",
	"lmp": "LOINC|8665-2",
	"gravida": "LOINC|11996-6",
	"parity": "LOINC|11977-6",
}


def _obs(encounter, case, concept_key, value, dt):
	"""Insert one Observation for encounter+concept if not already present."""
	concept = _CONCEPTS[concept_key]
	if frappe.db.get_value("Observation", {"encounter": encounter, "concept": concept}, "name"):
		return
	frappe.get_doc(
		{
			"doctype": "Observation",
			"client_uuid": frappe.generate_hash(length=36),
			"encounter": encounter,
			"case": case,
			"concept": concept,
			"value": str(value),
			"observed_dt": dt,
			"source_form": "clinical_question",
			"source_docname": concept_key,
		}
	).insert(ignore_permissions=True)


def run():
	frappe.set_user("Administrator")

	# ── Geography Types ───────────────────────────────────────────────────────
	for name, label, level, cc in [
		("Country", "Country", 1, "KE"),
		("Province", "Province", 2, "KE"),
		("District", "District", 3, "KE"),
		("Ward", "Ward", 4, "KE"),
	]:
		_insert("Geography Type", name, label=label, level_order=level, country_code=cc)

	# ── Geography Nodes ───────────────────────────────────────────────────────
	_insert("Geography Node", "Kenya", label="Kenya", geography_type="Country", parent_node=None)
	_insert(
		"Geography Node", "Nairobi Province", label="Nairobi", geography_type="Province", parent_node="Kenya"
	)
	_insert(
		"Geography Node",
		"Westlands District",
		label="Westlands",
		geography_type="District",
		parent_node="Nairobi Province",
	)
	_insert(
		"Geography Node",
		"Parklands Ward",
		label="Parklands",
		geography_type="Ward",
		parent_node="Westlands District",
	)

	# ── Organization & Facility ───────────────────────────────────────────────
	_insert("Organization", "MOH Kenya", label="Ministry of Health Kenya", org_type="Government")
	_insert(
		"Facility",
		"Parklands Health Centre",
		label="Parklands Health Centre",
		facility_type="Health Centre",
		organization="MOH Kenya",
		geography_node="Parklands Ward",
	)
	_insert(
		"Facility",
		"Nairobi County Hospital",
		label="Nairobi County Hospital",
		facility_type="District Hospital",
		organization="MOH Kenya",
		geography_node="Westlands District",
	)

	# ── Providers ─────────────────────────────────────────────────────────────
	_insert(
		"Provider", "P-001", full_name="Jane Wanjiku", role_type="CHW", facility="Parklands Health Centre"
	)
	_insert(
		"Provider", "P-002", full_name="David Kamau", role_type="Nurse", facility="Parklands Health Centre"
	)

	# ── Care Team ─────────────────────────────────────────────────────────────
	if not frappe.db.exists("Care Team", "CT-Parklands-01"):
		ct = frappe.new_doc("Care Team")
		ct.name = "CT-Parklands-01"
		ct.label = "Parklands Team 01"
		ct.facility = "Parklands Health Centre"
		ct.append("members", {"provider": "P-001", "role": "CHW"})
		ct.append("members", {"provider": "P-002", "role": "Nurse"})
		ct.insert(ignore_permissions=True)

	# ── Original 2 households + 3 patients ───────────────────────────────────
	hh1 = _insert_clinical(
		"Household",
		"hh-ochieng-001",
		address="14 Banana Hill Rd, Parklands",
		geography_node="Parklands Ward",
		socioeconomic_tier="2",
	)
	hh2 = _insert_clinical(
		"Household",
		"hh-wanjiku-001",
		address="7 Westlands Avenue",
		geography_node="Parklands Ward",
		socioeconomic_tier="3",
	)

	pt1 = _insert_clinical(
		"Patient",
		"pt-ochieng-001",
		full_name="John Ochieng",
		gender="Male",
		dob="1978-03-12",
		phone="+254712345678",
		primary_household=hh1,
	)
	pt2 = _insert_clinical(
		"Patient",
		"pt-hassan-001",
		full_name="Amina Hassan",
		gender="Female",
		dob="1985-07-22",
		phone="+254723456789",
		primary_household=hh2,
	)
	pt3 = _insert_clinical(
		"Patient",
		"pt-kamau-001",
		full_name="Peter Kamau",
		gender="Male",
		dob="1960-11-05",
		phone="+254734567890",
		primary_household=hh1,
	)

	cs1 = _insert_clinical(
		"Case",
		"cs-ochieng-001",
		patient=pt1,
		programme="NCD",
		care_team="CT-Parklands-01",
		status="Active",
		opened_on="2026-01-01",
	)
	cs2 = _insert_clinical(
		"Case",
		"cs-hassan-001",
		patient=pt2,
		programme="NCD",
		care_team="CT-Parklands-01",
		status="Active",
		opened_on="2026-01-01",
	)
	cs3 = _insert_clinical(
		"Case",
		"cs-kamau-001",
		patient=pt3,
		programme="NCD",
		care_team="CT-Parklands-01",
		status="Active",
		opened_on="2026-01-01",
	)

	_insert_clinical(
		"Encounter",
		"enc-ochieng-scr-001",
		case=cs1,
		patient=pt1,
		provider="P-001",
		facility="Parklands Health Centre",
		encounter_type="Routine",
		encounter_dt="2026-01-03 10:00:00",
	)
	_insert_clinical(
		"Encounter",
		"enc-hassan-scr-001",
		case=cs2,
		patient=pt2,
		provider="P-001",
		facility="Parklands Health Centre",
		encounter_type="Routine",
		encounter_dt="2026-01-03 10:00:00",
	)
	_insert_clinical(
		"Encounter",
		"enc-kamau-scr-001",
		case=cs3,
		patient=pt3,
		provider="P-002",
		facility="Parklands Health Centre",
		encounter_type="Routine",
		encounter_dt="2026-01-03 10:00:00",
	)

	# ── Seed referral (used by FHIR ServiceRequest E2E test) ─────────────────
	_insert_clinical(
		"Referral",
		"ref-mutua-james-001",
		case="cs-mutua-james-001",
		from_facility="Parklands Health Centre",
		to_facility="Nairobi County Hospital",
		status="Pending",
		reason="Uncontrolled hypertension, requires cardiology specialist review",
	)

	# ── Extended seed: 5 households, 5 patients each ──────────────────────────
	seed_households()

	frappe.db.commit()

	hh_count = frappe.db.count("Household")
	pt_count = frappe.db.count("Patient")
	cs_count = frappe.db.count("Case")
	enc_count = frappe.db.count("Encounter")
	obs_count = frappe.db.count("Observation")

	print("\n── Seed complete ─────────────────────────────────────")
	print("  Geography Types  : 4 (Country → Ward)")
	print("  Geography Nodes  : 4 (Kenya → Parklands Ward)")
	print("  Organization     : MOH Kenya")
	print("  Facility         : Parklands Health Centre")
	print("  Providers        : Jane Wanjiku (CHW), David Kamau (Nurse)")
	print("  Care Team        : CT-Parklands-01")
	print(f"  Households       : {hh_count}")
	print(f"  Patients         : {pt_count}")
	print(f"  Cases            : {cs_count}")
	print(f"  Encounters       : {enc_count}")
	print(f"  Observations     : {obs_count}")
	print("──────────────────────────────────────────────────────")


def seed_households():
	"""Create 5 households × 5 patients with staggered encounters and observations."""
	FAC = "Parklands Health Centre"
	CT = "CT-Parklands-01"
	GEO = "Parklands Ward"

	# ── HH-1: Mutua ──────────────────────────────────────────────────────────
	hh = _insert_clinical(
		"Household",
		"hh-mutua-001",
		address="22 Oak Avenue, Parklands",
		geography_node=GEO,
		socioeconomic_tier="2",
	)

	# James Mutua — HTN, BP declining on treatment (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-mutua-james-001",
		full_name="James Mutua",
		gender="Male",
		dob="1975-04-10",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-mutua-james-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, etype, obs in [
		(
			"enc-mutua-james-001",
			"2026-01-10 10:00:00",
			"Routine",
			[("bp_sys", 162), ("bp_dia", 98), ("weight", 78), ("glucose", 5.8), ("tobacco", 1)],
		),
		(
			"enc-mutua-james-002",
			"2026-02-14 10:00:00",
			"Followup",
			[("bp_sys", 158), ("bp_dia", 96), ("weight", 78), ("glucose", 5.6), ("tobacco", 1)],
		),
		(
			"enc-mutua-james-003",
			"2026-03-20 10:00:00",
			"Followup",
			[("bp_sys", 154), ("bp_dia", 92), ("weight", 77), ("glucose", 5.7), ("tobacco", 0)],
		),
		(
			"enc-mutua-james-004",
			"2026-05-08 10:00:00",
			"Followup",
			[("bp_sys", 148), ("bp_dia", 90), ("weight", 77), ("glucose", 5.5), ("tobacco", 0)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type=etype,
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Grace Mutua — Normal, stable (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-mutua-grace-001",
		full_name="Grace Mutua",
		gender="Female",
		dob="1980-08-15",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-mutua-grace-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-mutua-grace-001",
			"2026-02-06 09:00:00",
			[("bp_sys", 118), ("bp_dia", 76), ("weight", 65), ("glucose", 4.9)],
		),
		(
			"enc-mutua-grace-002",
			"2026-04-10 09:00:00",
			[("bp_sys", 120), ("bp_dia", 78), ("weight", 65), ("glucose", 5.0)],
		),
		(
			"enc-mutua-grace-003",
			"2026-06-05 09:00:00",
			[("bp_sys", 116), ("bp_dia", 74), ("weight", 64), ("glucose", 4.8)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Kevin Mutua — Borderline BP+glucose (2 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-mutua-kevin-001",
		full_name="Kevin Mutua",
		gender="Male",
		dob="2001-02-20",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-mutua-kevin-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-03-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-mutua-kevin-001",
			"2026-03-05 11:00:00",
			[("bp_sys", 132), ("bp_dia", 84), ("weight", 82), ("glucose", 5.6)],
		),
		(
			"enc-mutua-kevin-002",
			"2026-05-21 11:00:00",
			[("bp_sys", 134), ("bp_dia", 86), ("weight", 83), ("glucose", 5.8)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Faith Mutua — Pregnant ANC (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-mutua-faith-001",
		full_name="Faith Mutua",
		gender="Female",
		dob="2003-11-05",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-mutua-faith-001",
		patient=pt,
		programme="PW Profile",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-mutua-faith-001",
			"2026-01-08 08:30:00",
			[("lmp", "2025-11-12"), ("gravida", 1), ("parity", 0), ("weight", 58)],
		),
		(
			"enc-mutua-faith-002",
			"2026-03-12 08:30:00",
			[("lmp", "2025-11-12"), ("gravida", 1), ("parity", 0), ("weight", 61)],
		),
		(
			"enc-mutua-faith-003",
			"2026-05-15 08:30:00",
			[("lmp", "2025-11-12"), ("gravida", 1), ("parity", 0), ("weight", 64)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Mary Mutua — Elderly HTN (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-mutua-mary-001",
		full_name="Mary Mutua",
		gender="Female",
		dob="1955-06-03",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-mutua-mary-001",
		patient=pt,
		programme="Eye Care",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-mutua-mary-001",
			"2026-02-20 10:30:00",
			[("bp_sys", 156), ("bp_dia", 92), ("weight", 58), ("height", 152), ("bmi", 25.1)],
		),
		(
			"enc-mutua-mary-002",
			"2026-04-17 10:30:00",
			[("bp_sys", 160), ("bp_dia", 94), ("weight", 58), ("height", 152), ("bmi", 25.1)],
		),
		(
			"enc-mutua-mary-003",
			"2026-06-06 10:30:00",
			[("bp_sys", 152), ("bp_dia", 88), ("weight", 57), ("height", 152), ("bmi", 24.7)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# ── HH-2: Njoroge ────────────────────────────────────────────────────────
	hh = _insert_clinical(
		"Household",
		"hh-njoroge-001",
		address="5 Riverside Close, Parklands",
		geography_node=GEO,
		socioeconomic_tier="3",
	)

	# Samuel Njoroge — Combined HTN+DM (5 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-njoroge-samuel-001",
		full_name="Samuel Njoroge",
		gender="Male",
		dob="1971-09-22",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-njoroge-samuel-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, etype, obs in [
		(
			"enc-njoroge-samuel-001",
			"2026-01-06 09:00:00",
			"Routine",
			[("bp_sys", 170), ("bp_dia", 104), ("glucose", 8.2), ("hba1c", 7.8), ("weight", 91)],
		),
		(
			"enc-njoroge-samuel-002",
			"2026-02-10 09:00:00",
			"Followup",
			[("bp_sys", 165), ("bp_dia", 100), ("glucose", 7.9), ("weight", 91)],
		),
		(
			"enc-njoroge-samuel-003",
			"2026-03-17 09:00:00",
			"Followup",
			[("bp_sys", 160), ("bp_dia", 98), ("glucose", 8.5), ("hba1c", 7.5), ("weight", 90)],
		),
		(
			"enc-njoroge-samuel-004",
			"2026-04-22 09:00:00",
			"Followup",
			[("bp_sys", 158), ("bp_dia", 96), ("glucose", 7.6), ("weight", 90)],
		),
		(
			"enc-njoroge-samuel-005",
			"2026-05-28 09:00:00",
			"Followup",
			[("bp_sys", 154), ("bp_dia", 94), ("glucose", 7.2), ("weight", 89)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type=etype,
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Rachel Njoroge — Improving trend (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-njoroge-rachel-001",
		full_name="Rachel Njoroge",
		gender="Female",
		dob="1976-01-14",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-njoroge-rachel-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-njoroge-rachel-001",
			"2026-01-20 10:00:00",
			[("bp_sys", 155), ("bp_dia", 96), ("glucose", 7.1)],
		),
		(
			"enc-njoroge-rachel-002",
			"2026-03-03 10:00:00",
			[("bp_sys", 148), ("bp_dia", 90), ("glucose", 6.5)],
		),
		(
			"enc-njoroge-rachel-003",
			"2026-04-14 10:00:00",
			[("bp_sys", 140), ("bp_dia", 86), ("glucose", 6.0)],
		),
		(
			"enc-njoroge-rachel-004",
			"2026-06-02 10:00:00",
			[("bp_sys", 130), ("bp_dia", 82), ("glucose", 5.4)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Daniel Njoroge — Borderline glucose (2 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-njoroge-daniel-001",
		full_name="Daniel Njoroge",
		gender="Male",
		dob="1998-05-08",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-njoroge-daniel-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-02-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-njoroge-daniel-001",
			"2026-02-24 11:00:00",
			[("bp_sys", 124), ("bp_dia", 80), ("glucose", 6.1), ("weight", 88)],
		),
		(
			"enc-njoroge-daniel-002",
			"2026-05-19 11:00:00",
			[("bp_sys", 128), ("bp_dia", 82), ("glucose", 6.3), ("weight", 89)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Esther Njoroge — Pregnant multigravida ANC (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-njoroge-esther-001",
		full_name="Esther Njoroge",
		gender="Female",
		dob="2001-09-30",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-njoroge-esther-001",
		patient=pt,
		programme="PW Profile",
		care_team=CT,
		status="Active",
		opened_on="2026-02-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-njoroge-esther-001",
			"2026-02-03 08:00:00",
			[("lmp", "2026-01-15"), ("gravida", 2), ("parity", 1), ("weight", 67)],
		),
		(
			"enc-njoroge-esther-002",
			"2026-03-10 08:00:00",
			[("lmp", "2026-01-15"), ("gravida", 2), ("parity", 1), ("weight", 68)],
		),
		(
			"enc-njoroge-esther-003",
			"2026-04-14 08:00:00",
			[("lmp", "2026-01-15"), ("gravida", 2), ("parity", 1), ("weight", 70)],
		),
		(
			"enc-njoroge-esther-004",
			"2026-05-19 08:00:00",
			[("lmp", "2026-01-15"), ("gravida", 2), ("parity", 1), ("weight", 72)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Joseph Njoroge — Elderly, smoker + HTN (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-njoroge-joseph-001",
		full_name="Joseph Njoroge",
		gender="Male",
		dob="1953-03-16",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-njoroge-joseph-001",
		patient=pt,
		programme="Eye Care",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-njoroge-joseph-001",
			"2026-01-15 14:00:00",
			[("bp_sys", 168), ("bp_dia", 100), ("tobacco", 1), ("weight", 74)],
		),
		(
			"enc-njoroge-joseph-002",
			"2026-04-08 14:00:00",
			[("bp_sys", 172), ("bp_dia", 104), ("tobacco", 1), ("weight", 74)],
		),
		(
			"enc-njoroge-joseph-003",
			"2026-06-04 14:00:00",
			[("bp_sys", 160), ("bp_dia", 98), ("tobacco", 1), ("weight", 73)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# ── HH-3: Achieng ────────────────────────────────────────────────────────
	hh = _insert_clinical(
		"Household",
		"hh-achieng-001",
		address="18 Mpaka Road, Parklands",
		geography_node=GEO,
		socioeconomic_tier="2",
	)

	# David Achieng — Worsening trend (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-achieng-david-001",
		full_name="David Achieng",
		gender="Male",
		dob="1983-07-14",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-achieng-david-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		("enc-achieng-david-001", "2026-01-12 10:00:00", [("bp_sys", 138), ("bp_dia", 86), ("glucose", 5.8)]),
		("enc-achieng-david-002", "2026-02-16 10:00:00", [("bp_sys", 144), ("bp_dia", 90), ("glucose", 6.4)]),
		("enc-achieng-david-003", "2026-04-07 10:00:00", [("bp_sys", 152), ("bp_dia", 96), ("glucose", 7.2)]),
		(
			"enc-achieng-david-004",
			"2026-06-03 10:00:00",
			[("bp_sys", 162), ("bp_dia", 100), ("glucose", 8.1)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Mercy Achieng — Pregnant primigravida (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-achieng-mercy-001",
		full_name="Mercy Achieng",
		gender="Female",
		dob="1988-02-28",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-achieng-mercy-001",
		patient=pt,
		programme="PW Profile",
		care_team=CT,
		status="Active",
		opened_on="2026-03-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-achieng-mercy-001",
			"2026-03-05 08:30:00",
			[("lmp", "2026-02-20"), ("gravida", 1), ("parity", 0), ("weight", 70)],
		),
		(
			"enc-achieng-mercy-002",
			"2026-04-16 08:30:00",
			[("lmp", "2026-02-20"), ("gravida", 1), ("parity", 0), ("weight", 73)],
		),
		(
			"enc-achieng-mercy-003",
			"2026-06-04 08:30:00",
			[("lmp", "2026-02-20"), ("gravida", 1), ("parity", 0), ("weight", 76)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Brian Achieng — Normal young adult (2 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-achieng-brian-001",
		full_name="Brian Achieng",
		gender="Male",
		dob="2007-11-10",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-achieng-brian-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-03-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-achieng-brian-001",
			"2026-03-18 11:00:00",
			[("bp_sys", 116), ("bp_dia", 72), ("glucose", 4.8), ("weight", 65)],
		),
		(
			"enc-achieng-brian-002",
			"2026-06-08 11:00:00",
			[("bp_sys", 118), ("bp_dia", 74), ("glucose", 4.9), ("weight", 66)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Susan Achieng — Diabetes only (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-achieng-susan-001",
		full_name="Susan Achieng",
		gender="Female",
		dob="1960-04-25",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-achieng-susan-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-achieng-susan-001",
			"2026-01-09 09:30:00",
			[("bp_sys", 140), ("bp_dia", 86), ("glucose", 9.2), ("hba1c", 8.2)],
		),
		("enc-achieng-susan-002", "2026-03-14 09:30:00", [("bp_sys", 138), ("bp_dia", 84), ("glucose", 8.8)]),
		(
			"enc-achieng-susan-003",
			"2026-04-21 09:30:00",
			[("bp_sys", 142), ("bp_dia", 88), ("glucose", 9.5), ("hba1c", 8.0)],
		),
		("enc-achieng-susan-004", "2026-06-07 09:30:00", [("bp_sys", 136), ("bp_dia", 82), ("glucose", 8.0)]),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Thomas Achieng — Elderly HTN (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-achieng-thomas-001",
		full_name="Thomas Achieng",
		gender="Male",
		dob="1958-12-01",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-achieng-thomas-001",
		patient=pt,
		programme="Eye Care",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		("enc-achieng-thomas-001", "2026-02-11 13:00:00", [("bp_sys", 154), ("bp_dia", 90), ("weight", 70)]),
		("enc-achieng-thomas-002", "2026-04-09 13:00:00", [("bp_sys", 158), ("bp_dia", 94), ("weight", 70)]),
		("enc-achieng-thomas-003", "2026-06-06 13:00:00", [("bp_sys", 150), ("bp_dia", 88), ("weight", 69)]),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# ── HH-4: Karimi ─────────────────────────────────────────────────────────
	hh = _insert_clinical(
		"Household",
		"hh-karimi-001",
		address="9 Gitanga Road, Parklands",
		geography_node=GEO,
		socioeconomic_tier="1",
	)

	# John Karimi — Smoker + HTN (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-karimi-john-001",
		full_name="John Karimi",
		gender="Male",
		dob="1977-01-18",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-karimi-john-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-karimi-john-001",
			"2026-01-19 10:00:00",
			[("bp_sys", 168), ("bp_dia", 102), ("tobacco", 1), ("weight", 85)],
		),
		(
			"enc-karimi-john-002",
			"2026-03-10 10:00:00",
			[("bp_sys", 164), ("bp_dia", 98), ("tobacco", 1), ("weight", 85)],
		),
		(
			"enc-karimi-john-003",
			"2026-04-28 10:00:00",
			[("bp_sys", 160), ("bp_dia", 96), ("tobacco", 1), ("weight", 84)],
		),
		(
			"enc-karimi-john-004",
			"2026-06-08 10:00:00",
			[("bp_sys", 156), ("bp_dia", 94), ("tobacco", 1), ("weight", 84)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Lucy Karimi — Normal, good control (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-karimi-lucy-001",
		full_name="Lucy Karimi",
		gender="Female",
		dob="1980-11-22",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-karimi-lucy-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-karimi-lucy-001",
			"2026-02-17 09:00:00",
			[("bp_sys", 122), ("bp_dia", 78), ("glucose", 5.0), ("weight", 60)],
		),
		(
			"enc-karimi-lucy-002",
			"2026-04-21 09:00:00",
			[("bp_sys", 118), ("bp_dia", 76), ("glucose", 4.8), ("weight", 59)],
		),
		(
			"enc-karimi-lucy-003",
			"2026-06-03 09:00:00",
			[("bp_sys", 120), ("bp_dia", 78), ("glucose", 5.1), ("weight", 60)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Michael Karimi — Normal young adult (2 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-karimi-michael-001",
		full_name="Michael Karimi",
		gender="Male",
		dob="2003-08-15",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-karimi-michael-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-03-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-karimi-michael-001",
			"2026-03-24 11:00:00",
			[("bp_sys", 114), ("bp_dia", 72), ("glucose", 4.5), ("weight", 70)],
		),
		(
			"enc-karimi-michael-002",
			"2026-06-02 11:00:00",
			[("bp_sys", 112), ("bp_dia", 70), ("glucose", 4.6), ("weight", 71)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Jane Karimi — Pregnant ANC (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-karimi-jane-001",
		full_name="Jane Karimi",
		gender="Female",
		dob="2006-03-09",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-karimi-jane-001",
		patient=pt,
		programme="PW Profile",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-karimi-jane-001",
			"2026-01-22 08:00:00",
			[("lmp", "2025-12-15"), ("gravida", 1), ("parity", 0), ("weight", 54)],
		),
		(
			"enc-karimi-jane-002",
			"2026-03-25 08:00:00",
			[("lmp", "2025-12-15"), ("gravida", 1), ("parity", 0), ("weight", 57)],
		),
		(
			"enc-karimi-jane-003",
			"2026-06-04 08:00:00",
			[("lmp", "2025-12-15"), ("gravida", 1), ("parity", 0), ("weight", 60)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Wambui Karimi — Worsening DM (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-karimi-wambui-001",
		full_name="Wambui Karimi",
		gender="Female",
		dob="1958-09-14",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-karimi-wambui-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-karimi-wambui-001",
			"2026-01-28 10:00:00",
			[("bp_sys", 148), ("bp_dia", 90), ("glucose", 8.5), ("hba1c", 7.9)],
		),
		("enc-karimi-wambui-002", "2026-02-25 10:00:00", [("bp_sys", 150), ("bp_dia", 92), ("glucose", 9.2)]),
		(
			"enc-karimi-wambui-003",
			"2026-04-15 10:00:00",
			[("bp_sys", 152), ("bp_dia", 94), ("glucose", 10.1), ("hba1c", 8.4)],
		),
		(
			"enc-karimi-wambui-004",
			"2026-06-07 10:00:00",
			[("bp_sys", 154), ("bp_dia", 96), ("glucose", 11.3)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# ── HH-5: Hassan ─────────────────────────────────────────────────────────
	hh = _insert_clinical(
		"Household",
		"hh-hassan-001",
		address="31 Muthithi Road, Parklands",
		geography_node=GEO,
		socioeconomic_tier="3",
	)

	# Ahmed Hassan — Severe HTN+DM, improving (5 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-hassan-ahmed-001",
		full_name="Ahmed Hassan",
		gender="Male",
		dob="1974-05-06",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-hassan-ahmed-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, etype, obs in [
		(
			"enc-hassan-ahmed-001",
			"2026-01-05 09:00:00",
			"Routine",
			[("bp_sys", 178), ("bp_dia", 108), ("glucose", 10.5), ("hba1c", 9.1), ("weight", 94)],
		),
		(
			"enc-hassan-ahmed-002",
			"2026-02-09 09:00:00",
			"Followup",
			[("bp_sys", 172), ("bp_dia", 104), ("glucose", 9.8), ("weight", 93)],
		),
		(
			"enc-hassan-ahmed-003",
			"2026-03-16 09:00:00",
			"Followup",
			[("bp_sys", 168), ("bp_dia", 100), ("glucose", 9.0), ("hba1c", 8.5), ("weight", 92)],
		),
		(
			"enc-hassan-ahmed-004",
			"2026-04-20 09:00:00",
			"Followup",
			[("bp_sys", 162), ("bp_dia", 98), ("glucose", 8.5), ("weight", 91)],
		),
		(
			"enc-hassan-ahmed-005",
			"2026-06-01 09:00:00",
			"Followup",
			[("bp_sys", 155), ("bp_dia", 94), ("glucose", 7.8), ("weight", 90)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type=etype,
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Fatima Hassan — HTN improving on treatment (4 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-hassan-fatima-001",
		full_name="Fatima Hassan",
		gender="Female",
		dob="1979-10-12",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-hassan-fatima-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		("enc-hassan-fatima-001", "2026-01-26 10:00:00", [("bp_sys", 152), ("bp_dia", 96), ("weight", 70)]),
		("enc-hassan-fatima-002", "2026-03-30 10:00:00", [("bp_sys", 145), ("bp_dia", 90), ("weight", 70)]),
		("enc-hassan-fatima-003", "2026-05-11 10:00:00", [("bp_sys", 138), ("bp_dia", 86), ("weight", 69)]),
		("enc-hassan-fatima-004", "2026-06-08 10:00:00", [("bp_sys", 132), ("bp_dia", 82), ("weight", 69)]),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Omar Hassan — Borderline BP+glucose (2 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-hassan-omar-001",
		full_name="Omar Hassan",
		gender="Male",
		dob="2000-03-25",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-hassan-omar-001",
		patient=pt,
		programme="NCD",
		care_team=CT,
		status="Active",
		opened_on="2026-04-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-hassan-omar-001",
			"2026-04-06 11:00:00",
			[("bp_sys", 130), ("bp_dia", 84), ("glucose", 5.9), ("weight", 84)],
		),
		(
			"enc-hassan-omar-002",
			"2026-06-02 11:00:00",
			[("bp_sys", 133), ("bp_dia", 86), ("glucose", 6.0), ("weight", 85)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Zainab Hassan — Pregnant ANC (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-hassan-zainab-001",
		full_name="Zainab Hassan",
		gender="Female",
		dob="2002-07-18",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-hassan-zainab-001",
		patient=pt,
		programme="PW Profile",
		care_team=CT,
		status="Active",
		opened_on="2026-03-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-hassan-zainab-001",
			"2026-03-09 08:00:00",
			[("lmp", "2026-02-05"), ("gravida", 1), ("parity", 0), ("weight", 57)],
		),
		(
			"enc-hassan-zainab-002",
			"2026-04-13 08:00:00",
			[("lmp", "2026-02-05"), ("gravida", 1), ("parity", 0), ("weight", 59)],
		),
		(
			"enc-hassan-zainab-003",
			"2026-06-08 08:00:00",
			[("lmp", "2026-02-05"), ("gravida", 1), ("parity", 0), ("weight", 62)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-001",
			facility=FAC,
			encounter_type="Routine",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)

	# Halima Hassan — Elderly HTN+DM (3 encounters)
	pt = _insert_clinical(
		"Patient",
		"pt-hassan-halima-001",
		full_name="Halima Hassan",
		gender="Female",
		dob="1952-01-30",
		primary_household=hh,
	)
	cs = _insert_clinical(
		"Case",
		"cs-hassan-halima-001",
		patient=pt,
		programme="Eye Care",
		care_team=CT,
		status="Active",
		opened_on="2026-01-01",
	)
	for enc_id, dt, obs in [
		(
			"enc-hassan-halima-001",
			"2026-02-23 13:00:00",
			[("bp_sys", 176), ("bp_dia", 106), ("glucose", 8.8), ("weight", 54)],
		),
		(
			"enc-hassan-halima-002",
			"2026-04-27 13:00:00",
			[("bp_sys", 170), ("bp_dia", 102), ("glucose", 8.2), ("weight", 54)],
		),
		(
			"enc-hassan-halima-003",
			"2026-06-07 13:00:00",
			[("bp_sys", 164), ("bp_dia", 98), ("glucose", 7.9), ("weight", 53)],
		),
	]:
		enc = _insert_clinical(
			"Encounter",
			enc_id,
			case=cs,
			patient=pt,
			provider="P-002",
			facility=FAC,
			encounter_type="Followup",
			encounter_dt=dt,
		)
		for k, v in obs:
			_obs(enc, cs, k, v, dt)


_SYNCABLE = ["Patient", "Household", "Case", "Encounter", "Observation"]


def bump_sync_seqs():
	"""Initialize Sync Seq Counter and stamp sync_seq on all seed records that have sync_seq=0."""
	if not frappe.db.table_exists("Sync Seq Counter"):
		frappe.throw("Sync Seq Counter table missing — run bench migrate first")

	if not frappe.db.exists("Sync Seq Counter", "global"):
		frappe.get_doc(
			{
				"doctype": "Sync Seq Counter",
				"counter_name": "global",
				"current_seq": 0,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	total = 0
	for dt in _SYNCABLE:
		try:
			names = frappe.get_all(dt, filters={"sync_seq": 0}, pluck="name")
			for name in names:
				frappe.db.sql(
					"UPDATE `tabSync Seq Counter` SET current_seq = LAST_INSERT_ID(current_seq + 1)"
					" WHERE name = 'global'"
				)
				seq = frappe.db.sql("SELECT LAST_INSERT_ID()")[0][0]
				frappe.db.set_value(dt, name, "sync_seq", seq, update_modified=False)
			if names:
				frappe.db.commit()
			print(f"  {dt}: stamped {len(names)} records")
			total += len(names)
		except Exception as e:
			print(f"  {dt}: skipped — {e}")

	print(f"Total: {total} records stamped")
