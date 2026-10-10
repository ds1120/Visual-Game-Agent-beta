"""Deterministic reactions; no model calls or persistent writes here."""

from __future__ import annotations
import time
import math
from app.core.action_command import ActionCommand


def command(
    action,
    *,
    target=None,
    direction=None,
    track_id=None,
    source="PROFILE_RULE",
    reason="",
    duration_ms=180,
    epoch=0,
    skill_id=None,
    cooldown_seconds=None,
):
    now = time.monotonic()
    priority = {
        "STOP": 95,
        "USE_POTION": 100,
        "DODGE": 80,
        "ATTACK": 50,
        "TAKE": 40,
        "MOVE": 30,
    }.get(action, 20)
    cooldown = {"USE_POTION": 1, "USE_SKILL": 1, "CAST_BUFF": 1, "ATTACK": 0.15, "TAKE": 0.5, "DODGE": 0.6}.get(
        action, 0
    )
    return ActionCommand(
        action,
        priority,
        now,
        None if action == 'STOP' else now + 0.5,
        cooldown if cooldown_seconds is None else cooldown_seconds,
        False,
        source,
        reason,
        target,
        direction,
        track_id,
        duration_ms,
        epoch,
        skill_id,
    )


class FastPolicy:
    @staticmethod
    def entry(obj, docs):
        records = docs.get(
            "monsters.json" if obj.object_type == "monster" else "items.json", {}
        ).get("entries", [])
        return next(
            (
                r
                for r in records
                if (obj.memory_id is not None and r.get("memory_id") == obj.memory_id)
                or obj.name
                and r["name"] == obj.name
            ),
            {},
        )

    def decide(
        self, objects, hud, docs, shape, directive=None, epoch=0, potion_ready=True, preferred_track_id=None, combat_on_move=False
    ):
        policy = docs.get("hunting.json", {}).get("policy", {})
        if not hud.health_valid or hud.health is None or hud.health <= 0:
            return command("STOP", reason="HUD_INVALID_OR_DEAD", epoch=epoch)
        if hud.health < policy.get("potion_hp_threshold", 30) and potion_ready:
            return command(
                "USE_POTION",
                source="HP_EMERGENCY",
                reason=f"hp={hud.health:.1f}%",
                epoch=epoch,
            )
        known = [
            o
            for o in objects
            if o.status == "confirmed" and o.semantic_confidence >= 0.6
        ]

        def usable(o):
            entry = self.entry(o, docs)
            if o.object_type == "monster":
                return (
                    o.relation == "hostile"
                    and entry.get("relation", o.relation) == "hostile"
                )
            return (
                o.object_type == "item"
                and policy.get("pickup_enabled", False)
                and entry.get("pickup", True)
            )

        targets = [o for o in known if usable(o)]
        h, w = shape[:2]

        def center(o):
            return ((o.bbox[0] + o.bbox[2]) / 2 / w, (o.bbox[1] + o.bbox[3]) / 2 / h)

        def score(o):
            r = self.entry(o, docs)
            x, y = center(o)
            order = policy.get("target_priority", [])
            kind = r.get("rank", "normal")
            rank = (len(order) - order.index(kind)) * 100 if kind in order else 0
            return (
                0 if o.object_type == "monster" else 1,
                0 if o.track_id == preferred_track_id else 1,
                -r.get("priority", 0) - rank,
                math.hypot(x - 0.5, y - 0.5),
            )

        danger = [o for o in targets if o.object_type == "monster"]
        if danger and hud.health < policy.get("retreat_hp_threshold", 20):
            target = min(danger, key=score)
            x, y = center(target)
            return command(
                "DODGE", direction=(0.5 - x, 0.5 - y), source="HP_RETREAT", epoch=epoch
            )
        if hud.health < policy.get("potion_hp_threshold", 30) and not potion_ready:
            return command("STOP", reason="POTION_COOLDOWN_WAIT", epoch=epoch)
        if directive:
            action = directive.get("action")
            if action in {"USE_SKILL", "CAST_BUFF"}:
                return command(action, source="USER_COMMAND", reason="Qwen skill directive", epoch=epoch)
            if action == "MOVE":
                if combat_on_move and danger:
                    target = min(danger, key=score)
                    return command("ATTACK", target=center(target), track_id=target.track_id,
                                   source="ENCOUNTER_COMBAT", reason="confirmed enemy during move", epoch=epoch)
                return command(
                    "MOVE",
                    direction=tuple(directive["direction"]),
                    source="USER_COMMAND",
                    reason="short move",
                    epoch=epoch,
                )
            if action in {"ATTACK", "TAKE", "INTERACT"}:
                target = next(
                    (o for o in known if o.track_id == directive.get("track_id")), None
                )
                valid = target and (
                    (
                        action == "ATTACK"
                        and target in targets
                        and target.object_type == "monster"
                    )
                    or (
                        action == "TAKE"
                        and target in targets
                        and target.object_type == "item"
                    )
                    or (
                        action == "INTERACT"
                        and target.object_type == "npc"
                        and target.relation in {"neutral", "friendly"}
                    )
                )
                if not valid:
                    return command(
                        "STOP", reason="USER_TARGET_MISSING_OR_NOT_ALLOWED", epoch=epoch
                    )
                return command(
                    action,
                    target=center(target),
                    track_id=target.track_id,
                    source="USER_COMMAND",
                    epoch=epoch,
                )
        target = min(targets, key=score) if targets else None
        if target:
            if target.object_type == "monster" and hud.health < policy.get(
                "retreat_hp_threshold", 20
            ):
                x, y = center(target)
                return command(
                    "DODGE",
                    direction=(0.5 - x, 0.5 - y),
                    source="HP_RETREAT",
                    epoch=epoch,
                )
            return command(
                "ATTACK" if target.object_type == "monster" else "TAKE",
                target=center(target),
                track_id=target.track_id,
                epoch=epoch,
            )
        return command("STOP", reason="NO_KNOWN_TARGET", epoch=epoch)
