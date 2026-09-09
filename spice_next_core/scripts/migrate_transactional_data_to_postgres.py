"""
One-time MariaDB -> PostgreSQL data carry-over for the uhis-next dev bench.

Frappe has no built-in cross-engine site migration (bench migrate-to targets
Frappe Cloud hosting, not DB engines; backups are engine-native and not
cross-restorable). Fixture-backed config data (Clinical Question, Concept,
Programme, CDSS rules, Workspace, Geography Node's reference set) already
repopulates via `bench migrate` on any fresh site regardless of backend.
This script carries over the remaining real records -- non-fixture User,
Geography Node, Facility, Provider, Care Team, and the clinical/transactional
doctypes -- via the ORM only (frappe.get_doc()/.insert()), never raw SQL, so
MariaDB/Postgres dialect differences never enter the picture.

Usage (two separate `bench execute` invocations, against two different sites):

  # 1. Export, run against the preserved MariaDB site:
  bench --site uhis.localhost.mariadb-backup execute \
    spice_next_core.scripts.migrate_transactional_data_to_postgres.export_data

  # 2. Import, run against the new Postgres site:
  bench --site uhis.localhost execute \
    spice_next_core.scripts.migrate_transactional_data_to_postgres.import_data

Idempotent: safe to re-run either step. Export always re-dumps current
source state; import skips any record that already exists by name.
"""

import json
import os

import frappe

# Dependency order matters as a *starting* order (parents before children) --
# but import_data() also retries failures across passes, so this doesn't need
# to be perfectly exhaustive; anything genuinely out of order self-corrects.
DOCTYPES_IN_DEPENDENCY_ORDER = [
	"User",
	"Geography Type",
	"Geography Node",
	"Organization",
	"Facility",
	"Provider",
	"Care Team",
	"Household",
	"Patient",
	"Case",
	"Encounter",
	"Observation",
	"Condition",
	"Referral",
	"Task",
]

# Household.members (child table) and Patient.primary_household reference
# each other -- a genuine cycle, not just an ordering problem. Broken by
# inserting Patient with primary_household cleared, then backfilling it via
# db.set_value once the Household it points to exists.
_DEFER_ON_INSERT = {"Patient": "primary_household"}

# Source data predates a role_type option tightening on the Provider doctype
# -- these two values no longer validate. Remapped to the closest current
# equivalent (confirmed with the user) rather than silently dropped or
# force-inserted invalid.
_PROVIDER_ROLE_TYPE_REMAP = {"CHW": "SK", "Nurse": "Doctor"}

# Bench-root-level (not site-level) so it survives the source site directory
# being renamed aside and isn't tied to either site's name.
EXPORT_DIR = os.path.join(frappe.utils.get_bench_path(), "pg_migration_export")

# Users that always exist on any Frappe site and must never be touched.
_SKIP_USERS = {"Administrator", "Guest"}


def export_data():
	os.makedirs(EXPORT_DIR, exist_ok=True)
	summary = {}
	for dt in DOCTYPES_IN_DEPENDENCY_ORDER:
		names = frappe.get_all(dt, pluck="name")
		if dt == "User":
			names = [n for n in names if n not in _SKIP_USERS]
		records = []
		for name in names:
			doc = frappe.get_doc(dt, name)
			records.append(doc.as_dict())
		path = os.path.join(EXPORT_DIR, f"{frappe.scrub(dt)}.json")
		with open(path, "w") as f:
			json.dump(records, f, indent=1, default=str)
		summary[dt] = len(records)
		print(f"  [export] {dt}: {len(records)} -> {path}")
	print("Export summary:", summary)


def import_data():
	pending = {}
	for dt in DOCTYPES_IN_DEPENDENCY_ORDER:
		path = os.path.join(EXPORT_DIR, f"{frappe.scrub(dt)}.json")
		if not os.path.exists(path):
			print(f"  [skip] {dt}: no export file at {path}")
			continue
		with open(path) as f:
			records = json.load(f)
		if dt == "Provider":
			for r in records:
				if r.get("role_type") in _PROVIDER_ROLE_TYPE_REMAP:
					old = r["role_type"]
					r["role_type"] = _PROVIDER_ROLE_TYPE_REMAP[old]
					print(f"  [remap] Provider/{r['name']}: role_type {old} -> {r['role_type']}")
		pending[dt] = [r for r in records if not frappe.db.exists(dt, r["name"])]
		already = len(records) - len(pending[dt])
		print(f"  [queued] {dt}: {len(pending[dt])} to insert, {already} already present")

	inserted = {dt: 0 for dt in pending}
	deferred_links = []  # (doctype, name, fieldname, target) to backfill after the loop
	rounds = 0
	while any(pending.values()):
		rounds += 1
		progressed = False
		for dt in DOCTYPES_IN_DEPENDENCY_ORDER:
			still_pending = []
			defer_field = _DEFER_ON_INSERT.get(dt)
			for record in pending.get(dt, []):
				deferred_value = record.pop(defer_field, None) if defer_field else None
				try:
					doc = frappe.get_doc(record)
					doc.flags.ignore_links = False
					doc.insert(ignore_permissions=True)
					inserted[dt] += 1
					progressed = True
					if defer_field and deferred_value:
						deferred_links.append((dt, doc.name, defer_field, deferred_value))
				except (frappe.LinkValidationError, frappe.exceptions.MandatoryError):
					if deferred_value:
						record[defer_field] = deferred_value
					still_pending.append(record)
				except Exception as e:
					print(f"  [error] {dt}/{record.get('name')}: {e}")
			pending[dt] = still_pending
		frappe.db.commit()
		if not progressed:
			break

	backfilled = 0
	for dt, name, fieldname, target in deferred_links:
		if frappe.db.exists(dt, name) and frappe.db.get_value(dt, name, fieldname) != target:
			frappe.db.set_value(dt, name, fieldname, target, update_modified=False)
			backfilled += 1
	if deferred_links:
		frappe.db.commit()
	print(f"Backfilled {backfilled}/{len(deferred_links)} deferred links.")

	print(f"Import finished after {rounds} pass(es).")
	for dt in DOCTYPES_IN_DEPENDENCY_ORDER:
		remaining = len(pending.get(dt, []))
		print(f"  [{dt}] inserted={inserted[dt]} unresolved={remaining}")
		if remaining:
			for r in pending[dt]:
				print(f"    unresolved: {dt}/{r.get('name')}")


if __name__ == "__main__":
	pass
