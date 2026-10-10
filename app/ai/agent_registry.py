"""Select a game implementation without importing other games' agents."""
from importlib import import_module


def agent_class(profile_name):
    if profile_name == 'diablo4':
        return import_module('app.profiles.diablo4.agent').MainAgent
    if profile_name == 'diablo2':
        return import_module('app.profiles.diablo2.agent').Diablo2Agent
    return import_module('app.ai.profile_agent').ProfileAgent


def create_agent(*, profile, **kwargs):
    return agent_class(profile.name)(profile=profile, **kwargs)
