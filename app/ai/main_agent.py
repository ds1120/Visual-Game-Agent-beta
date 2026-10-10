"""Compatibility import for the Diablo IV agent; new callers use agent_registry."""
import sys
from app.profiles.diablo4 import agent as _diablo4_agent

# Preserve existing imports and patch targets during the profile migration.
sys.modules[__name__] = _diablo4_agent
