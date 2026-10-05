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
	is these two entity types only (enough to render a worklist); pregnancy
	programme-derived pregnancyInfos/treatmentDetails are Phase 4 (see the
	migration plan)."""
	geography_nodes = _resolve_villages(village_ids)
	if not geography_nodes:
		return [], []

	households = frappe.get_all(
		"Household",
		filters={"geography_node": ["in", geography_nodes]},
		fields=["name", "display_title", "geography_node"],
	)
	households_wire = []
	members_wire = []
	for hh in households:
		households_wire.append(
			{
				"id": hh.name,
				"referenceId": hh.name,
				"name": hh.display_title,
				"villageId": _reverse_village(hh.geography_node),
			}
		)
		hh_doc = frappe.get_doc("Household", hh.name)
		for row in hh_doc.members:
			patient = frappe.get_doc("Patient", row.patient)
			members_wire.append(
				{
					"id": patient.name,
					"referenceId": patient.name,
					"householdId": hh.name,
					"name": patient.full_name,
					"gender": patient.gender,
					"dateOfBirth": str(patient.dob) if patient.dob else None,
					"phoneNumber": patient.phone,
					"isHouseholdHead": bool(row.is_head),
				}
			)
	return households_wire, members_wire


def member_assessment_history(village_ids):
	"""AssessmentHistoryItem[] for member-assessment-history -- Phase 3 scope
	is NCD fields only (see _flat_ncd_observations); other programme types
	will get their own observations-flattening once their own translation
	phase lands, but still appear here with serviceProvided/referralStatus/
	etc. populated from Mobile Encounter Context the moment any programme's
	`create` path starts writing one."""
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
		],
	)

	# Oldest-processed-first so the per-member "seen before" set correctly
	# marks the chronologically LAST visit as isLatestVisit, independent of
	# whatever order frappe.get_all happened to return rows in.
	rows = []
	for ctx in contexts:
		encounter = frappe.get_doc("Encounter", ctx.encounter)
		rows.append((encounter.encounter_dt or encounter.creation, ctx, encounter))
	rows.sort(key=lambda r: r[0])

	latest_seen = set()
	items = []
	for visit_dt, ctx, encounter in reversed(rows):
		case = frappe.get_doc("Case", encounter.case)
		member_id = case.patient
		is_latest = member_id not in latest_seen
		latest_seen.add(member_id)
		observations = (
			_flat_ncd_observations(encounter.name) if ctx.service_provided == "NCD" else {}
		)
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
