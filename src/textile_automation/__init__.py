"""Textile Industrial Automation: a local, read-only, simulator-first monitoring foundation."""

import logging

__version__ = "0.1.0"

# Library best practice: no output unless the application configures logging.
logging.getLogger("textile_automation").addHandler(logging.NullHandler())
