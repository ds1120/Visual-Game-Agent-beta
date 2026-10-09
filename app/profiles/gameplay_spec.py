"""Profile-local Markdown instructions and validated, optional settings defaults."""
from dataclasses import dataclass
import copy
import hashlib
import json
from pathlib import Path
import re
from .profile_store import PROFILE_ROOT, validate_document

SPEC_FILE = 'GAMEPLAY.md'
SETTINGS_BLOCK = re.compile(r'^```gameplay-settings\s*\n(.*?)^```\s*$', re.M | re.S)


@dataclass(frozen=True)
class GameplaySpec:
    path: Path
    instructions: str
    defaults: dict
    revision: str


def merge_settings(target, defaults, *, overwrite=False):
    """Merge dictionaries; profile JSON tuning wins over Markdown defaults."""
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(target[key], dict):
            merge_settings(target[key], value, overwrite=overwrite)
        elif overwrite:
            target[key] = copy.deepcopy(value)
    return target


def ensure_gameplay_spec(directory):
    directory = Path(directory)
    path = directory / SPEC_FILE
    if not path.exists():
        game = 'ii' if directory.name in {'diablo2r', 'diablo2_resurrected'} else directory.name
        template = PROFILE_ROOT / game / SPEC_FILE
        if not template.is_file():
            template = PROFILE_ROOT / 'generic' / SPEC_FILE
        path.write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
    return path


def load_gameplay_spec(directory):
    path = ensure_gameplay_spec(directory)
    text = path.read_text(encoding='utf-8')
    blocks = SETTINGS_BLOCK.findall(text)
    if len(re.findall(r'^```gameplay-settings\b',text,re.M)) != len(blocks):
        raise ValueError('GAMEPLAY.md의 gameplay-settings 코드 블록을 닫아주세요.')
    if len(blocks) > 1:
        raise ValueError('GAMEPLAY.md에는 gameplay-settings 블록을 하나만 사용할 수 있습니다.')
    defaults = {}
    if blocks:
        try:
            defaults = json.loads(blocks[0], parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except ValueError as exc:
            raise ValueError('GAMEPLAY.md 기본값 JSON 형식 오류') from exc
        if not isinstance(defaults, dict) or set(defaults) - {'input.json', 'navigation.json'}:
            raise ValueError('GAMEPLAY.md 기본값은 input.json/navigation.json만 지원합니다.')
        from .runtime_settings import INPUT_DEFAULT, NAV_DEFAULT
        bases = {'input.json': INPUT_DEFAULT, 'navigation.json': NAV_DEFAULT}
        for name, values in defaults.items():
            if not isinstance(values, dict):
                raise ValueError('GAMEPLAY.md 기본값은 객체 형식이어야 합니다.')
            validate_document(name, merge_settings(copy.deepcopy(bases[name]), values, overwrite=True))
    instructions = SETTINGS_BLOCK.sub('', text).strip()
    return GameplaySpec(path, instructions, defaults, hashlib.sha256(text.encode('utf-8')).hexdigest())
