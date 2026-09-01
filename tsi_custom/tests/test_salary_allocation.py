# apps/tsi_custom/tsi_custom/tests/test_salary_allocation.py
"""Tests for effective-dated salary component allocation.

The contract that matters is the last one: a Salary Slip must read the
component amount that was in force for *its own* period, not the newest value.
Everything else guards the paths that could break it.

Fixtures are built inline rather than through hrms' test helpers, whose
signatures move between versions.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, get_first_day, get_last_day

from tsi_custom.salary_allocation import (
	TOTAL_FIELD,
	change_component_values,
	get_component_history,
)

COMPANY = "_Test Company"
CURRENCY = "INR"
STRUCTURE = "_Test TSI Allocation Structure"

# component label -> (abbr, SSA fieldname used as the formula)
COMPONENTS = {
	"_Test TSI Basic": ("TTB", "base"),
	"_Test TSI HRA": ("TTH", "custom_tsi_hra_amount"),
	"_Test TSI Transport": ("TTT", "custom_tsi_transport_amount"),
}


class TestSalaryAllocation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.require_custom_fields()
		cls.employee = make_employee("tsi_allocation@example.com")
		make_salary_structure()

	@classmethod
	def require_custom_fields(cls):
		"""Skip loudly rather than fail obscurely when fixtures are not migrated."""
		meta = frappe.get_meta("Salary Structure Assignment")
		missing = [f for _, (_, f) in COMPONENTS.items() if not meta.has_field(f)]
		if missing:
			raise unittest.SkipTest(f"tsi_custom Custom Fields not migrated on this site: {missing}")

	def setUp(self):
		frappe.db.delete("Salary Slip", {"employee": self.employee})
		frappe.db.delete("Salary Structure Assignment", {"employee": self.employee})

	# -- The contract ---------------------------------------------------------

	def test_slip_uses_the_amount_in_force_for_its_own_period(self):
		"""A raise dated 1 Apr must not reach the March slip."""
		make_assignment(self.employee, "2026-01-01", hra=1000)

		change_component_values(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={"custom_tsi_hra_amount": 2000},
		)

		self.assertEqual(hra_on_slip(self.employee, "2026-03-01"), 1000)
		self.assertEqual(hra_on_slip(self.employee, "2026-04-01"), 2000)

	def test_earlier_slip_still_reproduces_its_original_figure(self):
		"""Regenerating an old slip after a raise must not repay history."""
		make_assignment(self.employee, "2026-01-01", hra=1000)
		before = hra_on_slip(self.employee, "2026-02-01")

		change_component_values(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={"custom_tsi_hra_amount": 2000},
		)

		frappe.db.delete("Salary Slip", {"employee": self.employee})
		self.assertEqual(hra_on_slip(self.employee, "2026-02-01"), before)

	# -- Assignment chain -----------------------------------------------------

	def test_change_creates_a_new_dated_assignment_and_leaves_the_old_one_alone(self):
		original = make_assignment(self.employee, "2026-01-01", hra=1000, transport=500)

		result = change_component_values(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={"custom_tsi_hra_amount": 2000},
		)

		created = frappe.get_doc("Salary Structure Assignment", result["name"])
		self.assertEqual(created.docstatus, 1)
		self.assertEqual(str(created.from_date), "2026-04-01")
		self.assertEqual(flt(created.custom_tsi_hra_amount), 2000)
		# untouched components carry forward
		self.assertEqual(flt(created.custom_tsi_transport_amount), 500)
		self.assertEqual(flt(created.base), flt(original.base))
		# and the payable account, without which Payroll Entry drops the employee
		self.assertEqual(created.payroll_payable_account, original.payroll_payable_account)

		original.reload()
		self.assertEqual(flt(original.custom_tsi_hra_amount), 1000)

	def test_rejects_a_second_assignment_on_the_same_date(self):
		make_assignment(self.employee, "2026-01-01", hra=1000)

		with self.assertRaises(frappe.ValidationError):
			change_component_values(
				employee=self.employee,
				effective_from="2026-01-01",
				changes={"custom_tsi_hra_amount": 2000},
			)

	def test_rejects_back_dating_behind_a_later_assignment(self):
		"""Inserting behind the newest assignment would not change any payroll."""
		make_assignment(self.employee, "2026-01-01", hra=1000)
		change_component_values(
			employee=self.employee,
			effective_from="2026-06-01",
			changes={"custom_tsi_hra_amount": 3000},
		)

		with self.assertRaises(frappe.ValidationError):
			change_component_values(
				employee=self.employee,
				effective_from="2026-03-01",
				changes={"custom_tsi_hra_amount": 2000},
			)

	def test_back_dating_over_paid_periods_is_allowed_but_reported(self):
		"""A retro increment is routine, so it must succeed -- and say what it hit.

		The submitted slip keeps its own figures; the operator is told arrears
		are not created automatically.
		"""
		make_assignment(self.employee, "2026-01-01", hra=1000)
		slip = submit_slip(self.employee, "2026-02-01")
		paid_before = frappe.db.get_value("Salary Slip", slip, "gross_pay")

		result = change_component_values(
			employee=self.employee,
			effective_from="2026-02-01",
			changes={"custom_tsi_hra_amount": 2000},
		)

		self.assertTrue(result["name"])
		self.assertIsNotNone(result["already_paid"])
		self.assertEqual(result["already_paid"]["first_slip"], slip)

		# the submitted slip is untouched
		self.assertEqual(frappe.db.get_value("Salary Slip", slip, "gross_pay"), paid_before)

	def test_rejects_a_change_launched_from_a_superseded_assignment(self):
		"""The dialog's Current Value must be the one the server diffs against."""
		first = make_assignment(self.employee, "2026-01-01", hra=1000)
		change_component_values(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={"custom_tsi_hra_amount": 2000},
		)

		with self.assertRaises(frappe.ValidationError):
			change_component_values(
				employee=self.employee,
				effective_from="2026-07-01",
				changes={"custom_tsi_hra_amount": 3000},
				source_assignment=first.name,
			)

	def test_tax_opening_balances_are_not_carried_forward(self):
		"""Copying them into a later period re-arms a stale opening in tax calc."""
		assignment = make_assignment(self.employee, "2026-01-01", hra=1000)
		if not assignment.meta.has_field("taxable_earnings_till_date"):
			self.skipTest("payroll opening fields not present on this hrms version")

		frappe.db.set_value(
			"Salary Structure Assignment",
			assignment.name,
			{"taxable_earnings_till_date": 600000, "tax_deducted_till_date": 45500},
			update_modified=False,
		)

		result = change_component_values(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={"custom_tsi_hra_amount": 2000},
		)
		created = frappe.get_doc("Salary Structure Assignment", result["name"])
		self.assertEqual(flt(created.taxable_earnings_till_date), 0)
		self.assertEqual(flt(created.tax_deducted_till_date), 0)

	def test_rejects_a_no_op_change(self):
		make_assignment(self.employee, "2026-01-01", hra=1000)

		with self.assertRaises(frappe.ValidationError):
			change_component_values(
				employee=self.employee,
				effective_from="2026-04-01",
				changes={"custom_tsi_hra_amount": 1000},
			)

	# -- Guards ---------------------------------------------------------------

	def test_rejects_a_fieldname_that_is_not_an_allocatable_component(self):
		"""The fieldname reaches the DB as a column name, so it is not trusted."""
		make_assignment(self.employee, "2026-01-01", hra=1000)

		for bad in ("income_tax_slab", "name", "1=1"):
			with self.subTest(fieldname=bad), self.assertRaises(frappe.ValidationError):
				change_component_values(
					employee=self.employee,
					effective_from="2026-04-01",
					changes={bad: 1},
				)

		with self.assertRaises(frappe.ValidationError):
			get_component_history(self.employee, "income_tax_slab")

	# -- Derived total --------------------------------------------------------

	def test_total_salary_is_the_sum_of_its_components(self):
		assignment = make_assignment(self.employee, "2026-01-01", hra=1000, transport=500, base=4000)
		self.assertEqual(flt(assignment.get(TOTAL_FIELD)), 5500)

		result = change_component_values(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={"custom_tsi_hra_amount": 2000},
		)
		created = frappe.get_doc("Salary Structure Assignment", result["name"])
		self.assertEqual(flt(created.get(TOTAL_FIELD)), 6500)

	# -- History --------------------------------------------------------------

	def test_history_windows_and_marks_the_current_assignment(self):
		make_assignment(self.employee, "2026-01-01", hra=1000)
		change_component_values(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={"custom_tsi_hra_amount": 2000},
		)

		history = get_component_history(self.employee, "custom_tsi_hra_amount")
		self.assertEqual(len(history), 2)

		current, previous = history[0], history[1]
		self.assertEqual(flt(current["value"]), 2000)
		self.assertTrue(current["is_current"])
		self.assertIsNone(current["effective_until"])

		self.assertEqual(flt(previous["value"]), 1000)
		self.assertFalse(previous["is_current"])
		self.assertEqual(str(previous["effective_until"]), "2026-03-31")


# -- Fixtures ------------------------------------------------------------------


def make_employee(user_id: str) -> str:
	existing = frappe.db.get_value("Employee", {"user_id": user_id, "company": COMPANY})
	if existing:
		return existing

	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": "TSI Allocation",
			"company": COMPANY,
			"gender": "Female",
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"status": "Active",
			"user_id": user_id,
		}
	).insert()
	return employee.name


def make_salary_component(label: str, abbr: str, formula: str) -> None:
	if frappe.db.exists("Salary Component", label):
		return

	frappe.get_doc(
		{
			"doctype": "Salary Component",
			"salary_component": label,
			"salary_component_abbr": abbr,
			"type": "Earning",
			# Keep the assertion on the raw formula result: proration by
			# payment days is core behaviour and not what these tests cover.
			"depends_on_payment_days": 0,
			"amount_based_on_formula": 1,
			"formula": formula,
		}
	).insert()


def make_salary_structure() -> None:
	for label, (abbr, formula) in COMPONENTS.items():
		make_salary_component(label, abbr, formula)

	if frappe.db.exists("Salary Structure", STRUCTURE):
		return

	structure = frappe.get_doc(
		{
			"doctype": "Salary Structure",
			"name": STRUCTURE,
			"company": COMPANY,
			"currency": CURRENCY,
			"payroll_frequency": "Monthly",
			"earnings": [
				{
					"salary_component": label,
					"abbr": abbr,
					"amount_based_on_formula": 1,
					# The whole point: the row reads the SSA field by name.
					"formula": formula,
					"depends_on_payment_days": 0,
				}
				for label, (abbr, formula) in COMPONENTS.items()
			],
		}
	)
	structure.insert()
	structure.submit()


def make_assignment(employee: str, from_date: str, base: float = 4000, hra: float = 0, transport: float = 0):
	assignment = frappe.get_doc(
		{
			"doctype": "Salary Structure Assignment",
			"employee": employee,
			"salary_structure": STRUCTURE,
			"company": COMPANY,
			"currency": CURRENCY,
			"from_date": from_date,
			"base": base,
			"custom_tsi_hra_amount": hra,
			"custom_tsi_transport_amount": transport,
			"payroll_payable_account": payable_account(),
		}
	)
	assignment.insert()
	assignment.submit()
	return assignment


def payable_account() -> str:
	account = frappe.db.get_value("Company", COMPANY, "default_payroll_payable_account")
	if account:
		return account
	return frappe.db.get_value(
		"Account",
		{"company": COMPANY, "account_type": "Payable", "is_group": 0},
		"name",
	)


def submit_slip(employee: str, period_start: str) -> str:
	"""Generate and submit a slip for the month starting `period_start`."""
	slip = frappe.get_doc(
		{
			"doctype": "Salary Slip",
			"employee": employee,
			"salary_structure": STRUCTURE,
			"payroll_frequency": "Monthly",
			"start_date": get_first_day(period_start),
			"end_date": get_last_day(period_start),
			"posting_date": get_last_day(period_start),
		}
	).insert()
	slip.submit()
	return slip.name


def hra_on_slip(employee: str, period_start: str) -> float:
	"""Generate a slip for the month starting `period_start` and read its HRA."""
	slip = frappe.get_doc(
		{
			"doctype": "Salary Slip",
			"employee": employee,
			"salary_structure": STRUCTURE,
			"payroll_frequency": "Monthly",
			"start_date": get_first_day(period_start),
			"end_date": get_last_day(period_start),
			"posting_date": get_last_day(period_start),
		}
	).insert()

	for row in slip.earnings:
		if row.salary_component == "_Test TSI HRA":
			return flt(row.amount)
	return 0.0
