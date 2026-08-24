"""Makes ``python -m app.worker`` work."""

from __future__ import annotations

import sys

from app.worker.main import main

sys.exit(main())
