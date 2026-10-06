from frappe.model.document import Document


class OfflineSyncBatch(Document):
	"""One row per `create` request the mobile app sends, named by the
	mobile's own `requestId` so a retried POST (same or resubmitted batch)
	resolves to this same document rather than creating a duplicate.

	Every item in the batch is processed synchronously, in the request itself
	(api.offline_sync.create), with its terminal Success/Failed status and
	assigned fhir_id written to a child Offline Sync Item row before the HTTP
	response returns -- there is no async queue and therefore no window for an
	item to get permanently stuck, unlike the legacy Java offline-service's
	3-retry-then-silent-failure behaviour this app replaces.

	api.offline_sync.status is a near-trivial re-read of this document's own
	sync_items child table."""

	pass
