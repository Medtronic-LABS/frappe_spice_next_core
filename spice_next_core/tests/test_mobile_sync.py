"""
Integration tests for api/mobile_sync's NCD vertical slice (Phase 3 of the
"replace offline-service" migration plan) -- real DB, since the behaviour
under test (Concept-coded Observation writes, Encounter submission, village
resolution) only exists at the level of real rows.
"""

import unittest

import frappe

from spice_next_core.api import mobile_sync


class TestMobileSyncNcd(unittest.TestCase):

	def setUp(self):
		self._docs = []  # [(doctype, name), ...] in creation order, deleted in reverse

	def tearDown(self):
		for doctype, name in reversed(self._docs):
			if not frappe.db.exists(doctype, name):
				continue
			# Encounter is_submittable=1 -- process_ncd_assessment submits it,
			# so it must be cancelled before delete_doc will accept it.
			if frappe.get_meta(doctype).is_submittable and frappe.db.get_value(doctype, name, "docstatus") == 1:
				frappe.get_doc(doctype, name).cancel()
			frappe.delete_doc(doctype, name, ignore_permissions=True, delete_permanently=True, force=True)
		frappe.db.commit()

	def _make(self, doctype, fields, *, set_name=None):
		fields = dict(fields, doctype=doctype)
		if frappe.get_meta(doctype).has_field("client_uuid") and "client_uuid" not in fields:
			fields["client_uuid"] = frappe.generate_hash(length=16)
		doc = frappe.get_doc(fields)
		if set_name:
			doc.insert(ignore_permissions=True, set_name=set_name)
		else:
			doc.insert(ignore_permissions=True)
		self._docs.append((doctype, doc.name))
		return doc

	def _patient(self, **fields):
		return self._make("Patient", dict({"full_name": "Test Patient"}, **fields))

	def _ncd_payload(self, patient_name, *, village_id="0", extra_ncd=None, reference_id=None):
		ncd = {
			"bpLog": {
				"avgSystolic": 150,
				"avgDiastolic": 95,
				"bpLogDetails": [{"systolic": 150, "diastolic": 95}],
				"isRegularSmoker": False,
				"diagnosedBP": "Yes",
			},
			"glucoseLog": {
				"glucose": 7.2,
				"glucoseType": "fbs",
				"glucoseUnit": "mmol/L",
				"diagnosedGlucose": "No",
			},
			"biometric": {"height": 162.0, "weight": 58.0, "bmi": 22.1},
		}
		if extra_ncd:
			ncd.update(extra_ncd)
		return {
			"referenceId": reference_id if reference_id is not None else frappe.generate_hash(length=8),
			"assessmentType": "NCD",
			"assessmentDetails": {"ncd": ncd},
			"villageId": village_id,
			"assessmentDate": "2026-10-06T08:00:00.000Z",
			"patientStatus": "Referred",
			"referredReasons": "High BP",
			"encounter": {
				"householdId": "HH-test",
				"memberId": patient_name,
				"referred": True,
				"patientId": patient_name,
				"latitude": 23.7,
				"longitude": 90.4,
				"startTime": "2026-10-06 08:00:00",
				"endTime": "2026-10-06 08:30:00",
				"customStatus": ["Referred"],
			},
		}

	# ── get_or_create_case ───────────────────────────────────────────────────

	def test_get_or_create_case_creates_once_then_reuses(self):
		patient = self._patient()
		case_name = mobile_sync.get_or_create_case(patient.name)
		self._docs.append(("Case", case_name))
		self.assertTrue(frappe.db.exists("Case", case_name))
		self.assertEqual(mobile_sync.get_or_create_case(patient.name), case_name)

	# ── process_ncd_assessment ───────────────────────────────────────────────

	def test_process_ncd_assessment_creates_submitted_encounter(self):
		patient = self._patient()
		payload = self._ncd_payload(patient.name)

		encounter_name = mobile_sync.process_ncd_assessment(payload, "device-1")
		self._docs.append(("Mobile Encounter Context", encounter_name))
		self._docs.append(("Encounter", encounter_name))

		encounter = frappe.get_doc("Encounter", encounter_name)
		self.assertEqual(encounter.docstatus, 1)
		self.assertEqual(encounter.patient, patient.name)

		case_name = frappe.db.get_value("Case", {"patient": patient.name}, "name")
		self._docs.append(("Case", case_name))
		self.assertEqual(encounter.case, case_name)

	def test_process_ncd_assessment_writes_bp_glucose_and_biometric_observations(self):
		patient = self._patient()
		payload = self._ncd_payload(patient.name)

		encounter_name = mobile_sync.process_ncd_assessment(payload, "device-1")
		self._docs.append(("Mobile Encounter Context", encounter_name))
		self._docs.append(("Encounter", encounter_name))
		case_name = frappe.db.get_value("Case", {"patient": patient.name}, "name")
		self._docs.append(("Case", case_name))

		obs = frappe.get_all(
			"Observation", filters={"encounter": encounter_name}, fields=["concept", "value", "unit"]
		)
		for row in obs:
			self._docs.append(("Observation", frappe.get_value(
				"Observation", {"encounter": encounter_name, "concept": row.concept}, "name"
			)))
		by_concept = {r.concept: r.value for r in obs}

		self.assertEqual(by_concept["LOINC|8480-6"], "150")
		self.assertEqual(by_concept["LOINC|8462-4"], "95")
		self.assertEqual(by_concept["LOINC|2339-0"], "7.2")
		self.assertEqual(by_concept["SNOMED|87612001"], "fbs")
		self.assertEqual(by_concept["LOINC|8302-2"], "162.0")
		self.assertEqual(by_concept["LOINC|29463-7"], "58.0")
		self.assertEqual(by_concept["SNOMED|38341003"], "Yes")
		self.assertEqual(by_concept["SNOMED|73211009"], "No")
		self.assertEqual(by_concept["SNOMED|77176002"], "No")

	def test_process_ncd_assessment_writes_mobile_encounter_context(self):
		patient = self._patient()
		payload = self._ncd_payload(patient.name)

		encounter_name = mobile_sync.process_ncd_assessment(payload, "device-1")
		self._docs.append(("Mobile Encounter Context", encounter_name))
		self._docs.append(("Encounter", encounter_name))
		case_name = frappe.db.get_value("Case", {"patient": patient.name}, "name")
		self._docs.append(("Case", case_name))

		ctx = frappe.get_doc("Mobile Encounter Context", encounter_name)
		self.assertEqual(ctx.service_provided, "NCD")
		self.assertEqual(ctx.patient_status, "Referred")
		self.assertEqual(ctx.referred_reasons, "High BP")
		self.assertTrue(ctx.referred)

	def test_process_ncd_assessment_unknown_patient_raises(self):
		payload = self._ncd_payload("Patient-does-not-exist")
		with self.assertRaises(frappe.ValidationError):
			mobile_sync.process_ncd_assessment(payload, "device-1")

	def test_process_ncd_assessment_omits_fields_the_mobile_app_never_sends(self):
		"""confirmDiagnosis/stroke/heartAttack/kidneyDisease/copd have no
		source in uhis_lf_mobile's real _toNcd() push payload -- must not be
		fabricated as Observations."""
		patient = self._patient()
		payload = self._ncd_payload(patient.name)

		encounter_name = mobile_sync.process_ncd_assessment(payload, "device-1")
		self._docs.append(("Mobile Encounter Context", encounter_name))
		self._docs.append(("Encounter", encounter_name))
		case_name = frappe.db.get_value("Case", {"patient": patient.name}, "name")
		self._docs.append(("Case", case_name))

		obs = frappe.get_all("Observation", filters={"encounter": encounter_name}, fields=["concept"])
		for row in obs:
			self._docs.append(
				("Observation", frappe.get_value("Observation", {"encounter": encounter_name, "concept": row.concept}, "name"))
			)
		concepts = {r.concept for r in obs}
		self.assertNotIn("SNOMED|230690007", concepts)  # Stroke
		self.assertNotIn("SNOMED|22298006", concepts)  # Myocardial infarction
		self.assertNotIn("SNOMED|90708001", concepts)  # Kidney disease

	# ── village resolution ───────────────────────────────────────────────────

	def test_resolve_village_treats_zero_and_blank_as_unmapped(self):
		self.assertIsNone(mobile_sync._resolve_village("0"))
		self.assertIsNone(mobile_sync._resolve_village(""))
		self.assertIsNone(mobile_sync._resolve_village(None))

	def test_resolve_village_maps_numeric_string(self):
		node = self._make(
			"Geography Node",
			{"label": "Test Village", "legacy_village_id": 999123},
			set_name="Test Village 999123",
		)
		self.assertEqual(mobile_sync._resolve_village("999123"), node.name)
		self.assertEqual(mobile_sync._resolve_village(999123), node.name)

	# ── fetch_households_and_members ────────────────────────────────────────

	def test_fetch_households_and_members_scoped_to_requested_villages(self):
		node = self._make(
			"Geography Node",
			{"label": "Fetch Village", "legacy_village_id": 999124},
			set_name="Fetch Village 999124",
		)
		household = self._make(
			"Household",
			{"display_title": "Test HH", "address": "Somewhere", "geography_node": node.name},
		)
		patient = self._patient(gender="Female", phone="01700000000")
		household.append("members", {"patient": patient.name, "is_head": 1})
		household.save(ignore_permissions=True)

		households, members = mobile_sync.fetch_households_and_members(["999124"])

		self.assertEqual([h["id"] for h in households], [household.name])
		self.assertEqual(households[0]["villageId"], 999124)
		self.assertEqual(len(members), 1)
		self.assertEqual(members[0]["householdId"], household.name)
		self.assertEqual(members[0]["id"], patient.name)
		self.assertTrue(members[0]["isHouseholdHead"])

	def test_fetch_households_and_members_empty_for_unmapped_village(self):
		households, members = mobile_sync.fetch_households_and_members(["0"])
		self.assertEqual(households, [])
		self.assertEqual(members, [])

	# ── member_assessment_history ────────────────────────────────────────────

	def test_member_assessment_history_returns_ncd_observations(self):
		patient = self._patient()
		payload = self._ncd_payload(patient.name)
		encounter_name = mobile_sync.process_ncd_assessment(payload, "device-1")
		self._docs.append(("Mobile Encounter Context", encounter_name))
		self._docs.append(("Encounter", encounter_name))
		case_name = frappe.db.get_value("Case", {"patient": patient.name}, "name")
		self._docs.append(("Case", case_name))
		obs = frappe.get_all("Observation", filters={"encounter": encounter_name}, fields=["concept"])
		for row in obs:
			self._docs.append(
				("Observation", frappe.get_value("Observation", {"encounter": encounter_name, "concept": row.concept}, "name"))
			)

		items = mobile_sync.member_assessment_history([])
		matching = [i for i in items if i["encounterId"] == encounter_name]
		self.assertEqual(len(matching), 1)
		item = matching[0]
		self.assertEqual(item["householdMemberId"], patient.name)
		self.assertEqual(item["serviceProvided"], "NCD")
		self.assertEqual(item["referralStatus"], "Referred")
		self.assertEqual(item["referralReason"], "High BP")
		self.assertTrue(item["isLatestVisit"])
		self.assertEqual(item["customStatus"], ["Referred"])
		self.assertEqual(item["observations"]["bp"], "150/95")
		self.assertEqual(item["observations"]["bg"], "7.2")
		self.assertEqual(item["observations"]["bgType"], "fbs")
		self.assertEqual(item["observations"]["height"], "162.0")
		self.assertEqual(item["observations"]["weight"], "58.0")
		self.assertNotIn("confirmDiagnosis", item["observations"])


class TestMobileSyncPregnancy(unittest.TestCase):
	"""Phase 4 of the migration plan: ANC, PWPROFILE, PNC_MOTHER,
	PNC_NEONATE, PREGNANCYOUTCOME -- these interrelate via the wire's
	pregnancyEpisodeId, so (unlike NCD's one-Case-per-Patient model) the
	episode IS the Case boundary: every visit of the SAME pregnancy must
	land on the SAME Case, and a NEW pregnancy for the same Patient must get
	a different one."""

	def setUp(self):
		self._docs = []

	def tearDown(self):
		for doctype, name in reversed(self._docs):
			if not frappe.db.exists(doctype, name):
				continue
			if frappe.get_meta(doctype).is_submittable and frappe.db.get_value(doctype, name, "docstatus") == 1:
				frappe.get_doc(doctype, name).cancel()
			frappe.delete_doc(doctype, name, ignore_permissions=True, delete_permanently=True, force=True)
		frappe.db.commit()

	def _make(self, doctype, fields, *, set_name=None):
		fields = dict(fields, doctype=doctype)
		if frappe.get_meta(doctype).has_field("client_uuid") and "client_uuid" not in fields:
			fields["client_uuid"] = frappe.generate_hash(length=16)
		doc = frappe.get_doc(fields)
		if set_name:
			doc.insert(ignore_permissions=True, set_name=set_name)
		else:
			doc.insert(ignore_permissions=True)
		self._docs.append((doctype, doc.name))
		return doc

	def _patient(self, **fields):
		return self._make("Patient", dict({"full_name": "Pregnancy Test Patient"}, **fields))

	def _track_side_effects(self, patient_name, encounter_name):
		self._docs.append(("Mobile Encounter Context", encounter_name))
		self._docs.append(("Encounter", encounter_name))
		for obs_name in frappe.get_all("Observation", filters={"encounter": encounter_name}, pluck="name"):
			self._docs.append(("Observation", obs_name))

	def _track_case(self, pregnancy_episode_id):
		case_name = f"case-preg-{pregnancy_episode_id}"
		if frappe.db.exists("Case", case_name) and ("Case", case_name) not in self._docs:
			self._docs.append(("Case", case_name))

	def _payload(self, assessment_type, patient_name, pregnancy_episode_id, details, *, reference_id=None, extra_encounter=None):
		encounter = {
			"memberId": patient_name,
			"pregnancyEpisodeId": pregnancy_episode_id,
			"startTime": "2026-10-06 09:00:00",
			"endTime": "2026-10-06 09:30:00",
		}
		if extra_encounter:
			encounter.update(extra_encounter)
		return {
			"referenceId": reference_id if reference_id is not None else frappe.generate_hash(length=8),
			"assessmentType": assessment_type,
			"assessmentDetails": details,
			"villageId": "0",
			"patientStatus": "Recovered",
			"encounter": encounter,
		}

	# ── episode Case lifecycle ───────────────────────────────────────────────

	def test_get_or_create_pregnancy_case_requires_episode_id(self):
		patient = self._patient()
		with self.assertRaises(frappe.ValidationError):
			mobile_sync.get_or_create_pregnancy_case(patient.name, None)

	def test_same_episode_id_reuses_case_across_visits(self):
		patient = self._patient()
		episode_id = frappe.generate_hash(length=12)
		first = mobile_sync.get_or_create_pregnancy_case(patient.name, episode_id)
		self._track_case(episode_id)
		second = mobile_sync.get_or_create_pregnancy_case(patient.name, episode_id)
		self.assertEqual(first, second)

	def test_different_episode_ids_get_different_cases(self):
		patient = self._patient()
		ep1 = frappe.generate_hash(length=12)
		ep2 = frappe.generate_hash(length=12)
		case1 = mobile_sync.get_or_create_pregnancy_case(patient.name, ep1)
		self._track_case(ep1)
		case2 = mobile_sync.get_or_create_pregnancy_case(patient.name, ep2)
		self._track_case(ep2)
		self.assertNotEqual(case1, case2)

	# ── ANC ──────────────────────────────────────────────────────────────────

	def test_anc_assessment_writes_bp_and_vaccination_observations(self):
		patient = self._patient()
		episode_id = frappe.generate_hash(length=12)
		details = {
			"anc": {
				"visitNo": 2,
				"medicalHistoryPhysicalExamination": {
					"systolic": "139",
					"diastolic": "88",
					"weight": 60.0,
					"fundalHeight": 28.0,
					"hemoglobin": 11.2,
				},
				"pointOfCareInvestigations": {
					"urinaryAlbumin": "Nil",
					"bloodSugarFasting": 5.2,
					"bloodSugarFastingUnit": "mmol/L",
					"bloodSugar": "fasting",
				},
				"vaccinationAndSupplements": {
					"ttTdCompleted": "Yes",
					"ifaTotalConsumed": 30,
				},
				"ancServicesBirthPreparedness": {
					"ultrasound": "Yes",
				},
			}
		}
		payload = self._payload(
			"ANC", patient.name, episode_id, details, extra_encounter={"visitNumber": 2}
		)
		encounter_name = mobile_sync.process_pregnancy_assessment(payload, "device-preg")
		self._track_side_effects(patient.name, encounter_name)
		self._track_case(episode_id)

		obs = frappe.get_all(
			"Observation", filters={"encounter": encounter_name}, fields=["concept", "value"]
		)
		by_concept = {r.concept: r.value for r in obs}
		self.assertEqual(by_concept["LOINC|8480-6"], "139")
		self.assertEqual(by_concept["LOINC|8462-4"], "88")
		self.assertEqual(by_concept["LOINC|718-7"], "11.2")
		self.assertEqual(by_concept["SNOMED|364605001"], "28.0")
		self.assertEqual(by_concept["LOINC|2339-0"], "5.2")
		self.assertEqual(by_concept["SNOMED|56844000"], "Yes")
		self.assertEqual(by_concept["SNOMED|767399006"], "30")
		self.assertEqual(by_concept["SNOMED|359659005"], "Yes")

		ctx = frappe.get_doc("Mobile Encounter Context", encounter_name)
		self.assertEqual(ctx.pregnancy_episode_id, episode_id)
		self.assertEqual(ctx.visit_number, 2)
		self.assertEqual(ctx.service_provided, "ANC")

	def test_two_anc_visits_same_episode_share_one_case(self):
		patient = self._patient()
		episode_id = frappe.generate_hash(length=12)
		details = {"anc": {"medicalHistoryPhysicalExamination": {"weight": 58.0}}}

		first_payload = self._payload("ANC", patient.name, episode_id, details)
		first_encounter = mobile_sync.process_pregnancy_assessment(first_payload, "device-preg")
		self._track_side_effects(patient.name, first_encounter)

		second_payload = self._payload("ANC", patient.name, episode_id, details)
		second_encounter = mobile_sync.process_pregnancy_assessment(second_payload, "device-preg")
		self._track_side_effects(patient.name, second_encounter)
		self._track_case(episode_id)

		enc1 = frappe.get_doc("Encounter", first_encounter)
		enc2 = frappe.get_doc("Encounter", second_encounter)
		self.assertEqual(enc1.case, enc2.case)
		self.assertNotEqual(first_encounter, second_encounter)

	# ── PWPROFILE ────────────────────────────────────────────────────────────

	def test_pwprofile_assessment_writes_lmp_and_gravida(self):
		patient = self._patient()
		episode_id = frappe.generate_hash(length=12)
		details = {
			"pwProfile": {
				"pregnancyDetailsAndHistory": {
					"lmp": "2026-06-01T00:00:00+00:00",
					"gravida": 2.0,
					"parity": 1.0,
					"livingChildren": 1.0,
					"pregnancyTest": "Positive",
				}
			}
		}
		payload = self._payload("PWPROFILE", patient.name, episode_id, details)
		encounter_name = mobile_sync.process_pregnancy_assessment(payload, "device-preg")
		self._track_side_effects(patient.name, encounter_name)
		self._track_case(episode_id)

		obs = frappe.get_all(
			"Observation", filters={"encounter": encounter_name}, fields=["concept", "value"]
		)
		by_concept = {r.concept: r.value for r in obs}
		self.assertEqual(by_concept["LOINC|8665-2"], "2026-06-01T00:00:00+00:00")
		self.assertEqual(by_concept["LOINC|11996-6"], "2.0")
		self.assertEqual(by_concept["LOINC|11977-6"], "1.0")
		self.assertEqual(by_concept["SNOMED|224118004"], "1.0")
		self.assertEqual(by_concept["SNOMED|250416002"], "Positive")

	# ── PNC_MOTHER ───────────────────────────────────────────────────────────

	def test_pnc_mother_assessment_writes_maternal_and_pregnancy_history(self):
		patient = self._patient()
		episode_id = frappe.generate_hash(length=12)
		details = {
			"pncMother": {
				"maternalHealthAssessment": {
					"systolic": "120",
					"diastolic": "80",
					"hemoglobin": 10.5,
					"eclampsia": "No",
					"htnPatient": "No",
				},
				"pregnancyHistory": {"parity": 2, "gravida": 3},
				"visitNo": 1,
			}
		}
		payload = self._payload(
			"PNC_MOTHER", patient.name, episode_id, details, extra_encounter={"visitNumber": 1}
		)
		encounter_name = mobile_sync.process_pregnancy_assessment(payload, "device-preg")
		self._track_side_effects(patient.name, encounter_name)
		self._track_case(episode_id)

		obs = frappe.get_all(
			"Observation", filters={"encounter": encounter_name}, fields=["concept", "value"]
		)
		by_concept = {r.concept: r.value for r in obs}
		self.assertEqual(by_concept["LOINC|8480-6"], "120")
		self.assertEqual(by_concept["LOINC|8462-4"], "80")
		self.assertEqual(by_concept["LOINC|718-7"], "10.5")
		self.assertEqual(by_concept["LOINC|11977-6"], "2")
		self.assertEqual(by_concept["LOINC|11996-6"], "3")
		# "No" is itself a captured clinical fact (a true negative), not an
		# absent field -- it must be recorded, not skipped as falsy.
		self.assertEqual(by_concept["SNOMED|15938005"], "No")
		self.assertEqual(by_concept["SNOMED|38341003"], "No")

	# ── PNC_NEONATE ──────────────────────────────────────────────────────────

	def test_pnc_neonatal_assessment_writes_child_weight_and_alive_flag(self):
		# PNC_NEONATE is NOT pregnancy-episode-linked on the real client
		# (uhis_lf_mobile's pregnancy_episode_dao.dart: "Android does NOT
		# link PNC_NEONATE/PNC_CHILD to a pregnancy episode") -- its wire
		# payload never carries a pregnancyEpisodeId, so this uses the
		# one-Case-per-Patient model instead of the episode-keyed Case.
		patient = self._patient()
		details = {"pncNeonatal": {"childWeight": 3.2, "childHeight": 50.0, "isChildAlive": "Yes"}, "cbs": {}}
		payload = self._payload("PNC_NEONATE", patient.name, None, details)
		encounter_name = mobile_sync.process_pregnancy_assessment(payload, "device-preg")
		self._track_side_effects(patient.name, encounter_name)
		case_name = frappe.db.get_value("Case", {"patient": patient.name}, "name")
		if case_name:
			self._docs.append(("Case", case_name))

		ctx = frappe.get_doc("Mobile Encounter Context", encounter_name)
		self.assertIsNone(ctx.pregnancy_episode_id)

		obs = frappe.get_all(
			"Observation", filters={"encounter": encounter_name}, fields=["concept", "value"]
		)
		by_concept = {r.concept: r.value for r in obs}
		self.assertEqual(by_concept["LOINC|29463-7"], "3.2")
		self.assertEqual(by_concept["LOINC|8302-2"], "50.0")
		self.assertEqual(by_concept["SNOMED|281050002"], "Yes")

	def test_pnc_neonatal_reuses_patient_level_case_not_episode_case(self):
		"""Confirms PNC_NEONATE shares get_or_create_case's one-Case-per-
		Patient model -- a second PNC_NEONATE visit for the same patient must
		land on the SAME Case, with no episode id involved at all."""
		patient = self._patient()
		details = {"pncNeonatal": {"childWeight": 3.0}, "cbs": {}}

		first_payload = self._payload("PNC_NEONATE", patient.name, None, details)
		first_encounter = mobile_sync.process_pregnancy_assessment(first_payload, "device-preg")
		self._track_side_effects(patient.name, first_encounter)

		second_payload = self._payload("PNC_NEONATE", patient.name, None, details)
		second_encounter = mobile_sync.process_pregnancy_assessment(second_payload, "device-preg")
		self._track_side_effects(patient.name, second_encounter)
		case_name = frappe.db.get_value("Case", {"patient": patient.name}, "name")
		if case_name:
			self._docs.append(("Case", case_name))

		enc1 = frappe.get_doc("Encounter", first_encounter)
		enc2 = frappe.get_doc("Encounter", second_encounter)
		self.assertEqual(enc1.case, enc2.case)
		self.assertEqual(enc1.case, case_name)

	# ── PREGNANCYOUTCOME ─────────────────────────────────────────────────────

	def test_pregnancy_outcome_writes_delivery_and_newborn_observations(self):
		patient = self._patient()
		episode_id = frappe.generate_hash(length=12)
		details = {
			"pregnancyOutcome": {
				"deliveryOutcomes": {
					"modeOfDelivery": "Normal",
					"placeOfDelivery": "Facility",
					"birthAttendant": "Yes",
					"stillbirthNumbers": 0.0,
				},
				"newbornDetails": [{"isBabyAlive": "Yes", "sex": "Male"}],
			}
		}
		payload = self._payload("PREGNANCYOUTCOME", patient.name, episode_id, details)
		encounter_name = mobile_sync.process_pregnancy_assessment(payload, "device-preg")
		self._track_side_effects(patient.name, encounter_name)
		self._track_case(episode_id)

		obs = frappe.get_all(
			"Observation", filters={"encounter": encounter_name}, fields=["concept", "value"]
		)
		by_concept = {r.concept: r.value for r in obs}
		self.assertEqual(by_concept["SNOMED|289258004"], "Normal")
		self.assertEqual(by_concept["SNOMED|3950001"], "Facility")
		self.assertEqual(by_concept["SNOMED|408826000"], "Yes")
		self.assertEqual(by_concept["SNOMED|237364002"], "0.0")
		self.assertEqual(by_concept["SNOMED|281050002"], "Yes")

	# ── unwrapping ───────────────────────────────────────────────────────────

	def test_unwrap_pregnancy_details_handles_every_wrapped_type(self):
		self.assertEqual(
			mobile_sync._unwrap_pregnancy_details("ANC", {"anc": {"a": 1}}), {"a": 1}
		)
		self.assertEqual(
			mobile_sync._unwrap_pregnancy_details(
				"PWPROFILE", {"pwProfile": {"pregnancyDetailsAndHistory": {"b": 2}}}
			),
			{"b": 2},
		)
		self.assertEqual(
			mobile_sync._unwrap_pregnancy_details("PNC_MOTHER", {"pncMother": {"c": 3}}), {"c": 3}
		)
		self.assertEqual(
			mobile_sync._unwrap_pregnancy_details("PNC_NEONATE", {"pncNeonatal": {"d": 4}, "cbs": {}}),
			{"d": 4},
		)
		self.assertEqual(
			mobile_sync._unwrap_pregnancy_details(
				"PREGNANCYOUTCOME", {"pregnancyOutcome": {"e": 5}}
			),
			{"e": 5},
		)

	# ── member_assessment_history ────────────────────────────────────────────

	def test_member_assessment_history_includes_pregnancy_episode_id_and_anc_visit_number(self):
		patient = self._patient()
		episode_id = frappe.generate_hash(length=12)
		details = {"anc": {"medicalHistoryPhysicalExamination": {"hemoglobin": 12.0}}}
		payload = self._payload(
			"ANC", patient.name, episode_id, details, extra_encounter={"visitNumber": 3}
		)
		encounter_name = mobile_sync.process_pregnancy_assessment(payload, "device-preg")
		self._track_side_effects(patient.name, encounter_name)
		self._track_case(episode_id)

		items = mobile_sync.member_assessment_history([])
		matching = [i for i in items if i["encounterId"] == encounter_name]
		self.assertEqual(len(matching), 1)
		item = matching[0]
		self.assertEqual(item["serviceProvided"], "ANC")
		self.assertEqual(item["observations"]["hemoglobin"], "12.0")
		self.assertEqual(item["observations"]["pregnancyEpisodeId"], episode_id)
		self.assertEqual(item["observations"]["ancVisitNumber"], 3)

	# ── fetch_pregnancy_infos_and_treatment_details ─────────────────────────

	def test_fetch_pregnancy_infos_and_treatment_details_scoped_to_village(self):
		node = self._make(
			"Geography Node",
			{"label": "Pregnancy Fetch Village", "legacy_village_id": 999125},
			set_name="Pregnancy Fetch Village 999125",
		)
		household = self._make(
			"Household",
			{"display_title": "Pregnancy Test HH", "geography_node": node.name},
		)
		patient = self._patient()
		household.append("members", {"patient": patient.name, "is_head": 1})
		household.save(ignore_permissions=True)

		episode_id = frappe.generate_hash(length=12)
		details = {"pwProfile": {"pregnancyDetailsAndHistory": {"gravida": 1.0, "parity": 0.0}}}
		payload = self._payload("PWPROFILE", patient.name, episode_id, details)
		encounter_name = mobile_sync.process_pregnancy_assessment(payload, "device-preg")
		self._track_side_effects(patient.name, encounter_name)
		self._track_case(episode_id)

		pregnancy_infos, treatment_details = mobile_sync.fetch_pregnancy_infos_and_treatment_details(
			["999125"]
		)
		self.assertEqual(len(pregnancy_infos), 1)
		self.assertEqual(pregnancy_infos[0]["householdMemberId"], patient.name)
		self.assertEqual(pregnancy_infos[0]["gravida"], "1.0")
		self.assertEqual(treatment_details, [{"patientId": patient.name}])


if __name__ == "__main__":
	unittest.main()
