# Controller·전투·Qwen 활성화 대기 변경

수정 대상은 `C:\ai\project\Visual-Game-Agent-non_YOLO`입니다.

## 동작

- 게임 창 비활성 동안 Qwen 시작 보정, 자동 장면 분석, 사용자 명령 분석을 대기합니다. 창 활성화 후 최신 캡처로 명령을 처리합니다. 대기 중 중단·설정 변경으로 무효화된 명령은 취소합니다. 이미 전송한 HTTP 요청을 중간에 취소하는 기능은 아닙니다.
- Controller에 키보드와 마우스를 표시하고 실제 전송한 입력, 우클릭 유지 상태, 클릭 좌표를 표시합니다. 전역 키보드 입력 감시 기능은 아닙니다.
- 이동과 공격에서 HUD·플레이어 영역을 제외하고 실제 입력 직전에 재검증합니다. 플레이어 HP바의 최신 위치로 몸체 영역을 보완합니다.
- 자동 장면 분석은 폭 512px, 객체 최대 2개, 출력 최대 640토큰, 제한시간 8초로 줄였습니다. 이는 요청 예산이며 실게임 응답시간 측정값이 아닙니다.
- 최신 플레이어 HP바와 이전 플레이 판정을 활용하여 재분석 중 이동을 이어가고, 같은 적의 검증으로 우클릭 유지 시간을 갱신합니다.
- 등록 버프 아이콘이 5초 연속 없으면 버프 키를 한 번 누릅니다. 계속 없으면 5초마다 재시도하며, 아이콘이 확인되면 멈춥니다. HP·전경·입력 안전 조건을 통과해야 전송합니다.

## 변경 파일

- `app/ai/non_yolo_agent.py`, `app/ai/visual_agent.py`
- `app/vision/click_safety.py`, `app/vision/buff_monitor.py`, `app/vision/qwen_vl_client.py`
- `app/core/local_navigation.py`, `app/core/action_executor.py`, `app/core/action_scheduler.py`
- `app/controller/esp32_input_controller.py`, `app/controller/win32_input_controller.py`
- `Visual-Agent-Lab-local/app/input-monitor.tsx`, `Visual-Agent-Lab-local/app/controller-input.css`, `Visual-Agent-Lab-local/app/live-agent.tsx`
- `tests/test_non_yolo.py`, `BASIC_COMBAT.md`, `README_BUFF_MOVEMENT_RECOVERY.md`
- 웹 빌드 결과 `Visual-Agent-Lab-local/dist/`

## 실행·검증 결과

- `python -m unittest discover -s tests -p test_non_yolo.py -q`: 42개 통과.
- `python -`로 관련 unittest 묶음을 실행: 65개 통과. 공격 유지·ESP32·이동·전투 관련 테스트를 포함합니다.
- `python -`로 2300×1800 화면에 실제 등록 버프 이미지를 삽입하여 축소 검색 확인: 통과.
- 웹 폴더에서 `npm run typecheck`: 성공.
- 웹 폴더에서 `npm test`: 4개 통과.
- 웹 폴더에서 `npm run build`: 성공. 500kB 초과 번들 경고가 있습니다.

## 적용·미확인 범위

Agent를 재시작하고 `http://127.0.0.1:8766/#game`의 로컬 대시보드를 새로고침해야 합니다. 외부 Site의 페이지는 배포하지 않았습니다.
이번 변경 후 브라우저 화면 및 실제 게임의 좌표·버프 정확도와 사냥 속도는 확인하지 않았습니다.
