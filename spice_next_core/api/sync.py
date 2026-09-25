"""
Sync endpoints — the ONLY path for device ↔ server data exchange.

  spice_next_core.api.sync.push   device → server (idempotent, catchment-scoped)
  spice_next_core.api.sync.pull   server → device (cursor-based, server-side scope)
  spice_next_core.api.sync.config server → device (versioned form / geography / concept config)

Wire contract: ../../docs/api-contract/sync-envelope.md
"""

import frappe
from frappe import _

from spice_next_core.auth.decorators import current_remote_user_id
from spice_next_core.auth.decorators import whitelist as remote_whitelist

CONTRACT_VERSION = 1

_SYNCABLE_DOCTYPES = [
	"Patient",
	"Household",
	"Case",
	"Encounter",
	"Observation",
	"Condition",
	"Referral",
	"Task",
	# Deliberately pull-only, unlike every doctype above: push() never consults
	# this list at all (it dispatches generically on op["doctype"]), and there is
	# no legitimate offline-authored path for Call Logs -- booking a call always
	# requires a live round trip to the Shukhee vendor (shukhee_integration.api.
	# consultation.start_consultation), which cannot happen offline in the first
	# place. Do not add client_uuid/Sync Op Log push support for it.
	"Call Logs",
]


class CaseStatusConflict(Exception):
	pass


def _resolve_env(payload=None):
	"""Accept either a legacy `payload` string or raw JSON body keys (Flutter client).

	Flutter sends the envelope as a flat JSON body:
	  {"contract_version": 1, "ops": [...]}
	The original Frappe-desk approach wraps it in a `payload` string:
	  payload = '{"contract_version": 1, "ops": [...]}'
	Both arrive as frappe.local.form_dict keys; this helper handles both.
	"""
	if payload:
		return frappe.parse_json(payload)
	# Raw JSON body — Frappe v15 merges application/json body into form_dict,
	# so the top-level keys are directly accessible as function arguments.
	return frappe.local.form_dict


# ── push ─────────────────────────────────────────────────────────────────────


@remote_whitelist(methods=["POST"], remote_auth=True)
def push(payload=None, **kwargs):
	env = _resolve_env(payload)
	_assert_contract_version(env)
	results = []

	for op in env.get("ops", []):
		prior = frappe.db.get_value(
			"Sync Op Log",
			{"client_op_id": op["client_op_id"]},
			["sync_seq", "target_name"],
			as_dict=True,
		)
		if prior:
			results.append(
				{
					"client_op_id": op["client_op_id"],
					"status": "duplicate",
					"server_seq": prior.sync_seq,
					"name": prior.target_name,
				}
			)
			continue

		try:
			op_type = op.get("op", "upsert")

			if op_type == "answers":
				# Clinical Question answers → Observation rows (append-only, invariant 4)
				encounter_uuid, obs_created = _apply_answers_op(op)
				seq = frappe.db.get_value("Encounter", encounter_uuid, "sync_seq") or 0
				_log_op(op["client_op_id"], "Encounter", encounter_uuid, seq)
				results.append(
					{
						"client_op_id": op["client_op_id"],
						"status": "applied",
						"server_seq": seq,
						"name": encounter_uuid,
						"observations_created": obs_created,
					}
				)
			else:
				_assert_in_catchment(op)
				name = _apply_op(op)
				seq = frappe.db.get_value(op["doctype"], name, "sync_seq") or 0
				_log_op(op["client_op_id"], op["doctype"], name, seq)
				results.append(
					{
						"client_op_id": op["client_op_id"],
						"status": "applied",
						"server_seq": seq,
						"name": name,
					}
				)
		except CaseStatusConflict as exc:
			results.append(
				{
					"client_op_id": op["client_op_id"],
					"status": "conflict",
					"reason": str(exc),
					"name": op.get("client_uuid", ""),
				}
			)
		except frappe.ValidationError as exc:
			results.append(
				{
					"client_op_id": op["client_op_id"],
					"status": "rejected",
					"reason": str(exc),
				}
			)
			break

	return {"contract_version": CONTRACT_VERSION, "results": results}


def _apply_op(op):
	doctype = op["doctype"]
	client_uuid = op["client_uuid"]
	payload = {
		k: v
		for k, v in op.get("payload", {}).items()
		if k not in ("geography_node", "care_team", "catchment")
	}

	op_type = op.get("op", "upsert")
	exists = frappe.db.exists(doctype, client_uuid)

	if op_type == "upsert":
		if exists:
			if doctype == "Case":
				_handle_case_update(client_uuid, payload)
			else:
				doc = frappe.get_doc(doctype, client_uuid)
				doc.update(payload)
				doc.save(ignore_permissions=True)
		else:
			doc = frappe.get_doc({"doctype": doctype, "client_uuid": client_uuid, **payload})
			doc.insert(ignore_permissions=True)
		return doc.name

	if op_type == "submit":
		if not exists:
			doc = frappe.get_doc({"doctype": doctype, "client_uuid": client_uuid, **payload})
			doc.insert(ignore_permissions=True)
		else:
			doc = frappe.get_doc(doctype, client_uuid)
		if doc.docstatus == 0:
			doc.submit()
		return doc.name

	raise frappe.ValidationError(_(f"Unknown op type: {op_type}"))


def _apply_answers_op(op):
	"""
	Handle op_type="answers": write one Observation per clinical question answer.

	Wire format (payload):
	  {"answers": [{"question_id": "bp_systolic", "value": "130",
	                "client_uuid": "<uuidv7>", "observed_dt": "2026-06-09T10:30:00Z"}, ...]}

	Only questions with clinical=1 and a fhir_concept produce Observations.
	Non-clinical questions (navigation/context fields like has_symptoms) are skipped.
	Row-level idempotency: client_uuid is the Observation primary key; duplicate insert is skipped.
	Op-level idempotency: handled by the Sync Op Log check in push() before this is called.
	"""
	encounter_uuid = op.get("client_uuid", "")
	answers = op.get("payload", {}).get("answers", [])

	if not frappe.db.exists("Encounter", encounter_uuid):
		frappe.throw(
			_("Encounter {0} not found. Push the Encounter upsert op before its answers.").format(
				encounter_uuid
			),
			frappe.DoesNotExistError,
		)

	case_name = frappe.db.get_value("Encounter", encounter_uuid, "case")

	created = 0
	for ans in answers:
		question_id = ans.get("question_id")
		value = ans.get("value")
		obs_uuid = ans.get("client_uuid")
		raw_dt = ans.get("observed_dt")
		# MariaDB Datetime doesn't accept ISO 8601 Z-suffix or T-separator
		observed_dt = (
			str(raw_dt).replace("Z", "").replace("T", " ") if raw_dt else frappe.utils.now_datetime()
		)

		if not question_id or value is None or not obs_uuid:
			continue

		# Row-level idempotency — already inserted on a prior (partial) retry
		if frappe.db.exists("Observation", obs_uuid):
			created += 1
			continue

		cq = frappe.db.get_value(
			"Clinical Question",
			question_id,
			["clinical", "fhir_concept"],
			as_dict=True,
		)
		if not cq or not cq.clinical or not cq.fhir_concept:
			continue

		frappe.get_doc(
			{
				"doctype": "Observation",
				"client_uuid": obs_uuid,
				"encounter": encounter_uuid,
				"case": case_name,
				"concept": cq.fhir_concept,
				"value": str(value),
				"observed_dt": observed_dt,
				"source_form": "clinical_question",
				"source_docname": question_id,
			}
		).insert(ignore_permissions=True)
		created += 1

	return encounter_uuid, created


def _handle_case_update(name, payload):
	current = frappe.db.get_value("Case", name, "status")
	incoming = payload.get("status")
	if incoming and current and incoming != current and current in ("Closed", "Resolved"):
		raise CaseStatusConflict(
			f"Case {name} is already {current} on the server; supervisor review required."
		)
	doc = frappe.get_doc("Case", name)
	doc.update(payload)
	doc.save(ignore_permissions=True)


def _assert_in_catchment(op):
	catchment = get_user_catchment(frappe.session.user)
	if not catchment:
		return
	payload = op.get("payload", {})
	node = payload.get("geography_node") or payload.get("care_team")
	if node and node not in catchment:
		frappe.throw(_("Record is outside your assigned catchment."), frappe.PermissionError)


def _log_op(client_op_id, doctype, name, seq):
	frappe.get_doc(
		{
			"doctype": "Sync Op Log",
			"client_op_id": client_op_id,
			"target_doctype": doctype,
			"target_name": name,
			"applied_at": frappe.utils.now_datetime(),
			"sync_seq": seq,
		}
	).insert(ignore_permissions=True)


# ── pull ─────────────────────────────────────────────────────────────────────


@remote_whitelist(methods=["POST"], remote_auth=True)
def pull(payload=None, **kwargs):
	env = _resolve_env(payload)
	_assert_contract_version(env)
	cursor = int(env.get("cursor", 0))
	limit = min(int(env.get("limit", 200)), 500)
	catchment = get_user_catchment(frappe.session.user)

	rows = _changes_since(cursor, catchment, limit + 1)
	has_more = len(rows) > limit
	rows = rows[:limit]

	return {
		"contract_version": CONTRACT_VERSION,
		"changes": [_serialize_change(r) for r in rows],
		"next_cursor": rows[-1]["sync_seq"] if rows else cursor,
		"has_more": has_more,
	}


def _changes_since(cursor, catchment, limit):
	results = []
	provider_name = _UNRESOLVED
	for doctype in _SYNCABLE_DOCTYPES:
		try:
			meta = frappe.get_meta(doctype)
		except Exception:
			continue
		has_geography = meta.get_field("geography_node")
		has_care_team = meta.get_field("care_team")

		filters = [["sync_seq", ">", cursor]]
		or_filters = []

		if doctype == "Call Logs":
			# Call Logs' own geography_node is denormalized from its optional
			# `patient` Link (see consultation.start_consultation's uhis_patient_id
			# handling), which the mobile app does not currently populate -- there
			# is no bridge yet between this app's cross-system Patient identity and
			# the legacy platform's own patient ids, so `patient`/`geography_node`
			# are null on every call made through the current mobile build.
			# `uhis_user` (Provider Link), by contrast, is stamped unconditionally
			# on every booking. OR it in alongside the (currently mostly-inert)
			# geography filter, rather than replacing it, so a pulling SK always
			# sees the calls they personally made -- regardless of geography
			# catchment -- and the geography path still works once patient
			# linking is wired up.
			if catchment is not None:  # None == System Manager, unrestricted
				if catchment and has_geography:
					or_filters.append(["geography_node", "in", list(catchment)])
				if provider_name is _UNRESOLVED:
					calling_provider = _resolve_calling_provider(frappe.session.user)
					provider_name = calling_provider.name if calling_provider else None
				if provider_name:
					or_filters.append(["uhis_user", "=", provider_name])
				if not or_filters:
					# No geography match possible and no Provider record for this
					# session -- this doctype contributes nothing for this caller.
					continue
		elif catchment and (has_geography or has_care_team):
			scope_field = "geography_node" if has_geography else "care_team"
			filters.append([scope_field, "in", list(catchment)])

		rows = frappe.get_all(
			doctype,
			filters=filters,
			or_filters=or_filters or None,
			fields=["name", "sync_seq"],
			order_by="sync_seq asc",
			limit=limit,
		)
		for r in rows:
			results.append({"doctype": doctype, "name": r.name, "sync_seq": r.sync_seq})

	results.sort(key=lambda x: x["sync_seq"])
	return results[:limit]


_UNRESOLVED = object()


def _serialize_change(row):
	doc = frappe.get_doc(row["doctype"], row["name"])
	return {
		"doctype": row["doctype"],
		"name": row["name"],
		"sync_seq": row["sync_seq"],
		"deleted": False,
		"doc": doc.as_dict(),
	}


# ── config ────────────────────────────────────────────────────────────────────


@remote_whitelist(methods=["POST"], remote_auth=True)
def config(payload=None, **kwargs):
	env = _resolve_env(payload)
	_assert_contract_version(env)
	client_versions = env.get("config_versions", {})

	forms = _serialize_forms(client_versions.get("forms", 0))
	geography = _serialize_geography(client_versions.get("geography", 0))
	concepts = _serialize_concepts(client_versions.get("concepts", 0))
	clinical_questions = _serialize_clinical_questions()
	symptom_obs_mappings = _serialize_symptom_obs_mappings()
	priority_rules = _serialize_priority_rules()
	protocol_selection_rules = _serialize_protocol_selection_rules()
	recommendation_rules = _serialize_recommendation_rules()

	versions = {
		"forms": frappe.db.count("Programme Form"),
		"geography": frappe.db.count("Geography Node"),
		"concepts": frappe.db.count("Concept"),
		"clinical_questions": frappe.db.count("Clinical Question"),
		"symptom_obs_mappings": frappe.db.count("Symptom Observation Mapping"),
		"priority_rules": frappe.db.count("Priority Rule"),
		"protocol_selection_rules": frappe.db.count("Protocol Selection Rule"),
		"recommendation_rules": frappe.db.count("Recommendation Rule"),
	}

	return {
		"contract_version": CONTRACT_VERSION,
		"forms": forms,
		"geography": geography,
		"concepts": concepts,
		"clinical_questions": clinical_questions,
		"symptom_obs_mappings": symptom_obs_mappings,
		"priority_rules": priority_rules,
		"protocol_selection_rules": protocol_selection_rules,
		"recommendation_rules": recommendation_rules,
		"versions": versions,
	}


def _serialize_forms(client_version):
	from spice_next_core.api.form_config import serialize_programme

	programmes = frappe.get_all("Programme", filters={"active": 1}, fields=["name"])
	return [{"programme": p.name, "forms": serialize_programme(p.name)} for p in programmes]


def _serialize_clinical_questions():
	from spice_next_core.api.form_config import serialize_clinical_questions

	return serialize_clinical_questions()


def _serialize_geography(client_version):
	nodes = frappe.get_all(
		"Geography Node",
		fields=["name", "label", "geography_type", "parent_node"],
	)
	return [dict(n) for n in nodes]


def _serialize_concepts(client_version):
	concepts = frappe.get_all(
		"Concept",
		fields=["name", "code_system", "code", "display"],
	)
	return [dict(c) for c in concepts]


def _serialize_symptom_obs_mappings():
	rows = frappe.get_all(
		"Symptom Observation Mapping",
		fields=["name", "symptom_question_id", "observation_question_id", "mandatory_if_present", "display_order", "programme"],
		order_by="display_order asc",
	)
	return [dict(r) for r in rows]


def _serialize_priority_rules():
	rows = frappe.get_all(
		"Priority Rule",
		filters={"active": 1},
		fields=["name", "rule_name", "programme", "condition_field", "condition_operator", "condition_value", "weight", "reason_label"],
		order_by="weight desc",
	)
	return [dict(r) for r in rows]


def _serialize_protocol_selection_rules():
	rules = frappe.get_all(
		"Protocol Selection Rule",
		filters={"active": 1},
		fields=["name", "rule_name", "programme", "priority", "allow_concurrent", "condition_logic"],
		order_by="priority desc",
	)
	result = []
	for rule in rules:
		conditions = frappe.get_all(
			"Rule Condition",
			filters={"parenttype": "Protocol Selection Rule", "parent": rule.name},
			fields=["condition_field", "condition_operator", "condition_value"],
			order_by="idx asc",
		)
		rule_dict = dict(rule)
		rule_dict["conditions"] = [dict(c) for c in conditions]
		result.append(rule_dict)
	return result


def _serialize_recommendation_rules():
	rules = frappe.get_all(
		"Recommendation Rule",
		filters={"active": 1},
		fields=["name", "rule_name", "programme", "priority", "condition_logic", "severity", "action_type", "action_label", "rationale_template", "guideline_id"],
		order_by="priority desc",
	)
	result = []
	for rule in rules:
		conditions = frappe.get_all(
			"Rule Condition",
			filters={"parenttype": "Recommendation Rule", "parent": rule.name},
			fields=["condition_field", "condition_operator", "condition_value"],
			order_by="idx asc",
		)
		rule_dict = dict(rule)
		rule_dict["conditions"] = [dict(c) for c in conditions]
		result.append(rule_dict)
	return result


# ── helpers ───────────────────────────────────────────────────────────────────


def _get_subtree(geography_node):
	"""Return geography_node + all descendants (BFS, iterative)."""
	nodes, queue = {geography_node}, [geography_node]
	while queue:
		children = frappe.get_all(
			"Geography Node",
			filters={"parent_node": ["in", queue]},
			pluck="name",
		)
		fresh = [c for c in children if c not in nodes]
		nodes.update(fresh)
		queue = fresh
	return nodes


def _resolve_calling_provider(user):
	"""Resolves the calling identity to a Provider record, trying both auth
	paths this app's endpoints support:

	1. A real Frappe session (Desk/System Manager, whatever internal caller
	   already worked before -- Provider.user, a Frappe User Link).
	2. The mobile X-Auth-Token flow: @require_remote_auth (see
	   spice_next_core.auth.decorators) validates the token and sets
	   frappe.local.remote_user_id, but deliberately never touches
	   frappe.session.user (that flow implies allow_guest=True, so
	   frappe.session.user stays "Guest" for the whole request) -- so a
	   mobile caller only ever resolves via current_remote_user_id(),
	   matched against Provider.username, the exact same lookup
	   shukhee_integration.api.consultation._current_provider() already uses.

	Returns the Provider dict (name/role_type/geography_node/facility) or
	None if neither path resolves one."""
	provider = frappe.db.get_value(
		"Provider",
		{"user": user},
		["name", "role_type", "geography_node", "facility"],
		as_dict=True,
	)
	if provider:
		return provider

	remote_user_id = current_remote_user_id()
	if not remote_user_id:
		return None
	return frappe.db.get_value(
		"Provider",
		{"username": remote_user_id},
		["name", "role_type", "geography_node", "facility"],
		as_dict=True,
	)


def get_user_catchment(user):
	"""
	Resolve geography scope for a user.
	  None  → System Manager (unrestricted)
	  set() → empty (no Provider record; sees nothing)
	  {ids} → set of Geography Node names the user may access

	SS (Sashtasebika): scoped to their directly assigned ward + descendants.
	SK (Sashta Kormi): scoped to their facility's geography node + descendants.
	Legacy fallback: Care Team → Facility chain for records without direct assignment.
	"""
	if "System Manager" in frappe.get_roles(user):
		return None

	provider = _resolve_calling_provider(user)
	if not provider:
		return set()

	# SS: directly assigned to a ward geography_node
	if provider.geography_node:
		return _get_subtree(provider.geography_node)

	# SK: scoped via assigned facility → facility's geography_node
	if provider.facility:
		node = frappe.db.get_value("Facility", provider.facility, "geography_node")
		if node:
			return _get_subtree(node)

	# Legacy fallback: Care Team → Facility chain
	care_teams = frappe.get_all(
		"Care Team Member",
		filters={"provider": provider.name},
		pluck="parent",
	)
	if not care_teams:
		return set()
	facilities = frappe.get_all(
		"Care Team",
		filters=[["name", "in", care_teams]],
		pluck="facility",
	)
	nodes = set()
	for fac in filter(None, facilities):
		node = frappe.db.get_value("Facility", fac, "geography_node")
		if node:
			nodes |= _get_subtree(node)
	return nodes


def _assert_contract_version(env):
	version = env.get("contract_version")
	if version != CONTRACT_VERSION:
		frappe.throw(
			{"error": "contract_version_unsupported", "server_supports": [CONTRACT_VERSION]},
			frappe.InvalidStatusError,
		)
