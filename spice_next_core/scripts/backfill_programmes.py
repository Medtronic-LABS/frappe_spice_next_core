"""
One-time backfill: populate the `programmes` Table MultiSelect on every
Clinical Question that doesn't have one yet, deriving it from the legacy
`form_group` value. Companion to dedupe_clinical_questions.py -- that script
only populated `programmes` for the genuinely multi-programme merge targets;
this closes the gap so EVERY question carries an explicit Programme, making
`programmes` the single source of truth (no more "leave empty, fall back to
form_group" convention).

Run via bench:
  bench execute spice_next_core.scripts.backfill_programmes.run

Idempotent: skips any question that already has a programmes row.
"""

import frappe

FORM_GROUP_TO_PROGRAMME = {
    "ncd": "NCD",
    "anc": "ANC",
    "pnc_mother": "PNC",
    "pnc_child": "PNC",
    "pnc_neonatal": "PNC",
    "cataract": "Cataract",
    "eye_care": "Eye Care",
    "family_planning": "Family Planning",
    "pw_profile": "PW Profile",
    "pregnancy_outcome": "Pregnancy Outcome",
    "household": "Household",
    "household_member": "Household Member",
    "enrollment": "Enrollment",
    "tb": "TB",
    "child_health": "Child Health",
}


def run():
    frappe.flags.in_import = True
    already = set(frappe.get_all(
        "Clinical Question Programme", filters={"parenttype": "Clinical Question"}, pluck="parent",
    ))
    qs = frappe.get_all("Clinical Question", fields=["name", "form_group"])
    backfilled, skipped, unmapped = 0, 0, []
    for q in qs:
        if q.name in already:
            skipped += 1
            continue
        programme = FORM_GROUP_TO_PROGRAMME.get(q.form_group)
        if not programme:
            unmapped.append((q.name, q.form_group))
            continue
        doc = frappe.get_doc("Clinical Question", q.name)
        doc.append("programmes", {"programme": programme})
        doc.save(ignore_permissions=True)
        backfilled += 1

    frappe.db.commit()
    print(f"Backfilled: {backfilled} | already had programmes: {skipped} | unmapped: {len(unmapped)}")
    if unmapped:
        print("Unmapped (no Programme found for form_group):", unmapped)


if __name__ == "__main__":
    run()
