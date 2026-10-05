"""
Domain-write/read layer for uhis_lf_mobile's offline-sync wire contract (the
Frappe-side replacement for the legacy Java offline-service, scoped to this
one client). shukhee_integration.api.offline_sync is a thin wire-adapter
(auth/audit/idempotency bookkeeping only) that calls into this module for
every actual household/member/patient read or write -- this module owns the
domain model (Household/Patient/Household Member/Case/Encounter/Observation),
not the other way around.

  mobile_sync.upsert_household            one `households[]` wire item
  mobile_sync.upsert_member               one `householdMembers[]` wire item
                                           (nested under a household, or
                                           standalone)

Deliberately NOT catchment-scoped yet (unlike spice_next_core.api.sync.push) --
create is a write by an already-authenticated SK into their own village; read
paths (fetch_synced_data, member_assessment_history) are where catchment
scoping actually matters and will be added when those are implemented.
"""

import json

import frappe
from frappe import _


def _resolve_village(village_id):
	"""villageId -> Geography Node, via the legacy-village-id mapping added
	for this integration (see Geography Node.legacy_village_id's own doc
	comment). Returns None rather than throwing when unmapped -- a household
	write should never fail outright over a geography lookup miss; it
	persists with no village link, visible for manual reconciliation.

	villageId arrives on the wire as a STRING (LocalAssessmentEntity.
	toApiRequest's own comment: "Android's Assessment.villageId is non-null
	(String)"), with "0" as the client's own not-known sentinel -- both must
	be treated as unmapped, not as village id 0."""
	numeric = frappe.utils.cint(village_id)
	if not numeric:
		return None
	return frappe.db.get_value("Geography Node", {"legacy_village_id": numeric}, "name")


def _resolve_villages(village_ids):
	"""Plural form for the read paths (fetch_synced_data,
	member_assessment_history), which scope by the device's full villageIds
	list rather than one write's villageId."""
	numeric_ids = {n for n in (frappe.utils.cint(v) for v in village_ids or []) if n}
	if not numeric_ids:
		return []
	return frappe.get_all(
		"Geography Node", filters={"legacy_village_id": ["in", list(numeric_ids)]}, pluck="name"
	)


def _reverse_village(geography_node):
	if not geography_node:
		return None
	return frappe.db.get_value("Geography Node", geography_node, "legacy_village_id")


def _as_list(json_field_value):
	"""Mobile Encounter Context.custom_status is a JSON fieldtype -- Frappe's
	ORM already deserializes it to a Python list for frappe.get_all rows, but
	be defensive since other read paths (frappe.get_doc) can hand back the
	raw string instead."""
	if not json_field_value:
		return []
	if isinstance(json_field_value, list):
		return json_field_value
	return json.loads(json_field_value)


def _epoch_millis(dt):
	if not dt:
		return None
	if isinstance(dt, str):
		dt = frappe.utils.get_datetime(dt)
	return int(dt.timestamp() * 1000)


def _client_uuid(prefix, device_id, reference_id):
	"""Deterministic client_uuid for a wire entity, so a create retry that
	somehow bypasses the caller's own Offline Sync Item idempotency check
	(belt and braces, not the primary guard) still resolves to the same
	Household/Patient rather than creating a duplicate. device_id is part of
	the key because reference_id (the mobile's local SQLite PK) is only
	unique per device, never globally -- see Offline Sync Item's own doc
	comment."""
	return f"{prefix}-{device_id}-{reference_id}"


def upsert_household(payload, device_id):
	"""Creates or updates the Household one `households[]` wire item
	describes. Does NOT touch payload["householdMembers"] -- the caller
	(shukhee_integration.api.offline_sync.create) processes each nested
	member as its own independent entity via upsert_member, so every member
	gets its own Offline Sync Item row/status/fhir_id, matching the flat
	entityList shape the status poll returns.

	Returns the Household's own name (= client_uuid), which the caller
	stores as this entity's fhir_id."""
	reference_id = payload.get("referenceId")
	client_uuid = _client_uuid("lf-hh", device_id, reference_id)
	geography_node = _resolve_village(payload.get("villageId"))

	existing = frappe.db.exists("Household", client_uuid)
	doc = frappe.get_doc("Household", client_uuid) if existing else frappe.new_doc("Household")
	doc.client_uuid = client_uuid
	doc.display_title = payload.get("name") or ""
	doc.address = payload.get("village") or ""
	if geography_node:
		doc.geography_node = geography_node

	if existing:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)
	return doc.name


def upsert_member(payload, device_id, *, household_client_uuid=None):
	"""Creates or updates the Patient one `householdMembers[]` wire item
	describes (nested under a household, or standalone), and attaches/updates
	the corresponding Household Member child row on the owning Household.

	[household_client_uuid] is the just-created/updated parent Household's own
	name, for a nested member (payload has no resolvable household link of its
	own yet in that case). For a standalone member, the household is instead
	resolved from the payload's own `householdId`/`householdReferenceId`
	fields -- the same ambiguity the legacy mobile client itself already
	handles client-side (see offline_push_service.dart's `_memberWire`).

	Returns the Patient's own name (= client_uuid), the fhir_id the caller
	stores for this entity."""
	reference_id = payload.get("referenceId")
	client_uuid = _client_uuid("lf-pat", device_id, reference_id)

	existing = frappe.db.exists("Patient", client_uuid)
	doc = frappe.get_doc("Patient", client_uuid) if existing else frappe.new_doc("Patient")
	doc.client_uuid = client_uuid
	doc.full_name = payload.get("name") or ""
	doc.dob = payload.get("dateOfBirth") or None
	doc.gender = _wire_gender(payload.get("gender"))
	doc.phone = payload.get("phoneNumber") or ""

	household_name = household_client_uuid or _resolve_household(payload, device_id)
	if household_name:
		doc.primary_household = household_name

	if existing:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)

	if household_name:
		_attach_to_household(
			household_name,
			patient=doc.name,
			is_head=bool(payload.get("isHouseholdHead")),
		)

	return doc.name


def _wire_gender(raw):
	mapping = {
		"male": "Male",
		"female": "Female",
		"other": "Other",
	}
	return mapping.get((raw or "").strip().lower(), "Prefer not to say")


def _resolve_household(payload, device_id):
	"""Standalone-member path only: the wire's householdId is a previously-
	assigned fhir_id (== our client_uuid, since we mint both), so a direct
	existence check is enough -- no separate FHIR-id translation table."""
	household_fhir_id = payload.get("householdId")
	if household_fhir_id and frappe.db.exists("Household", household_fhir_id):
		return household_fhir_id
	household_reference_id = payload.get("householdReferenceId")
	if household_reference_id:
		candidate = _client_uuid("lf-hh", device_id, household_reference_id)
		if frappe.db.exists("Household", candidate):
			return candidate
	return None


def _attach_to_household(household_name, *, patient, is_head):
	"""Idempotent: updates the existing Household Member child row for this
	patient if one already exists (a member update, not a re-add), appends a
	new row otherwise."""
	household = frappe.get_doc("Household", household_name)
	for row in household.members:
		if row.patient == patient:
			row.is_head = is_head
			household.save(ignore_permissions=True)
			return
	household.append("members", {"patient": patient, "is_head": is_head})
	household.save(ignore_permissions=True)


def get_or_create_case(patient_name):
	"""One Case per Patient -- the NCD vertical slice's episode model is
	"one ongoing case", not per-visit; pregnancy-episode programmes (Phase 4)
	will need their own episode-boundary rule instead of reusing this as-is
	(see the migration plan's open question on episode lifecycle)."""
	existing = frappe.db.get_value("Case", {"patient": patient_name}, "name")
	if existing:
		return existing
	patient = frappe.get_doc("Patient", patient_name)
	case = frappe.get_doc(
		{
			"doctype": "Case",
			"client_uuid": f"case-{patient_name}",
			"patient": patient_name,
			"patient_name": patient.full_name,
		}
	)
	case.insert(ignore_permissions=True)
	return case.name


def process_ncd_assessment(payload, device_id):
	"""Translates one `assessments[]` wire item (assessmentType=NCD) into a
	submitted Encounter + its Observations, plus the wire-only fields on
	Mobile Encounter Context. Returns the Encounter's own name, which the
	caller stores as this entity's fhir_id -- this is the SAME id
	member_assessment_history later returns as encounterId (one canonical
	Encounter id, used everywhere -- see the migration plan)."""
	encounter_payload = payload.get("encounter") or {}
	member_id = encounter_payload.get("memberId")
	if not member_id or not frappe.db.exists("Patient", member_id):
		frappe.throw(_("Unknown patient for memberId {0}").format(member_id))

	case_name = get_or_create_case(member_id)
	ncd = ((payload.get("assessmentDetails") or {}).get("ncd")) or {}
	# A real datetime, not the wire's raw string -- Encounter is append-only
	# (spice_next_core.overrides.append_only_guard.reject_mutation) and
	# compares this field's in-memory value against the DB-cast value
	# submit()'s own save() re-reads; a str vs datetime.datetime mismatch
	# there reads as a post-submission mutation and submit() rejects itself.
	effective = frappe.utils.get_datetime(
		encounter_payload.get("startTime") or encounter_payload.get("endTime") or frappe.utils.now()
	)

	encounter = frappe.get_doc(
		{
			"doctype": "Encounter",
			"client_uuid": _client_uuid("lf-enc", device_id, payload.get("referenceId")),
			"case": case_name,
			"patient": member_id,
			"encounter_type": "Routine",
			"encounter_dt": effective,
		}
	)
	encounter.insert(ignore_permissions=True)
	if encounter.docstatus == 0:
		encounter.submit()

	custom_status = encounter_payload.get("customStatus")
	frappe.get_doc(
		{
			"doctype": "Mobile Encounter Context",
			"encounter": encounter.name,
			"service_provided": payload.get("assessmentType"),
			"village": _resolve_village(payload.get("villageId")),
			"referred": encounter_payload.get("referred"),
			"latitude": encounter_payload.get("latitude"),
			"longitude": encounter_payload.get("longitude"),
			"start_time": encounter_payload.get("startTime"),
			"end_time": encounter_payload.get("endTime"),
			"patient_status": payload.get("patientStatus"),
			"referred_reasons": payload.get("referredReasons"),
			"custom_status": frappe.as_json(custom_status) if custom_status else None,
		}
	).insert(ignore_permissions=True)

	_write_ncd_observations(case_name, encounter.name, ncd, effective)
	return encounter.name


def _write_observation(case, encounter, concept, value, unit, effective):
	frappe.get_doc(
		{
			"doctype": "Observation",
			"client_uuid": frappe.generate_hash(length=16),
			"case": case,
			"encounter": encounter,
			"concept": concept,
			"value": str(value),
			"unit": unit,
			"observed_dt": effective,
		}
	).insert(ignore_permissions=True)


def _write_ncd_observations(case_name, encounter_name, ncd, effective):
	"""Maps the real `_toNcd()` push shape (confirmed by reading
	unified_payload_mapper.dart directly -- nested bpLog/glucoseLog/
	biometric, NOT the flat bp/bg/bgType/confirmDiagnosis/stroke/heartAttack
	fields uhis_lf_mobile's own CLAUDE.md documents, which describe the
	history-PULL response shape instead) onto Concept-coded Observations.

	confirmDiagnosis/stroke/heartAttack/kidneyDisease/copd are deliberately
	NOT written here -- uhis_lf_mobile's NCD form never sends them in the
	push payload today, so fabricating values for them would be worse than
	omitting the keys from member_assessment_history's observations map."""
	bp_log = ncd.get("bpLog") or {}
	systolic = bp_log.get("avgSystolic")
	diastolic = bp_log.get("avgDiastolic")
	if systolic is not None and diastolic is not None:
		_write_observation(case_name, encounter_name, "LOINC|8480-6", systolic, "mm[Hg]", effective)
		_write_observation(case_name, encounter_name, "LOINC|8462-4", diastolic, "mm[Hg]", effective)
	if bp_log.get("isRegularSmoker") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|77176002",
			"Yes" if bp_log["isRegularSmoker"] else "No",
			None,
			effective,
		)
	if bp_log.get("diagnosedBP"):
		_write_observation(
			case_name, encounter_name, "SNOMED|38341003", bp_log["diagnosedBP"], None, effective
		)

	glucose_log = ncd.get("glucoseLog") or {}
	glucose = glucose_log.get("glucose")
	if glucose is not None:
		_write_observation(
			case_name,
			encounter_name,
			"LOINC|2339-0",
			glucose,
			glucose_log.get("glucoseUnit") or "mmol/L",
			effective,
		)
	if glucose_log.get("glucoseType"):
		_write_observation(
			case_name, encounter_name, "SNOMED|87612001", glucose_log["glucoseType"], None, effective
		)
	if glucose_log.get("diagnosedGlucose"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|73211009",
			glucose_log["diagnosedGlucose"],
			None,
			effective,
		)

	biometric = ncd.get("biometric") or {}
	if biometric.get("height") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|8302-2", biometric["height"], "cm", effective
		)
	if biometric.get("weight") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|29463-7", biometric["weight"], "kg", effective
		)
	if biometric.get("bmi") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|39156-5", biometric["bmi"], "kg/m2", effective
		)


def fetch_households_and_members(village_ids):
	"""households[]/householdMembers[] for fetch-synced-data -- Phase 3 scope
	was these two entity types only (enough to render a worklist); Phase 4
	adds the pregnancy programme-derived pregnancyInfos/treatmentDetails
	(see fetch_pregnancy_infos_and_treatment_details, below)."""
	geography_nodes = _resolve_villages(village_ids)
	if not geography_nodes:
		return [], []

	households = frappe.get_all(
		"Household",
		filters={"geography_node": ["in", geography_nodes]},
		fields=["name", "display_title", "geography_node"],
	)
	if not households:
		return [], []
	household_names = [hh.name for hh in households]

	# Batched instead of a per-household frappe.get_doc + per-member
	# frappe.get_doc -- the Engineering Design Standards explicitly forbid
	# N+1 fan-out, and this is read traffic that scales with village size
	# (architecture.md's pilot -> state -> national trajectory), not a
	# fixed-size admin screen.
	member_rows = frappe.get_all(
		"Household Member",
		filters={"parent": ["in", household_names]},
		fields=["parent", "patient", "is_head"],
	)
	patient_names = list({row.patient for row in member_rows})
	patients_by_name = {
		p.name: p
		for p in (
			frappe.get_all(
				"Patient",
				filters={"name": ["in", patient_names]},
				fields=["name", "full_name", "gender", "dob", "phone"],
			)
			if patient_names
			else []
		)
	}

	households_wire = [
		{
			"id": hh.name,
			"referenceId": hh.name,
			"name": hh.display_title,
			"villageId": _reverse_village(hh.geography_node),
		}
		for hh in households
	]
	members_wire = []
	for row in member_rows:
		patient = patients_by_name.get(row.patient)
		if not patient:
			continue
		members_wire.append(
			{
				"id": patient.name,
				"referenceId": patient.name,
				"householdId": row.parent,
				"name": patient.full_name,
				"gender": patient.gender,
				"dateOfBirth": str(patient.dob) if patient.dob else None,
				"phoneNumber": patient.phone,
				"isHouseholdHead": bool(row.is_head),
			}
		)
	return households_wire, members_wire


PREGNANCY_ASSESSMENT_TYPES = frozenset(
	{"ANC", "PWPROFILE", "PW_PROFILE", "PNC", "PNC_MOTHER", "PNC_NEONATE", "PNC_CHILD",
	"PNC_NEONATAL", "PREGNANCYOUTCOME", "PREGNANCY_OUTCOME"}
)

# Programmes that produce a pregnancyInfos[] row (worklist LMP/gravida/parity
# inference) -- narrower than PREGNANCY_ASSESSMENT_TYPES, which also includes
# PNC_NEONATE/PREGNANCYOUTCOME (neonate/delivery-outcome facts, not inputs to
# the mobile's own pregnancy-risk cohort rules).
_PREGNANCY_INFO_SERVICE_TYPES = frozenset({"PWPROFILE", "PW_PROFILE", "ANC", "PNC", "PNC_MOTHER"})


def fetch_pregnancy_infos_and_treatment_details(village_ids):
	"""pregnancyInfos[]/treatmentDetails[] for fetch-synced-data -- derived
	from Case+Observation at serialization time rather than stored as
	first-class entities, since the client only needs LMP/gravida/parity (to
	compute its own pregnancy-risk cohort flags) and bare per-patient
	presence (to infer "on treatment") -- see the migration plan's Phase 4
	scope note.

	highRiskPregnantWoman/gapsInAnc (the ANC mapper's own client-computed
	summary flags) are NOT recomputed here -- that would duplicate
	AncReferralEvaluator's business logic server-side, well outside this
	phase's scope. Likewise dateOfDelivery has no clean source: the real
	_toPregnancyOutcome() wire field has no matching seeded Concept, so it is
	left out rather than guessed at from the encounter's own visit date."""
	geography_nodes = _resolve_villages(village_ids)
	if not geography_nodes:
		return [], []

	household_names = frappe.get_all(
		"Household", filters={"geography_node": ["in", geography_nodes]}, pluck="name"
	)
	if not household_names:
		return [], []

	# Batched instead of a per-household/per-patient/per-case fan-out -- see
	# fetch_households_and_members's own note on the Engineering Design
	# Standards' no-N+1 rule.
	patient_names = list(
		{
			r.patient
			for r in frappe.get_all(
				"Household Member", filters={"parent": ["in", household_names]}, fields=["patient"]
			)
		}
	)
	if not patient_names:
		return [], []

	case_rows = frappe.get_all(
		"Case", filters={"patient": ["in", patient_names]}, fields=["name", "patient"]
	)
	case_to_patient = {c.name: c.patient for c in case_rows}
	if not case_to_patient:
		return [], []

	encounter_rows = frappe.get_all(
		"Encounter", filters={"case": ["in", list(case_to_patient)]}, fields=["name", "case"]
	)
	encounter_names = [e.name for e in encounter_rows]
	if not encounter_names:
		return [], []
	encounter_to_patient = {e.name: case_to_patient[e.case] for e in encounter_rows}

	context_rows = frappe.get_all(
		"Mobile Encounter Context",
		filters={"encounter": ["in", encounter_names]},
		fields=["encounter", "service_provided", "visit_number"],
	)

	pregnancy_infos = []
	patients_with_encounter = set()
	for ctx in context_rows:
		patient_name = encounter_to_patient.get(ctx.encounter)
		if not patient_name:
			continue
		patients_with_encounter.add(patient_name)
		service = (ctx.service_provided or "").upper()
		if service not in _PREGNANCY_INFO_SERVICE_TYPES:
			continue
		row = _flat_pregnancy_observations(
			ctx.encounter, service, visit_number=ctx.visit_number, pregnancy_episode_id=None
		)
		row["householdMemberId"] = patient_name
		pregnancy_infos.append(row)

	treatment_details = [{"patientId": p} for p in patients_with_encounter]
	return pregnancy_infos, treatment_details


def member_assessment_history(village_ids):
	"""AssessmentHistoryItem[] for member-assessment-history -- Phase 3 scope
	was NCD fields only (see _flat_ncd_observations); Phase 4 adds the
	pregnancy-episode programmes (see _flat_pregnancy_observations). Other
	programme types still appear here with serviceProvided/referralStatus/
	etc. populated from Mobile Encounter Context the moment any programme's
	`create` path starts writing one, just with an empty observations map
	until their own translation phase lands."""
	geography_nodes = _resolve_villages(village_ids)
	filters = {}
	if geography_nodes:
		filters["village"] = ["in", geography_nodes]
	contexts = frappe.get_all(
		"Mobile Encounter Context",
		filters=filters,
		fields=[
			"name",
			"encounter",
			"service_provided",
			"patient_status",
			"referred_reasons",
			"custom_status",
			"visit_number",
			"pregnancy_episode_id",
		],
	)

	if not contexts:
		return []

	# Batched instead of a per-context frappe.get_doc("Encounter", ...) +
	# frappe.get_doc("Case", ...) -- see fetch_households_and_members's own
	# note on the Engineering Design Standards' no-N+1 rule.
	encounter_names = [ctx.encounter for ctx in contexts]
	encounters_by_name = {
		e.name: e
		for e in frappe.get_all(
			"Encounter",
			filters={"name": ["in", encounter_names]},
			fields=["name", "case", "encounter_dt", "creation"],
		)
	}
	case_names = list({e.case for e in encounters_by_name.values() if e.case})
	patient_by_case = {
		c.name: c.patient
		for c in (
			frappe.get_all("Case", filters={"name": ["in", case_names]}, fields=["name", "patient"])
			if case_names
			else []
		)
	}

	# Oldest-processed-first so the per-member "seen before" set correctly
	# marks the chronologically LAST visit as isLatestVisit, independent of
	# whatever order frappe.get_all happened to return rows in.
	rows = []
	for ctx in contexts:
		encounter = encounters_by_name.get(ctx.encounter)
		if not encounter:
			continue
		rows.append((encounter.encounter_dt or encounter.creation, ctx, encounter))
	rows.sort(key=lambda r: r[0])

	latest_seen = set()
	items = []
	for visit_dt, ctx, encounter in reversed(rows):
		member_id = patient_by_case.get(encounter.case)
		if not member_id:
			continue
		is_latest = member_id not in latest_seen
		latest_seen.add(member_id)
		service = (ctx.service_provided or "").upper()
		if service == "NCD":
			observations = _flat_ncd_observations(encounter.name)
		elif service in PREGNANCY_ASSESSMENT_TYPES:
			observations = _flat_pregnancy_observations(
				encounter.name, service, visit_number=ctx.visit_number,
				pregnancy_episode_id=ctx.pregnancy_episode_id,
			)
		else:
			observations = {}
		items.append(
			{
				"householdMemberId": member_id,
				"encounterId": encounter.name,
				"visitDate": _epoch_millis(visit_dt),
				"serviceProvided": ctx.service_provided,
				"referralStatus": ctx.patient_status,
				"referralReason": ctx.referred_reasons,
				"isLatestVisit": is_latest,
				"customStatus": _as_list(ctx.custom_status),
				"observations": observations,
			}
		)
	return items


def _flat_ncd_observations(encounter_name):
	"""Reconstructs the flat `observations` map (bp/bg/bgType/height/weight)
	documented in uhis_lf_mobile's own CLAUDE.md from the Concept-coded
	Observation rows _write_ncd_observations wrote -- that doc's table
	describes this PULL response shape, not the (nested) push shape, which
	is why this is its own mapping rather than a reuse of _write_ncd_
	observations' input."""
	rows = frappe.get_all(
		"Observation", filters={"encounter": encounter_name}, fields=["concept", "value"]
	)
	by_concept = {r.concept: r.value for r in rows}
	observations = {}
	systolic = by_concept.get("LOINC|8480-6")
	diastolic = by_concept.get("LOINC|8462-4")
	if systolic and diastolic:
		observations["bp"] = f"{systolic}/{diastolic}"
	if "LOINC|2339-0" in by_concept:
		observations["bg"] = by_concept["LOINC|2339-0"]
	if "SNOMED|87612001" in by_concept:
		observations["bgType"] = by_concept["SNOMED|87612001"]
	if "LOINC|8302-2" in by_concept:
		observations["height"] = by_concept["LOINC|8302-2"]
	if "LOINC|29463-7" in by_concept:
		observations["weight"] = by_concept["LOINC|29463-7"]
	return observations


# ── Phase 4: pregnancy-episode programmes (ANC, PWPROFILE, PNC_MOTHER,
# PNC_NEONATE, PREGNANCYOUTCOME) ─────────────────────────────────────────────
#
# These interrelate via the wire's `pregnancyEpisodeId` (a client-minted UUID,
# stable across every visit of one pregnancy -- see uhis_lf_mobile's
# pregnancy_episode_dao.dart), which is why they get their OWN episode-
# lifecycle rule instead of reusing get_or_create_case's one-Case-per-Patient
# model: successive pregnancies for the same Patient must be separate Cases,
# and every visit of the SAME pregnancy must land on the SAME Case.


def get_or_create_pregnancy_case(patient_name, pregnancy_episode_id):
	"""One Case per pregnancy episode, keyed by the wire's own globally-unique
	pregnancyEpisodeId -- NOT per (device_id, reference_id) like Household/
	Patient, since the episode id is already stable and unique by
	construction on the client."""
	if not pregnancy_episode_id:
		frappe.throw(_("pregnancyEpisodeId is required for this programme."))
	client_uuid = f"case-preg-{pregnancy_episode_id}"
	if frappe.db.exists("Case", client_uuid):
		return client_uuid
	patient = frappe.get_doc("Patient", patient_name)
	case = frappe.get_doc(
		{
			"doctype": "Case",
			"client_uuid": client_uuid,
			"patient": patient_name,
			"patient_name": patient.full_name,
		}
	)
	case.insert(ignore_permissions=True)
	return client_uuid


def _unwrap_pregnancy_details(assessment_type, assessment_details):
	"""Undoes LocalAssessmentEntity._wrapDetailsForType's programme-key
	wrapping (confirmed by reading that function directly, not the CLAUDE.md
	wrapping table, which is wrong for ANC/PWPROFILE -- both DO wrap, despite
	the doc's "flat" claim)."""
	t = (assessment_type or "").upper()
	assessment_details = assessment_details or {}
	if t == "ANC":
		return assessment_details.get("anc") or {}
	if t in ("PWPROFILE", "PW_PROFILE"):
		pw = assessment_details.get("pwProfile") or {}
		return pw.get("pregnancyDetailsAndHistory") or pw
	if t in ("PNC", "PNC_MOTHER"):
		return assessment_details.get("pncMother") or {}
	if t in ("PNC_NEONATE", "PNC_CHILD", "PNC_NEONATAL"):
		return assessment_details.get("pncNeonatal") or {}
	if t in ("PREGNANCYOUTCOME", "PREGNANCY_OUTCOME"):
		return assessment_details.get("pregnancyOutcome") or {}
	return {}


def process_pregnancy_assessment(payload, device_id):
	"""Translates one `assessments[]` wire item for any of the Phase 4
	programmes into a submitted Encounter + its Observations, plus Mobile
	Encounter Context's wire-only fields -- same shape of translation as
	process_ncd_assessment, parameterised by assessment_type instead of
	hardcoded to NCD.

	Case resolution is NOT uniform across these 5 types: PNC_NEONATE is
	explicitly NOT pregnancy-episode-linked on the real client (confirmed in
	uhis_lf_mobile's own pregnancy_episode_dao.dart: "Android does NOT link
	PNC_NEONATE/PNC_CHILD to a pregnancy episode" -- its wire payload never
	carries a pregnancyEpisodeId at all), so it falls back to the same
	one-Case-per-Patient model process_ncd_assessment uses, rather than
	get_or_create_pregnancy_case which would otherwise throw on the missing
	id for every real PNC_NEONATE visit."""
	assessment_type = (payload.get("assessmentType") or "").upper()
	encounter_payload = payload.get("encounter") or {}
	member_id = encounter_payload.get("memberId")
	if not member_id or not frappe.db.exists("Patient", member_id):
		frappe.throw(_("Unknown patient for memberId {0}").format(member_id))

	pregnancy_episode_id = encounter_payload.get("pregnancyEpisodeId")
	if _normalize_pregnancy_type(assessment_type) == "PNC_NEONATE":
		case_name = get_or_create_case(member_id)
	else:
		case_name = get_or_create_pregnancy_case(member_id, pregnancy_episode_id)
	details = _unwrap_pregnancy_details(assessment_type, payload.get("assessmentDetails"))
	effective = frappe.utils.get_datetime(
		encounter_payload.get("startTime") or encounter_payload.get("endTime") or frappe.utils.now()
	)

	encounter = frappe.get_doc(
		{
			"doctype": "Encounter",
			"client_uuid": _client_uuid("lf-enc", device_id, payload.get("referenceId")),
			"case": case_name,
			"patient": member_id,
			"encounter_type": "Routine",
			"encounter_dt": effective,
		}
	)
	encounter.insert(ignore_permissions=True)
	if encounter.docstatus == 0:
		encounter.submit()

	custom_status = encounter_payload.get("customStatus")
	frappe.get_doc(
		{
			"doctype": "Mobile Encounter Context",
			"encounter": encounter.name,
			"service_provided": payload.get("assessmentType"),
			"village": _resolve_village(payload.get("villageId")),
			"referred": encounter_payload.get("referred"),
			"latitude": encounter_payload.get("latitude"),
			"longitude": encounter_payload.get("longitude"),
			"start_time": encounter_payload.get("startTime"),
			"end_time": encounter_payload.get("endTime"),
			"visit_number": encounter_payload.get("visitNumber"),
			"pregnancy_episode_id": pregnancy_episode_id,
			"patient_status": payload.get("patientStatus"),
			"referred_reasons": payload.get("referredReasons"),
			"custom_status": frappe.as_json(custom_status) if custom_status else None,
		}
	).insert(ignore_permissions=True)

	writer = _PREGNANCY_OBSERVATION_WRITERS.get(_normalize_pregnancy_type(assessment_type))
	if writer:
		writer(case_name, encounter.name, details, effective)
	return encounter.name


def _normalize_pregnancy_type(assessment_type):
	t = (assessment_type or "").upper()
	if t in ("PW_PROFILE",):
		return "PWPROFILE"
	if t == "PNC":
		return "PNC_MOTHER"
	if t in ("PNC_CHILD", "PNC_NEONATAL"):
		return "PNC_NEONATE"
	if t == "PREGNANCY_OUTCOME":
		return "PREGNANCYOUTCOME"
	return t


def _write_anc_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toAnc() push shape -- medicalHistoryPhysicalExamination/
	pointOfCareInvestigations/vaccinationAndSupplements/
	ancServicesBirthPreparedness -- onto Concept-coded Observations. Fields
	with no seeded Concept (e.g. ancVisitsOtherProviders, the dangerSigns
	trimester lists) are intentionally not written -- see the same
	no-fabrication rule _write_ncd_observations documents."""
	med_hx = details.get("medicalHistoryPhysicalExamination") or {}
	poc = details.get("pointOfCareInvestigations") or {}
	vaccination = details.get("vaccinationAndSupplements") or {}
	birth_prep = details.get("ancServicesBirthPreparedness") or {}

	systolic = med_hx.get("systolic")
	diastolic = med_hx.get("diastolic")
	if systolic and diastolic:
		_write_observation(case_name, encounter_name, "LOINC|8480-6", systolic, "mm[Hg]", effective)
		_write_observation(case_name, encounter_name, "LOINC|8462-4", diastolic, "mm[Hg]", effective)
	if med_hx.get("weight") is not None:
		_write_observation(case_name, encounter_name, "LOINC|29463-7", med_hx["weight"], "kg", effective)
	if med_hx.get("height") is not None:
		_write_observation(case_name, encounter_name, "LOINC|8302-2", med_hx["height"], "cm", effective)
	if med_hx.get("fundalHeight") is not None:
		_write_observation(
			case_name, encounter_name, "SNOMED|364605001", med_hx["fundalHeight"], "cm", effective
		)
	if med_hx.get("hemoglobin") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|718-7", med_hx["hemoglobin"], "g/dL", effective
		)
	if med_hx.get("edema"):
		_write_observation(case_name, encounter_name, "SNOMED|267038008", med_hx["edema"], None, effective)

	if poc.get("urinaryAlbumin"):
		_write_observation(
			case_name, encounter_name, "SNOMED|167272007", poc["urinaryAlbumin"], None, effective
		)
	if poc.get("urinaryBilirubin"):
		_write_observation(
			case_name, encounter_name, "SNOMED|167274008", poc["urinaryBilirubin"], None, effective
		)
	if poc.get("urinarySugar"):
		_write_observation(
			case_name, encounter_name, "SNOMED|167273002", poc["urinarySugar"], None, effective
		)
	blood_sugar = poc.get("bloodSugarFasting") if "bloodSugarFasting" in poc else poc.get("bloodSugarRandom")
	if blood_sugar is not None:
		unit = poc.get("bloodSugarFastingUnit") or poc.get("bloodSugarRandomUnit") or "mmol/L"
		_write_observation(case_name, encounter_name, "LOINC|2339-0", blood_sugar, unit, effective)
		if poc.get("bloodSugar"):
			_write_observation(
				case_name, encounter_name, "SNOMED|87612001", poc["bloodSugar"], None, effective
			)

	if vaccination.get("ttTdCompleted"):
		_write_observation(
			case_name, encounter_name, "SNOMED|56844000", vaccination["ttTdCompleted"], None, effective
		)
	if vaccination.get("folicAcidProvided") is not None:
		_write_observation(
			case_name, encounter_name, "SNOMED|63718003", vaccination["folicAcidProvided"], None, effective
		)
	if vaccination.get("ifaTotalConsumed") is not None:
		_write_observation(
			case_name, encounter_name, "SNOMED|767399006", vaccination["ifaTotalConsumed"], None, effective
		)
	if vaccination.get("calciumTotalConsumed") is not None:
		_write_observation(
			case_name, encounter_name, "SNOMED|5540006", vaccination["calciumTotalConsumed"], None, effective
		)

	if birth_prep.get("ancFromMedicalDoctor"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|424525001",
			birth_prep["ancFromMedicalDoctor"],
			None,
			effective,
		)
	if birth_prep.get("ultrasound"):
		_write_observation(
			case_name, encounter_name, "SNOMED|359659005", birth_prep["ultrasound"], None, effective
		)
	if birth_prep.get("facilityIdentifiedForDelivery"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|182992009",
			birth_prep["facilityIdentifiedForDelivery"],
			None,
			effective,
		)


def _write_pwprofile_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toPwProfile() push shape (flat lmp/gravida/parity/
	livingChildren/ageOfLastChild/pregnancyTest, once unwrapped from its
	pwProfile.pregnancyDetailsAndHistory card)."""
	if details.get("lmp"):
		_write_observation(case_name, encounter_name, "LOINC|8665-2", details["lmp"], None, effective)
	if details.get("gravida") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|11996-6", details["gravida"], None, effective
		)
	if details.get("parity") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|11977-6", details["parity"], None, effective
		)
	if details.get("livingChildren") is not None:
		_write_observation(
			case_name, encounter_name, "SNOMED|224118004", details["livingChildren"], None, effective
		)
	if details.get("ageOfLastChild"):
		_write_observation(
			case_name, encounter_name, "SNOMED|424144002", details["ageOfLastChild"], None, effective
		)
	if details.get("pregnancyTest"):
		_write_observation(
			case_name, encounter_name, "SNOMED|250416002", details["pregnancyTest"], None, effective
		)


def _write_pnc_mother_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toPncMother() push shape --
	maternalHealthAssessment/pregnancyHistory."""
	maternal = details.get("maternalHealthAssessment") or {}
	pregnancy = details.get("pregnancyHistory") or {}

	systolic = maternal.get("systolic")
	diastolic = maternal.get("diastolic")
	if systolic and diastolic:
		_write_observation(case_name, encounter_name, "LOINC|8480-6", systolic, "mm[Hg]", effective)
		_write_observation(case_name, encounter_name, "LOINC|8462-4", diastolic, "mm[Hg]", effective)
	if maternal.get("weight") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|29463-7", maternal["weight"], "kg", effective
		)
	if maternal.get("temperature") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|8310-5", maternal["temperature"], "degF", effective
		)
	if maternal.get("hemoglobin") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|718-7", maternal["hemoglobin"], "g/dL", effective
		)
	if maternal.get("urinaryAlbumin"):
		_write_observation(
			case_name, encounter_name, "SNOMED|167272007", maternal["urinaryAlbumin"], None, effective
		)
	if maternal.get("urinaryBilirubin"):
		_write_observation(
			case_name, encounter_name, "SNOMED|167274008", maternal["urinaryBilirubin"], None, effective
		)
	if maternal.get("edema"):
		_write_observation(case_name, encounter_name, "SNOMED|267038008", maternal["edema"], None, effective)
	if maternal.get("eclampsia"):
		_write_observation(
			case_name, encounter_name, "SNOMED|15938005", maternal["eclampsia"], None, effective
		)
	blood_sugar = maternal.get("fastingBloodSugar")
	if blood_sugar is None:
		blood_sugar = maternal.get("randomBloodSugar")
	if blood_sugar is not None:
		_write_observation(case_name, encounter_name, "LOINC|2339-0", blood_sugar, "mmol/L", effective)
	if maternal.get("htnPatient"):
		_write_observation(
			case_name, encounter_name, "SNOMED|38341003", maternal["htnPatient"], None, effective
		)
	if maternal.get("dmPatient"):
		_write_observation(
			case_name, encounter_name, "SNOMED|73211009", maternal["dmPatient"], None, effective
		)
	if maternal.get("gdmPatient"):
		_write_observation(
			case_name, encounter_name, "SNOMED|11687002", maternal["gdmPatient"], None, effective
		)
	if maternal.get("vitaminAConsumed"):
		_write_observation(
			case_name, encounter_name, "SNOMED|37237003", maternal["vitaminAConsumed"], None, effective
		)
	if maternal.get("ifaTabletsConsumed") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|767399006",
			maternal["ifaTabletsConsumed"],
			None,
			effective,
		)
	if maternal.get("calciumTabletsConsumed") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|5540006",
			maternal["calciumTabletsConsumed"],
			None,
			effective,
		)
	if maternal.get("postpartumDangerSigns"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|267024001",
			maternal["postpartumDangerSigns"],
			None,
			effective,
		)

	if pregnancy.get("parity") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|11977-6", pregnancy["parity"], None, effective
		)
	if pregnancy.get("gravida") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|11996-6", pregnancy["gravida"], None, effective
		)
	if pregnancy.get("livingChildren") is not None:
		_write_observation(
			case_name, encounter_name, "SNOMED|224118004", pregnancy["livingChildren"], None, effective
		)


def _write_pnc_neonatal_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toPncNeonatal() push shape. Scope note: this vertical
	slice does not create a separate newborn Patient -- neonate findings are
	recorded against the MOTHER's own Case/Encounter, same as the legacy
	wire's own pncNeonatal grouping under the mother's assessment. A
	first-class newborn Patient is a product decision out of this phase's
	scope."""
	if details.get("childWeight") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|29463-7", details["childWeight"], "kg", effective
		)
	if details.get("childHeight") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|8302-2", details["childHeight"], "cm", effective
		)
	if details.get("isChildAlive") is not None:
		_write_observation(
			case_name, encounter_name, "SNOMED|281050002", details["isChildAlive"], None, effective
		)


def _write_pregnancy_outcome_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toPregnancyOutcome() push shape --
	deliveryOutcomes/maternalDeath/abortion/newbornDetails[]."""
	delivery = details.get("deliveryOutcomes") or {}
	maternal_death = details.get("maternalDeath") or {}
	abortion = details.get("abortion") or {}
	newborns = details.get("newbornDetails") or []

	if delivery.get("modeOfDelivery"):
		_write_observation(
			case_name, encounter_name, "SNOMED|289258004", delivery["modeOfDelivery"], None, effective
		)
	if delivery.get("placeOfDelivery"):
		_write_observation(
			case_name, encounter_name, "SNOMED|3950001", delivery["placeOfDelivery"], None, effective
		)
	if delivery.get("birthAttendant"):
		_write_observation(
			case_name, encounter_name, "SNOMED|408826000", delivery["birthAttendant"], None, effective
		)
	if delivery.get("stillbirthNumbers") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|237364002",
			delivery["stillbirthNumbers"],
			None,
			effective,
		)
	if maternal_death.get("timeOfDeath"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|184305005",
			maternal_death["timeOfDeath"],
			None,
			effective,
		)
	if abortion.get("typeOfAbortion"):
		_write_observation(
			case_name, encounter_name, "SNOMED|386639001", abortion["typeOfAbortion"], None, effective
		)
	for newborn in newborns:
		if not isinstance(newborn, dict):
			continue
		if newborn.get("isBabyAlive") is not None:
			_write_observation(
				case_name,
				encounter_name,
				"SNOMED|281050002",
				newborn["isBabyAlive"],
				None,
				effective,
			)


_PREGNANCY_OBSERVATION_WRITERS = {
	"ANC": _write_anc_observations,
	"PWPROFILE": _write_pwprofile_observations,
	"PNC_MOTHER": _write_pnc_mother_observations,
	"PNC_NEONATE": _write_pnc_neonatal_observations,
	"PREGNANCYOUTCOME": _write_pregnancy_outcome_observations,
}


def _flat_pregnancy_observations(encounter_name, service_provided, *, visit_number=None, pregnancy_episode_id=None):
	"""Reconstructs a flat `observations` map for pregnancy-episode
	programmes, the same role _flat_ncd_observations plays for NCD --
	sourced from the Concept-coded Observation rows the matching writer
	above wrote, plus the wire-only visitNumber/pregnancyEpisodeId that live
	on Mobile Encounter Context rather than as Observations."""
	rows = frappe.get_all(
		"Observation", filters={"encounter": encounter_name}, fields=["concept", "value"]
	)
	by_concept = {r.concept: r.value for r in rows}
	observations = {}

	systolic = by_concept.get("LOINC|8480-6")
	diastolic = by_concept.get("LOINC|8462-4")
	if systolic and diastolic:
		observations["bp"] = f"{systolic}/{diastolic}"
	if "LOINC|718-7" in by_concept:
		observations["hemoglobin"] = by_concept["LOINC|718-7"]
	if "SNOMED|364605001" in by_concept:
		observations["fundalHeight"] = by_concept["SNOMED|364605001"]
	if "LOINC|11996-6" in by_concept:
		observations["gravida"] = by_concept["LOINC|11996-6"]
	if "LOINC|11977-6" in by_concept:
		observations["parity"] = by_concept["LOINC|11977-6"]
	if "SNOMED|224118004" in by_concept:
		observations["numberOfLivingChildren"] = by_concept["SNOMED|224118004"]
	if "SNOMED|289258004" in by_concept:
		observations["modeOfDelivery"] = by_concept["SNOMED|289258004"]
	if "LOINC|8665-2" in by_concept:
		observations["lastMenstrualPeriod"] = by_concept["LOINC|8665-2"]
	if "SNOMED|424144002" in by_concept:
		observations["ageOfLastChild"] = by_concept["SNOMED|424144002"]
	if "SNOMED|250416002" in by_concept:
		observations["pregnancyTest"] = by_concept["SNOMED|250416002"]

	if pregnancy_episode_id:
		observations["pregnancyEpisodeId"] = pregnancy_episode_id
	if visit_number is not None:
		service = (service_provided or "").upper()
		if service == "ANC":
			observations["ancVisitNumber"] = visit_number
		elif service in ("PNC", "PNC_MOTHER", "PNC_NEONATE", "PNC_CHILD", "PNC_NEONATAL"):
			observations["pncVisitNumber"] = visit_number
	return observations


# ── Phase 5: remaining programmes (CHILDHOOD_VISIT, ICCM, EYE_CARE,
# CATARACT, FAMILY_PLANNING) ─────────────────────────────────────────────────
#
# Case resolution is per-type, not uniform: CHILDHOOD_VISIT IS pregnancy-
# episode-linked on the real client (confirmed in uhis_lf_mobile's
# kPregnancyEpisodeLinkedTypes, which explicitly includes CHILDHOOD_VISIT/
# CHILD_MENU alongside ANC/PWPROFILE/PNC_MOTHER/PREGNANCY_OUTCOME) -- a
# child's immunisation visit shares its Case with the mother's pregnancy
# episode, which looks surprising but matches the real system rather than a
# "cleaner" model invented here. ICCM/EYE_CARE/CATARACT/FAMILY_PLANNING are
# NOT episode-linked, so they use get_or_create_case's one-Case-per-Patient
# model, same as NCD and PNC_NEONATE.

OTHER_ASSESSMENT_TYPES = frozenset(
	{"CHILDHOOD_VISIT", "CHILD_MENU", "ICCM", "IMCI", "EYE_CARE", "CATARACT", "FAMILY_PLANNING", "FP"}
)

_EPISODE_LINKED_OTHER_TYPES = frozenset({"CHILDHOOD_VISIT", "CHILD_MENU"})


def _unwrap_other_details(assessment_type, assessment_details):
	"""Undoes _wrapDetailsForType's wrapping for the Phase 5 types."""
	t = (assessment_type or "").upper()
	assessment_details = assessment_details or {}
	if t in ("CHILDHOOD_VISIT", "CHILD_MENU"):
		return assessment_details.get("pncChild") or {}
	if t in ("ICCM", "IMCI"):
		return assessment_details.get("iccm") or {}
	if t == "EYE_CARE":
		return assessment_details.get("eye_care") or {}
	if t == "CATARACT":
		return assessment_details.get("cataract") or {}
	if t in ("FAMILY_PLANNING", "FP"):
		return assessment_details.get("familyPlanning") or {}
	return {}


def process_other_assessment(payload, device_id):
	"""Translates one `assessments[]` wire item for any of the Phase 5
	programmes -- same encounter/context shape as process_ncd_assessment /
	process_pregnancy_assessment, parameterised by assessment_type and its
	own Case-resolution rule (see the module note above)."""
	assessment_type = (payload.get("assessmentType") or "").upper()
	encounter_payload = payload.get("encounter") or {}
	member_id = encounter_payload.get("memberId")
	if not member_id or not frappe.db.exists("Patient", member_id):
		frappe.throw(_("Unknown patient for memberId {0}").format(member_id))

	if assessment_type in _EPISODE_LINKED_OTHER_TYPES:
		pregnancy_episode_id = encounter_payload.get("pregnancyEpisodeId")
		case_name = get_or_create_pregnancy_case(member_id, pregnancy_episode_id)
	else:
		pregnancy_episode_id = None
		case_name = get_or_create_case(member_id)

	details = _unwrap_other_details(assessment_type, payload.get("assessmentDetails"))
	effective = frappe.utils.get_datetime(
		encounter_payload.get("startTime") or encounter_payload.get("endTime") or frappe.utils.now()
	)

	encounter = frappe.get_doc(
		{
			"doctype": "Encounter",
			"client_uuid": _client_uuid("lf-enc", device_id, payload.get("referenceId")),
			"case": case_name,
			"patient": member_id,
			"encounter_type": "Routine",
			"encounter_dt": effective,
		}
	)
	encounter.insert(ignore_permissions=True)
	if encounter.docstatus == 0:
		encounter.submit()

	custom_status = encounter_payload.get("customStatus")
	frappe.get_doc(
		{
			"doctype": "Mobile Encounter Context",
			"encounter": encounter.name,
			"service_provided": payload.get("assessmentType"),
			"village": _resolve_village(payload.get("villageId")),
			"referred": encounter_payload.get("referred"),
			"latitude": encounter_payload.get("latitude"),
			"longitude": encounter_payload.get("longitude"),
			"start_time": encounter_payload.get("startTime"),
			"end_time": encounter_payload.get("endTime"),
			"visit_number": encounter_payload.get("visitNumber"),
			"pregnancy_episode_id": pregnancy_episode_id,
			"patient_status": payload.get("patientStatus"),
			"referred_reasons": payload.get("referredReasons"),
			"custom_status": frappe.as_json(custom_status) if custom_status else None,
		}
	).insert(ignore_permissions=True)

	writer = _OTHER_OBSERVATION_WRITERS.get(_normalize_other_type(assessment_type))
	if writer:
		writer(case_name, encounter.name, details, effective)
	return encounter.name


def _normalize_other_type(assessment_type):
	t = (assessment_type or "").upper()
	if t == "CHILD_MENU":
		return "CHILDHOOD_VISIT"
	if t == "IMCI":
		return "ICCM"
	if t == "FP":
		return "FAMILY_PLANNING"
	return t


def _yes_no(value):
	"""Cataract's nested `ncd` card captures diagnosis/smoker flags as real
	booleans (unlike NCD's own "Yes"/"No" string convention) -- normalize to
	the same string convention so Observation.value stays consistent across
	every writer in this module."""
	if value is None:
		return None
	if isinstance(value, bool):
		return "Yes" if value else "No"
	return str(value)


def _write_childhood_visit_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toChildhoodVisit() push shape. anyIllness/
	childReferral/childReferralFacilityType are administrative/workflow
	fields already captured via patientStatus/referred/referredReasons on
	Mobile Encounter Context -- not duplicated as Observations."""
	if details.get("congenitalDefect"):
		_write_observation(
			case_name, encounter_name, "SNOMED|276654001", details["congenitalDefect"], None, effective
		)
	if details.get("weight") is not None:
		_write_observation(case_name, encounter_name, "LOINC|29463-7", details["weight"], "kg", effective)
	if details.get("additionalFood24Hrs"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|169745008",
			details["additionalFood24Hrs"],
			None,
			effective,
		)
	if details.get("receivedVaccine"):
		_write_observation(
			case_name, encounter_name, "SNOMED|127785005", details["receivedVaccine"], None, effective
		)
	if details.get("dewormingMedicine"):
		_write_observation(
			case_name, encounter_name, "SNOMED|414580003", details["dewormingMedicine"], None, effective
		)
	if details.get("childIllnessType"):
		_write_observation(
			case_name, encounter_name, "SNOMED|225834001", details["childIllnessType"], None, effective
		)


def _write_iccm_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toIccm() push shape. Free-text/list fields
	(chiefComplaint, presentingSymptoms) and the individual IMCI danger
	signs (convulsions, unconscious, ...) have no seeded Concept -- not
	written, same no-fabrication rule as every other writer here. muac has
	no seeded Concept either, despite being a real vital -- a genuine gap,
	not an oversight."""
	classification = details.get("iccmClassification")
	if classification:
		_write_observation(
			case_name, encounter_name, "SNOMED|225834001", classification, None, effective
		)
	if details.get("temperature") is not None:
		_write_observation(
			case_name, encounter_name, "LOINC|8310-5", details["temperature"], "degF", effective
		)
	if details.get("respiratoryRate") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"LOINC|9279-1",
			details["respiratoryRate"],
			"breaths/min",
			effective,
		)


def _eye_care_card_observations(case_name, encounter_name, card, effective):
	"""Shared by EYE_CARE's own `eyeCare` card and CATARACT's identically-
	shaped `cataract` card -- both forms collect the same glasses/referral
	fields under different wire keys."""
	outcomes = card.get("eyeTestOutcomes")
	if outcomes:
		value = ", ".join(outcomes) if isinstance(outcomes, list) else outcomes
		_write_observation(case_name, encounter_name, "SNOMED|371405004", value, None, effective)
	if card.get("typeOfFrame"):
		_write_observation(
			case_name, encounter_name, "SNOMED|363983007", card["typeOfFrame"], None, effective
		)
	if card.get("referPlace"):
		_write_observation(
			case_name, encounter_name, "SNOMED|306206005", card["referPlace"], None, effective
		)


def _write_eye_care_observations(case_name, encounter_name, details, effective):
	_eye_care_card_observations(case_name, encounter_name, details.get("eyeCare") or {}, effective)


def _write_cataract_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toCataract() push shape -- the `cataract` card (shared
	eye-care fields + cataract-specific ones) and, when NCD vitals were also
	captured during the same visit, the nested `ncd` card's bpLog/glucoseLog
	(reusing the SAME Concepts process_ncd_assessment uses, since these are
	the same clinical facts -- just a bool, not "Yes"/"No" string,
	convention on this particular form)."""
	card = details.get("cataract") or {}
	_eye_care_card_observations(case_name, encounter_name, card, effective)
	if card.get("historyOfOtherDiseases"):
		value = card["historyOfOtherDiseases"]
		value = ", ".join(value) if isinstance(value, list) else value
		_write_observation(case_name, encounter_name, "SNOMED|417662000", value, None, effective)
	if card.get("patientReferredForOperation"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|306204006",
			card["patientReferredForOperation"],
			None,
			effective,
		)
	if card.get("operationName"):
		value = card["operationName"]
		value = ", ".join(value) if isinstance(value, list) else value
		_write_observation(case_name, encounter_name, "SNOMED|387713003", value, None, effective)
	if card.get("reason"):
		value = card["reason"]
		value = ", ".join(value) if isinstance(value, list) else value
		_write_observation(case_name, encounter_name, "SNOMED|410666004", value, None, effective)
	if card.get("pseudophakiaPostCataractSurgery"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|406859001",
			card["pseudophakiaPostCataractSurgery"],
			None,
			effective,
		)

	ncd = details.get("ncd") or {}
	bp_log = ncd.get("bpLog") or {}
	systolic = bp_log.get("avgSystolic")
	diastolic = bp_log.get("avgDiastolic")
	if systolic is not None and diastolic is not None:
		_write_observation(case_name, encounter_name, "LOINC|8480-6", systolic, "mm[Hg]", effective)
		_write_observation(case_name, encounter_name, "LOINC|8462-4", diastolic, "mm[Hg]", effective)
	if bp_log.get("height") is not None:
		_write_observation(case_name, encounter_name, "LOINC|8302-2", bp_log["height"], "cm", effective)
	if bp_log.get("weight") is not None:
		_write_observation(case_name, encounter_name, "LOINC|29463-7", bp_log["weight"], "kg", effective)
	if bp_log.get("isRegularSmoker") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|77176002",
			_yes_no(bp_log["isRegularSmoker"]),
			None,
			effective,
		)
	if bp_log.get("isBeforeHtnDiagnosis") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|38341003",
			_yes_no(bp_log["isBeforeHtnDiagnosis"]),
			None,
			effective,
		)
	glucose_log = ncd.get("glucoseLog") or {}
	if glucose_log.get("glucose") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"LOINC|2339-0",
			glucose_log["glucose"],
			glucose_log.get("glucoseUnit") or "mmol/L",
			effective,
		)
	if glucose_log.get("glucoseType"):
		_write_observation(
			case_name, encounter_name, "SNOMED|87612001", glucose_log["glucoseType"], None, effective
		)
	if glucose_log.get("isBeforeDiabetesDiagnosis") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|73211009",
			_yes_no(glucose_log["isBeforeDiabetesDiagnosis"]),
			None,
			effective,
		)


def _write_family_planning_observations(case_name, encounter_name, details, effective):
	"""Maps the real _toFamilyPlanning() push shape. familyPlanningMethods
	is deliberately NOT written -- no seeded Concept represents "family
	planning method" (SNOMED|13197004 "First-time contraceptive user" is a
	different fact, not the method list), and inventing a code here would
	be worse than omitting the field."""
	if details.get("numberOfLivingChildren") is not None:
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|224118004",
			details["numberOfLivingChildren"],
			None,
			effective,
		)
	if details.get("ageOfLastChild"):
		_write_observation(
			case_name, encounter_name, "SNOMED|424144002", details["ageOfLastChild"], None, effective
		)
	if details.get("desireForChildrenInFuture"):
		_write_observation(
			case_name,
			encounter_name,
			"SNOMED|415510000",
			details["desireForChildrenInFuture"],
			None,
			effective,
		)


_OTHER_OBSERVATION_WRITERS = {
	"CHILDHOOD_VISIT": _write_childhood_visit_observations,
	"ICCM": _write_iccm_observations,
	"EYE_CARE": _write_eye_care_observations,
	"CATARACT": _write_cataract_observations,
	"FAMILY_PLANNING": _write_family_planning_observations,
}
