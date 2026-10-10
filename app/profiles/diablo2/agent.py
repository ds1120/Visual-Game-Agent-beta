"""Diablo II implementation entry point; no Diablo IV behavior is inherited."""
from app.ai.profile_agent import ProfileAgent


class Diablo2Agent(ProfileAgent):
    """Implement Diablo II navigation, HUD interpretation and combat here."""
