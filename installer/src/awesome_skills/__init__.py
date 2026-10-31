"""Optional Python installer for awesome-skills.

The shipped installer is still ``bin/awesome-skills`` (POSIX shell, no
runtime dependencies). This package is the architecture port of the
agentinstaller reference: a reviewed registry, read-only detection,
idempotent writes, a state manifest, reversible uninstall, and drift
checking. It needs Python 3.9+ and nothing outside the standard library.

Entry points::

    python3 -m awesome_skills --help
    awesome-skills-py --help        # after `pip install -e installer`
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
