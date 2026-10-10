from __future__ import annotations

import asyncio
import configparser
import argparse

from app.ai.agent_registry import create_agent
from app.profiles.agent_runtime import load_agent_runtime
from app.core.screen_capture import ScreenCapture
from app.controller.input_controller_factory import create_input_controller
from app.core.action_executor import ActionExecutor
from app.core.action_scheduler import ActionScheduler
from app.core.logger import setup_logging
from app.vision.qwen_vl_client import QwenVLClient
from app.profiles.registry import create_game_profile
from app.profiles.profile_store import PROJECT_ROOT, ProfileStore
from app.profiles.runtime_settings import ensure_runtime_settings


async def main() -> None:
    parser = argparse.ArgumentParser(description="대화형 Visual Game Agent")
    parser.add_argument("--no-chat", action="store_true", help="대화 콘솔 없이 실행")
    parser.add_argument(
        "--web",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="비공개 웹 연결 서버 기본 실행 (끄려면 --no-web, 게임별 기능은 버튼·핫키로 실행)",
    )
    parser.add_argument("--web-port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true", help="로컬 대시보드 자동 열기 끄기")
    parser.add_argument("--chat-max-tokens", type=int, help="프로필 대화 응답 토큰 제한 (config.ini보다 우선)")
    args = parser.parse_args()
    if args.chat_max_tokens is not None and args.chat_max_tokens <= 0:
        parser.error("--chat-max-tokens는 양수여야 합니다.")
    cfg = configparser.ConfigParser()
    if not cfg.read(PROJECT_ROOT / "config.ini", encoding="utf-8"):
        raise FileNotFoundError("config.ini")

    setup_logging(cfg.get("LOG", "level", fallback="INFO"))

    game_profile = cfg.get("GAME", "profile", fallback="").strip().lower()
    from app.profiles.game_catalog import active_game
    profile = create_game_profile(active_game(game_profile))
    ensure_runtime_settings(profile.profile_dir)
    profile_docs, _ = ProfileStore(profile.profile_dir).snapshot()
    runtime = load_agent_runtime(profile.profile_dir).get('agent', {})
    configured_titles = [
        x.strip()
        for x in cfg.get("CAPTURE", "window_titles", fallback="").split(",")
        if x.strip()
    ]
    window_titles = (
        profile_docs["vision.json"]["window_titles"]
        or configured_titles
        or []
    )

    capture = ScreenCapture(
        output_idx=cfg.getint("CAPTURE", "output_idx", fallback=0),
        mode=cfg.get("CAPTURE", "mode", fallback="window"),
        window_titles=window_titles,
        fallback_to_screen=cfg.getboolean(
            "CAPTURE", "fallback_to_screen", fallback=False
        ),
    )

    vl = QwenVLClient(
        endpoint=cfg.get("QWEN_VL", "endpoint"),
        model=cfg.get("QWEN_VL", "model", fallback="Qwen3-VL-2B-Instruct"),
        timeout=cfg.getfloat("QWEN_VL", "timeout", fallback=15.0),
        max_tokens=cfg.getint("QWEN_VL", "max_tokens", fallback=320),
        image_width=cfg.getint("QWEN_VL", "image_width", fallback=1280),
        jpeg_quality=cfg.getint("QWEN_VL", "jpeg_quality", fallback=80),
    )

    chat_vl = QwenVLClient(
        endpoint=cfg.get("QWEN_VL", "endpoint"),
        model=cfg.get("QWEN_VL", "model"),
        timeout=cfg.getfloat("PROFILE_CHAT", "timeout", fallback=120),
        max_tokens=args.chat_max_tokens if args.chat_max_tokens is not None else cfg.getint("PROFILE_CHAT", "max_tokens", fallback=4096),
        image_width=cfg.getint("QWEN_VL", "image_width", fallback=960),
    )

    controller = create_input_controller(
        cfg.get("INPUT", "backend", fallback="mock"),
        capture=capture,
        settings_provider=lambda: agent._docs["input.json"],
        esp32_options={
            "port": cfg.get("ESP32", "port", fallback=""),
            "baudrate": cfg.getint("ESP32", "baudrate", fallback=115200),
            "timeout": cfg.getfloat("ESP32", "timeout", fallback=0.7),
        },
    )
    executor = ActionExecutor(controller)
    scheduler = ActionScheduler(executor)

    agent = create_agent(
        capture=capture,
        vl=vl,
        scheduler=scheduler,
        interval=cfg.getfloat("QWEN_VL", "interval", fallback=1.0),
        min_intent_confidence=cfg.getfloat(
            "AGENT",
            "min_intent_confidence",
            fallback=0.60,
        ),
        hud_retry_interval=cfg.getfloat("AGENT", "hud_retry_interval", fallback=5.0),
        summary_interval=cfg.getfloat("LOG", "summary_interval", fallback=2.0),
        profile=profile,
        chat_vl=chat_vl,
        console_enabled=cfg.getboolean("AGENT", "console_enabled", fallback=True)
        and not args.no_chat,
        reaction_interval=runtime.get('reaction_interval',cfg.getfloat("AGENT", "reaction_interval", fallback=0.05)),
        capture_fps=profile_docs["vision.json"].get(
            "capture_fps", cfg.getfloat("AGENT", "capture_fps", fallback=60.0)
        ),
        hud_interval=runtime.get('hud_interval',cfg.getfloat("AGENT", "hud_interval", fallback=0.05)),
        intent_interval=cfg.getfloat("AGENT", "intent_interval", fallback=1.0),
        intent_cache_ttl=cfg.getfloat("AGENT", "intent_cache_ttl", fallback=3.0),
        yolo_config={
            **profile_docs["vision.json"]["yolo"],
            "model_path": str(
                PROJECT_ROOT / profile_docs["vision.json"]["yolo"]["model_path"]
            ),
        },
    )

    agent.hud_mode=runtime.get('hud_mode',cfg.getboolean('AGENT','hud_mode',fallback=True))
    agent.shared_movement_mode=True
    if hasattr(agent,'minimap_memory'):
        agent.minimap_memory.registration_reset_seconds=max(2,min(300,runtime.get('minimap_reset_wait_seconds',30)))
    agent._move_only=True
    print('[MODE] 공통 핫키/이동/사냥 · HUD 읽기 ' + ('켜짐' if agent.hud_mode else '꺼짐'))
    bridge = None
    if args.web:
        from app.web.bridge import AgentBridge

        agent._paused = not getattr(agent,"default_auto_hunt",False)
        bridge = AgentBridge(agent, port=args.web_port)
    await scheduler.start()
    try:
        if bridge:
            await bridge.start()
            if bridge.dashboard.available and not args.no_browser:
                try:
                    await bridge.open_dashboard_if_needed()
                except Exception as exc:
                    print(f"[WEB] 브라우저에서 로컬 연결 주소를 열어주세요: {exc}")
        await agent.run()
    except KeyboardInterrupt:
        print("\n[Main] KeyboardInterrupt")
    finally:
        agent.stop()
        if bridge:
            await bridge.close()
        await scheduler.stop()
        await executor.close()
        vl.close()
        chat_vl.close()
        capture.close()
        print("[Main] stopped")


if __name__ == "__main__":
    asyncio.run(main())
