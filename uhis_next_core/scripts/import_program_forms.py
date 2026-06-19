"""
Import program_forms.json into Clinical Question fixtures.

Adds Clinical Question records for the 7 programme forms that are NOT already
covered by the existing fixtures:
  anc, pncMother, pncNeonatal, pncChild,
  household_registration, household_member_registration, enrollment

Run via bench:
  bench execute uhis_next_core.scripts.import_program_forms.run

Or directly (updates fixture JSON in-place):
  python import_program_forms.py /path/to/program_forms.json /path/to/fixtures/
"""

import json
import os
import re
import sys

# ---- form groups already covered by existing fixtures — skip these ----
_SKIP_FORMS = {
    "ncd",
    "eye_care",
    "cataract",
    "family_planning",
    "pwProfile",
    "pregnancyOutcome",
}

# ---- formType → programme name + snake_case form_group ----
_FORM_TYPE_MAP = {
    "anc":                            ("ANC",              "anc"),
    "pncMother":                      ("PNC",              "pnc_mother"),
    "pncNeonatal":                    ("PNC",              "pnc_neonatal"),
    "pncChild":                       ("PNC",              "pnc_child"),
    "household_registration":         ("Household",        "household"),
    "household_member_registration":  ("Household Member", "household_member"),
    "enrollment":                     ("Enrollment",       "enrollment"),
}

# ---- new programmes to add to programme.json ----
_NEW_PROGRAMMES = [
    {"doctype": "Programme", "name": "ANC",              "label": "ANC Programme",              "program_type": "MCH",     "active": 1, "forms": []},
    {"doctype": "Programme", "name": "PNC",              "label": "PNC Programme",              "program_type": "MCH",     "active": 1, "forms": []},
    {"doctype": "Programme", "name": "Household",        "label": "Household Programme",        "program_type": "General", "active": 1, "forms": []},
    {"doctype": "Programme", "name": "Household Member", "label": "Household Member Programme", "program_type": "General", "active": 1, "forms": []},
    {"doctype": "Programme", "name": "Enrollment",       "label": "Enrollment Programme",       "program_type": "General", "active": 1, "forms": []},
]

# ---- viewType → Frappe/schema fieldtype ----
_SKIP_VIEW_TYPES = {
    "CardView", "InformationLabel", "TextLabel", "Instruction", "QRView",
    "AgeYMD",  # display-only age calculation widget
}

_VIEW_TYPE_MAP = {
    "EditText":              "text",
    "RadioGroup":            "select",
    "SingleSelectionView":   "select",
    "Spinner":               "select",
    "DialogCheckbox":        "multiselect",
    "MultiSelectSpinner":    "multiselect",
    "CheckBox":              "bool",
    "DatePicker":            "date",
    "AgeOrDob":              "date",
    "BP":                    "bp",          # sentinel — split into two fields
}

# ---- well-known FHIR concept assignments by field id ----
_FHIR_CONCEPTS = {
    "systolic":           "LOINC|8480-6",
    "diastolic":          "LOINC|8462-4",
    "weight":             "LOINC|29463-7",
    "height":             "LOINC|8302-2",
    "bmi":                "LOINC|39156-5",
    "hemoglobin":         "LOINC|718-7",
    "gravida":            "LOINC|11996-6",
    "parity":             "LOINC|11977-6",
    "bloodSugarFasting":  "LOINC|2339-0",
    "bloodSugarRandom":   "LOINC|2339-0",
    "fastingBloodSugar":  "LOINC|2339-0",
    "randomBloodSugar":   "LOINC|2339-0",
}


def _to_snake(name):
    s = re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")
    return s.replace("-", "_")


def _condition_to_triple(cond_entry):
    """Convert a single condition entry to a visibility_expression string."""
    target = cond_entry.get("targetId") or cond_entry.get("target_id", "")
    visibility = cond_entry.get("visibility", "visible")
    if not target:
        return None, None

    if cond_entry.get("eq") is not None:
        triple = {"field": target, "operator": "=", "value": str(cond_entry["eq"])}
    elif cond_entry.get("eqList"):
        triple = {"field": target, "operator": "in", "value": [str(v) for v in cond_entry["eqList"]]}
    else:
        return None, None

    expr = f"{target} {triple['operator']} {triple['value']}"
    vc = {
        "doctype":    "Clinical Question Condition",
        "field_name": triple["field"],
        "operator":   triple["operator"],
        "value":      str(triple["value"]) if not isinstance(triple["value"], list) else ",".join(triple["value"]),
        "logic":      "AND",
    }
    # If visibility == "gone" the condition means "hide when X=Y" — invert
    if visibility == "gone":
        inv_op = {"=": "!=", "!=": "=", "in": "not in"}.get(triple["operator"], triple["operator"])
        triple["operator"] = inv_op
        vc["operator"] = inv_op
        expr = f"{target} {inv_op} {triple['value']}"

    return vc, json.dumps(triple)


def _build_records(form_type, form_group, layout):
    records = []
    # Track CardView sequence to build a sub-group label
    current_section = form_type
    seq = 0

    for item in layout:
        view_type = item.get("viewType", "")

        if view_type == "CardView":
            current_section = item.get("id", current_section)
            continue

        if view_type in _SKIP_VIEW_TYPES:
            continue

        fieldtype = _VIEW_TYPE_MAP.get(view_type, "text")
        field_id = item.get("id", "")
        if not field_id:
            continue

        # Build options list
        options_list = item.get("optionsList") or []
        options_str = "\n".join(o.get("name", "") for o in options_list if o.get("name")) if options_list else None

        # Build visibility condition
        conditions = item.get("condition") or []
        vis_conditions = []
        vis_expression = None
        if conditions:
            vc, expr = _condition_to_triple(conditions[0])
            if vc:
                vis_conditions = [vc]
                vis_expression = expr

        fhir_concept = _FHIR_CONCEPTS.get(field_id)
        clinical = 1 if fhir_concept else 0

        if fieldtype == "bp":
            # Split into systolic + diastolic
            for suffix, concept in [("systolic", "LOINC|8480-6"), ("diastolic", "LOINC|8462-4")]:
                seq += 1
                records.append({
                    "doctype": "Clinical Question",
                    "name": f"{form_type}_{field_id}_{suffix}",
                    "label": f"{item.get('title', field_id)} ({suffix.capitalize()})",
                    "form_group": form_group,
                    "sequence": seq,
                    "fieldtype": "float",
                    "options": None,
                    "mandatory": int(bool(item.get("isMandatory"))),
                    "clinical": 1,
                    "fhir_concept": concept,
                    "min_age_months": None,
                    "max_age_months": None,
                    "gender": "Any",
                    "visibility_conditions": vis_conditions,
                    "visibility_expression": vis_expression,
                    "mandatory_conditions": [],
                    "mandatory_expression": None,
                })
            continue

        seq += 1
        records.append({
            "doctype": "Clinical Question",
            "name": f"{form_type}_{field_id}",
            "label": item.get("title") or field_id,
            "form_group": form_group,
            "sequence": seq,
            "fieldtype": "float" if fieldtype == "text" and field_id in _FHIR_CONCEPTS else fieldtype,
            "options": options_str,
            "mandatory": int(bool(item.get("isMandatory"))),
            "clinical": clinical,
            "fhir_concept": fhir_concept,
            "min_age_months": None,
            "max_age_months": None,
            "gender": "Any",
            "visibility_conditions": vis_conditions,
            "visibility_expression": vis_expression,
            "mandatory_conditions": [],
            "mandatory_expression": None,
        })

    return records


def run(source_json=None, fixtures_dir=None):
    """
    Main entry point. Called by bench execute or directly.
    source_json: path to program_forms.json (default: auto-locate)
    fixtures_dir: path to uhis_next_core/fixtures/ (default: auto-locate)
    """
    base = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.join(base, "..", "..")   # uhis_next_core/

    if source_json is None:
        # Walk up to find uhis-next/program_forms.json
        candidate = os.path.normpath(os.path.join(app_root, "..", "..", "..", "..", "..", "..", "program_forms.json"))
        if not os.path.exists(candidate):
            raise FileNotFoundError(f"program_forms.json not found at {candidate}. Pass source_json explicitly.")
        source_json = candidate

    if fixtures_dir is None:
        fixtures_dir = os.path.normpath(os.path.join(app_root, "uhis_next_core", "fixtures"))

    cq_path = os.path.join(fixtures_dir, "clinical_question.json")
    prog_path = os.path.join(fixtures_dir, "programme.json")

    with open(source_json) as f:
        src = json.load(f)

    form_data = src["entity"]["formData"]

    # Build a lookup of form layouts
    layouts = {}
    for fd in form_data:
        ft = fd["formType"]
        fi = json.loads(fd["formInput"])
        layouts[ft] = fi["formLayout"]

    # Load existing fixtures
    with open(cq_path) as f:
        existing_cq = json.load(f)
    with open(prog_path) as f:
        existing_prog = json.load(f)

    existing_cq_names = {q["name"] for q in existing_cq}
    existing_prog_names = {p["name"] for p in existing_prog}

    new_cq = list(existing_cq)
    new_prog = list(existing_prog)

    # Add missing programmes
    for prog in _NEW_PROGRAMMES:
        if prog["name"] not in existing_prog_names:
            new_prog.append(prog)
            print(f"  [programme] + {prog['name']}")

    # Generate Clinical Questions for missing forms
    for form_type, (programme, form_group) in _FORM_TYPE_MAP.items():
        if form_type in _SKIP_FORMS:
            continue
        layout = layouts.get(form_type, [])
        if not layout:
            print(f"  [skip] {form_type}: no layout found in source")
            continue

        records = _build_records(form_type, form_group, layout)
        added = 0
        for rec in records:
            if rec["name"] in existing_cq_names:
                print(f"  [exists] {rec['name']} — skipping")
                continue
            new_cq.append(rec)
            existing_cq_names.add(rec["name"])
            added += 1
        print(f"  [form] {form_type} → {form_group}: +{added} questions")

    with open(cq_path, "w") as f:
        json.dump(new_cq, f, indent=2, ensure_ascii=False)

    with open(prog_path, "w") as f:
        json.dump(new_prog, f, indent=2, ensure_ascii=False)

    print(f"\nDone. clinical_question.json: {len(existing_cq)} → {len(new_cq)} records")
    print(f"programme.json: {len(existing_prog)} → {len(new_prog)} records")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    fdir = sys.argv[2] if len(sys.argv) > 2 else None
    run(src, fdir)
