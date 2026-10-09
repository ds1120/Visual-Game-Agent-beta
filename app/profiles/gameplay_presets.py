"""Game-specific settings around a shared movement/combat engine; never copy learned objects."""
import json
from .profile_store import atomic_json, validate_document
from .gameplay_spec import load_gameplay_spec, merge_settings


def ensure_gameplay_defaults(directory):
    """Caller owns the profile lock. Add knobs without replacing user's skills or memories."""
    defaults=load_gameplay_spec(directory).defaults
    nav_path=directory/'navigation.json';nav=json.loads(nav_path.read_text(encoding='utf-8'))
    before=json.dumps(nav,sort_keys=True)
    merge_settings(nav,defaults.get('navigation.json',{}))
    validate_document('navigation.json',nav)
    if before!=json.dumps(nav,sort_keys=True):atomic_json(nav_path,nav)
    input_path=directory/'input.json';settings=json.loads(input_path.read_text(encoding='utf-8'))
    before=json.dumps(settings,sort_keys=True)
    merge_settings(settings,defaults.get('input.json',{}))
    if directory.name in {'ii','diablo2r','diablo2_resurrected'} and settings.get('gameplay_preset_version')!=1:
        # Only migrate inherited generic controls. User-defined skill keys remain intact.
        movement=settings['movement']
        if movement.get('mode')=='keys' and [movement.get(k) for k in ('up','down','left','right')]==['W','S','A','D']:movement['mode']='click'
        settings.setdefault('basic_attack_mode','tap')
        if settings['bindings'].get('USE_POTION')=='Q':settings['bindings']['USE_POTION']='1'
        settings.setdefault('disabled_actions',['USE_SKILL','CAST_BUFF'])
        settings['gameplay_preset_version']=1
    validate_document('input.json',settings)
    if before!=json.dumps(settings,sort_keys=True):atomic_json(input_path,settings)
