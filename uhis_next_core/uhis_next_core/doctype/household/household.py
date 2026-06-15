import frappe
from frappe.model.document import Document


class Household(Document):
	def before_save(self):
		if not self.display_title:
			addr = (self.address or "").strip()[:40]
			self.display_title = addr or (self.geography_node or "Household")
