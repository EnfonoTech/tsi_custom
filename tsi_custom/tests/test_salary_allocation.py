# apps/tsi_custom/tsi_custom/tests/test_salary_allocation.py
"""Tests for per-component salary allocation on Salary Structure Assignment.

The contract that matters is the first group: a Salary Slip must read the
amount that was in force for *its own* period, through the formula variable each
allocation row publishes. Everything else guards the paths that could break it.

Fixtures are built inline rather than through hrms' test helpers, whose
signatures move between versions.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, get_first_day, get_last_day

from tsi_custom.salary_allocation import (
	ALLOCATION_FIELD,
	TOTAL_FIELD,
	backfill_allocations,
	change_allocations,
	formula_variable,
	get_component_history,
	get_structure_components,
	wire_salary_structure_formulas,
)

COMPANY = "_Test Company"
CURRENCY = "INR"
STRUCTURE = "_Test TSI Allocation Structure"

BASIC = "_Test TSI Basic"
HRA = "_Test TSI HRA"
TRANSPORT = "_Test TSI Transport"
PF = "_Test TSI PF"
TAX = "_Test TSI Income Tax"

# label -> (abbr, type) -- components this feature manages
COMPONENTS = {
	BASIC: ("TTBASIC", "Earning"),
	HRA: ("TTHRA", "Earning"),
	TRANSPORT: ("TTTRAN", "Earning"),
	PF: ("TTPF", "Deduction"),
}

# hrms computes this one itself; allocation must leave it alone entirely
TAX_ABBR = "TTTAX"


class TestSalaryAllocation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.get_meta("Salary Structure Assignment").has_field(ALLOCATION_FIELD):
			raise unittest.SkipTest(f"tsi_custom Custom Field {ALLOCATION_FIELD} not migrated on this site")
		cls.employee = make_employee("tsi_allocation@example.com")
		make_salary_structure()
		wire_salary_structure_formulas(STRUCTURE, dry_run=0)

	def setUp(self):
		frappe.db.delete("Salary Slip", {"employee": self.employee})
		stale = frappe.get_all(
			"Salary Structure Assignment", filters={"employee": self.employee}, pluck="name"
		)
		if stale:
			frappe.db.delete("TSI Salary Allocation", {"parent": ["in", stale]})
		frappe.db.delete("Salary Structure Assignment", {"employee": self.employee})

	# -- The contract ---------------------------------------------------------

	def test_slip_uses_the_amount_in_force_for_its_own_period(self):
		"""A raise dated 1 Apr must not reach the March slip."""
		make_assignment(self.employee, "2026-01-01", {HRA: 1000, BASIC: 4000})

		change_allocations(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={HRA: 2000},
		)

		self.assertEqual(component_on_slip(self.employee, "2026-03-01", HRA), 1000)
		self.assertEqual(component_on_slip(self.employee, "2026-04-01", HRA), 2000)

	def test_earlier_slip_still_reproduces_its_original_figure(self):
		"""Regenerating an old slip after a raise must not repay history."""
		make_assignment(self.employee, "2026-01-01", {HRA: 1000, BASIC: 4000})
		before = component_on_slip(self.employee, "2026-02-01", HRA)

		change_allocations(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={HRA: 2000},
		)

		frappe.db.delete("Salary Slip", {"employee": self.employee})
		self.assertEqual(component_on_slip(self.employee, "2026-02-01", HRA), before)

	def test_every_component_including_deductions_reaches_the_slip(self):
		make_assignment(self.employee, "2026-01-01", {BASIC: 4000, HRA: 1000, TRANSPORT: 500, PF: 300})

		self.assertEqual(component_on_slip(self.employee, "2026-02-01", BASIC), 4000)
		self.assertEqual(component_on_slip(self.employee, "2026-02-01", HRA), 1000)
		self.assertEqual(component_on_slip(self.employee, "2026-02-01", TRANSPORT), 500)
		self.assertEqual(component_on_slip(self.employee, "2026-02-01", PF, "deductions"), 300)

	# -- Grid is driven by the structure --------------------------------------

	def test_structure_components_are_listed_earnings_first(self):
		components = get_structure_components(STRUCTURE)
		self.assertEqual({c["salary_component"] for c in components}, set(COMPONENTS))
		self.assertEqual(components[-1]["salary_component"], PF)
		self.assertEqual(
			components[0]["formula_variable"],
			formula_variable(COMPONENTS[components[0]["salary_component"]][0]),
		)

	def test_validate_fills_missing_rows_from_the_structure(self):
		"""Only the structure is chosen; every component still gets a row."""
		assignment = frappe.get_doc(
			{
				"doctype": "Salary Structure Assignment",
				"employee": self.employee,
				"salary_structure": STRUCTURE,
				"company": COMPANY,
				"currency": CURRENCY,
				"from_date": "2026-01-01",
				"payroll_payable_account": payable_account(),
			}
		).insert()

		self.assertEqual({row.salary_component for row in assignment.get(ALLOCATION_FIELD)}, set(COMPONENTS))
		for row in assignment.get(ALLOCATION_FIELD):
			self.assertEqual(row.formula_variable, formula_variable(COMPONENTS[row.salary_component][0]))
			self.assertEqual(row.component_type, COMPONENTS[row.salary_component][1])

	def test_duplicate_component_rows_are_rejected(self):
		assignment = frappe.get_doc(
			{
				"doctype": "Salary Structure Assignment",
				"employee": self.employee,
				"salary_structure": STRUCTURE,
				"company": COMPANY,
				"currency": CURRENCY,
				"from_date": "2026-01-01",
				"payroll_payable_account": payable_account(),
				ALLOCATION_FIELD: [
					{"salary_component": HRA, "amount": 1000},
					{"salary_component": HRA, "amount": 2000},
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			assignment.insert()

	def test_total_is_the_sum_of_earning_rows_only(self):
		assignment = make_assignment(
			self.employee, "2026-01-01", {BASIC: 4000, HRA: 1000, TRANSPORT: 500, PF: 300}
		)
		self.assertEqual(flt(assignment.get(TOTAL_FIELD)), 5500)

	# -- Assignment chain -----------------------------------------------------

	def test_change_creates_a_new_dated_assignment_and_leaves_the_old_one_alone(self):
		original = make_assignment(self.employee, "2026-01-01", {HRA: 1000, TRANSPORT: 500})

		result = change_allocations(
			employee=self.employee,
			effective_from="2026-04-01",
			changes={HRA: 2000},
		)

		created = frappe.get_doc("Salary Structure Assignment", result["name"])
		self.assertEqual(created.docstatus, 1)
		self.assertEqual(str(created.from_date), "2026-04-01")
		self.assertEqual(allocation_of(created, HRA), 2000)
		# untouched components carry forward
		self.assertEqual(allocation_of(created, TRANSPORT), 500)
		# and the payable account, without which Payroll Entry drops the employee
		self.assertEqual(created.payroll_payable_account, original.payroll_payable_account)

		original.reload()
		self.assertEqual(allocation_of(original, HRA), 1000)

	def test_rejects_a_second_assignment_on_the_same_date(self):
		make_assignment(self.employee, "2026-01-01", {HRA: 1000})

		with self.assertRaises(frappe.ValidationError):
			change_allocations(
				employee=self.employee,
				effective_from="2026-01-01",
				changes={HRA: 2000},
			)

	def test_rejects_back_dating_behind_a_later_assignment(self):
		make_assignment(self.employee, "2026-01-01", {HRA: 1000})
		change_allocations(employee=self.employee, effective_from="2026-06-01", changes={HRA: 3000})

		with self.assertRaises(frappe.ValidationError):
			change_allocations(employee=self.employee, effective_from="2026-03-01", changes={HRA: 2000})

	def test_rejects_a_change_launched_from_a_superseded_assignment(self):
		first = make_assignment(self.employee, "2026-01-01", {HRA: 1000})
		change_allocations(employee=self.employee, effective_from="2026-04-01", changes={HRA: 2000})

		with self.assertRaises(frappe.ValidationError):
			change_allocations(
				employee=self.employee,
				effective_from="2026-07-01",
				changes={HRA: 3000},
				source_assignment=first.name,
			)

	def test_rejects_a_no_op_change(self):
		make_assignment(self.employee, "2026-01-01", {HRA: 1000})

		with self.assertRaises(frappe.ValidationError):
			change_allocations(employee=self.employee, effective_from="2026-04-01", changes={HRA: 1000})

	def test_rejects_a_component_not_on_the_assignment(self):
		make_assignment(self.employee, "2026-01-01", {HRA: 1000})

		with self.assertRaises(frappe.ValidationError):
			change_allocations(
				employee=self.employee,
				effective_from="2026-04-01",
				changes={"_Test TSI Nonexistent": 500},
			)

	def test_back_dating_over_paid_periods_is_allowed_but_reported(self):
		"""A retro increment is routine, so it must succeed -- and say what it hit."""
		make_assignment(self.employee, "2026-01-01", {HRA: 1000, BASIC: 4000})
		slip = submit_slip(self.employee, "2026-02-01")
		paid_before = frappe.db.get_value("Salary Slip", slip, "gross_pay")

		result = change_allocations(employee=self.employee, effective_from="2026-02-01", changes={HRA: 2000})

		self.assertTrue(result["name"])
		self.assertIsNotNone(result["already_paid"])
		self.assertEqual(result["already_paid"]["first_slip"], slip)
		self.assertEqual(frappe.db.get_value("Salary Slip", slip, "gross_pay"), paid_before)

	def test_tax_opening_balances_are_not_carried_forward(self):
		assignment = make_assignment(self.employee, "2026-01-01", {HRA: 1000})
		if not assignment.meta.has_field("taxable_earnings_till_date"):
			self.skipTest("payroll opening fields not present on this hrms version")

		frappe.db.set_value(
			"Salary Structure Assignment",
			assignment.name,
			{"taxable_earnings_till_date": 600000, "tax_deducted_till_date": 45500},
			update_modified=False,
		)

		result = change_allocations(employee=self.employee, effective_from="2026-04-01", changes={HRA: 2000})
		created = frappe.get_doc("Salary Structure Assignment", result["name"])
		self.assertEqual(flt(created.taxable_earnings_till_date), 0)
		self.assertEqual(flt(created.tax_deducted_till_date), 0)

	# -- History --------------------------------------------------------------

	def test_history_windows_and_marks_the_current_assignment(self):
		make_assignment(self.employee, "2026-01-01", {HRA: 1000})
		change_allocations(employee=self.employee, effective_from="2026-04-01", changes={HRA: 2000})

		history = get_component_history(self.employee, HRA)
		self.assertEqual(len(history), 2)

		current, previous = history[0], history[1]
		self.assertEqual(flt(current["value"]), 2000)
		self.assertTrue(current["is_current"])
		self.assertIsNone(current["effective_until"])

		self.assertEqual(flt(previous["value"]), 1000)
		self.assertFalse(previous["is_current"])
		self.assertEqual(str(previous["effective_until"]), "2026-03-31")

	def test_income_tax_rows_get_no_allocation_and_are_never_wired(self):
		"""hrms computes tax from the slab -- a formula there switches that off."""
		components = get_structure_components(STRUCTURE)
		self.assertNotIn(TAX, {c["salary_component"] for c in components})

		assignment = make_assignment(self.employee, "2026-01-01", {HRA: 1000})
		self.assertNotIn(TAX, {row.salary_component for row in assignment.get(ALLOCATION_FIELD)})

		result = wire_salary_structure_formulas(STRUCTURE, dry_run=0)
		self.assertIn(TAX, result["left_unmanaged"])
		self.assertNotIn(TAX, [item["salary_component"] for item in result["planned"]])

		formula = frappe.db.get_value(
			"Salary Detail",
			{"parent": STRUCTURE, "parenttype": "Salary Structure", "salary_component": TAX},
			"formula",
		)
		self.assertFalse(formula)

	def test_future_dated_assignment_is_scheduled_not_current(self):
		"""Payroll still uses the earlier one until the date arrives."""
		make_assignment(self.employee, "2026-01-01", {HRA: 1000})
		change_allocations(employee=self.employee, effective_from="2099-01-01", changes={HRA: 9000})

		history = get_component_history(self.employee, HRA)
		future, in_force = history[0], history[1]

		self.assertTrue(future["is_scheduled"])
		self.assertFalse(future["is_current"])
		self.assertFalse(in_force["is_scheduled"])
		self.assertTrue(in_force["is_current"])

	# -- Adoption on an existing site -----------------------------------------

	def test_wiring_refuses_while_an_assignment_has_no_allocation(self):
		"""Wiring first would fail that employee's next slip with a Name error."""
		strip_allocations(make_assignment(self.employee, "2026-01-01", {HRA: 1000}).name)

		with self.assertRaises(frappe.ValidationError):
			wire_salary_structure_formulas(STRUCTURE, dry_run=0)

	def test_backfill_adds_the_grid_to_a_submitted_assignment_at_zero(self):
		"""A submitted assignment cannot grow the grid by being re-saved."""
		assignment = make_assignment(self.employee, "2026-01-01", {HRA: 1000})
		strip_allocations(assignment.name)

		preview = backfill_allocations(STRUCTURE)
		self.assertEqual(preview["assignments_touched"], 1)
		self.assertEqual(rows_on(assignment.name), {})  # dry run wrote nothing

		backfill_allocations(STRUCTURE, dry_run=0)
		self.assertEqual(rows_on(assignment.name), dict.fromkeys(COMPONENTS, 0.0))

		# idempotent, and wiring is unblocked again
		self.assertEqual(backfill_allocations(STRUCTURE, dry_run=0)["assignments_touched"], 0)
		wire_salary_structure_formulas(STRUCTURE, dry_run=0)

	def test_change_allocation_sets_real_amounts_after_a_backfill(self):
		"""The documented recovery path: backfill at zero, then date the real figures."""
		strip_allocations(make_assignment(self.employee, "2026-01-01", {HRA: 1000}).name)
		backfill_allocations(STRUCTURE, dry_run=0)

		result = change_allocations(employee=self.employee, effective_from="2026-04-01", changes={HRA: 2500})
		created = frappe.get_doc("Salary Structure Assignment", result["name"])
		self.assertEqual(allocation_of(created, HRA), 2500)

	# -- Wiring ---------------------------------------------------------------

	def test_wiring_is_idempotent_and_points_rows_at_their_variable(self):
		result = wire_salary_structure_formulas(STRUCTURE, dry_run=0)
		self.assertEqual(result["planned"], [])
		self.assertEqual(set(result["already_wired"]), set(COMPONENTS))
		self.assertEqual(result["left_unmanaged"], [TAX])

		for label, (abbr, _type) in COMPONENTS.items():
			formula = frappe.db.get_value(
				"Salary Detail",
				{"parent": STRUCTURE, "parenttype": "Salary Structure", "salary_component": label},
				"formula",
			)
			self.assertEqual(formula, formula_variable(abbr))


# -- Fixtures ------------------------------------------------------------------


def make_employee(user_id: str) -> str:
	existing = frappe.db.get_value("Employee", {"user_id": user_id, "company": COMPANY})
	if existing:
		return existing

	return (
		frappe.get_doc(
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
		)
		.insert()
		.name
	)


def make_salary_component(
	label: str, abbr: str, component_type: str, variable_based_on_taxable_salary: int = 0
) -> None:
	if frappe.db.exists("Salary Component", label):
		return

	frappe.get_doc(
		{
			"doctype": "Salary Component",
			"salary_component": label,
			"salary_component_abbr": abbr,
			"type": component_type,
			"variable_based_on_taxable_salary": variable_based_on_taxable_salary,
			# Keep assertions on the raw formula result: proration by payment
			# days is core behaviour and not what these tests cover.
			"depends_on_payment_days": 0,
		}
	).insert()


def make_salary_structure() -> None:
	for label, (abbr, component_type) in COMPONENTS.items():
		make_salary_component(label, abbr, component_type)

	if frappe.db.exists("Salary Structure", STRUCTURE):
		return

	def row(label):
		abbr, _type = COMPONENTS[label]
		return {
			"salary_component": label,
			"abbr": abbr,
			"amount_based_on_formula": 1,
			"formula": formula_variable(abbr),
			"depends_on_payment_days": 0,
		}

	make_salary_component(TAX, TAX_ABBR, "Deduction", variable_based_on_taxable_salary=1)

	structure = frappe.get_doc(
		{
			"doctype": "Salary Structure",
			"name": STRUCTURE,
			"company": COMPANY,
			"currency": CURRENCY,
			"payroll_frequency": "Monthly",
			"earnings": [row(BASIC), row(HRA), row(TRANSPORT)],
			"deductions": [
				row(PF),
				{"salary_component": TAX, "abbr": TAX_ABBR, "variable_based_on_taxable_salary": 1},
			],
		}
	)
	structure.insert()
	structure.submit()


def make_assignment(employee: str, from_date: str, amounts: dict):
	assignment = frappe.get_doc(
		{
			"doctype": "Salary Structure Assignment",
			"employee": employee,
			"salary_structure": STRUCTURE,
			"company": COMPANY,
			"currency": CURRENCY,
			"from_date": from_date,
			"payroll_payable_account": payable_account(),
		}
	)
	assignment.insert()  # validate fills the grid from the structure

	for row in assignment.get(ALLOCATION_FIELD):
		row.amount = flt(amounts.get(row.salary_component, 0))
	assignment.save()
	assignment.submit()
	return assignment


def strip_allocations(assignment: str) -> None:
	"""Make an assignment look like one submitted before this app existed."""
	frappe.db.delete(
		"TSI Salary Allocation",
		{"parent": assignment, "parenttype": "Salary Structure Assignment"},
	)
	frappe.clear_document_cache("Salary Structure Assignment", assignment)


def rows_on(assignment: str) -> dict:
	return {
		row.salary_component: flt(row.amount)
		for row in frappe.get_all(
			"TSI Salary Allocation",
			filters={"parent": assignment, "parenttype": "Salary Structure Assignment"},
			fields=["salary_component", "amount"],
		)
	}


def allocation_of(assignment, salary_component: str) -> float:
	for row in assignment.get(ALLOCATION_FIELD) or []:
		if row.salary_component == salary_component:
			return flt(row.amount)
	return 0.0


def payable_account() -> str:
	return frappe.db.get_value("Company", COMPANY, "default_payroll_payable_account") or frappe.db.get_value(
		"Account", {"company": COMPANY, "account_type": "Payable", "is_group": 0}, "name"
	)


def build_slip(employee: str, period_start: str):
	# hrms rejects a second slip for the same employee and period, so clear any
	# slip a previous assertion in the same test already built.
	frappe.db.delete(
		"Salary Slip",
		{"employee": employee, "start_date": get_first_day(period_start), "docstatus": 0},
	)
	return frappe.get_doc(
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


def submit_slip(employee: str, period_start: str) -> str:
	slip = build_slip(employee, period_start)
	slip.submit()
	return slip.name


def component_on_slip(employee: str, period_start: str, component: str, table: str = "earnings") -> float:
	slip = build_slip(employee, period_start)
	for row in slip.get(table):
		if row.salary_component == component:
			return flt(row.amount)
	return 0.0
