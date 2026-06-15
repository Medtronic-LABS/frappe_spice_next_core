"""
Sync endpoints — the ONLY path for device ↔ server data exchange.

  uhis_next_core.api.sync.push   device → server (idempotent, catchment-scoped)
  uhis_next_core.api.sync.pull   server → device (cursor-based, server-side scope)
  uhis_next_core.api.sync.config server → device (versioned form / geography / concept config)

Wire contract: ../../docs/api-contract/sync-envelope.md
"""

import frappe
from frappe import _

CONTRACT_VERSION = 1

_SYNCABLE_DOCTYPES = [
    "Patient", "Household", "Case", "Encounter",
    "Observation", "Condition", "Referral", "Task",
]


class CaseStatusConflict(Exception):
    pass


# ── push ─────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def push(payload):
    env = frappe.parse_json(payload)
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
            results.append({
                "client_op_id": op["client_op_id"],
                "status": "duplicate",
                "server_seq": prior.sync_seq,
                "name": prior.target_name,
            })
            continue

        try:
            op_type = op.get("op", "upsert")

            if op_type == "answers":
                # Clinical Question answers → Observation rows (append-only, invariant 4)
                encounter_uuid, obs_created = _apply_answers_op(op)
                seq = frappe.db.get_value("Encounter", encounter_uuid, "sync_seq") or 0
                _log_op(op["client_op_id"], "Encounter", encounter_uuid, seq)
                results.append({
                    "client_op_id": op["client_op_id"],
                    "status": "applied",
                    "server_seq": seq,
                    "name": encounter_uuid,
                    "observations_created": obs_created,
                })
            else:
                _assert_in_catchment(op)
                name = _apply_op(op)
                seq = frappe.db.get_value(op["doctype"], name, "sync_seq") or 0
                _log_op(op["client_op_id"], op["doctype"], name, seq)
                results.append({
                    "client_op_id": op["client_op_id"],
                    "status": "applied",
                    "server_seq": seq,
                    "name": name,
                })
        except CaseStatusConflict as exc:
            results.append({
                "client_op_id": op["client_op_id"],
                "status": "conflict",
                "reason": str(exc),
                "name": op.get("client_uuid", ""),
            })
        except frappe.ValidationError as exc:
            results.append({
                "client_op_id": op["client_op_id"],
                "status": "rejected",
                "reason": str(exc),
            })
            break

    return {"contract_version": CONTRACT_VERSION, "results": results}


def _apply_op(op):
    doctype = op["doctype"]
    client_uuid = op["client_uuid"]
    payload = {k: v for k, v in op.get("payload", {}).items()
               if k not in ("geography_node", "care_team", "catchment")}

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
            str(raw_dt).replace("Z", "").replace("T", " ") if raw_dt
            else frappe.utils.now_datetime()
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

        frappe.get_doc({
            "doctype": "Observation",
            "client_uuid": obs_uuid,
            "encounter": encounter_uuid,
            "case": case_name,
            "concept": cq.fhir_concept,
            "value": str(value),
            "observed_dt": observed_dt,
            "source_form": "clinical_question",
            "source_docname": question_id,
        }).insert(ignore_permissions=True)
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
    frappe.get_doc({
        "doctype": "Sync Op Log",
        "client_op_id": client_op_id,
        "target_doctype": doctype,
        "target_name": name,
        "applied_at": frappe.utils.now_datetime(),
        "sync_seq": seq,
    }).insert(ignore_permissions=True)


# ── pull ─────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def pull(payload):
    env = frappe.parse_json(payload)
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
    for doctype in _SYNCABLE_DOCTYPES:
        try:
            meta = frappe.get_meta(doctype)
        except Exception:
            continue
        has_geography = meta.get_field("geography_node")
        has_care_team = meta.get_field("care_team")

        filters = [["sync_seq", ">", cursor]]
        if catchment and (has_geography or has_care_team):
            scope_field = "geography_node" if has_geography else "care_team"
            filters.append([scope_field, "in", list(catchment)])

        rows = frappe.get_all(
            doctype,
            filters=filters,
            fields=["name", "sync_seq"],
            order_by="sync_seq asc",
            limit=limit,
        )
        for r in rows:
            results.append({"doctype": doctype, "name": r.name, "sync_seq": r.sync_seq})

    results.sort(key=lambda x: x["sync_seq"])
    return results[:limit]


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

@frappe.whitelist()
def config(payload):
    env = frappe.parse_json(payload)
    _assert_contract_version(env)
    client_versions = env.get("config_versions", {})

    forms = _serialize_forms(client_versions.get("forms", 0))
    geography = _serialize_geography(client_versions.get("geography", 0))
    concepts = _serialize_concepts(client_versions.get("concepts", 0))
    clinical_questions = _serialize_clinical_questions()

    versions = {
        "forms": frappe.db.count("Programme Form"),
        "geography": frappe.db.count("Geography Node"),
        "concepts": frappe.db.count("Concept"),
        "clinical_questions": frappe.db.count("Clinical Question"),
    }

    return {
        "contract_version": CONTRACT_VERSION,
        "forms": forms,
        "geography": geography,
        "concepts": concepts,
        "clinical_questions": clinical_questions,
        "versions": versions,
    }


def _serialize_forms(client_version):
    from uhis_next_core.api.form_config import serialize_programme
    programmes = frappe.get_all("Programme", filters={"active": 1}, fields=["name"])
    return [{"programme": p.name, "forms": serialize_programme(p.name)} for p in programmes]


def _serialize_clinical_questions():
    from uhis_next_core.api.form_config import serialize_clinical_questions
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


# ── helpers ───────────────────────────────────────────────────────────────────

def get_user_catchment(user):
    if "System Manager" in frappe.get_roles(user):
        return None

    provider = frappe.db.get_value("Provider", {"user": user}, "name")
    if not provider:
        return set()

    care_team_rows = frappe.get_all(
        "Care Team Member",
        filters={"provider": provider},
        fields=["parent"],
    )
    care_teams = [r.parent for r in care_team_rows]
    if not care_teams:
        return set()

    facility_rows = frappe.get_all(
        "Care Team",
        filters=[["name", "in", care_teams]],
        fields=["facility"],
    )
    facilities = [r.facility for r in facility_rows]
    if not facilities:
        return set()

    node_rows = frappe.get_all(
        "Facility",
        filters=[["name", "in", facilities]],
        fields=["geography_node"],
    )
    return {r.geography_node for r in node_rows if r.geography_node}


def _assert_contract_version(env):
    version = env.get("contract_version")
    if version != CONTRACT_VERSION:
        frappe.throw(
            {"error": "contract_version_unsupported", "server_supports": [CONTRACT_VERSION]},
            frappe.InvalidStatusError,
        )
