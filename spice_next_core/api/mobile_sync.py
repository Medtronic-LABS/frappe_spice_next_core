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

import frappe


def _resolve_village(village_id):
	"""villageId (int) -> Geography Node, via the legacy-village-id mapping
	added for this integration (see Geography Node.legacy_village_id's own doc
	comment). Returns None rather than throwing when unmapped -- a household
	write should never fail outright over a geography lookup miss; it
	persists with no village link, visible for manual reconciliation."""
	if not village_id:
		return None
	return frappe.db.get_value("Geography Node", {"legacy_village_id": village_id}, "name")


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
