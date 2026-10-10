"""Resolve only profile-approved keys at the execution boundary."""
def binding_for_command(settings, command):
    if command.action_type=='INTERACT' and command.source=='SCREEN_SPACE_PROMPT':
        return 'SPACE'
    if command.action_type=='ATTACK' and command.reason in {'STATIONARY_RIGHT_CLICK','STATIONARY_HOLD','STATIONARY_POST_DEATH','STATIONARY_TARGET_GRACE','STATIONARY_MODE_HOLD'}:
        return 'mouse_right'
    if command.action_type=='DODGE' and command.source in {'NAVIGATION_ESCAPE','SCREEN_ARROW_ESCAPE'}:
        skill=settings.get('movement_skill',{})
        if not skill.get('enabled',False):raise ValueError('이동 스킬이 등록되지 않았거나 비활성 상태입니다.')
        return skill['key']
    if command.skill_id:
        skill = next((s for s in settings.get("attack_skills", []) if s["id"] == command.skill_id and s["enabled"]), None)
        if not skill or command.action_type != "USE_SKILL":
            raise ValueError("등록되지 않거나 비활성인 공격 스킬입니다.")
        return skill["key"]
    return settings["bindings"][command.action_type]
