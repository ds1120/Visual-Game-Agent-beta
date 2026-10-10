"""Startup-only, profile-local execution settings; separate from device config."""
import json
from pathlib import Path


def load_agent_runtime(directory):
    path = Path(directory) / 'runtime.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError(f'Invalid agent runtime profile: {path}')
    for section in ('agent', 'scene', 'buff'):
        if section in data and not isinstance(data[section], dict):
            raise ValueError(f'Invalid runtime section: {section}')
    return data
