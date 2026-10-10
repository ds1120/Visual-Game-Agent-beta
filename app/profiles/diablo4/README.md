# 디아블로4 프로파일

2026-10-11까지 작업한 디아블로4 구현을 이 폴더에 모았습니다.

## 파일 구성

- `agent.py`: 이동, 화면 화살표·안내선, 미니맵 경로, 몬스터 HP바, 제자리사냥, 반복스킬, Qwen 장면 분석과 웹 상태를 포함하는 디아블로4 실행 코드.
- `runtime.json`: 이 게임의 HUD 읽기 여부·주기, 반응 주기, 미니맵 등록 초기화 대기, 버프 매칭 및 장면 분석 설정. 프로그램 재시작 시 적용.
- `GAMEPLAY.md`: 현재 기능과 게임 규칙. 저장된 JSON 설정이 Markdown 기본값보다 우선.
- `profile.py`, `health_sensor.py`, `buff_sensor.py`, `hud_layout.py`: 디아블로4 HUD 측정.
- `input.json`: 실제 키 바인딩, 활성 스킬과 반복 간격.
- `navigation.json`: 미니맵 ROI, 해상도, 투영, 통로·벽 간격.
- `vision.json`: 캡처 FPS, 창 제목, 인식 설정.
- `hud.json`, `assets/`: HUD 위치·보정 결과와 이미지.
- `assets/navigation/`: 디아블로4 화면 화살표 매칭에 사용하는 원본·흰색·꺾임 템플릿. 공통 vision 폴더에서 이곳으로 이동.
- `knowledge.json`, `monsters.json`, `items.json`, `object_memory.json`, `scene_memory.json`: 게임 지식과 학습 기억.
- `statistics.sqlite3`, `_backups/`: 실행 통계 및 프로파일 편집 이력. 삭제·초기화하지 않음.

## 실행 경계

`app/main.py`는 `app/ai/agent_registry.py`를 통해 선택한 게임의 Agent만 생성합니다.
`app/ai/main_agent.py`는 기존 코드와 테스트를 위한 디아블로4 호환 import입니다. 새 게임에서 상속하거나 호출하지 않습니다.

공통 캡처, ESP32 전송, 실행 검증/스케줄러, 핫키 수신, RELEASE, 웹 연결, 프로파일 저장과 모델 연결은 기존 공통 모듈에 남습니다.
게임을 다른 프로파일로 변경하면 입력을 정지하고 선택을 저장합니다. 게임별 Agent 교체는 프로그램 재시작 후 적용됩니다.

디아블로4의 JSON, 이미지, 기억과 통계는 디아블로2로 복사하지 않습니다.

`runtime.json`은 시작 시 읽는 실행 설정이며 현재 웹 JSON 편집 목록에는 포함되지 않습니다. 이 파일을 바꾼 뒤에는 프로그램을 재시작합니다.
