from frappe.model.document import Document


class MobileEncounterContext(Document):
	"""1:1 side doctype off Encounter, holding the uhis_lf_mobile wire-only
	fields that have no FHIR/clinical meaning (village, lat/lng, visit
	workflow timestamps, pregnancy episode linkage, referral bookkeeping).
	Encounter itself stays generic/clean for its other consumer,
	flutter-uhis-next, which has no equivalent concept of these fields."""

	pass
