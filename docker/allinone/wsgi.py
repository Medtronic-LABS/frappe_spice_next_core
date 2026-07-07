# Gunicorn's `frappe.app:application` target is the bare WSGI callable — it skips
# the SharedDataMiddleware/StaticDataMiddleware wrapping that `bench serve` applies
# via `application_with_statics()` to serve /assets and /files directly. Without it,
# every asset request 404s even though the files exist on disk. Point gunicorn at
# this module instead (`wsgi:application`) so /assets and /files work the same way
# under gunicorn as they do under `bench serve`.
import frappe.app

application = frappe.app.application_with_statics()
