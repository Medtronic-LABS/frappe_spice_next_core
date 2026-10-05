"""
Integration tests for api/fhir.observations_by_encounter -- real DB, real
Patient/Case/Encounter/Observation/Concept creation, since the behaviour under
test (grouping sibling systolic/diastolic Observations into one FHIR blood-
pressure composite) only exists at the level of real rows with real concept
codes, not in isolatable pure logic.
"""

import unittest

import frappe

from spice_next_core.api.fhir import observations_by_encounter


class TestObservationsByEncounter(unittest.TestCase):

	def setUp(self):
		self._docs = []  # [(doctype, name), ...] in creation order, deleted in reverse

	def tearDown(self):
		for doctype, name in reversed(self._docs):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, ignore_permissions=True, delete_permanently=True, force=True)
		frappe.db.commit()

	def _make(self, doctype, fields, *, set_name=None):
		fields = dict(fields, doctype=doctype)
		# Patient/Case/Encounter/Observation all autoname on client_uuid,
		# reqd=1 -- generate one unless the caller already supplied it.
		if frappe.get_meta(doctype).has_field("client_uuid") and "client_uuid" not in fields:
			fields["client_uuid"] = frappe.generate_hash(length=16)
		doc = frappe.get_doc(fields)
		if set_name:
			doc.insert(ignore_permissions=True, set_name=set_name)
		else:
			doc.insert(ignore_permissions=True)
		self._docs.append((doctype, doc.name))
		return doc

	def _concept(self, name, code):
		if frappe.db.exists("Concept", name):
			return name
		self._make(
			"Concept", {"code_system": "LOINC", "code": code, "display": name}, set_name=name
		)
		return name

	def _encounter_with_observations(self, observations):
		"""observations: list of (concept_name, loinc_code, value, unit,
		effective) -- all sharing one Patient/Case/Encounter."""
		patient = self._make("Patient", {"full_name": "Test Patient"})
		case = self._make(
			"Case", {"patient": patient.name, "patient_name": patient.full_name}
		)
		encounter = self._make("Encounter", {"case": case.name, "encounter_type": "Routine"})
		for concept_name, code, value, unit, effective in observations:
			self._concept(concept_name, code)
			self._make(
				"Observation",
				{
					"encounter": encounter.name,
					"case": case.name,
					"concept": concept_name,
					"value": value,
					"unit": unit,
					"observed_dt": effective,
				},
			)
		return encounter.name

	def test_bp_pair_grouped_into_one_composite(self):
		effective = "2026-10-06 10:00:00"
		encounter = self._encounter_with_observations(
			[
				("t-systolic", "8480-6", "120", "mm[Hg]", effective),
				("t-diastolic", "8462-4", "80", "mm[Hg]", effective),
			]
		)
		bundle = observations_by_encounter(encounter)

		self.assertEqual(bundle["resourceType"], "Bundle")
		self.assertEqual(bundle["total"], 1)
		resource = bundle["entry"][0]["resource"]
		self.assertEqual(resource["code"]["coding"][0]["code"], "85354-9")
		component_codes = {c["code"]["coding"][0]["code"] for c in resource["component"]}
		self.assertEqual(component_codes, {"8480-6", "8462-4"})
		values = {
			c["code"]["coding"][0]["code"]: c["valueQuantity"]["value"] for c in resource["component"]
		}
		self.assertEqual(values["8480-6"], 120.0)
		self.assertEqual(values["8462-4"], 80.0)

	def test_non_bp_observation_passes_through_flat(self):
		effective = "2026-10-06 10:00:00"
		encounter = self._encounter_with_observations(
			[("t-height", "8302-2", "165", "cm", effective)]
		)
		bundle = observations_by_encounter(encounter)

		self.assertEqual(bundle["total"], 1)
		resource = bundle["entry"][0]["resource"]
		self.assertEqual(resource["code"]["coding"][0]["code"], "8302-2")
		self.assertNotIn("component", resource)

	def test_mixed_bp_and_other_observations(self):
		effective = "2026-10-06 10:00:00"
		encounter = self._encounter_with_observations(
			[
				("t-systolic2", "8480-6", "130", "mm[Hg]", effective),
				("t-diastolic2", "8462-4", "85", "mm[Hg]", effective),
				("t-weight", "29463-7", "60", "kg", effective),
			]
		)
		bundle = observations_by_encounter(encounter)

		self.assertEqual(bundle["total"], 2)
		codes = {e["resource"]["code"]["coding"][0]["code"] for e in bundle["entry"]}
		self.assertEqual(codes, {"85354-9", "29463-7"})

	def test_unpaired_systolic_passes_through_rather_than_dropped(self):
		"""Only one half of a BP pair present (e.g. a partial/corrected
		reading) -- must not silently disappear."""
		effective = "2026-10-06 10:00:00"
		encounter = self._encounter_with_observations(
			[("t-systolic-only", "8480-6", "120", "mm[Hg]", effective)]
		)
		bundle = observations_by_encounter(encounter)

		self.assertEqual(bundle["total"], 1)
		resource = bundle["entry"][0]["resource"]
		self.assertEqual(resource["code"]["coding"][0]["code"], "8480-6")

	def test_no_observations_returns_empty_bundle(self):
		patient = self._make("Patient", {"full_name": "Empty Patient"})
		case = self._make("Case", {"patient": patient.name, "patient_name": patient.full_name})
		encounter = self._make("Encounter", {"case": case.name, "encounter_type": "Routine"})

		bundle = observations_by_encounter(encounter.name)
		self.assertEqual(bundle, {"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []})


if __name__ == "__main__":
	unittest.main()
