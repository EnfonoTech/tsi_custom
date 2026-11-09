# apps/tsi_custom/tsi_custom/oman_leave.py
"""Leave Types required by the Oman Labour Law (Royal Decree 53/2023).

Entitlements are taken from Articles 78-84 as summarised by decree.om and
Khaleej Times (both agree on the sick-pay tiers). Review against the client's
own leave policy before relying on them: the policy may be more generous.

Why create-only
---------------
These are seeded by a patch and by ``after_install``, not shipped as fixtures.
Fixtures re-import on every migrate and would overwrite whatever HR later
changes on the live site (max days, carry-forward caps). Here a Leave Type that
already exists is left exactly as it is.

How the law maps onto hrms
--------------------------
* Sick leave is paid in tiers (100/75/50/35 %). hrms has one pay fraction per
  Leave Type (``fraction_of_daily_salary_per_leave`` = share still paid), so each
  tier is its own type. HR books the tier the employee has reached.
* Event leaves (marriage, bereavement, paternity) have a per-event cap, not a
  yearly balance. They use ``allow_negative`` so no allocation is needed, and
  ``max_continuous_days_allowed`` enforces the cap per application.
* Calendar-day leaves (sick, maternity, iddah) count holidays.
"""

import frappe

ANNUAL = "Annual Leave"

#: name -> Leave Type field values. Anything not listed keeps the hrms default.
OMAN_LEAVE_TYPES = {
	# Art. 78/81: 30 days after 6 months' service; carry-forward capped at 30 days
	ANNUAL: {
		"max_leaves_allowed": 30,
		"applicable_after": 180,
		"is_carry_forward": 1,
		"maximum_carry_forwarded_leaves": 30,
	},
	# Art. 82: up to 182 days a year, paid in tiers on gross wage
	"Sick Leave - Full Pay": {
		"max_leaves_allowed": 21,
		"include_holiday": 1,
	},
	"Sick Leave - 75% Pay": {
		"max_leaves_allowed": 14,
		"include_holiday": 1,
		"is_ppl": 1,
		"fraction_of_daily_salary_per_leave": 0.75,
	},
	"Sick Leave - 50% Pay": {
		"max_leaves_allowed": 35,
		"include_holiday": 1,
		"is_ppl": 1,
		"fraction_of_daily_salary_per_leave": 0.50,
	},
	"Sick Leave - 35% Pay": {
		"max_leaves_allowed": 112,
		"include_holiday": 1,
		"is_ppl": 1,
		"fraction_of_daily_salary_per_leave": 0.35,
	},
	# Art. 84: per-event leaves
	"Maternity Leave": {
		"max_continuous_days_allowed": 98,
		"include_holiday": 1,
		"allow_negative": 1,
	},
	"Paternity Leave": {
		"max_continuous_days_allowed": 7,
		"allow_negative": 1,
	},
	"Marriage Leave": {
		"max_continuous_days_allowed": 3,
		"allow_negative": 1,
	},
	"Bereavement Leave - Spouse or Child": {
		"max_continuous_days_allowed": 10,
		"allow_negative": 1,
	},
	"Bereavement Leave - Parent, Grandparent or Sibling": {
		"max_continuous_days_allowed": 3,
		"allow_negative": 1,
	},
	"Bereavement Leave - Uncle or Aunt": {
		"max_continuous_days_allowed": 2,
		"allow_negative": 1,
	},
	"Iddah Leave - Muslim": {
		"max_continuous_days_allowed": 130,
		"include_holiday": 1,
		"allow_negative": 1,
	},
	"Iddah Leave - Non-Muslim": {
		"max_continuous_days_allowed": 14,
		"include_holiday": 1,
		"allow_negative": 1,
	},
	# once in service: HR allocates 15 days when approved
	"Hajj Leave": {
		"max_leaves_allowed": 15,
		"max_continuous_days_allowed": 15,
	},
	# Omani workers only
	"Patient Companion Leave": {
		"max_leaves_allowed": 15,
	},
	"Exam Leave": {
		"max_leaves_allowed": 15,
	},
	# unpaid
	"Childcare Leave (Unpaid)": {
		"is_lwp": 1,
		"max_continuous_days_allowed": 365,
		"include_holiday": 1,
		"allow_negative": 1,
	},
	"Leave Without Pay": {
		"is_lwp": 1,
		"allow_negative": 1,
	},
}


def create_oman_leave_types():
	"""Insert every missing Oman Leave Type. Returns the names created."""
	created = []
	for name, values in OMAN_LEAVE_TYPES.items():
		if frappe.db.exists("Leave Type", name):
			continue
		doc = frappe.new_doc("Leave Type")
		doc.leave_type_name = name
		doc.update(values)
		doc.insert(ignore_permissions=True)
		created.append(name)
	return created
