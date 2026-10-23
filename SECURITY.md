# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue: use
GitHub's **Report a vulnerability** button on this repository's
**Security** tab (private vulnerability reporting), or mail
support@classobjects.com. Include what you found, how to reproduce it, and
the version (commit) you tested.

You can expect an acknowledgement within a week and a fix or an
explanation within 90 days. Once a fix is released the report may be
published with credit to you, unless you ask otherwise.

## Supported versions

Only the latest commit on `main` is supported; there are no maintained
release branches.

## Scope

In scope: the API (`backend/`), the shop and console (`frontend/`), the
operational scripts (`scripts/`) and the deployment templates (`deploy/`).

The deployment documents describe the supported way to run the system on
the internet -- the API behind Caddy, which proxies only `/api/*`, with the
security headers in `deploy/Caddyfile`. A report that depends on exposing
the API or the development servers directly to the internet is still
welcome, but is weighed against that.

## Practices the code follows

- Passwords are hashed with argon2; sign-in and registration are throttled
  per address and per account.
- Every console route requires a manager; the shop's responses are built
  field by field so cost, storage location and history never reach a
  customer.
- Uploaded and fetched photographs are decoded within size and pixel
  limits, re-encoded without metadata, and served only as renditions;
  fetching by web address refuses private and local addresses.
- Secrets are never in the repository: `JWT_SECRET` has no default and a
  published or short value is refused at start-up.
