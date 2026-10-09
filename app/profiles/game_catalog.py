"""Game tabs backed by independent profile directories; no downloaded code."""
from __future__ import annotations
import copy,json,re,uuid
from pathlib import Path
from app.profiles.profile_store import PROFILE_ROOT,FILES,ProfileStore,atomic_json,resolve_profile_dir
from app.profiles.runtime_settings import ensure_runtime_settings

LABELS={'diablo4':'디아블로 IV','generic':'범용 게임'}

def list_games(root=PROFILE_ROOT):
    result=[]
    for path in sorted(Path(root).iterdir()):
        if not path.is_dir() or path.is_symlink() or not (path/'knowledge.json').is_file():continue
        try:
            resolve_profile_dir(path.name,Path(root))
            meta=json.loads((path/'game.json').read_text(encoding='utf-8')) if (path/'game.json').exists() else {}
            result.append({'id':path.name,'name':meta.get('name',LABELS.get(path.name,path.name))})
        except (ValueError,OSError):continue
    return result


def add_game(name,window_title='',root=PROFILE_ROOT):
    if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError('게임 이름은 1~80자로 입력하세요.')
    if not isinstance(window_title,str) or len(window_title)>120:raise ValueError('게임 창 제목은 120자 이내로 입력하세요.')
    name=name.strip();root=Path(root)
    if any(g['name'].casefold()==name.casefold() for g in list_games(root)):raise ValueError('같은 이름의 게임이 이미 있습니다.')
    slug=re.sub('[^a-z0-9_]+','_',name.lower()).strip('_')[:40]
    if not slug or (root/slug).exists():slug='game_'+uuid.uuid4().hex[:12]
    directory=root/slug;directory.mkdir(exist_ok=False)
    try:
        # Knowledge is the only generic template; learned objects, HUD geometry,
        # keys and statistics from another game are never copied.
        knowledge=json.loads((PROFILE_ROOT/'generic/knowledge.json').read_text(encoding='utf-8'))
        knowledge.update(game=name,semantic_rules=[],named_entities=[])
        seed={'knowledge.json':knowledge,'monsters.json':{'version':1,'entries':[]},'items.json':{'version':1,'entries':[]},
              'object_memory.json':{'version':3,'objects':[]},'hunting.json':{'version':1,'policy':{'style':'balanced','target_priority':['boss','elite','normal'],'pickup_enabled':False,'potion_hp_threshold':30,'retreat_hp_threshold':20},'methods':[]}}
        for file,data in seed.items():atomic_json(directory/file,data)
        ensure_runtime_settings(directory)
        docs,_=ProfileStore(directory).snapshot()
        vision=docs['vision.json'];vision['window_titles']=[window_title.strip()] if window_title.strip() else []
        atomic_json(directory/'vision.json',vision)
        atomic_json(directory/'game.json',{'version':1,'name':name,'sensor':'generic'})
        (directory/'assets/hud').mkdir(parents=True);(directory/'assets/buffs').mkdir(parents=True)
        return {'id':slug,'name':name}
    except Exception:
        import shutil
        shutil.rmtree(directory)
        raise


def active_game(fallback,root=PROFILE_ROOT):
    path=Path(root)/'active_game.json'
    if path.exists():
        try:
            name=json.loads(path.read_text(encoding='utf-8'))['id'];resolve_profile_dir(name,Path(root));return name
        except (OSError,ValueError,KeyError,TypeError):pass
    return fallback
