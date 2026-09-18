"""
One-time cleanup: physically drop the `form_group` column from
`tabClinical Question`. The field was removed from the doctype JSON (see
clinical_question.json / backfill_programmes.py) once `programmes` became
the sole scoping field, but Frappe's schema sync on this bench version
doesn't drop orphaned columns automatically -- it just stops reading/writing
them. This finishes the job at the DB level.

Run via bench:
  bench execute spice_next_core.scripts.drop_form_group_column.run

Idempotent: no-ops if the column is already gone.
"""

import frappe


def run():
	cols = [c[0] for c in frappe.db.sql("SHOW COLUMNS FROM `tabClinical Question`")]
	if "form_group" not in cols:
		print("form_group column already absent -- nothing to do.")
		return
	frappe.db.sql_ddl("ALTER TABLE `tabClinical Question` DROP COLUMN `form_group`")
	frappe.db.commit()
	print("Dropped form_group column from tabClinical Question.")


if __name__ == "__main__":
	run()
