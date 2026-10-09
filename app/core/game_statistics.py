"""Game-local durable encounter/input statistics; no extra AI calls or per-frame disk writes."""
from __future__ import annotations
import json
from contextlib import closing
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

KINDS=("monster","npc","item","obstacle","player","unknown")
KST=timezone(timedelta(hours=9))

class GameStatistics:
    def __init__(self, profile_dir):
        self.path=Path(profile_dir)/"statistics.sqlite3"
        self.session=uuid.uuid4().hex
        self._pending=deque()
        self._queue_lock=threading.Lock()
        self._db_lock=threading.Lock()
        self._tracks={}
        self._suppress_until=0
        self.error=None
        self.last_saved_at=None
        self._initialize()

    def _connect(self):
        connection=sqlite3.connect(self.path,timeout=3)
        connection.execute("PRAGMA busy_timeout=3000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self._connect()) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS encounters(id TEXT PRIMARY KEY, session TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, first_seen REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, session TEXT NOT NULL, day TEXT NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL, action TEXT, status TEXT, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS encounters_day ON encounters(day);
            CREATE INDEX IF NOT EXISTS events_day ON events(day);
            """)

    @staticmethod
    def _day(at):
        return datetime.fromtimestamp(at,KST).date().isoformat()

    def _enqueue(self,kind,payload,*,id=None,action=None,status=None,at=None):
        at=time.time() if at is None else at
        row=(id or uuid.uuid4().hex,self.session,self._day(at),at,kind,action,status,json.dumps(payload,ensure_ascii=False,allow_nan=False))
        with self._queue_lock:self._pending.append(row)

    def event(self,kind,**payload):
        self._enqueue(kind,payload)

    def suspend(self):
        # Loss of HUD, movement, scene/profile resets and pause cannot be kill evidence.
        self._tracks.clear()
        self._suppress_until=time.monotonic()+2

    def observe(self,objects,*,now=None,wall_time=None,roi=(0,0,1,1),shape=(1,1)):
        now=time.monotonic() if now is None else now
        wall_time=time.time() if wall_time is None else wall_time
        live=set()
        h,w=shape[:2]
        for obj in objects:
            if obj.track_id is None:continue
            tid=obj.track_id;live.add(tid)
            kind=obj.object_type if obj.status=="confirmed" and obj.object_type in KINDS else "unknown"
            old=self._tracks.get(tid)
            payload={"track_id":tid,"memory_id":obj.memory_id,"name":obj.name,"kind":kind,"relation":obj.relation,"status":obj.status}
            if old is None or old["kind"]!=kind or old["memory_id"]!=obj.memory_id:
                self._enqueue("encounter",payload,id=f"{self.session}:{tid}",at=wall_time)
            if old is not None and old["kind"]!=kind:
                self._enqueue("object_reclassified", {**payload, "previous_kind":old["kind"]}, at=wall_time)
            cx=(obj.bbox[0]+obj.bbox[2])/2/max(1,w);cy=(obj.bbox[1]+obj.bbox[3])/2/max(1,h)
            x,y,rw,rh=roi
            central=x+.04<cx<x+rw-.04 and y+.04<cy<y+rh-.04
            current={**(old or {"hits":0,"attack_at":None,"estimated":False}),**payload,"last_seen":now,"central":central}
            current["hits"]+=1
            self._tracks[tid]=current
        for tid,track in list(self._tracks.items()):
            missing=now-track["last_seen"]
            if (tid not in live and missing>=2 and not track["estimated"] and now>=self._suppress_until
                and track["kind"]=="monster" and track["relation"]=="hostile" and track["hits"]>=2
                and track["central"] and track["attack_at"] is not None and now-track["attack_at"]<=6):
                self._enqueue("kill_estimated",{"track_id":tid,"name":track["name"],"memory_id":track["memory_id"],"reason":"공격 전송 후 화면 중앙의 적이 2초 이상 미검출; 가림/추적 실패 가능"},at=wall_time)
                track["estimated"]=True
            if missing>10:self._tracks.pop(tid,None)

    def on_input(self,command,status,error=None):
        if command.action_type=="STOP":return
        self._enqueue("input",{"source":command.source,"track_id":command.track_id,"skill_id":command.skill_id,"target":command.target,"error":error},action=command.action_type,status=status)
        if status!="sent":return
        if command.action_type in {"MOVE","DODGE"}:
            self.suspend()
        elif command.action_type in {"ATTACK","USE_SKILL"} and command.track_id in self._tracks:
            self._tracks[command.track_id]["attack_at"]=time.monotonic()

    def _flush_locked(self,c):
        with self._queue_lock:
            rows=list(self._pending);self._pending.clear()
        try:
            with c:
                for row in rows:
                    if row[4]=="encounter":
                        payload=json.loads(row[7])
                        c.execute("INSERT INTO encounters VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,payload=excluded.payload",(row[0],row[1],row[2],payload["kind"],row[3],row[7]))
                    else:c.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)",row)
            self.error=None
            if rows:self.last_saved_at=time.time()
        except Exception:
            with self._queue_lock:self._pending.extendleft(reversed(rows))
            raise

    def flush(self):
        try:
            with self._db_lock, closing(self._connect()) as c:self._flush_locked(c)
        except (OSError,sqlite3.Error) as exc:
            self.error=str(exc)

    def snapshot(self,day=None):
        if day is not None:
            if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:raise ValueError("통계 날짜는 YYYY-MM-DD 필요")
        with self._db_lock,closing(self._connect()) as c:
            self._flush_locked(c)
            def summary(filter_day):
                where=" WHERE day=?" if filter_day else ""
                args=(filter_day,) if filter_day else ()
                objects={k:0 for k in KINDS}
                objects.update(dict(c.execute("SELECT kind,count(*) FROM encounters"+where+" GROUP BY kind",args)))
                actions={}
                for action,status,count in c.execute("SELECT action,status,count(*) FROM events"+(" WHERE day=? AND kind='input'" if filter_day else " WHERE kind='input'")+" GROUP BY action,status",args):
                    actions.setdefault(action,{})[status]=count
                event_counts=dict(c.execute("SELECT kind,count(*) FROM events"+where+" GROUP BY kind",args))
                return {"objects":objects,"encounters":sum(objects.values()),"actions":actions,"kills_confirmed":event_counts.get("kill_confirmed",0),"kills_estimated":event_counts.get("kill_estimated",0),"events":event_counts}
            selected=day or self._day(time.time())
            result={"today":self._day(time.time()),"selected_day":selected,"total":summary(None),"day":summary(selected),"days":[r[0] for r in c.execute("SELECT day FROM encounters UNION SELECT day FROM events ORDER BY day DESC LIMIT 90")],"recent":[{"id":r[0],"at":r[1],"kind":r[2],"action":r[3],"status":r[4],**json.loads(r[5])} for r in c.execute("SELECT id,at,kind,action,status,payload FROM events ORDER BY at DESC LIMIT 40")],"kill_candidates":[{"id":r[0],"at":r[1],**json.loads(r[2])} for r in c.execute("SELECT id,at,payload FROM events WHERE kind='kill_estimated' ORDER BY at DESC LIMIT 20")],"pending":0,"last_saved_at":self.last_saved_at,"error":self.error,"storage":"statistics.sqlite3","kill_detection":"공격 후 미검출은 추정. 확인 처치는 사용자 확인 기준."}
            return result

    def confirm_kill(self,event_id):
        if not isinstance(event_id,str) or len(event_id)>100:raise ValueError("유효한 추정 처치 기록 ID 필요")
        with self._db_lock,closing(self._connect()) as c:
            self._flush_locked(c)
            row=c.execute("SELECT kind,payload FROM events WHERE id=?",(event_id,)).fetchone()
            if not row or row[0] not in {"kill_estimated","kill_confirmed"}:raise ValueError("추정 처치 기록을 선택하세요.")
            if row[0]=="kill_estimated":
                payload=json.loads(row[1]);payload["confirmed_by"]="user";payload["confirmed_at"]=time.time()
                with c:c.execute("UPDATE events SET kind='kill_confirmed',payload=? WHERE id=?",(json.dumps(payload,ensure_ascii=False),event_id))
                self.last_saved_at=time.time()
        return self.snapshot()
