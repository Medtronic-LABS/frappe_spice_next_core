"""
Invariant 4: Observation and Encounter are append-only facts.
Raises ValidationError if any non-system field is mutated on a submitted doc.
"""

import frappe
from frappe import _

_SYSTEM_FIELDS = frozenset({
    "modified", "modified_by", "sync_seq", "idx",
    "__islocal", "__unsaved", "docstatus",
})


def reject_mutation(doc, event):
    if doc.docstatus != 1:
        return

    before = doc.get_doc_before_save()
    if not before:
        return

    for field in doc.meta.fields:
        fname = field.fieldname
        if fname in _SYSTEM_FIELDS:
            continue
        if doc.get(fname) != before.get(fname):
            frappe.throw(
                _("{0} {1} is an append-only clinical fact and cannot be modified after submission.").format(
                    doc.doctype, doc.name
                ),
                frappe.ValidationError,
            )
