NEW GAME PROFILE

Copy app/profiles/generic to app/profiles/<game>.
Update profile.py name and implement game-specific OpenCV HUD sensors.
Keep knowledge.json, object_memory.json, monsters.json, items.json, hunting.json here.
Provide create_profile() in profile.py. Set [GAME] profile=<game>.
YOLO only locates objects. Qwen-VL owns semantics/Intent. Python validates current evidence and executes.
Use py -m tools.profile_chat --profile <game> to edit game JSON by conversation.
See ROLE_AND_PROFILE_GUIDE.md for schema and commands.
