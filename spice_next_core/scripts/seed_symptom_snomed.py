"""
Seed SNOMED-coded symptom Clinical Questions (ANC/PNC/NCD) and consolidate
the pre-existing per-programme duplicate rows the new codes exposed
(Headache, Fever, Dizziness) into single multi-programme rows via the new
`programmes` (Table MultiSelect -> Clinical Question Programme) field.

Run via bench:
  bench execute spice_next_core.scripts.seed_symptom_snomed.run

Idempotent: safe to re-run — existing Concepts/renames/updates are skipped
or reapplied without creating duplicates.
"""

import frappe

_CONCEPTS = [
    ("SNOMED|25064002", "25064002", "Headache"),
    ("SNOMED|267038008", "267038008", "Oedema"),
    ("SNOMED|21522001", "21522001", "Abdominal pain"),
    ("SNOMED|63102001", "63102001", "Visual disturbance"),
    ("SNOMED|276369006", "276369006", "Reduced fetal movement"),
    ("SNOMED|289530006", "289530006", "Bleeding from vagina"),
    ("SNOMED|386661006", "386661006", "Fever"),
    ("SNOMED|404640003", "404640003", "Dizziness"),
    ("SNOMED|84229001", "84229001", "Fatigue"),
    ("SNOMED|267036007", "267036007", "Polydipsia"),
    ("SNOMED|29857009", "29857009", "Chest pain"),
    ("SNOMED|44077006", "44077006", "Numbness"),
]


def _ensure_concepts():
    for name, code, display in _CONCEPTS:
        if frappe.db.exists("Concept", name):
            continue
        frappe.get_doc({
            "doctype": "Concept",
            "code_system": "SNOMED",
            "code": code,
            "display": display,
        }).insert(ignore_permissions=True, set_name=name)
        print(f"  [concept] + {name}")


def _set_programmes(doc, programme_names):
    doc.set("programmes", [])
    for p in programme_names:
        doc.append("programmes", {"programme": p})


def _repoint_mappings(old_symptom_question_id, new_symptom_question_id):
    frappe.db.set_value(
        "Symptom Observation Mapping",
        {"symptom_question_id": old_symptom_question_id},
        "symptom_question_id",
        new_symptom_question_id,
    )


def _merge(old_primary_name, new_name, dropped_names, programmes, fhir_concept):
    """Rename old_primary_name -> new_name (auto-repoints its own Links),
    repoint any dropped_names' mapping rows onto new_name, delete dropped_names,
    then set programmes/clinical/fhir_concept on the merged doc."""
    if frappe.db.exists("Clinical Question", old_primary_name):
        frappe.rename_doc("Clinical Question", old_primary_name, new_name, force=True)
        print(f"  [rename] {old_primary_name} -> {new_name}")

    for dropped in dropped_names:
        if not frappe.db.exists("Clinical Question", dropped):
            continue
        _repoint_mappings(dropped, new_name)
        frappe.delete_doc("Clinical Question", dropped, force=True, ignore_permissions=True)
        print(f"  [delete] {dropped} (mapping rows repointed to {new_name})")

    doc = frappe.get_doc("Clinical Question", new_name)
    _set_programmes(doc, programmes)
    doc.clinical = 1
    doc.fhir_concept = fhir_concept
    doc.save(ignore_permissions=True)
    print(f"  [update] {new_name}: programmes={programmes}, clinical=1, {fhir_concept}")


def _update_in_place(name, fhir_concept, label=None, programmes=None):
    doc = frappe.get_doc("Clinical Question", name)
    if label:
        doc.label = label
    if programmes:
        _set_programmes(doc, programmes)
    doc.clinical = 1
    doc.fhir_concept = fhir_concept
    doc.save(ignore_permissions=True)
    print(f"  [update] {name}: label={doc.label!r}, clinical=1, {fhir_concept}"
          + (f", programmes={programmes}" if programmes else ""))


def _create_new(name, label, form_group, sequence, fhir_concept, programmes=None):
    if frappe.db.exists("Clinical Question", name):
        print(f"  [exists] {name} — skipping create")
        return
    doc = frappe.get_doc({
        "doctype": "Clinical Question",
        "label": label,
        "form_group": form_group,
        "question_type": "symptom",
        "sequence": sequence,
        "fieldtype": "Check",
        "mandatory": 0,
        "clinical": 1,
        "fhir_concept": fhir_concept,
    })
    if programmes:
        _set_programmes(doc, programmes)
    doc.insert(ignore_permissions=True, set_name=name)
    print(f"  [create] {name}")


def run():
    # Matches the same bypass bench's own fixture sync relies on: several
    # pre-existing symptom rows use fieldtype="Check", which is outside the
    # doctype's declared Select options but already tolerated in production.
    # Setting this preserves that existing (untouched) wire value exactly —
    # it must not flip to a "valid" value like "bool" as a side effect here.
    frappe.flags.in_import = True

    print("Seeding SNOMED concepts...")
    _ensure_concepts()

    print("Merging duplicate per-programme symptom rows...")
    _merge("ncd_sym_headache", "sym_headache", ["anc_sym_headache"],
           ["NCD", "ANC", "PNC"], "SNOMED|25064002")
    _merge("ncd_sym_fever", "sym_fever", ["anc_sym_fever"],
           ["NCD", "ANC", "PNC"], "SNOMED|386661006")
    _merge("ncd_sym_dizziness", "sym_dizziness", [],
           ["NCD", "PNC"], "SNOMED|404640003")

    print("Updating in-place rows...")
    _update_in_place("ncd_sym_chest_pain", "SNOMED|29857009")
    _update_in_place("ncd_sym_fatigue", "SNOMED|84229001", label="Feeling tired/weak")
    _update_in_place("anc_sym_swelling", "SNOMED|267038008", label="Swelling")
    _update_in_place("anc_sym_visual_disturbance", "SNOMED|63102001", label="Blurry vision")
    _update_in_place("anc_sym_reduced_fetal_movement", "SNOMED|276369006", label="Baby not moving")
    _update_in_place("anc_sym_bleeding", "SNOMED|289530006", label="Bleeding",
                      programmes=["ANC", "PNC"])
    _update_in_place("ncd_sym_leg_swelling", "SNOMED|267038008")
    _update_in_place("ch_sym_fever", "SNOMED|386661006")

    print("Creating new rows...")
    _create_new("sym_abdominal_pain", "Abdominal pain", "anc", 106,
                "SNOMED|21522001", programmes=["ANC", "PNC"])
    _create_new("ncd_sym_polydipsia", "Very thirsty", "ncd", 108, "SNOMED|267036007")
    _create_new("ncd_sym_numbness", "Numbness", "ncd", 109, "SNOMED|44077006")

    frappe.db.commit()
    print("Done.")


def dedupe_symptom_observation_mapping():
    """One-off cleanup: repeated bench migrate/reinstall cycles re-inserted the
    full Symptom Observation Mapping fixture set each time (autoname=autoincrement
    doesn't match fixture rows by their JSON `name`), leaving exact-duplicate rows.
    Keeps the oldest row per unique (symptom, observation, programme, mandatory,
    display_order) combination and deletes the rest."""
    keep_rows = frappe.db.sql(
        """
        select min(cast(name as unsigned)) as keep_name
        from `tabSymptom Observation Mapping`
        group by symptom_question_id, observation_question_id, programme,
                 mandatory_if_present, display_order
        """,
        as_dict=True,
    )
    keep_names = {str(r["keep_name"]) for r in keep_rows}
    all_names = frappe.db.sql_list("select name from `tabSymptom Observation Mapping`")
    to_delete = [n for n in all_names if n not in keep_names]
    print(f"Symptom Observation Mapping: {len(all_names)} rows, keeping {len(keep_names)}, deleting {len(to_delete)}")
    for n in to_delete:
        frappe.delete_doc("Symptom Observation Mapping", n, force=True, ignore_permissions=True)
    frappe.db.commit()
    print("Total after:", frappe.db.count("Symptom Observation Mapping"))


def reapply_merge_repoints():
    """Recovery helper: after a fixture re-sync restores the original 15
    Symptom Observation Mapping rows (with their original pre-merge
    symptom_question_id values), repoint the ones affected by the
    Headache/Fever/Dizziness merges onto the new merged Clinical Question
    names. Safe to re-run — no-ops once already repointed."""
    for old, new in [
        ("ncd_sym_headache", "sym_headache"),
        ("anc_sym_headache", "sym_headache"),
        ("ncd_sym_fever", "sym_fever"),
        ("anc_sym_fever", "sym_fever"),
        ("ncd_sym_dizziness", "sym_dizziness"),
    ]:
        n = frappe.db.count("Symptom Observation Mapping", {"symptom_question_id": old})
        if n:
            _repoint_mappings(old, new)
            print(f"  [repoint] {old} -> {new} ({n} rows)")
    frappe.db.commit()
    rows = frappe.db.sql(
        "select symptom_question_id, count(*) c from `tabSymptom Observation Mapping` group by symptom_question_id order by symptom_question_id",
        as_dict=True,
    )
    print("Final grouping:", rows)
    print("Total:", frappe.db.count("Symptom Observation Mapping"))


def cleanup_resurrected_duplicates():
    """Recovery helper: a bench migrate run before fixtures were re-exported
    re-synced the still-stale clinical_question.json, resurrecting the 5
    pre-merge docs (ncd_sym_headache, anc_sym_headache, ncd_sym_fever,
    anc_sym_fever, ncd_sym_dizziness) alongside their merged replacements.
    Deletes the resurrected duplicates — safe since Symptom Observation
    Mapping already points at the merged names, not these."""
    for name in [
        "ncd_sym_headache", "anc_sym_headache",
        "ncd_sym_fever", "anc_sym_fever",
        "ncd_sym_dizziness",
    ]:
        if frappe.db.exists("Clinical Question", name):
            linked = frappe.db.count("Symptom Observation Mapping", {"symptom_question_id": name})
            if linked:
                print(f"  [skip] {name} still has {linked} linked mapping rows — not deleting")
                continue
            frappe.delete_doc("Clinical Question", name, force=True, ignore_permissions=True)
            print(f"  [delete] {name}")
    frappe.db.commit()


if __name__ == "__main__":
    run()
