"""CHONK: fit a PDF under a byte ceiling, locally, keeping the clearest result."""

import logging

__version__ = "0.2.0"

# A library must not print through logging's last-resort stderr handler.
logging.getLogger("chonk").addHandler(logging.NullHandler())
