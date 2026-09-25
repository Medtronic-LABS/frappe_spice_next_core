# Copyright (c) 2026, Medtronic Labs and contributors
# For license information, please see license.txt

"""One-time rename: `UHIS Settings` -> `Spice Settings` (same fields, same
Single-doctype shape -- this is a pure rename, not a schema change). Copies
every existing field value across, then removes the old DocType entirely so
no orphaned Single row/permissions/DocType record is left behind.

Copies via raw SQL against `tabSingles` rather than
`frappe.db.get_singles_dict()` + `frappe.db.set_single_value()` -- the
latter round-trips every value through Python-level fieldtype casting
(confirmed: a Check field's stored '1'/'0' comes back as a Python bool via
the getter, and the setter's parameterized insert then has psycopg2 adapt
that bool to the Postgres boolean literal `true`/`false`, landing in
`tabSingles.value` -- a `varchar` column -- as the literal STRING
'true'/'false' instead of '1'/'0'; `cint('true')` silently returns 0, so
every such field, regardless of its real prior value, reads back as
disabled). Raw SQL copies the exact stored string bytes with no type
conversion in either direction.

`name`/`docstatus`/`idx` are Document-machinery fields, not this doctype's
own -- copying them verbatim would stamp the new Single's identity as the
literal string "UHIS Settings" and (whatever `docstatus` happened to
serialize to) rather than the new doctype's own name and default status,
so they're set explicitly instead of copied.

Guarded on the old DocType still existing so a fresh site (which never had
`UHIS Settings`, only ever `Spice Settings` from the start) skips this
cleanly -- `bench migrate`'s own doctype-sync step runs before patches, so
`Spice Settings` is already present in the DB by the time this executes."""

import frappe


def execute():
	if not frappe.db.exists("DocType", "UHIS Settings"):
		return

	frappe.db.sql('DELETE FROM "tabSingles" WHERE doctype=%s', ("Spice Settings",))
	frappe.db.sql(
		'INSERT INTO "tabSingles" (doctype, field, value) '
		"SELECT %s, field, value FROM \"tabSingles\" WHERE doctype=%s "
		"AND field NOT IN ('name', 'docstatus', 'idx')",
		("Spice Settings", "UHIS Settings"),
	)
	frappe.db.sql(
		'INSERT INTO "tabSingles" (doctype, field, value) VALUES (%s,%s,%s), (%s,%s,%s), (%s,%s,%s)',
		(
			"Spice Settings", "name", "Spice Settings",
			"Spice Settings", "docstatus", "0",
			"Spice Settings", "idx", "0",
		),
	)
	frappe.db.sql('DELETE FROM "tabSingles" WHERE doctype=%s', ("UHIS Settings",))

	frappe.delete_doc("DocType", "UHIS Settings", force=True, ignore_permissions=True)
	frappe.db.commit()
