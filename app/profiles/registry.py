from __future__ import annotations
import importlib
from pathlib import Path
from app.profiles.profile_store import resolve_profile_dir, PROFILE_ROOT


def create_game_profile(name: str, root=None):
    directory=resolve_profile_dir(name,Path(root) if root is not None else PROFILE_ROOT)
    if directory.name in {"generic","diablo4"}:
        module=importlib.import_module(f'app.profiles.{directory.name}.profile')
        profile=module.create_profile()
    else:
        from app.profiles.generic.profile import GenericProfile
        profile=GenericProfile()
        profile.name=directory.name
    profile.profile_dir=directory
    return profile
