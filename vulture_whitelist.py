# vulture whitelist — FastAPI route functions are not "called" by name;
# they are registered via decorators and invoked by the framework.
# List them here so vulture does not report them as unused.
#
# To regenerate: run vulture with --min-confidence 80 and add any new
# route functions or other framework-registered callables.
#
# Rule: never whitelist anything that is not a framework-registered callable.

from routers import auth, dashboard, documents, newsfeed, private, psx, reports, settings, health  # noqa: F401

# auth routes
auth.login_get
auth.login_post
auth.logout

# dashboard routes
dashboard.dashboard
dashboard.data_health_partial
dashboard.data_health_refresh

# documents routes
documents.documents_page

# newsfeed routes
newsfeed.page

# private routes
private.page
private.save
private.entries_list
private.load
private.delete_entry_route
private.rename_entry_route
private.analyse

# psx routes
psx.landing
psx.search
psx.analysis
psx.tab_panel
psx.staleness
psx.entry_form
psx.run_dcf
psx.save_overrides
psx.clear_overrides
psx.refresh_profile

# reports routes
reports.reports_page
reports.download_pdf

# health routes
health.health_page

# settings routes
settings.settings_page
settings.verify_audit
settings.change_password
settings.setup_totp
settings.confirm_totp
settings.disable_totp
settings.clients_partial
settings.clients_create
settings.clients_update
settings.clients_delete
settings.clients_purge
settings.dcf_rates_partial
settings.dcf_rates_set
