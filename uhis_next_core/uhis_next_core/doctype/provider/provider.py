import frappe
from frappe.model.document import Document

_ROLE_MAP = {"SK": "SK", "SS": "SS"}


class Provider(Document):
	def on_update(self):
		if not self.user:
			return
		target = _ROLE_MAP.get(self.role_type)
		user_doc = frappe.get_doc("User", self.user)
		user_doc.roles = [r for r in user_doc.roles if r.role not in ("SK", "SS")]
		if target:
			user_doc.append("roles", {"role": target})
		user_doc.save(ignore_permissions=True)
