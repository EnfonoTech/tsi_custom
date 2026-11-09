# apps/tsi_custom/tsi_custom/tests/test_payslip.py
"""Tests for the TSI Salary Slip print-format helpers and the Oman leave seed."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from tsi_custom.oman_leave import ANNUAL, OMAN_LEAVE_TYPES, create_oman_leave_types
from tsi_custom.payslip import clip_to_period, pay_note


class TestClipToPeriod(FrappeTestCase):
	def test_inside_period_unchanged(self):
		self.assertEqual(
			clip_to_period("2026-10-05", "2026-10-07", "2026-10-01", "2026-10-31"),
			(getdate("2026-10-05"), getdate("2026-10-07")),
		)

	def test_crossing_month_end_is_cut_at_period_end(self):
		self.assertEqual(
			clip_to_period("2026-10-29", "2026-11-03", "2026-10-01", "2026-10-31"),
			(getdate("2026-10-29"), getdate("2026-10-31")),
		)

	def test_crossing_month_start_is_cut_at_period_start(self):
		self.assertEqual(
			clip_to_period("2026-09-28", "2026-10-02", "2026-10-01", "2026-10-31"),
			(getdate("2026-10-01"), getdate("2026-10-02")),
		)

	def test_outside_period_is_none(self):
		self.assertIsNone(clip_to_period("2026-11-01", "2026-11-03", "2026-10-01", "2026-10-31"))


class TestPayNote(FrappeTestCase):
	def test_labels(self):
		self.assertEqual(pay_note(frappe._dict(is_lwp=1, is_ppl=0)), "Unpaid")
		self.assertEqual(
			pay_note(frappe._dict(is_lwp=0, is_ppl=1, fraction_of_daily_salary_per_leave=0.75)), "75% paid"
		)
		self.assertEqual(pay_note(frappe._dict(is_lwp=0, is_ppl=0)), "")
		self.assertEqual(pay_note(None), "")


class TestOmanLeaveTypes(FrappeTestCase):
	def test_sick_leave_tiers_cover_182_days(self):
		sick = [v for k, v in OMAN_LEAVE_TYPES.items() if k.startswith("Sick Leave")]
		self.assertEqual(sum(v["max_leaves_allowed"] for v in sick), 182)

	def test_seed_creates_missing_and_never_overwrites(self):
		create_oman_leave_types()
		for name in OMAN_LEAVE_TYPES:
			self.assertTrue(frappe.db.exists("Leave Type", name), name)

		# HR changes a value on the live site; a re-run must keep it
		frappe.db.set_value("Leave Type", ANNUAL, "max_leaves_allowed", 35)
		self.assertEqual(create_oman_leave_types(), [])
		self.assertEqual(frappe.db.get_value("Leave Type", ANNUAL, "max_leaves_allowed"), 35)
