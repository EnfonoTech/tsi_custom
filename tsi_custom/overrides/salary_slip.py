# apps/tsi_custom/tsi_custom/overrides/salary_slip.py
"""Publish an assignment's component allocations into the salary formula namespace."""

import frappe
from frappe.utils import flt
from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip

from tsi_custom.salary_allocation import get_allocations


class TSISalarySlip(SalarySlip):
	"""Adds ``alloc_<ABBR>`` variables to the formula namespace. Nothing else.

	Why a class override rather than a doc_event: the namespace is assembled in
	``get_data_for_eval``, which core calls from ``calculate_net_pay`` -- after
	the slip's dates and the employee's joining date are resolved, and before any
	formula runs. A ``before_validate`` hook is the only doc_event that fires
	early enough, but at that point ``joining_date`` is not yet set, so resolving
	the assignment there can pick a different one than core would for an employee
	who joined mid-period.

	Extending the method keeps core's own selection logic, and returns to it
	immediately.
	"""

	def get_data_for_eval(self):
		data, default_data = super().get_data_for_eval()

		assignment = getattr(self, "_salary_structure_assignment", None)
		if not assignment or not assignment.get("name"):
			return data, default_data

		for row in get_allocations(assignment["name"]):
			if not row.formula_variable:
				continue
			amount = flt(row.amount)
			# Both namespaces: default_data drives the un-prorated amounts core
			# uses for tax projection, and must see the same allocation.
			data[row.formula_variable] = amount
			default_data[row.formula_variable] = amount

		return data, default_data
