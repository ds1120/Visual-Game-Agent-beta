"""Validated, optimistic, profile-local JSON edits; the model never gets file access."""

from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

PROFILE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PROFILE_ROOT.parent.parent
FILES = (
    "knowledge.json",
    "monsters.json",
    "items.json",
    "hunting.json",
    "object_memory.json",
    "hud.json",
    "navigation.json",
    "input.json",
    "vision.json",
)
ALIASES = {
    "디아블로 iv": "diablo4",
    "디아블로4": "diablo4",
    "diablo iv": "diablo4",
    "diablo 4": "diablo4",
}


def resolve_profile_dir(name: str, root: Path = PROFILE_ROOT) -> Path:
    slug = ALIASES.get(name.strip().lower(), name.strip().lower())
    if (
        not slug
        or not slug.isascii()
        or not slug.replace("_", "").isalnum()
        or slug.startswith("_")
    ):
        raise ValueError("프로필 이름은 등록된 폴더 이름이어야 합니다.")
    path = (root / slug).resolve()
    if path.parent != root.resolve() or not path.is_dir():
        raise ValueError(f"존재하지 않는 프로필: {slug}")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


@contextmanager
def profile_lock(directory: Path):
    """OS locks are released even after a crashed process; works on Windows/Linux."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".profile.lock").open("a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _number(v, lo, hi):
    return type(v) in (int, float) and math.isfinite(v) and lo <= v <= hi


def validate_document(filename: str, data: dict):
    if filename not in FILES or not isinstance(data, dict):
        raise ValueError("지원하지 않는 프로필 문서입니다.")
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 2_000_000:
        raise ValueError("프로필 문서가 너무 큽니다.")
    if filename == "knowledge.json":
        for key in (
            "semantics",
            "relation_rules",
            "intent_rules",
            "intent_policy",
            "policy",
        ):
            if key in data and not isinstance(data[key], dict):
                raise ValueError(f"{key}: object 필요")
        for key in ("semantic_rules", "named_entities"):
            if key in data and not isinstance(data[key], list):
                raise ValueError(f"{key}: array 필요")
        return
    if filename == "object_memory.json":
        if data.get("version") != 3 or not isinstance(data.get("objects"), list):
            raise ValueError("ObjectMemory v3 필요")
        seen = set()
        for o in data["objects"]:
            if (
                not isinstance(o, dict)
                or type(o.get("memory_id")) is not int
                or o["memory_id"] in seen
            ):
                raise ValueError("잘못되거나 중복된 memory_id")
            seen.add(o["memory_id"])
            if o.get("label") not in {"monster", "npc", "item", "player", "obstacle"}:
                raise ValueError("지원하지 않는 semantic label")
            if o.get("relation", "unknown") not in {
                "hostile",
                "friendly",
                "neutral",
                "unknown",
            }:
                raise ValueError("잘못된 relation")
            if (
                not _number(o.get("confidence"), 0, 1)
                or type(o.get("locked", False)) is not bool
            ):
                raise ValueError("confidence/locked 형식 오류")
            if o.get("status", "confirmed") not in {"provisional", "confirmed"}:
                raise ValueError("status 형식 오류")
            if not isinstance(o.get("appearances"), list):
                raise ValueError("appearances array 필요")
            for a in o["appearances"]:
                if (
                    not isinstance(a, dict)
                    or not isinstance(a.get("fingerprint"), list)
                    or not all(_number(v, -1, 1) for v in a["fingerprint"])
                ):
                    raise ValueError("fingerprint 형식 오류")
        return
    if filename in {"hud.json", "navigation.json", "input.json", "vision.json"}:
        from app.profiles.runtime_settings import validate_settings

        validate_settings(filename, data)
        return
    if data.get("version") != 1 or not isinstance(
        data.get("entries" if filename != "hunting.json" else "methods"), list
    ):
        raise ValueError("version=1과 기록 배열이 필요합니다.")
    entries = data.get("entries", data.get("methods", []))
    seen = set()
    for item in entries:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in seen
            or not isinstance(item.get("name"), str)
        ):
            raise ValueError("기록에 고유 id와 name이 필요합니다.")
        seen.add(item["id"])
        if "memory_id" in item and (
            type(item["memory_id"]) is not int or item["memory_id"] < 1
        ):
            raise ValueError("memory_id 양의 정수 필요")
        if "relation" in item and item["relation"] not in {
            "hostile",
            "friendly",
            "neutral",
            "unknown",
        }:
            raise ValueError("relation 형식 오류")
        if "priority" in item and not _number(item["priority"], 0, 100):
            raise ValueError("priority 범위 0~100")
        if "pickup" in item and type(item["pickup"]) is not bool:
            raise ValueError("pickup boolean 필요")
    if filename == "hunting.json":
        policy = data.get("policy")
        if not isinstance(policy, dict):
            raise ValueError("hunting policy 필요")
        for key in ("potion_hp_threshold", "retreat_hp_threshold"):
            if not _number(policy.get(key), 0, 100):
                raise ValueError(f"{key}: 0~100 필요")
        if type(policy.get("pickup_enabled")) is not bool:
            raise ValueError("pickup_enabled boolean 필요")
        if not isinstance(policy.get("target_priority"), list) or not all(
            isinstance(x, str) for x in policy["target_priority"]
        ):
            raise ValueError("target_priority string array 필요")


def _patch(data: dict, operation: dict):
    op, path = operation.get("op"), operation.get("path")
    if (
        op not in {"add", "replace", "remove"}
        or not isinstance(path, str)
        or not path.startswith("/")
        or path == "/"
    ):
        raise ValueError("add/replace/remove 및 JSON pointer 필요")
    parts = [x.replace("~1", "/").replace("~0", "~") for x in path[1:].split("/")]
    if any(x in {"__proto__", "constructor", "prototype"} for x in parts):
        raise ValueError("잘못된 경로")
    parent = data
    for part in parts[:-1]:
        if isinstance(parent, list):
            if not part.isdigit() or int(part) >= len(parent):
                raise ValueError("배열 경로 없음")
            parent = parent[int(part)]
        elif isinstance(parent, dict) and part in parent:
            parent = parent[part]
        else:
            raise ValueError("경로 없음")
    key = parts[-1]
    value = None
    if op != "remove":
        raw = operation.get("value_json")
        if not isinstance(raw, str):
            raise ValueError("value_json string 필요")
        value = json.loads(
            raw, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v))
        )
    if isinstance(parent, list):
        if key == "-" and op == "add":
            parent.append(value)
            return
        if not key.isdigit():
            raise ValueError("배열 인덱스 필요")
        i = int(key)
        if op == "add" and i <= len(parent):
            parent.insert(i, value)
        elif i >= len(parent):
            raise ValueError("배열 경로 없음")
        elif op == "remove":
            parent.pop(i)
        else:
            parent[i] = value
    elif isinstance(parent, dict):
        if op != "add" and key not in parent:
            raise ValueError("경로 없음")
        if op == "remove":
            del parent[key]
        else:
            parent[key] = value
    else:
        raise ValueError("수정할 object/array 없음")


class ProfileStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()

    def snapshot(self):
        with profile_lock(self.directory):
            docs = {}
            revisions = {}
            for filename in FILES:
                path = self.directory / filename
                if path.exists():
                    docs[filename] = json.loads(path.read_text(encoding="utf-8"))
                    revisions[filename] = digest(path)
                    validate_document(filename, docs[filename])
            return docs, revisions

    def apply(self, operations: list, revisions: dict, before_commit=None):
        if not isinstance(operations, list) or len(operations) > 40:
            raise ValueError("최대 40개 변경만 가능합니다.")
        if not operations:
            return []
        with profile_lock(self.directory):
            docs = {}
            previous = {}
            for operation in operations:
                if not isinstance(operation, dict):
                    raise ValueError("변경 형식 오류")
                filename = operation.get("file")
                if filename not in FILES:
                    raise ValueError("profiles 내 허용된 JSON만 수정할 수 있습니다.")
                path = self.directory / filename
                if filename not in docs:
                    if digest(path) != revisions.get(filename):
                        raise ValueError(
                            "프로필이 다른 작업에서 변경되었습니다. 다시 질문해 주세요."
                        )
                    previous[filename] = path.read_bytes()
                    docs[filename] = json.loads(previous[filename])
                pointer = operation.get("path", "")
                if pointer in {"/version"}:
                    raise ValueError("파일 버전은 변경할 수 없습니다.")
                if filename == "object_memory.json":
                    parts = pointer.split("/")
                    if (
                        len(parts) != 4
                        or parts[1] != "objects"
                        or not parts[2].isdigit()
                        or parts[3]
                        not in {
                            "label",
                            "relation",
                            "confidence",
                            "locked",
                            "name",
                            "notes",
                            "status",
                        }
                    ):
                        raise ValueError(
                            "ObjectMemory는 의미 필드만 수정할 수 있습니다."
                        )
                _patch(docs[filename], operation)
            for filename, data in docs.items():
                validate_document(filename, data)
            if before_commit is not None:
                before_commit(docs)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup_dir = self.directory / "_backups" / stamp
            backup_dir.mkdir(parents=True)
            for filename, old in previous.items():
                (backup_dir / filename).write_bytes(old)
            written = []
            try:
                for filename, data in docs.items():
                    atomic_json(self.directory / filename, data)
                    written.append(filename)
            except Exception:
                for filename in written:
                    atomic_json(
                        self.directory / filename, json.loads(previous[filename])
                    )
                raise
            return written

    def prompt_context(self):
        docs, _ = self.snapshot()
        return json.dumps(
            {k: v for k, v in docs.items() if k != "object_memory.json"},
            ensure_ascii=False,
            separators=(",", ":"),
        )


def migrate_legacy_memory(directory: Path):
    """Copy once, preserve the original as a backup; each game gets independent memory."""
    legacy = PROJECT_ROOT / "data" / "object_memory.json"
    target = directory / "object_memory.json"
    with profile_lock(directory):
        if not target.exists() and legacy.exists() and directory.name == "diablo4":
            data = json.loads(legacy.read_text(encoding="utf-8"))
            validate_document("object_memory.json", data)
            atomic_json(target, data)
