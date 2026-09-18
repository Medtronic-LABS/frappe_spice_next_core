import frappe
from frappe.model.document import Document


class SymptomObservationMapping(Document):
    def autoname(self):
        # Deterministic composite key, not autoincrement: fixture sync
        # (import_doc, frappe/modules/import_file.py) only stays idempotent
        # when frappe.db.exists(doctype, doc.name) can actually match an
        # existing record so it can delete-then-recreate it. autoincrement
        # unconditionally overrides doc.name with a fresh sequence value on
        # every insert (frappe/model/naming.py:is_autoincremented), so the
        # name a fixture row asks for is never the name it actually gets —
        # the exists() check can never match, nothing is ever deleted, and
        # every bench migrate silently piled on 15 more duplicate rows.
        # The (symptom, observation, programme) triple is already the row's
        # real identity, so it doubles as a stable, collision-free name.
        self.name = "-".join([
            self.symptom_question_id,
            self.observation_question_id,
            self.programme or "ALL",
        ])
