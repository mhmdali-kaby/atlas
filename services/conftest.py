"""Session-wide test setup for every Python package (ATL-186).

Every stream and Redis key written during a pytest run is prefixed with this checkout's
``atlas_shared.testing.redis_prefix()``, so two worktrees running pytest at once on the shared test
Redis DB never read or delete each other's streams.
"""

import os

from atlas_shared import bus, testing

os.environ[bus.PREFIX_VAR] = testing.redis_prefix()
