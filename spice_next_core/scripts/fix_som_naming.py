"""
One-time production migration for the Symptom Observation Mapping autoname
fix — see symptom_observation_mapping.py's autoname() docstring for the root
cause (autoincrement unconditionally overrode any supplied name on every
insert, so bench migrate's fixture sync could never find-and-replace an
existing row and just piled on 15 more duplicates every run).

Deploying the code change alone is NOT enough: the doctype's `name` column
is still physically a bigint from when autoname was "autoincrement" (schema
sync does not retroactively alter an existing primary key's column type),
and the table likely already holds many duplicate numeric-named rows
accumulated from repeated migrates before this fix. Run this ONCE, manually,
after deploying this change and before/instead of relying on the next
automatic bench migrate to sort it out on its own:

  bench execute spice_next_core.scripts.fix_som_naming.run

Idempotent: safe to re-run.
  1. Widens the `name` column to varchar(140) if it's still numeric-typed.
  2. Deduplicates by (symptom_question_id, observation_question_id,
     programme, mandatory_if_present, display_order), keeping one row per
     unique combination.
  3. Renames every remaining row to its deterministic composite-key name.
"""

import frappe


def run():
	_widen_name_column()
	_dedupe()
	_rename_to_composite_keys()
	frappe.db.commit()
	print("Total rows now:", frappe.db.count("Symptom Observation Mapping"))


def _widen_name_column():
	col_type = frappe.db.sql(
		"""
		SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
		WHERE TABLE_SCHEMA = DATABASE()
		  AND TABLE_NAME = 'tabSymptom Observation Mapping'
		  AND COLUMN_NAME = 'name'
		"""
	)[0][0]
	if col_type != "varchar":
		frappe.db.sql("ALTER TABLE `tabSymptom Observation Mapping` MODIFY COLUMN `name` varchar(140) NOT NULL")
		print(f"Widened name column from {col_type} to varchar(140).")
	else:
		print("name column is already varchar — skipping.")


def _dedupe():
	frappe.db.sql("SET SQL_SAFE_UPDATES = 0")
	frappe.db.sql(
		"""
		DELETE t1 FROM `tabSymptom Observation Mapping` t1
		JOIN `tabSymptom Observation Mapping` t2
		  ON t1.symptom_question_id = t2.symptom_question_id
		  AND t1.observation_question_id = t2.observation_question_id
		  AND t1.programme = t2.programme
		  AND t1.mandatory_if_present = t2.mandatory_if_present
		  AND t1.display_order = t2.display_order
		  AND (
		      (t1.name REGEXP '^[0-9]+$' AND t2.name REGEXP '^[0-9]+$'
		       AND CAST(t1.name AS UNSIGNED) > CAST(t2.name AS UNSIGNED))
		      OR (t1.name REGEXP '^[0-9]+$' AND t2.name NOT REGEXP '^[0-9]+$')
		  )
		"""
	)
	print("Deduplicated. Rows remaining:", frappe.db.count("Symptom Observation Mapping"))


def _rename_to_composite_keys():
	renamed = 0
	for row in frappe.get_all(
		"Symptom Observation Mapping",
		fields=["name", "symptom_question_id", "observation_question_id", "programme"],
	):
		new_name = "-".join([row.symptom_question_id, row.observation_question_id, row.programme or "ALL"])
		if row.name != new_name:
			frappe.rename_doc("Symptom Observation Mapping", row.name, new_name, force=True)
			renamed += 1
	print(f"Renamed {renamed} rows to their composite-key name.")
