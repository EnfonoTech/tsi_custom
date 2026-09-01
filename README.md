### TSI

Custom ERPNext App for Traffic Service International LLC

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app tsi_custom
```

## Salary allocation

Per-component salary amounts on the Salary Structure Assignment (SSA), changeable
from a date, and read correctly by payroll for each period.

### How it works

Each component has its own Currency field on the SSA:

| Field | Label |
|---|---|
| `base` (core) | Basic |
| `custom_tsi_hra_amount` | HRA / Living Allowances |
| `custom_tsi_transport_amount` | Transport / Food Allowance |
| `custom_tsi_other_allowance_amount` | Other Allowance |
| `custom_tsi_total_salary` | Total Salary (read-only, = sum of the above) |

On an SSA form each of these carries two buttons: a clock (the component's
history across the employee's assignments) and a pencil (change it from a date).

**The pencil does not edit the current assignment.** It creates the *next* one:
a new submitted SSA dated from the effective date, carrying every other value
forward — the other components, `base`, `variable`, `income_tax_slab`,
`currency`, `payroll_payable_account` and the payroll cost centres.

That is what makes payroll correct. hrms picks the assignment for a Salary Slip
with `from_date <= <slip start date> ORDER BY from_date DESC`, then merges every
field of that row into the salary formula namespace. So a slip reads the amounts
that were in force for its own period, and regenerating an old slip after a
raise still reproduces its original figures.

A change effective mid-month applies from the **following** period, because a
slip resolves one assignment for the whole period. Date changes to the first of
the month to avoid surprise.

### Required setup: wire the Salary Structure

The component fields are written by this app but read by the **Salary
Structure**. Until each earning row is formula-based and names its SSA field,
the values are stored and ignored — payroll keeps paying the old row amounts,
silently.

Inspect what would change (nothing is written):

```bash
bench --site <site> execute tsi_custom.salary_allocation.wire_salary_structure_formulas --kwargs "{'salary_structure': 'TSI Monthly'}"
```

Check the report:

- `planned` — rows that will be switched to formula-based. `clears_amount` shows
  a fixed amount currently on the row; leaving one in place beside a formula is
  the classic double-pay bug, so it is set to 0.
- `unmapped_components` — earning rows with no SSA field. Pass your own
  `mapping` (`{"<Salary Component>": "<ssa fieldname>"}`) if the component names
  differ from `DEFAULT_COMPONENT_MAP`.
- `already_wired` — idempotent, safe to re-run.

Then apply:

```bash
bench --site <site> execute tsi_custom.salary_allocation.wire_salary_structure_formulas --kwargs "{'salary_structure': 'TSI Monthly', 'dry_run': 0}"
```

Rows are written directly with `frappe.db.set_value`: `formula` and `condition`
are `allow_on_submit` on Salary Detail but `amount_based_on_formula` is not, and
cancelling the structure to amend it would break every assignment linked to it.

Verify on one employee before a payroll run — generate a slip in a period before
the change and one after, and check both component amounts.

### Notes

- `custom_tsi_total_salary` is the contracted monthly allocation, **not** the
  paid gross. A slip prorates and rounds each component independently, so the
  two legitimately differ. Do not use it as gross in a report.
- **Retro increments are allowed.** Dating a change before periods that are
  already paid is permitted, because "effective 1 March, approved in September"
  is normal. The submitted slips keep the amounts they were paid at, so **no
  arrears are created** — settle the difference separately, e.g. with an
  Additional Salary. The change reports which periods it reached behind.
- A change is refused only when it would be meaningless or ambiguous: an
  assignment already starts on that date, or a *later* assignment already exists
  (which would keep overriding it).
- Mid-year income tax openings (`taxable_earnings_till_date`,
  `tax_deducted_till_date`) are **not** carried onto the new assignment. They
  belong to the assignment that opened the payroll period; copying them into a
  later one inside the same period would re-arm a stale opening for every
  remaining slip.
- Deliberately not included: the Salary Breakup Table band lookup, and any
  in-place rewriting of a submitted assignment.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/tsi_custom
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
