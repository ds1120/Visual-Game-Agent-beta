"""Resolve only profile-approved keys at the execution boundary."""
def binding_for_command(settings, command):
    if command.skill_id:
        skill = next((s for s in settings.get("attack_skills", []) if s["id"] == command.skill_id and s["enabled"]), None)
        if not skill or command.action_type != "USE_SKILL":
            raise ValueError("등록되지 않거나 비활성인 공격 스킬입니다.")
        return skill["key"]
    return settings["bindings"][command.action_type]
