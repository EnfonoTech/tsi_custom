# apps/tsi_custom/tsi_custom/tsi/doctype/tsi_salary_allocation/tsi_salary_allocation.py
from frappe.model.document import Document


class TSISalaryAllocation(Document):
	"""One salary component's allocated amount on a Salary Structure Assignment.

	Rows are kept in step with the selected Salary Structure by
	``tsi_custom.salary_allocation.sync_allocations`` on the parent's validate.
	"""

	pass
