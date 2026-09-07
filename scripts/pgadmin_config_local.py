"""Local pgAdmin overrides for this machine.

pgAdmin loads ``config.py``, then ``config_distro.py``, then ``config_local.py``,
so anything set here wins. This file is the master copy; it is version
controlled because the live one lives inside the installed package, which a
``uv tool upgrade pgadmin4`` replaces wholesale. ``scripts/ccweb_pgadmin.cmd``
copies it back into place whenever it has gone missing.

pgAdmin reads this by exec'ing it, so the module-level constants below are the
interface -- they are not unused.
"""

# Desktop mode: a single local user, no login screen and no master password.
# This is only safe in combination with the loopback bind below. In server mode
# pgAdmin behaves as a shared multi-user service and demands an account, which
# is not what a one-person local install wants.
SERVER_MODE = False

# Loopback only. Never 0.0.0.0: with SERVER_MODE off there is no authentication
# in front of this, so binding a routable address would publish an
# unauthenticated console over the network.
DEFAULT_SERVER = "127.0.0.1"
DEFAULT_SERVER_PORT = 5050

# The launch script opens the browser once the port answers, so pgAdmin does
# not need to race it.
OPEN_BROWSER = False

# No phone-home from a development box.
UPGRADE_CHECK_ENABLED = False
