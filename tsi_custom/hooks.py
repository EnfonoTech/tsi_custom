app_name = "tsi_custom"
app_title = "TSI"
app_publisher = "Enfono"
app_description = "Custom ERPNext App for Traffic Service International LLC"
app_email = "neha@enfono.com"
app_license = "mit"

# Apps
# ------------------

# Salary allocation extends hrms' Salary Structure Assignment / Salary Slip.
required_apps = ["frappe/erpnext", "frappe/hrms"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "tsi_custom",
# 		"logo": "/assets/tsi_custom/logo.png",
# 		"title": "TSI",
# 		"route": "/tsi_custom",
# 		"has_permission": "tsi_custom.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/tsi_custom/css/tsi_custom.css"
# app_include_js = "/assets/tsi_custom/js/tsi_custom.js"

# include js, css files in header of web template
# web_include_css = "/assets/tsi_custom/css/tsi_custom.css"
# web_include_js = "/assets/tsi_custom/js/tsi_custom.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "tsi_custom/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Salary Structure Assignment": "public/js/salary_structure_assignment.js",
}

doctype_list_js = {
	"Payroll Entry": "public/js/zip_export_list.js",
	"Salary Slip": "public/js/zip_export_list.js",
	"Attendance": "public/js/zip_export_list.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "tsi_custom/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "tsi_custom.utils.jinja_methods",
# 	"filters": "tsi_custom.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "tsi_custom.install.before_install"
# after_install = "tsi_custom.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "tsi_custom.uninstall.before_uninstall"
# after_uninstall = "tsi_custom.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "tsi_custom.utils.before_app_install"
# after_app_install = "tsi_custom.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "tsi_custom.utils.before_app_uninstall"
# after_app_uninstall = "tsi_custom.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "tsi_custom.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Salary Structure Assignment": {
		# sync_allocations fills the grid from the selected Salary Structure and
		# refreshes each row's formula variable; set_total_salary then sums the
		# Earning rows. Both run on hand-made assignments too, not only on the
		# ones this app generates.
		"validate": [
			"tsi_custom.salary_allocation.sync_allocations",
			"tsi_custom.salary_allocation.set_total_salary",
		],
	},
}

# Salary Slip is extended only to publish the allocation amounts into the salary
# formula namespace -- see tsi_custom/overrides/salary_slip.py for why this
# cannot be a doc_event. Note: only one app can override a given DocType class.
override_doctype_class = {
	"Salary Slip": "tsi_custom.overrides.salary_slip.TSISalarySlip",
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"tsi_custom.tasks.all"
# 	],
# 	"daily": [
# 		"tsi_custom.tasks.daily"
# 	],
# 	"hourly": [
# 		"tsi_custom.tasks.hourly"
# 	],
# 	"weekly": [
# 		"tsi_custom.tasks.weekly"
# 	],
# 	"monthly": [
# 		"tsi_custom.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "tsi_custom.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "tsi_custom.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "tsi_custom.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["tsi_custom.utils.before_request"]
# after_request = ["tsi_custom.utils.after_request"]

# Job Events
# ----------
# before_job = ["tsi_custom.utils.before_job"]
# after_job = ["tsi_custom.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"tsi_custom.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

# Fixtures
# --------
# The Salary Allocation Custom Fields on Salary Structure Assignment. Without
# these the allocation API validates against missing fields and refuses to run,
# so they must ship with the app rather than be created per site.
fixtures = [
	{"doctype": "Custom Field", "filters": [["module", "=", "TSI"]]},
	{"doctype": "Property Setter", "filters": [["module", "=", "TSI"]]},
]
