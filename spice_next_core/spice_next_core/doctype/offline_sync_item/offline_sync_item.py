from frappe.model.document import Document


class OfflineSyncItem(Document):
	"""One row per entity inside an Offline Sync Batch's create payload (child
	table of Offline Sync Batch) -- this is the row-level record status.fhirId
	is read from. See Offline Sync Batch's own doc comment for the idempotency
	key this row participates in."""

	pass
