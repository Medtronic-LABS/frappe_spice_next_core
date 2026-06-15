import frappe
from frappe import _
from frappe.model.document import Document


class ClinicalQuestion(Document):
    def before_save(self):
        compiled_visibility = _compile_conditions(self.visibility_conditions)
        if compiled_visibility:
            self.visibility_expression = compiled_visibility

        compiled_mandatory = _compile_conditions(self.mandatory_conditions)
        if compiled_mandatory:
            self.mandatory_expression = compiled_mandatory

        if self.clinical and not self.fhir_concept:
            frappe.throw(
                _("Clinical question '{0}' must have a FHIR concept mapping.").format(self.name)
            )


def _compile_conditions(rows):
    if not rows:
        return None
    parts = []
    for i, r in enumerate(rows):
        parts.append(f"{r.field_name} {r.operator} {r.value}")
        if i < len(rows) - 1:
            parts.append(r.logic or "AND")
    return " ".join(parts)
