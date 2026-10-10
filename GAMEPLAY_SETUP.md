# 게임별 이동·회피·공격 설정

## 적용

1. GameBot Agent를 중단하고 재시작합니다. 로컬 대시보드 `http://127.0.0.1:8766/#game`을 새로고침합니다.
2. 게임 설정의 **게임별 이동·회피·전투 → 전투 거리 방식**에서 원거리 캐릭터 또는 근거리 캐릭터를 선택하고 저장합니다. 초기값은 기존 동작을 보존하는 현재 대상 공격 유지입니다.
3. 원거리는 화면 높이 기준 최소 거리 18%, 최대 공격 거리 45%가 초기값입니다. 근거리는 공격 거리 16%입니다. 실제 스킬 사거리와 카메라 배율에 맞춰 조정합니다. 원거리 최대 거리는 최소 거리보다 6% 이상 커야 합니다.
4. 미니맵 기록의 하늘색 경로가 통로 중앙인지, 흰점이 미니맵 캐릭터 위치와 맞는지 확인합니다. 다르면 ROI·플레이어 X/Y·회전·지형 모드를 조정합니다.
5. 복구 실패 후에는 중단하고 위치·지도 설정을 확인한 뒤 시작/재개합니다. 로컬 지도와 실제 이동 성공 여부로 복구합니다.

## 게임 선택과 MD 자동 적용

게임 선택 시 해당 폴더의 `GAMEPLAY.md`와 JSON 설정을 함께 적용합니다. 시작 시에도 선택된 프로필의 MD를 불러옵니다.

- IV: `app/profiles/diablo4/GAMEPLAY.md`
- II: `app/profiles/diablo2/GAMEPLAY.md`
- 새 게임: 범용 템플릿에서 자기 폴더의 `GAMEPLAY.md`를 생성합니다. 다른 게임의 사용자 문서나 기억을 복사하지 않습니다.

`BASIC_COMBAT.md`는 공통 규칙만 담습니다. 게임별 지도 표현·적 HP 기준·회피·공격 규칙은 선택한 MD에서 Qwen에 제공합니다. 게임 전환은 이전 문서 캐시와 거리 유지 상태를 교체하고 일시정지를 유지합니다. 문서 수정은 다음 장면 요청에서 감지합니다. 자동 분석에서는 두 문서를 합쳐 최대 700자로 요약하며 MD 때문에 Qwen 호출을 추가하지 않습니다.

MD의 `gameplay-settings` 코드 블록은 `input.json`과 `navigation.json`에 누락된 기본값을 보완합니다. 기존 JSON의 캐릭터별 사거리·전투 방식·스킬 키·HUD 보정은 우선 유지합니다. MD 기본값 수정은 다음 프로필 적용 때 검사하며, 이미 저장된 값의 변경은 같은 프로필의 JSON 또는 웹 설정에서 합니다. 일반 문장은 Qwen 판단 지침이며 코드의 HP·입력 검증을 변경하지 않습니다. 잘못된 기본값·지원하지 않는 문서·닫히지 않은 코드 블록은 거부합니다.

상태 API의 `gameplay_spec`에는 현재 프로필·적용 문서·문서 리비전을 표시합니다. 실제 게임과 브라우저 UI는 이 변경에서 실행하지 않았습니다.

변경 파일: `app/profiles/gameplay_spec.py`, `app/profiles/gameplay_presets.py`, `app/ai/main_agent.py`, 세 프로필의 `GAMEPLAY.md`, `BASIC_COMBAT.md`, `tests/test_gameplay_presets.py`, `tests/test_main_agent.py`, `tests/test_game_catalog.py`, 이 문서입니다. MD 기본값·잘못된 값 거부·게임 전환·문서 캐시·새 게임 독립 문서 검사를 추가했습니다.

MD 자동 적용 변경의 최종 검사: 전체 Python 272개 실행, 28.884초, `OK (skipped=2)`. 최종 명령은 아래의 동일한 unittest/진단 명령이며 로그는 임시 폴더의 `visual-game-agent-profile-md-tests.log`에 기록했습니다. 관련 검사 묶음 51개도 통과했습니다. `git diff --check`를 통과했습니다. 이번 변경은 웹 소스를 수정하지 않아 웹 빌드를 반복하지 않았습니다.

## 게임별 초기 설정

| 항목 | 디아블로 IV | 디아블로 II 준비 프로필 (`ii`) |
|---|---|---|
| 기본 공격 | 기존 우클릭 유지 | 클릭 공격, 실제 스킬 키 확인 필요 |
| 이동 | 클릭, 기본 최대 거리 30% | 상속된 WASD 기본값이면 클릭으로 변경 |
| 회피 | 설정된 키 | 이동 후퇴, Space 전송 안 함 |
| 미니맵 | 우상단 밝은 통로 | 윤곽선 모드 준비, ROI/색상 미설정 |
| 장애물 회피 | 중앙 경로·단계별 복구 | 지도 보정 후 같은 엔진 사용 |
| 적 HP 확인 | 연결된 단일 빨간 바 규칙 | IV 규칙 미적용, 별도 HP 템플릿/장면 확인 필요 |
| 물약 | 기존 Q 유지 | 상속된 Q 기본값을 벨트 1로 변경, 사용자 키는 유지 |
| 미확인 스킬·버프 | 기존 설정 유지 | USE_SKILL/CAST_BUFF 차단 |

스킬 목록과 아군·객체 기억은 보존했습니다. II의 이름과 `디아2` 창 제목도 보존했으므로 실제 레저렉션 창 제목에 맞춰 수정해야 합니다. II는 아직 실제 화면에서 HUD·HP·미니맵·버프·상호작용/아이템 키를 보정하지 않았습니다. 지도 꺼짐 상태는 보정 완료를 뜻하지 않습니다.

## 변경 파일

- 이동: `app/core/minimap_memory.py`, `app/core/local_navigation.py`, `app/vision/click_safety.py`
- 전투/실행: `app/ai/main_agent.py`, `app/ai/visual_agent.py`
- 설정: `app/profiles/gameplay_presets.py`, `app/profiles/runtime_settings.py`, IV와 II의 `input.json`, `navigation.json`
- 로컬 웹: `Visual-Agent-Lab-local/app/gameplay-controls.tsx`, `app/game-settings.tsx`, `app/minimap-mapping.tsx`와 빌드 결과
- 검사: `tests/test_minimap_memory.py`, `tests/test_main_agent.py`, `tests/test_gameplay_presets.py`, `tests/test_detection_runtime.py`의 UTF-8 파일 읽기/쓰기 수정
- 문서: `BASIC_COMBAT.md`, `MINIMAP_MAPPING.md`, 이 문서

## 검증 및 한계

- 전체 Python 검사 최종 결과: 269개 실행, `OK (skipped=2)`, 30.401초. YOLO 가중치가 없는 모델 검사와 Windows 심볼릭 링크 권한 검사를 제외했습니다. 최초 실행의 UTF-8/CP949 오류는 테스트의 읽기·쓰기 인코딩을 명시하여 수정했습니다.
- 최종 실행 명령: `python -u -c "import faulthandler,unittest; faulthandler.dump_traceback_later(45,repeat=True); suite=unittest.defaultTestLoader.discover('tests',pattern='test_*.py'); result=unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(not result.wasSuccessful())"`. 출력은 임시 폴더의 `visual-game-agent-gameplay-tests.log`에 기록했습니다. 중간 전체 실행 한 번은 진행이 정체되어 테스트 프로세스만 종료했고, 웹 검사와 전체 검사를 재실행해 위 결과를 확인했습니다.
- 로컬 웹 폴더에서 `npm run typecheck`: 성공, `npm test`: 4개 통과, `npm run build`: 성공. `git diff --check`: 성공.
- 중앙 경로, 코너, 스크롤 등록, 복구 단계, 화면 비율, 원거리/근거리 전환, 아군 보호, HP 긴급 처리, 설정·기억 보존을 합성 화면과 모의 입력으로 검사했습니다.
- 실제 게임 이동, ESP32 입력 전송, 브라우저 UI 조작은 이번 작업에서 확인하지 않았습니다. 실행 중 Agent/COM3를 재시작하지 않았으므로 사용자 재시작 후 실게임 확인이 필요합니다.
- 로컬 웹 빌드만 적용했고 외부 Site 페이지는 배포하지 않았습니다. 웹 빌드는 500kB 초과 번들 경고가 있습니다.
- 색상 기반 지도는 모든 문·동적 장애물·반투명 오버레이를 완벽히 판별하지 못합니다. 실제 IV와 향후 II 화면에서 사거리와 지도 보정이 필요합니다.


## 현재 이동·전투 화면 분리 (2026-10-08)

- 이동: OpenCV 미니맵 갱신과 중앙 경로 계산. 자동 전체 화면 Qwen 요청·장면 유효시간 대기를 제거했습니다. 게임별 미니맵 미설정·명시적 미보정·오래된 지도에서는 일반 이동을 중단합니다.
- 전투: OpenCV 적 조우 감지 후 기본 화면 Qwen 분석. 디아블로 IV의 새 빨간 적 HP바는 장면 분석을 기다리지 않고 기존 검증 절차로 공격할 수 있습니다. 검증된 유지 공격 중에는 반복 장면 요청을 생략합니다.
- 이동 중 HP·적 HP바·버프 검사와 캐릭터 클릭 제외는 계속 실행합니다. 시작 시 1회 HUD 보정과 사용자가 요청한 객체 확인은 전체 화면 분석의 별도 예외입니다.
- 명확한 이동 방향은 로컬에서 해석하고, 해석이 필요한 이동 지시에는 미니맵 잘라낸 이미지만 보냅니다. 중단·창 비활성 후 도착한 결과는 폐기합니다.
- 지도 첫 꺾임까지의 거리를 화면에 투영하고 최종 클릭 전체 구간을 다시 검사합니다. 최소 클릭 거리를 이유로 확인 경로보다 멀리 늘리지 않습니다. 이전 전체 화면 장애물 좌표는 지도 경로를 덮어쓰지 않습니다. 실제 클릭점의 캐릭터·HUD 제외는 유지합니다.
- `navigation.json → minimap.mapping.screen_pixels_per_map_pixel`: 폭 192px 지도 1px당 1080p 화면 클릭 거리. 기본 12는 추정값이며 게임 확대율에 맞춰 보정해야 합니다. 화면 높이에 비례해 환산하고 `step_fraction`은 상한으로 적용합니다. 코너를 지나쳐 클릭하면 값을 낮추세요. 짧은 경로의 클릭점이 캐릭터 제외 영역에 걸리면 이동을 보류합니다.
- 로컬 대시보드의 게임별 이동 설정에서 환산값을 수정할 수 있습니다. 일반 이동은 미니맵이 필수이며 화면 비율 보정은 항상 적용합니다. II는 실제 미니맵 ROI·벽 색상·입력·HUD를 보정하기 전까지 이동하지 않습니다.

변경 파일: `app/ai/main_agent.py`, `app/core/local_navigation.py`, `app/core/minimap_memory.py`, `app/profiles/runtime_settings.py`, `app/profiles/diablo4/GAMEPLAY.md`, `app/profiles/ii/GAMEPLAY.md`, `app/profiles/generic/GAMEPLAY.md`, `Visual-Agent-Lab-local/app/gameplay-controls.tsx`, `tests/test_main_agent.py`, `tests/test_minimap_memory.py`, `BASIC_COMBAT.md`, `MINIMAP_MAPPING.md`, 이 문서, 웹 `dist/index.html`과 생성 JS 번들입니다. 프로필의 실제 게임 통계 DB는 편집하지 않았습니다.

최종 검증 명령:

```powershell
python -u -c "import faulthandler,unittest; faulthandler.dump_traceback_later(45,repeat=True); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests',pattern='test_*.py')); raise SystemExit(not result.wasSuccessful())"
# Visual-Agent-Lab-local 폴더
npm run typecheck
npm test
npm run build
# 프로젝트 폴더
git diff --check
```

결과: Python 283개 실행, `OK (skipped=2)`, 31.366초. YOLO 가중치 미포함 검사와 Windows 심볼릭 링크 권한 검사를 건너뛰었습니다. 웹 타입 검사 성공, 웹 테스트 4개 통과, 빌드 성공(500kB 초과 번들 경고 유지), whitespace 오류 없음. 테스트는 합성 지도·모델/입력 모의 객체로 수행했습니다. 실제 디아블로 이동 정확도·확대율과 브라우저 표시를 직접 검증하지 않았습니다.

적용: GameBot Agent를 재시작하고 로컬 대시보드를 새로고침하세요. 게임을 활성화한 뒤 미니맵 영역·플레이어 위치와 거리 환산값을 확인하세요.


## 대시보드 자동 연결 (2026-10-08)

페이지를 열면 Agent에 자동 연결합니다. 수동 `Agent 연결` 버튼, 연결 주소 입력창과 `연결 종료` 버튼을 제거했습니다. 기본 주소는 `http://127.0.0.1:8765`이며 Agent가 제공하는 페이지에서는 삽입된 실제 주소를 사용합니다. 첫 연결 실패 시 요청이 끝난 뒤 2초 간격으로 계속 재시도합니다. Agent 재시작·토큰 만료·세션 종료 후에도 새 세션을 자동으로 얻습니다. 자동 연결 자체는 사냥을 시작하지 않으며 기존 시작/재개·즉시 중단을 유지합니다. 화면을 닫을 때 요청과 재시도 예약을 취소하고 늦은 연결 결과를 폐기합니다.

변경 파일: `Visual-Agent-Lab-local/app/live-agent.tsx`, `Visual-Agent-Lab-local/app/statistics-panel.tsx`, `Visual-Agent-Lab-local/lib/auto-connect.ts`, `Visual-Agent-Lab-local/tests/auto-connect.test.mjs`, 웹 빌드 `dist/index.html` 및 생성 JS 번들, 이 문서.

실행 명령: 웹 폴더에서 `npm run typecheck`, `npm test`, `npm run build`, 프로젝트 폴더에서 `git diff --check`.
결과: 타입 검사 성공, 테스트 7개 통과(연결 실패 후 자동 재시도·동시 요청 방지·페이지 종료 시 예약 취소·늦은 응답 폐기 포함), 빌드 성공, whitespace 오류 없음. 500kB 초과 번들 경고는 유지됩니다. 실제 브라우저 연결은 직접 검증하지 않았습니다. Python 코드는 이번 연결 변경에서 수정하지 않았습니다.

새로고침하면 적용됩니다. Agent 프로세스가 실행되면 페이지가 자동으로 연결합니다.


## 실시간 입력 컨트롤러 키 표시 확인 (2026-10-08)

- 키보드 자판에 설정된 기능을 항상 표시합니다. 현재 IV 설정은 Q=물약, 4=버프, Space=회피, F=대화, 2=활성 공격 스킬 2입니다. 마우스 좌/우에도 설정 기능을 표시합니다. 키보드 이동 모드는 설정된 방향 키에 이동 기능을 표시합니다.
- 진행 중인 입력은 파란색, 최근 성공한 전송은 1.5초 동안 초록색, 우클릭 유지는 해제될 때까지 표시합니다. 브라우저 타이머로 최근 표시를 만료시키고 오프라인에서는 강조를 해제합니다. 차단·취소 기록은 성공 전송으로 강조하지 않습니다.
- 상태 API가 내보내는 최근 입력 기록을 12개에서 40개로 늘렸습니다. 스킬 전에 전송한 기본 우클릭 유지와 검증으로 차단한 명령도 기록합니다. 유지 공격의 실제 버튼은 mouse_right로 표시합니다. 설정 변경이 입력 동작을 바꾸지는 않습니다.
- 실제 열린 127.0.0.1:8766 페이지는 이전 빌드가 남아 있었고 HP 미검출로 중단된 상태였습니다. 기존 탭을 새로고침한 뒤 Controller 탭에서 Q·4·Space·2와 마우스 기능 표시를 확인했습니다. 게임 입력이나 사냥 재개는 실행하지 않았습니다. 전송 중·짧은 입력 강조는 모의 입력 테스트로 검증했습니다.

변경 파일: `Visual-Agent-Lab-local/app/input-monitor.tsx`, `Visual-Agent-Lab-local/app/controller-input.css`, `Visual-Agent-Lab-local/lib/input-display.ts`, `Visual-Agent-Lab-local/tests/input-display.test.mjs`, `app/ai/visual_agent.py`, `app/core/action_executor.py`, `tests/test_attack_hold.py`, `tests/test_web_bridge.py`, 웹 빌드 결과, 이 문서. 브라우저 확인 화면은 `artifacts/input-controller-keys.png`에 저장했습니다.

실행 명령:

```powershell
python -u -c "import faulthandler,unittest; faulthandler.dump_traceback_later(45,repeat=True); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests',pattern='test_*.py')); raise SystemExit(not result.wasSuccessful())"
# Visual-Agent-Lab-local 폴더
npm run typecheck
npm test
npm run build
# 프로젝트 폴더
git diff --check
```

결과: Python 285개 실행, `OK (skipped=2)`, 31.638초. YOLO 가중치 미포함 및 Windows symlink 권한 검사 제외. 웹 테스트 12개 통과, 타입 검사·빌드 성공, whitespace 오류 없음. 500kB 번들 경고는 유지됩니다. 화면 표시는 열린 브라우저에서 확인했으며 실제 ESP32 키·마우스 입력 테스트는 수행하지 않았습니다. Python 입력 기록 변경을 현재 실행 프로세스에 적용하려면 Agent를 재시작해야 합니다. UI는 열린 대시보드에 새로고침해 적용했습니다.


## 미니맵 처리·화면 미리보기·Qwen/Controller 통합 (2026-10-08)

실제 웹 미리보기에서 1718×1368 캡처에 위아래 검은 여백이 있고, 미니맵 ROI가 상단 여백과 지도 일부를 포함하는 것을 확인했습니다. 거의 검은 대칭 여백을 제외한 게임 영역을 기준으로 미니맵을 잘라냅니다. `mapping.crop_to_viewport`는 기본 true이며 설정 UI에서 끌 수 있습니다. 맵핑·HSV 통로 검사·정체 검사·Qwen용 미니맵 이미지·미니맵 클릭 제외 영역에 같은 좌표를 적용하고, 이동 클릭 거리도 실제 게임 영역의 높이로 환산합니다. 검은 여백은 클릭 금지 영역으로 처리합니다.

지도 확인을 일시정지와 HP 준비 조건에서 분리했습니다. 최신 전경 게임 캡처가 있으면 일시정지·HUD 준비 대기 중에도 지도만 확인하며 입력 검증은 유지합니다. 처리 중단·게임 비활성·캡처 대기·지형 판정 실패·플레이어 장애물 판정 등을 표시합니다. 플레이어 칸이 막혀 있으면 지도 유효 판정과 이동을 차단합니다. 처리 오류가 한 번 발생해도 지도 작업이 끝나지 않고 재시도합니다. 이전 지도는 현재 지도와 구분해 표시합니다.

Qwen-VL/Controller 전환 버튼과 숨김 패널을 제거하고 대화·미니맵·키보드/마우스를 한 게임 화면에 표시합니다. 미니맵을 제한하던 공용 180px 높이를 해제했습니다. 화면 미리보기는 마우스 오버·포커스·클릭으로 열고 닫기/ESC로 닫습니다. 열려 있는 게임 화면에서만 750ms 간격으로 인증된 미리보기 API를 요청하며 요청 완료 후 다음 요청을 예약합니다. 전체 캡처의 분석 영역, 원본 미니맵의 설정 플레이어 위치, 흰색 통로/검정 장애물 마스크를 비교할 수 있습니다. 비활성·처리 중단 시 마지막 전경 게임 캡처의 경과 시간을 표시합니다. 화면에 표시하기 위한 인코딩은 모델 호출·게임 입력·공유 프레임 변경을 하지 않습니다. 프로필 변경 중 도착한 인코딩 결과는 폐기합니다.

변경 파일:
- `app/vision/game_viewport.py`, `app/vision/click_safety.py`
- `app/core/minimap_memory.py`, `app/core/local_navigation.py`, `app/core/navigation_monitor.py`
- `app/ai/main_agent.py`, `app/ai/visual_agent.py`, `app/profiles/runtime_settings.py`
- `app/web/bridge.py`, `app/web/screen_preview.py`
- `Visual-Agent-Lab-local/app/live-agent.tsx`, `app/minimap-mapping.tsx`, `app/globals.css`, `app/gameplay-controls.tsx`, `app/game-screen-preview.tsx`, `app/game-screen-preview.css` (이 목록의 뒤쪽 app 경로도 Visual-Agent-Lab-local 기준)
- `tests/test_game_viewport.py`, `tests/test_minimap_memory.py`, `tests/test_main_agent.py`, `tests/test_web_bridge.py`
- `MINIMAP_MAPPING.md`, 이 문서, 웹 빌드 결과
- 확인 이미지: `artifacts/game-screen-preview.png`, `artifacts/qwen-controller-merged.png`, `artifacts/minimap-before-viewport.jpg`

실행 명령:

```powershell
python -m unittest tests.test_game_viewport tests.test_minimap_memory tests.test_main_agent tests.test_web_bridge
python -u -c "import faulthandler,unittest; faulthandler.dump_traceback_later(45,repeat=True); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests',pattern='test_*.py')); raise SystemExit(not result.wasSuccessful())"
# Visual-Agent-Lab-local 폴더
npm run typecheck
npm test
npm run build
# 프로젝트 폴더
git diff --check
```

결과: 관련 Python 149개 `OK (skipped=1)`. 최종 전체 Python 294개 `OK (skipped=2)`, 33.552초. YOLO 가중치 미포함 및 Windows symlink 권한 검사 제외. 웹 테스트 12개 통과, 타입 검사·빌드 성공. 500kB 초과 번들 경고 유지. whitespace 오류 없음.

실제 브라우저: 기존 127.0.0.1:8766 탭을 새로고침해 Qwen 대화와 Controller의 동시 표시, 모드 탭 0개, 미리보기의 게임 캡처·미니맵 원본·마스크, 닫기 버튼을 확인하고 이미지를 저장했습니다. 실제 캡처를 표시하던 Agent는 작업 중간 코드로 실행됐으며 마지막 검은 여백 보정은 해당 프로세스에 적용하지 않았습니다. 따라서 확인 이미지에는 보정 전 ROI가 표시되어 있습니다. 게임 시작·공격·이동 명령을 브라우저에서 전송하지 않았습니다.

실제 캡처 검증: 브라우저의 960×764 JPEG 미리보기를 저장했습니다. 노란 진단 테두리가 있는 우측을 제외한 좌측 80% 픽셀에서 게임 영역 Y=82..688을 확인하고, 그 영역의 미니맵을 새 알고리즘으로 검사했습니다. ROI=192×142, 통로 비율 33.3%, 지형 판정 성공, 플레이어 칸 통로, 로컬 경로 생성 성공을 관찰했습니다. 이는 저장한 축소 캡처에서의 검사이며 실제 이동·벽 탈출 성공률 검증은 아닙니다. 최종 검은 여백 보정을 실시간 지도에 적용하려면 GameBot Agent를 재시작하세요. UI 최종 빌드는 기존 열린 탭에 새로고침해 적용했습니다.


## 실제 미니맵 영역과 장애물 기준 저장·적용 (2026-10-08)

이전 확인 화면과 사용자 첨부에서 미니맵 ROI에 검은 상단·지역 제목이 포함되고 지도 하단이 잘리며, 밝기 기준이 낮아 지형까지 통로로 보이는 것을 확인했습니다. 현재 실행 중인 Agent는 작업 중간 코드였으므로 보정 값을 전체 캡처 기준으로 직접 저장했습니다. 새 좌표를 여백 제거 좌표로 중복 해석하지 않도록 crop_to_viewport=false를 명시합니다.

최종 설정 (1718×1368 전체 캡처 기준):
- ROI [820,140,988,295]: X=82%, Y=14%, 너비=16.8%, 높이=15.5%
- mapping.mode=bright_floor, dark_floor=138, wall_margin_px=1
- 플레이어 [0.5,0.5], 중앙 선호도 4, 선호 여유 폭 10px, 이동 거리 설정 30% 유지
- 보조 HSV 범위 [[[0,0,138],[35,135,255]]]. 이 범위는 HSV 모드에서만 사용하며 최종 활성 모드는 밝기 기반입니다.

기존 열린 대시보드의 게임 설정을 읽고 필요한 필드만 수정해 ‘게임 설정 저장·적용’을 실행했습니다. 최종 값이 navigation.json과 설정 화면에 남아 있고 저장 완료 메시지를 확인했습니다. Agent를 재시작하지 않고 실행 중인 프로필에 적용했습니다. 사냥은 일시정지 상태로 유지했습니다.

플레이어 표시의 별도 오류도 수정했습니다. 이전 Agent가 player_walkable 필드를 보내지 않았을 때 프런트엔드가 undefined를 false처럼 처리하던 문구를 고쳤습니다. 명시적 false만 실패로 표시하고 필드가 없으면 지형 판정만 표시합니다. 실제 Agent가 반환한 PNG 마스크(192×141)에서 중앙 플레이어 격자 칸은 평균 255로 통로였습니다. 중간에 HSV 모드·49% 위치를 확인했으나 최종 설정은 위 bright_floor·50% 중심이며 중간 시도는 사용하지 않습니다.

실제 미니맵 JPEG를 테스트 fixture로 저장하고 큰 지형 섬 5곳이 막히는지, 밝은 길 3곳이 통과 가능한지, 플레이어 시작 칸과 모든 경로 칸이 통로인지 검증하는 회귀 테스트를 추가했습니다. 미리보기는 지도 본체 전체와 통로 비율 약 38%인 마스크를 표시했습니다. 확인 시 게임 창이 비활성이었으므로 이는 마지막 전경 캡처에 새 보정을 적용한 결과입니다. 게임 창이 활성화되면 최신 캡처로 지도 기억을 다시 계산합니다. 실제 사냥·벽 탈출 성공률은 측정하지 않았습니다.

변경 파일: app/profiles/diablo4/navigation.json, app/profiles/diablo4/GAMEPLAY.md, Visual-Agent-Lab-local/app/game-screen-preview.tsx, tests/test_minimap_memory.py, tests/fixtures/diablo4_minimap.jpg, MINIMAP_MAPPING.md, 이 문서와 웹 빌드 결과. 확인 사진 artifacts/minimap-calibration-applied.png 및 실제 미니맵 자료 artifacts/diablo4-minimap-raw-calibration.jpg, artifacts/diablo4-minimap-mask-live.png를 저장했습니다.

실행 명령:

```powershell
python -m unittest tests.test_minimap_memory tests.test_game_viewport tests.test_main_agent
python -u -c "import faulthandler,unittest; faulthandler.dump_traceback_later(45,repeat=True); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests',pattern='test_*.py')); raise SystemExit(not result.wasSuccessful())"
# Visual-Agent-Lab-local
npm run typecheck
npm test
npm run build
# 프로젝트 폴더
git diff --check
```

결과: 관련 Python 78개 OK. 전체 Python 295개 실행, 33.030초, OK (skipped=2: YOLO 가중치 미포함 및 Windows symlink 권한 검사). 웹 테스트 12개 통과, 타입 검사·빌드 성공. 500kB 초과 번들 경고 유지, whitespace 오류 없음. 현재 열린 웹페이지를 최종 빌드로 새로고침한 뒤 정상 지도 잘라내기·장애물 형태·이전 Agent에서의 정상 판정 문구를 확인했습니다. 확인 자료는 이번 최종 설정이 실행 Agent에 적용된 결과입니다.

## 2026-10-08 실시간 입력 표시 및 클릭 좌표 정리

변경 파일: Visual-Agent-Lab-local/app/input-monitor.tsx, app/controller-input.css, app/minimap-mapping.tsx, app/live-agent.tsx, lib/input-display.ts, tests/input-display.test.mjs (위 경로는 모두 Visual-Agent-Lab-local 기준), app/core/minimap_memory.py, tests/test_minimap_memory.py, 이 문서와 웹 dist 빌드 결과.

설정된 키가 항상 파란 배경을 갖던 스타일을 기본 회색으로 변경했습니다. 전송 완료 입력 강조는 1.5초에서 250ms로 줄이고 화면에서 50ms 간격으로 만료를 계산합니다. 상태 응답이 750ms 이상 갱신되지 않으면 이전 active 명령과 우클릭 유지 강조를 해제합니다. hold_right 모드의 우클릭 해제 후에는 최근 공격 이벤트가 남아도 유지 표시를 다시 켜지 않습니다. 키 아래의 설정 기능 이름은 유지합니다.

마지막으로 전송에 성공한 마우스 명령의 실제 target을 미니맵 이동 기록 아래 X%, Y%와 좌/우클릭으로 표시합니다. 게임 캡처 전체의 정규화 좌표이며 미니맵 안의 좌표가 아닙니다. 실패·차단·요청 명령과 키보드 명령은 클릭 위치로 표시하지 않습니다. Agent 연결이 끊기면 마지막 연결 기록임을 표시합니다. 클릭 기록이 없으면 없음으로 표시합니다.

요청한 '키 아래: 설정된 기능 …' 안내문부터 입력 방향판, 명령 상세, 스킬 목록, 입력 이벤트 표를 삭제했습니다. 컨트롤러 뒤의 버프 아이콘 설명과 전체 객체 목록도 화면에서 삭제했습니다. 게임 동작과 버프 처리 로직은 변경하지 않았습니다.

실제 열린 대시보드에서 Object of type float32 is not JSON serializable 오류로 상태 갱신이 중단되는 것을 확인했습니다. 경로 벽 여유를 계산한 NumPy float32를 snapshot에서 Python float로 변환했습니다. 실제 계획 경로를 생성한 뒤 json.dumps가 성공하는 회귀 검사를 추가했습니다.

실행 명령:
```powershell
# Visual-Agent-Lab-local
npm run typecheck
npm test
npm run build
# 프로젝트 폴더
python -m unittest tests.test_minimap_memory tests.test_web_bridge
git diff --check
```
결과: 웹 테스트 16개 통과, 타입 검사·최종 빌드 성공. 관련 Python 94개 실행, OK (skipped=1). 500kB 번들 경고 유지. git diff --check에서 whitespace 오류 없음.

실행 Agent에 제어 API /quit을 보내 입력 해제 및 정상 종료를 확인한 뒤 기존 Python 경로로 같은 GameBot 프로젝트의 app.main을 --web --web-port 8766 --no-chat --chat-max-tokens 4096 --no-browser로 다시 실행했습니다. Start-Process -WindowStyle Hidden을 사용했습니다. 새 탭 없이 기존 대시보드를 새로고침했습니다. 상태 API를 세 번 읽어 paused=True, hunt_active=False, attack_held=False, ESP32InputController를 확인했습니다. 브라우저에서 설정된 키의 회색 배경, 입력 강조 0개, 클릭 좌표 없음, 안내문과 상세 목록 제거, 정상 연결을 확인했습니다. 실제 게임 입력은 검증 목적으로 보내지 않았고 클릭·해제 동작은 회귀 테스트로 검증했습니다.

확인 사진: artifacts/input-controller-applied.png. Agent 실행 로그: artifacts/input-monitor-agent.log, artifacts/input-monitor-agent-error.log. Agent는 일시정지 상태입니다.

## 2026-10-08 중앙 클릭 제거, 이동 목표 유지, 캡처 종료 정리

변경 파일:
- app/controller/esp32_input_controller.py, app/controller/win32_input_controller.py
- app/capture/screen_capture.py
- app/core/click_journey.py (새 파일), app/ai/main_agent.py, app/ai/visual_agent.py
- tests/test_click_journey.py, tests/test_capture_cleanup.py (새 파일), tests/test_esp32_ble.py, tests/test_main_agent.py
- BASIC_COMBAT.md, MINIMAP_MAPPING.md, app/profiles/diablo4/GAMEPLAY.md, app/profiles/ii/GAMEPLAY.md, app/profiles/generic/GAMEPLAY.md, 이 문서

실제 중앙 클릭 원인: ESP32 및 Win32의 stop()이 직전 이동 성공 후 MOVE 키로 (0.5,0.5)를 다시 클릭했습니다. 두 컨트롤러에서 이 클릭을 제거했습니다. STOP은 키·마우스 해제만 수행합니다. 이미 게임에 전달한 지점 이동은 짧은 잔여 경로를 완료할 수 있습니다.

보정된 매핑의 클릭 이동에서는 실제 전송에 성공한 탐색·사용자 이동 목표를 지도상의 위치로 기억합니다. 같은 MOVE가 실행 중이거나 기존 목표까지의 남은 통로가 열려 있으면 추가 클릭을 보내지 않습니다. 도착·새 장애물·이동 정체·지도 구간 변경·지도 미확인·2.5초 제한에서 다시 검증합니다. 전투·물약·회피·전투 거리 조절은 우선 통과하며 기존 캐릭터·HUD 클릭 차단은 유지합니다. 미니맵 검사 주기는 0.2초로 유지했습니다. Qwen 호출은 추가하지 않았습니다.

설치된 DXCAM 코드에서 DXGI duplicator와 texture를 Release()한 뒤 소유한 comtypes 포인터를 제거하는 중복 해제 경로를 확인했습니다. ScreenCapture.close()에서 해당 COM 포인터의 소유 참조를 한 번만 정리한 후 camera.release()를 호출하도록 수정했습니다. 라이브러리 파일은 수정하지 않았습니다. 캡처와 종료는 같은 잠금으로 보호해 취소된 비동기 캡처 작업이 아직 grab 중일 때 자원을 해제하지 않도록 했고 close() 반복 호출을 허용합니다.

실행 명령:
```powershell
python -X utf8 -m unittest tests.test_click_journey tests.test_capture_cleanup tests.test_esp32_ble tests.test_main_agent tests.test_bar_navigation
python -X utf8 -m unittest discover -s tests -p 'test_*.py' *> artifacts\movement-cleanup-tests.log
python -X utf8 -m unittest tests.test_gameplay_presets
git diff --check
```
결과: 관련 82개 통과. 이후 통합 검사를 추가한 전체 Python 307개, 33.413초, OK (skipped=2: YOLO 가중치 및 Windows symlink 권한). 문서 수정 후 게임 프로필 적용 검사 3개 통과. whitespace 오류 없음. artifacts/movement-cleanup-tests.log에 실제 전체 결과를 저장했습니다.

추가 실행: 실제 ScreenCapture(mode='screen')를 생성해 grab 후 close를 두 번 호출하고 COM 소멸자 오류를 확인했습니다. 또 subprocess.run([sys.executable,'-u','-X','utf8','-c',script])으로 별도 프로세스 3개에서 ThreadPoolExecutor(1)로 grab을 시작하고 close 및 executor 종료를 검사했습니다. 3개 모두 종료 코드 0, stderr 빈 문자열이며 access violation이나 Exception ignored가 없었습니다. 테스트는 화면 캡처와 종료만 수행했고 게임 입력을 보내지 않았습니다.

이전부터 실행 중인 Agent PID 16024에는 제어 API /quit을 보내 정상 종료 및 입력 해제를 확인했습니다. 수정한 Agent를 Start-Process -WindowStyle Hidden으로 실행하고 종료 검사 후 일시정지 재실행하려던 후속 명령은 자동 승인 검토에서 blocked by policy로 거절되어 실행되지 않았습니다. 현재 Python Agent가 실행 중이지 않음을 프로세스 조회로 확인했습니다. 수정은 다음 GameBot.bat 실행부터 적용됩니다. 전체 Agent의 수정 후 종료와 실제 게임의 이동·벽 탈출 성공률은 이 세션에서 확인하지 못했습니다.


## 2026-10-08 포커스 자동 복귀와 이동 지연 수정

변경 파일: app/ai/visual_agent.py, app/ai/main_agent.py, app/capture/screen_capture.py, app/core/click_journey.py, app/core/local_navigation.py, app/core/minimap_memory.py, app/web/bridge.py, app/profiles/diablo4/navigation.json, app/profiles/ii/navigation.json, 각 diablo4/ii/generic GAMEPLAY.md, Visual-Agent-Lab-local/app/game-settings.tsx 및 빌드 결과, tests/test_main_agent.py, tests/test_click_journey.py, tests/test_profile_management.py.

게임 전경 여부를 캡처 전에 확인하고 포커스 이탈 시 캡처·HUD·지도·객체 추적·새 Qwen 요청·동작 루프를 대기시킵니다. 입력을 해제하고 실행 세대를 갱신하여 늦게 도착한 응답을 폐기합니다. 포커스 복귀 시 이전 사냥/이동 지시를 복원하고 최신 HP·지도를 기다립니다. 직접 중단하거나 HP 처리 중단된 경우에는 자동 시작하지 않습니다. 포커스 대기 동안 HP 15초 미탐색 타이머는 누적하지 않습니다.

설정한 클릭 거리를 게임 화면의 짧은 변 기준으로 투영합니다. 지도에서 첫 6개 셀까지만 보던 제한을 제거하고 설정 거리까지 연결된 직선 경로를 살핍니다. 코너에서는 검증된 회전 지점으로 줄이지만 클릭 거리의 12.5%까지 임의 축소하던 탐색 클릭은 제거했습니다. 최종 입력 검사에서 다른 최신 경로의 lookahead 제한을 가져와 기존 클릭을 거부하던 조건을 제거하고 실제 클릭까지 현재 통로를 검사합니다. 지도 갱신을 0.2초에서 0.1초, 이동 전송 간격을 500ms에서 120ms로 줄였습니다. 이동 진행이 0.6초 동안 없으면 경로를 다시 판단하고 적이 나타나면 기존 전투 정책이 우선합니다. 이동 중 HP 가림 확인을 위한 별도 주변 클릭을 삽입하지 않습니다. 디아블로 IV/II 및 범용 프로필 MD에도 동작을 기록했습니다.

실행한 명령:
```powershell
python -X utf8 -m unittest discover -s tests
python -X utf8 -m unittest tests.test_main_agent tests.test_click_journey tests.test_gameplay_presets
npm run typecheck
npm test
npm run build
git diff --check
```

전체 Python 검사: 313개, 34.220초, OK (skipped=2). 이후 꺾인 통로의 실제 클릭 검증을 추가하고 최종 변경 관련 70개를 8.916초에 통과했습니다(Python 종료 코드 0). artifacts/focus-travel-tests.log와 artifacts/focus-travel-related-tests.log에 UTF-8 결과를 저장했습니다. 웹 타입 검사 통과, 16개 테스트 통과, 빌드 성공. 빌드에 기존 번들 크기 경고가 있습니다. 실제 게임 입력을 보내 이동 속도·벽 탈출 성공률을 확인하지는 않았습니다. 에이전트를 재시작하면 수정한 Python 코드가 적용됩니다.


## 2026-10-08 이동 점검 3분류와 진행 유지

변경 파일: app/core/click_journey.py, app/ai/main_agent.py, Visual-Agent-Lab-local/app/minimap-mapping.tsx와 빌드 결과, tests/test_click_journey.py, tests/test_main_agent.py, MINIMAP_MAPPING.md, 각 게임 GAMEPLAY.md 및 이 기록.

예정 방향(하늘색 화살표), 실제 전송한 클릭(주황색 ×와 화면 좌표), 실제 이동거리(분홍색 궤적)를 나눴습니다. 클릭 목표의 미니맵 좌표는 화면 거리/회전을 투영한 추정이며 관측된 플레이어 위치와 구분합니다. 클릭 확인 후 지도 위치의 변화로 이동 경로 길이·시작점 대비 변위·남은 거리를 계산합니다. 0.5 지도 px 미만의 흔들림은 누적하지 않습니다. 지도 구간이 바뀌거나 연결이 실패하면 관측을 중단하고 좌표를 새 지도에 잘못 표시하지 않습니다. 보류/실패한 입력은 실제 클릭 기록을 만들지 않습니다. 도착·정체·전투/중단·지도 연결 실패 상태와 경과 시간을 표시합니다.

진행이 확인되는 클릭은 기존 2.5초 제한만으로 불필요하게 교체하지 않습니다. 최대 8초 검증 한도와 0.6초 무진행 시 경로 재검토, 적 발견 시 전투 우선 처리는 유지합니다. Qwen 요청은 추가하지 않았습니다.

실행 명령:
```powershell
python -X utf8 -m unittest tests.test_click_journey tests.test_main_agent
python -X utf8 -m unittest discover -s tests
python -X utf8 -m unittest tests.test_click_journey
npm run typecheck
npm test
npm run build
git diff --check
```
전체 Python 320개, 37.002초, OK (skipped=2), Python 종료 코드 0. artifacts/movement-telemetry-tests.log에 UTF-8 결과를 저장했습니다. 이후 도착 상태를 STOP 뒤에도 유지하는 검사를 추가해 ClickJourney 12개가 통과했습니다. 웹 타입 검사, 16개 테스트, 빌드 통과(기존 번들 크기 경고). 실제 게임에서 이동 궤적/벽 탈출을 검증하지는 않았습니다. Agent 재시작과 웹 새로고침 후 적용됩니다.


## 2026-10-08 현재 클릭 목표 도착까지 경로 고정

변경 파일: app/ai/visual_agent.py, app/ai/main_agent.py, app/core/click_journey.py, Visual-Agent-Lab-local/app/minimap-mapping.tsx 및 빌드 결과, tests/test_main_agent.py, tests/test_click_journey.py, BASIC_COMBAT.md, MINIMAP_MAPPING.md, diablo4/ii/generic GAMEPLAY.md와 이 기록.

이전 구현은 새 경로를 먼저 계산하고 마지막 전송 단계에서만 기존 클릭 유지를 검사했습니다. 새 경로가 클릭 조건을 통과하지 못해 STOP이 만들어지면 기존 이동 목표도 해제될 수 있었습니다. 현재 목표 유지 검사를 경로 계산 앞으로 옮겨 진행 중에는 새 방향 계산·새 클릭·불필요한 STOP을 생략합니다. 전송 큐와 포인터 이동 중에도 목표 교체를 막고, 도착(지도 2px 이내) 후 다음 경로를 계산합니다. 목표 진행이 정상이라면 8초 경과만으로 교체하지 않습니다. 새 벽·0.6초 무진행·지도/실행 세대 변경은 재검증하고 전투·긴급 동작·버프·수동 중단은 우선합니다. 미니맵 화살표와 표시 경로는 현재 실제 클릭 목표에 고정하고 스크롤 위치만 반영합니다. 웹에 ‘목표 도착까지 유지’를 표시합니다.

프로필 MD가 길어지면서 뒤에 추가한 사용자 규칙이 상세 Qwen 문서의 2500자 앞부분 제한에서 누락되는 회귀를 검사에서 발견했습니다. 같은 문자 예산 안에서 앞부분과 끝부분을 함께 포함하도록 수정했습니다. 자동 전투 요약 예산과 이미지 요청 횟수는 늘리지 않았습니다.

실행 명령:
```powershell
python -X utf8 -m unittest tests.test_main_agent tests.test_click_journey
python -X utf8 -m unittest discover -s tests
npm run typecheck
npm test
npm run build
git diff --check
```
최종 전체 Python: 325개, 34.808초, OK (skipped=2), 종료 코드 0. artifacts/waypoint-lock-tests.log에 UTF-8 결과 저장. 목표 도착 전 새 계획/클릭 없음, 도착 후 계획 재개, 화살표 고정과 스크롤 좌표, 새 벽에서 유지 해제, 8초 이후에도 진행 중인 목표 유지, 게임별 문서 추가 규칙 반영을 검증했습니다. 웹 타입 검사·16개 테스트·빌드 통과. 실제 게임 입력으로 이동 부드러움을 확인하지는 않았습니다. Agent 재시작 및 웹 새로고침 후 적용됩니다.


## 2026-10-08 인터넷 길찾기 자료 기반 코너·경로 검사 보강

변경 파일: app/core/route_geometry.py(신규), app/core/minimap_memory.py, app/core/local_navigation.py, app/core/click_journey.py, tests/test_route_geometry.py(신규), PATHFINDING_REFERENCES.md(신규) 및 이 기록.

Red Blob Games의 Dijkstra/A* 구현과 격자 선분(supercover), Nav2 공식 Regulated Pure Pursuit 문서를 확인했습니다. 출처 링크와 적용 이유는 PATHFINDING_REFERENCES.md에 기록했습니다. 목적지를 정하기 전 여러 도달 지점을 평가하는 탐색에는 기존 Dijkstra를 유지합니다. 클릭 이동에 맞춰 경로 단순화·가시 목표 선택·충돌 검사를 독립 구현했으며 ROS/외부 패키지 설치와 Qwen 요청을 추가하지 않았습니다.

DDA로 선분이 닿는 모든 격자를 열거하고 대각선 코너의 양옆 칸을 포함합니다. 경로 단순화에는 기존 벽 여유와 벽 근접 비용을 적용하여 중앙 경로를 위험한 벽 옆 지름길로 교체하지 않습니다. 최종 지도 기반 클릭 검사와 이동 중 남은 통로 검사도 같은 선분 열거를 사용합니다. 중앙 픽셀이 막히면 주변 3×3 통로 비율로 통과시키지 않습니다. 시작 위치가 좁은 통로일 때 최초 여유를 기준에 포함하여 중앙으로 빠져나오는 첫 움직임을 과도하게 막지 않습니다. 첫 안전 지점이 없으면 경로를 보류합니다. 현재 목표 도착 전 방향 고정과 기존 전투/중단 우선순위는 유지합니다.

실행한 명령:
```powershell
python -X utf8 -m unittest tests.test_route_geometry tests.test_minimap_memory tests.test_click_journey tests.test_main_agent
python -X utf8 -m unittest discover -s tests
git diff --check
```
관련 105개 통과 후 최종 전체 Python 330개, 34.462초, OK (skipped=2), 종료 코드 0. artifacts/pathfinding-reference-tests.log에 UTF-8 결과 저장. 대각선 코너, 역방향·정수 끝점, 한 픽셀 벽, 벽 여유/비용, 지도 밖 목표, 실제 디아블로 IV 미니맵 사진 및 기존 이동 고정을 검사했습니다. Python에서 사진을 memory.update(...,now=100)로 읽고 memory.suggest((1,0),now=100,lookahead_px=27)를 30회 실행한 참고 측정은 평균 26.194ms, 30회 모두 경로 반환입니다(artifacts/pathfinding-local-timing.json). 캡처·Qwen·입력 시간을 제외한 단일 사진 측정이며 전체 게임 속도 향상이나 벽 탈출 성공률을 의미하지 않습니다. 웹 코드는 변경하지 않아 웹 빌드를 추가 실행하지 않았습니다. Agent 재시작 후 적용됩니다.


## 2026-10-08 조기 목표 교체 상세 점검과 도착 근처 준비

변경 파일: app/core/click_journey.py, app/ai/main_agent.py, app/ai/visual_agent.py, Visual-Agent-Lab-local/app/minimap-mapping.tsx 및 빌드 결과, tests/test_click_journey.py, tests/test_main_agent.py, diablo4/ii/generic GAMEPLAY.md, BASIC_COMBAT.md, MINIMAP_MAPPING.md 및 이 기록.

조기 해제 경로를 점검했습니다. 기존 dispatch/on_input은 버프·물약에도 목표를 해제했습니다. 직선 목표 거리 감소만으로 0.6초 정체를 판정해 측면 이동을 오판할 수 있었습니다. 실패한 다른 이동 결과도 현재 목표를 지울 수 있었습니다. 자동 아이템 수집/상호작용이 이동 중 목표 클릭을 선점할 수 있었고, 같은 캡처의 반복 지도 처리 시각이 새 관측처럼 쓰일 수 있었습니다. 각 조건을 수정했습니다.

현재 목표는 버프·물약 및 무관한 실패 명령 동안 유지합니다. 실제 위치 변화를 진행으로 인정하고 1.2초 변화 없음에서 복구합니다. 자동 수집/상호작용은 도착까지 기다리며 명시적인 사용자 지시, 공격·회피, 중단·HP·포커스 안전 조건은 우선합니다. 지도 작업은 같은 실행 세대/캡처 번호/화면 식별자를 반복 처리하지 않습니다.

다음 경로 준비와 실제 전송을 나눴습니다. 남은 거리 ≤ 요청 거리 15% 또는 지도 4px에서 다음 후보를 준비하되 현재 planner의 목표·방향·경로 상태를 보존합니다. 현재 목표 뒤쪽이나 동일 목표의 후보는 미리 준비하지 않습니다. 도착은 지도 2px 이내를 서로 다른 지도 갱신에서 최소 0.08초 간격으로 두 번 확인합니다. 확인 전에는 다음 클릭을 보내지 않습니다. 도착 후 캐시의 목표 번호/실행 세대/지도 구간/0.8초 신선도/실제 통로를 검사해 전송하며, 유효하지 않으면 다시 계산합니다.

웹에 이동 목표 번호, 다음 경로 준비/클릭 보류, 최근 목표 종료 사유와 남은 거리를 표시합니다. backend에는 최근 20개 종료 이력을 제공합니다. 지도 스크롤 시 투영 목표 좌표가 움직이는 것과 실제 화면 클릭 목표 교체를 목표 번호로 구분할 수 있습니다. Qwen 요청을 추가하지 않았습니다.

실행 명령:
```powershell
python -X utf8 -m unittest tests.test_main_agent tests.test_click_journey
python -X utf8 -m unittest discover -s tests
python -X utf8 -m unittest tests.test_main_agent.AgentTests.test_new_wall_releases_goal_before_arrival tests.test_main_agent.AgentTests.test_near_goal_stages_next_path_without_sending_a_click tests.test_click_journey
npm run typecheck
npm test
npm run build
git diff --check
```
전체 Python 337개, 37.020초, OK (skipped=2), 종료 코드 0: artifacts/goal-completion-audit-tests.log. 이후 뒤쪽 후보 제외/준비 과정의 활성 경로 보존을 추가해 관련 87개가 10.374초에 통과했습니다: artifacts/goal-completion-related-tests.log. 마지막 종료 이력 표시 후 관련 19개 통과. 웹 16개 테스트·타입 검사·빌드 통과(기존 번들 크기 경고). 실제 게임의 이동과 목표 교체 재현은 수행하지 않았습니다. Agent 재시작·웹 새로고침 후 적용됩니다.


## 클릭 속도와 도착 전 목표 소실 보강 (2026-10-08)
변경 파일: app/core/click_journey.py, app/core/minimap_memory.py, app/ai/main_agent.py, app/ai/visual_agent.py, 관련 테스트 3개 파일 및 BASIC_COMBAT.md·MINIMAP_MAPPING.md·게임별 GAMEPLAY.md.
일반 매핑 클릭 이동에만 전송 이후 duration_ms=0, cooldown=0을 적용했다. 입력 전송 완료를 실제 목표 생성 기준으로 유지하며 현재 목표 도착 전 중복 클릭을 막는다. 키보드 이동·전투 접근·클릭 자체의 입력 완료와 안전 검증은 유지한다.
지도 정렬이 한 번 실패하면 즉시 segment를 초기화하던 경로를 수정했다. 마지막 정상 지도 프레임과 좌표를 0.6초간 유지하며 재정렬한다. 짧은 미측정은 목표를 유지하고, 지속적인 실패는 안전하게 목표를 해제한다. 통로 장애물은 서로 다른 지도 샘플에서 0.18초 지속되는지 확인한다. HUD/지도/경로 대기 STOP은 목표 기록을 지우지 않는다. 실제 중단·전투 등은 활성 이동을 해제하되 런타임 기록은 다음 실제 클릭 전송까지 유지한다. 수동 중단/게임 전환은 기존 초기화 동작을 유지한다.
검증 명령: python -X utf8 -m unittest tests.test_click_journey tests.test_main_agent; python -X utf8 -m unittest discover -s tests; git diff --check.
관련 93개 테스트 통과. 전체 테스트 첫 실행은 차단된 플레이어 위치의 사유 표시 검사 1개 실패했고, 정렬 대기 중에도 player_blocked 사유를 유지하도록 수정한 뒤 다시 실행했다. 최종 전체 346개, 38.074초, OK (skipped=2), 종료 코드 0. 로그: artifacts/movement-handoff-full-tests.log. git diff --check 종료 코드 0 (기존 LF/CRLF 안내만 있음).
실제 게임 이동·ESP32 전송 지연·라이브 웹 화면은 이번에 실행 검증하지 않았다. Python 변경은 Agent 재시작 후 적용된다. 웹 소스와 번들은 이번 변경에 포함되지 않는다.


## 이동 중 목표 고정과 도착 후 경로 계산 (2026-10-08)
변경: app/core/click_journey.py, app/ai/main_agent.py, app/ai/visual_agent.py, tests/test_click_journey.py, tests/test_main_agent.py, BASIC_COMBAT.md·MINIMAP_MAPPING.md·게임별 GAMEPLAY.md.
실제 위치가 진행 중인데 통로 표시 변경 또는 과거 stuck 플래그만으로 목표를 해제하던 조건을 제거했다. 실제 위치 변화가 1.2초간 없는 경우에만 정체 복구한다. 도착 전 다음 경로 계산/캐시 사용을 없애고, 도착 확인 후 기존 planner 목표를 비운 뒤 도착 위치에서 새 경로를 계산한다. 탐색은 방금 진행한 방향을 이어 사용하며 클릭 후 추가 대기와 쿨다운은 0을 유지한다. 버프 만료가 목표 유지 검사를 우회하여 이동 planner를 실행하던 경로를 수정했다. 의미 확인/공격 대상 재확인 대기 STOP도 현재 이동 목표를 지우지 않는다. 실제 공격/회피·지도 연결 실패·명시적 중단의 안전 우선순위는 유지한다.
실행 명령: python -X utf8 -m unittest tests.test_click_journey tests.test_main_agent; python -X utf8 -m unittest tests.test_main_agent.AgentTests.test_buff_due_does_not_plan_competing_move_while_goal_locked; python -X utf8 -m unittest discover -s tests; git diff --check.
관련 최초 실행 96개 중 버프 테스트 1개 실패: 복사한 프로필에서 CAST_BUFF가 비활성화되어 있었다. 테스트에서 버프를 명시적으로 활성화하여 실제 분기를 검증했고 단독 테스트 통과. 최종 전체 349개, 36.757초, OK (skipped=2), 종료 코드 0: artifacts/arrival-only-full-tests.log. git diff --check 종료 코드 0. 수정 파일 UTF-8 재읽기·Python 컴파일 확인.
실제 게임·ESP32 입력과 웹 화면을 라이브로 검증하지 않았다. Agent 재시작 후 적용되며 웹 소스 변경은 없다.


## Qwen-VL 옆 컨트롤러 배치 (2026-10-08)
변경 파일: Visual-Agent-Lab-local/app/live-agent.tsx, Visual-Agent-Lab-local/app/globals.css 및 dist 빌드 산출물.
게임 화면을 2열로 구성했다. 왼쪽은 Qwen-VL 상태·대화이며 오른쪽은 화면/미니맵 미리보기, 연결/중단 안내, 미니맵 이동 기록, 실시간 키보드·마우스 컨트롤러다. 1100px 이하에서는 세로 배치로 전환한다. 오른쪽 미리보기 팝업은 오른쪽 끝에 맞춰 화면 밖으로 넘어가는 것을 줄였다.
실행 명령: npm run typecheck; npm test; npm run build; git diff --check.
타입 검사 통과, 웹 테스트 16개 통과, 빌드 성공(기존 500kB 번들 크기 안내), git diff --check 종료 코드 0. 수정 파일을 UTF-8로 다시 읽어 적용을 확인했다. 브라우저 화면은 이번에 직접 확인하지 않았다. 대시보드 새로고침 후 새 배치를 사용한다.


## 어두운 던전 맵핑과 빠른 우클릭 (2026-10-08)
변경 파일: app/core/minimap_memory.py, app/ai/main_agent.py, app/profiles/runtime_settings.py, app/profiles/diablo4/navigation.json·input.json·GAMEPLAY.md, Visual-Agent-Lab-local/app/gameplay-controls.tsx 및 dist 산출물, tests/test_minimap_memory.py·test_main_agent.py.
첨부 사진에서 지도 본체는 상단 오른쪽에 있고, 기존 [820,140,988,295] 전체 캡처 ROI는 새 사진의 지도를 온전히 포함하지 못한다. 사진 배치를 기준으로 게임 영역 [818,46,989,237], crop_to_viewport=true로 수정했다. 화면 검은 여백을 제외한 게임 영역에 적용한다. IV 지형 모드는 diablo4_auto로 변경했다. 밝은 야외 판정이 실패하면 고정 V=138 대신 로컬 대비로 어두운 방·통로를 판정하며 플레이어와 연결된 영역만 사용한다. 고립 영역·어두운 섬·단색 지도는 통로로 만들지 않는다. II의 별도 wall_lines 모드는 유지한다.
공격은 basic_attack_mode=tap, tap_ms=20, pointer_duration_ms=60으로 변경했다. 최종 실행 경계에서 tap 모드의 ATTACK·USE_SKILL은 maintain_attack=False를 적용해 모델의 이전 유지 플래그도 제거한다. 적 검증·아군 보호·적 HP바 추적·포커스/중단 안전 조건을 유지한다. 반복 주기는 기존 전투 스케줄러 제한을 따르며 20ms는 버튼을 누르는 시간이다.
검증 명령: python -X utf8 -m unittest tests.test_minimap_memory tests.test_main_agent tests.test_attack_hold tests.test_esp32_ble; python -X utf8 -m unittest discover -s tests; npm run typecheck; npm test; npm run build; git diff --check.
관련 Python 123개 통과. 최종 전체 353개, 36.815초, OK (skipped=2), 종료 코드 0: artifacts/dungeon-tap-full-tests.log. 웹 타입 검사·16개 테스트·빌드 통과(기존 번들 크기 안내). 프로필 JSON 검증·수정 소스 재읽기·컴파일·git diff --check 통과.
던전 테스트는 첨부 사진의 특징을 참고한 합성 지도이며 첨부 이미지 픽셀에서 직접 마스크를 검증한 결과는 아니다. 실제 게임·ESP32 지연·라이브 웹 미리보기는 이번에 실행 검증하지 않았다. Agent 재시작과 대시보드 새로고침 후 적용된다.


## 예상 목표를 유지하는 막힘 복구 (2026-10-08)
변경 파일: app/core/click_journey.py·minimap_memory.py, app/ai/main_agent.py, Visual-Agent-Lab-local/app/minimap-mapping.tsx 및 dist, 관련 Python 테스트 3개, BASIC_COMBAT.md·MINIMAP_MAPPING.md·게임별 GAMEPLAY.md.
최종 destination을 실제 중간 클릭 goal과 분리했다. 정체 복구와 중간 지점 도착 후에도 동일한 목적지·목표 번호·누적 이동 거리를 유지한다. planner의 target_world 경로는 탐색 점수나 회복 단계로 다른 목표를 고르지 않고 같은 최종 목적지까지 Dijkstra 경로를 찾는다. 목적지가 연결되지 않으면 다른 탐색 지점으로 대체하지 않는다. 최종 목적지가 같은 그리드 셀 안에 있는 경우에도 검증된 정확한 끝점을 클릭 지점으로 사용한다. 중간 도착은 waypoint_arrived, 실제 최종 도착만 arrived로 처리한다. 하늘색 예상 목표와 경로 상태 표시도 최종 목적지 유지에 맞췄다. 전투/회피·포커스·지도 좌표 연결 실패·명시적 중단은 기존 제어 조건을 유지한다.
실행 명령: python -X utf8 -m unittest tests.test_click_journey tests.test_minimap_memory tests.test_main_agent; python -X utf8 -m unittest tests.test_minimap_memory.MinimapMemoryTests.test_fixed_destination_is_not_replaced_by_exploration_or_unreachable_fallback tests.test_click_journey tests.test_main_agent.AgentTests.test_stalled_destination_is_passed_to_planner_and_stays_visible; python -X utf8 -m unittest discover -s tests; npm run typecheck; npm test; npm run build; git diff --check.
관련 최초 실행에서 목적지 고정 테스트 1개 실패: 테스트가 실제 합성 지도의 벽 위 좌표를 도달 가능한 점으로 지정했다. 연결된 꺾임 통로의 좌표로 바로잡고, 벽 위 목적지는 경로 반환을 거부하는 검증도 유지했다. 수정 후 관련 26개 통과. 최종 전체 356개, 36.399초, OK (skipped=2), 종료 코드 0: artifacts/fixed-destination-full-tests.log. 웹 타입 검사·16개 테스트·빌드 통과(기존 번들 크기 안내). git diff --check 종료 코드 0. 수정 파일 재읽기/컴파일 확인.
실제 게임·HID 입력은 이번에 실행 검증하지 않았다. Agent 재시작 및 대시보드 새로고침 후 적용한다. 물리적으로 막힌 곳이나 검증되지 않은 지도에서 실제 도착을 보장하는 변경은 아니다.


## HUD 영역 미리보기 및 던전 이동/자동 분석 부하 (2026-10-08)
변경: app/web/screen_preview.py·bridge.py, app/core/minimap_memory.py, app/ai/main_agent.py, Visual-Agent-Lab-local/app/game-screen-preview.tsx·css 및 dist, 관련 Python 테스트.
기존 열린 8766 대시보드에서 자동사냥/게임 전경 상태인데 미니맵 원본의 통로 마스크가 0%, 지형 판정 실패로 이동이 보류되는 것을 확인했다. 그림을 열어 캡처 해상도 1718x1368, 게임 영역 1718x1072도 확인했다. 당시 nvidia-smi는 GPU 24%, VRAM 8050/10240MiB이고 llama /slots는 is_processing=false였다. 페이지에서는 다른 순간 GPU 30~40%가 표시되었다. GPU 100%는 재현하지 못했으므로 단일 원인으로 확정하지 않는다.
던전 판정은 밝은 아이콘을 대비 기준에서 제외하고 플레이어 주변 바닥을 기준으로 Otsu 임계값을 보완한다. 연결된 큰 방이 75%를 넘으면 전부 실패하던 면적 상한을 야외와 같은 94%로 조정했다. 단색/아이콘뿐인 지도 거부·연결 영역·벽 여유는 유지한다. 관련 합성 지도 검증을 추가했다.
D4에서 빨간 HP바로 이미 확인된 적은 자동 Qwen 재분석을 요청하지 않는다. 실제 백그라운드 요청 종료 후 12초 휴지기를 둔다. 사용자 대화와 시작 1회 HUD 보정에는 휴지기를 적용하지 않는다. 이동은 로컬 OpenCV 판정으로 계속 처리한다. 이는 불필요한 자동 추론 제한이며 게임 렌더링을 포함한 전체 GPU 점유율을 제한하는 기능은 아니다.
미리보기 원본 화면에 HP·SP·MP·BUFF·SKILLS 색상 테두리와 범례를 추가했다. 최신 HUD 추적 좌표를 사용하고 원본 캡처는 변경하지 않는다. 스킬의 개별 visual_ready ROI가 있으면 표시하며, D4 스킬바 ROI가 없으면 게임 영역 기준 기본 영역임을 명시한다. 이는 아이콘/상태 인식 결과가 아니라 분석 영역 표시다.
실행 명령: python -X utf8 -m unittest tests.test_screen_preview tests.test_minimap_memory tests.test_main_agent tests.test_web_bridge; python -X utf8 -m unittest discover -s tests; npm run typecheck; npm test; npm run build; git diff --check.
관련 최초 실행에서 비동기 분석/초기 보정 테스트 2개 실패했다. 새 휴지기 검사가 초기 HUD 보정에 잘못 들어간 부분을 백그라운드 함수로 옮겼고, 추적 비차단 테스트는 의미 분석이 필요한 경우를 명시하도록 수정했다. 최종 전체 360개, 36.894초, OK (skipped=2), 종료 코드 0: artifacts/preview-movement-load-full-tests.log. 웹 타입 검사·16개 테스트·빌드 통과(기존 번들 크기 안내). 파일 재읽기/컴파일 및 git diff --check 통과.
변경 후 실제 게임의 이동/최대 GPU 부하는 검증하지 않았다. 실행 중 Agent는 재시작해야 Python 변경이 적용되고 대시보드는 새로고침이 필요하다.


### 2026-10-08 Qwen 대기 표시 후속 확인
- 사용자 보고: GPU 100% 시 QWEN 장면인식대기 표시. 코드 점검 결과 이 문구는 적 객체가 없을 때의 공격 진단 문구이며, 실제 Qwen 요청 여부나 이동 차단 원인을 나타내지 않았음.
- GameBot 공격 진단을 확인된 적 없음 / 미니맵 이동 가능으로 정정. 실제 이동 보류 사유는 별도의 이동 상태에서 표시. 유효한 지도와 최신 HP / 포커스 조건은 유지.
- 테스트 추가: 장면 unknown 및 Qwen idle/waiting/analyzing 모두 유효한 미니맵 이동 허용, 공격 진단이 Qwen 대기로 표시되지 않음.
- 실행: python -X utf8 -m unittest discover -s tests -p test_main_agent.py. 결과: 77 tests, OK. 로그: artifacts/qwen-wait-status-tests.log. GPU 100%와 실게임 이동은 이번 후속 작업에서 재현 검증하지 않았음.


### 2026-10-08 예정 목표 보존 및 지형 실패 후속 수정
- 원인: ClickJourney가 미측정 0.6초 후 map_lost로 목표를 해제하고, MinimapMemory는 정렬 실패 0.6초 후 좌표 구간을 초기화했다. 단순 판정/정렬 실패가 예정 목표 소실을 일으킬 수 있었음.
- 변경: 미측정 동안 기존 목표/목표 번호 유지, 새 클릭은 기존 지도 유효성 검사로 차단. 정렬 실패만으로 좌표 구간을 초기화하지 않으며, 2초 이상 실패와 지도 평균 명암 차이 35 초과가 모두 확인될 때만 구간 초기화. 실제 도착과 정체 복구는 유지.
- 던전 지형: 로컬 대비 최솟값을 10에서 6으로 조정하여 어두운 낮은 대비 방을 인식. 단색 거부 및 중앙 바닥/연결 영역 검증 유지.
- 공격 설정 확인: 기본 우클릭 tap, tap_ms=20, ATTACK 쿨다운 0.15초. 유지 HOLD가 아닌 반복 클릭. 포인터 이동/장치 응답으로 실제 속도는 낮아질 수 있음. 공격 설정은 이번 작업에서 변경하지 않음.
- 테스트: python -X utf8 -m unittest discover -s tests; 363 tests, 36.354초, OK (skipped=2). 로그 artifacts/persistent-minimap-goal-tests.log. 최초 관련 테스트의 구간 전환 기대 시점을 새 2초 확인 조건에 맞춰 변경. 파일 재읽기/컴파일 검증.
- 실제 게임 이동, 지형 및 공격 전송 속도는 이번 작업에서 검증하지 않았음. Agent 재시작 필요.


### 2026-10-08 예정 방향 고정 / 방문 경로 후순위
- MinimapMemory: 탐색 목표의 4초 유효기간 및 새 heading에 따른 목표 교체 조건 제거. 기존 목표가 현재 통로에서 확인되지 않으면 다른 목표 대신 경로 대기. 탐색 종점은 미방문 우선, 같은 분류 안에서 기존 방향/중앙 통로 점수 사용. 방문 경로 비용 계수 0.08에서 0.8로 강화.
- ClickJourney/MainAgent: 유효한 새 지도 구간에 마지막 측정 위치로부터 남은 목표 벡터를 재배치. 목표 번호/남은 방향 보존, 확인되지 않은 이동량은 거리 기록에 더하지 않음. 새 구간 미측정 중에도 현재 목표 유지. 실제 클릭은 지도 유효성과 기존 장애물 검사를 통과해야 함.
- 테스트 추가: 새 구간 재배치의 방향/거리/번호 보존, 미측정 새 구간 중 목표 보존, 목표 만료/반대 방향 후보에도 목표 고정, 미방문 종점 우선.
- 실행: python -X utf8 -m unittest discover -s tests => 366 tests, 37.140초, OK (skipped=2). 이후 미측정 새 구간 보존 보완 및 추가 테스트에 python -X utf8 -m unittest discover -s tests -p test_click_journey.py => 26 tests, OK. 로그 artifacts/locked-heading-tests.log 및 artifacts/locked-heading-final-tests.log.
- 파일 재읽기/컴파일/변경 파일 diff 검증. 실게임 동작 검증 및 Agent 재시작은 수행하지 않았음.


### 2026-10-08 미니맵 이어 붙이기 / 누적 전체 지도
- app/core/minimap_memory.py: 공통 좌표의 누적 통로/장애물과 방문 칸을 유지, 12000칸 초과 시 먼 지역 삭제 제거. 알려진 지도 주변의 미탐색 칸도 탐색 점수에 반영.
- 겹치는 기준 화면 최대 16장을 보관하여 프레임 연결 실패 시 최근 6장으로 복구 시도. 2초마다 이전 기준 화면으로 3px 이하의 작은 좌표 누적 오차 보정. 검증되지 않은 지도는 합치지 않고 이전 구간 6개 별도 보관.
- 누적 지도 snapshot atlas: grid/bounds/player/resolution/archived_segments. 최대 192칸 표시, 실제 누적 데이터는 축소하지 않음. 웹 반복 요청에는 1초 캐시.
- Visual-Agent-Lab-local/app/minimap-mapping.tsx: 현재 로컬 지도 외에 누적 전체 통로 지도 캔버스/미탐색 범례/위치/보관 구간 수 표시. 프론트 dist 재빌드.
- 테스트: 스크롤에 따른 영역 확장, 예전 칸 유지, 기준 화면 복구, 구간 별도 보관/명시적 reset, 누적 오차 보정 및 JSON 직렬화 검증.
- 실행: python -X utf8 -m unittest discover -s tests => 371개 37.737초 OK (skipped=2), artifacts/accumulated-atlas-tests.log. 이후 오차 보정 테스트 추가 후 python -X utf8 -m unittest discover -s tests -p test_minimap_memory.py => 38개 OK, artifacts/accumulated-atlas-final-tests.log. npm run typecheck 통과, npm test 16개 통과, npm run build 통과(기존 500kB 번들 안내). 변경 파일 재읽기/컴파일/diff 검증.
- 누적 데이터는 실행 세션에만 유지하며 디스크 재시작 복원은 구현하지 않음. 실게임에서 지도 연결 품질/성능은 이번 작업에서 확인하지 않았음. Agent 재시작과 웹 새로고침 필요.


### 2026-10-08 게임 활성화 무조건 시작/재개
- app/ai/main_agent.py: focus_activation_starts_hunt 활성화(게임별 GameBot 프로필에 공통). 비활성 이동 진단은 즉시 중단 및 활성화 자동 재개로 표시.
- app/ai/visual_agent.py: 위 플래그가 있는 에이전트는 최초 활성화와 포커스 복귀에서 /hunt 실행. 최초 비활성도 /stop 실행. 기존 YOLO 에이전트의 재개 조건은 유지. 활성화 전환은 수동/HP 처리 중단도 재개하되 최신 측정 입력 가드는 유지. 같은 포커스에서 반복 재시작하지 않음.
- 비활성 STOP은 기존 emergency 제출, 명령 epoch 무효화, HUD/객체/지도 최신 상태 폐기, 모든 처리의 포커스 가드를 적용. 포커스 감시는 기존 10ms 주기이며 OS/이벤트 루프 지연은 있을 수 있음.
- tests/test_main_agent.py: 최초 활성/비활성, 중복 재시작 방지, 수동 중단 후 포커스 복귀 재개, HP 중단 복귀 및 최신 측정 요구 검증.
- 실행: python -X utf8 -m unittest discover -s tests => 374 tests, 39.138초, OK (skipped=2). 로그 artifacts/focus-auto-hunt-tests.log. 변경 파일 재읽기/컴파일/diff 검증. 실게임 포커스 전환 및 장치 STOP 시간은 확인하지 않았음. Agent 재시작 필요.


### 2026-10-08 미리보기 전체 지도 / 지도 확장 목적의 탐색
- app/web/bridge.py와 screen_preview.py: 저장된 atlas 복사본을 미리보기 요청에 포함. 누적 전체 통로 PNG, 마지막 플레이어 위치, 기억한 목표 및 지도 표시 해상도 추가. 캡처와 지도 상태를 수정하지 않는 읽기 전용 처리.
- Visual-Agent-Lab-local/app/game-screen-preview.tsx: 화면·미니맵 미리보기 안에 누적 전체 지도 이미지/범례/현재 위치/목표를 표시. dist 재빌드.
- app/core/minimap_memory.py: 후보 위치에서 보일 미니맵 영역의 미탐색 칸 수를 누적 지도와 비교. 영역 합 계산은 현재 미니맵 주변 3배 격자의 누적합을 사용해 전체 기억 크기에 비례한 후보별 탐색을 피함. 안전한 중앙 통로 후보 우선, 미방문 우선, 새 지도 노출량 우선, 같은 경우 기존 방향/경로 비용 점수. 명시적 이동/복구 경로 순위는 변경하지 않음. 목표 고정 규칙 유지.
- 테스트 추가: 이미 매핑된 오른쪽보다 새 지도가 나타나는 반대편을 선택, atlas 미리보기 PNG 생성/기존 atlas 불변. 최초 전체 실행에서 중앙 통로 여유 테스트 1개 실패하여 탐색 후보의 중앙 여유 우선 조건을 추가.
- 실행: python -X utf8 -m unittest discover -s tests => 최종 376 tests, 39.484초, OK (skipped=2). 로그 artifacts/atlas-preview-frontier-final-tests.log. 관련 미니맵 39개 통과. npm run typecheck, npm test(16개), npm run build 통과(기존 번들 500kB 안내). 파일 재읽기/컴파일/변경 파일 diff 확인.
- 실게임 미리보기와 지도 확장 동작은 이번 작업에서 확인하지 않았음. Agent 재시작과 웹 새로고침 필요.


### 2026-10-08 사용자 BUFF 아이콘 영역 적용
- 첨부 버프 아이콘 줄 및 이전 전체 게임 캡처를 기준으로 디아블로 IV BUFF 영역을 스킬바 위로 설정. 기존 [0,0,1000,1000] 전체 화면 검색을 [389,807,568,857] 게임 화면 상대 좌표로 축소.
- app/vision/game_viewport.py hud_region_bbox 추가. app/vision/buff_monitor.py 검출 crop, app/web/screen_preview.py BUFF 표시, app/vision/click_safety.py HUD 클릭 제외 모두 검은 여백 보정에 같은 변환 사용. HP/SP/MP 및 버프 키/disabled_actions 수정 없음.
- hud.json 문서 검증, 소스 재읽기/컴파일, 버프 crop과 클릭 제외가 검은 여백 유무에 동일하게 대응하는 테스트 추가.
- 실행: python -X utf8 -m unittest discover -s tests => 377 tests, 39.936초, OK (skipped=2). 로그 artifacts/buff-region-tests.log. 변경 파일 diff 검증. 실제 최신 게임 캡처에서 테두리/검출은 확인하지 않았음. Agent 재시작 필요.


### 2026-10-08 이동 방식 변경: 짧은 클릭 / 실제 이동 피드백
- 사용자 요청: 기존 예정 방향까지 진행하지 못해 다른 방법으로 전환. 디아블로 IV navigation.json에 control_mode feedback_steps 적용. 기존 point_goal 방식은 다른 프로필 및 회귀 검증용으로 유지.
- ClickJourney: 큰 frontier destination과 작은 실제 클릭 goal 분리, 실제 진행 확인 step_progress로 다음 구간 계획을 허용. 전송 최소 0.25초/전송 후 최신 지도 0.4초 이내/측정 이동 1.5px 이상을 함께 요구. 목표 번호/남은 큰 목표/누적 이동거리 유지.
- MainAgent: 다음 작은 구간은 최대 8지도 px. step_progress 후에도 고정된 최종 목표 target_world를 경로 계획에 전달. 실제 입력 전 포커스/HP/지도/장애물/HUD 검사 유지. 웹 step_progress 한국어 상태 추가 및 dist 빌드. Qwen 호출 추가 없음.
- tests: 기존 point_goal 회귀 테스트는 명시적으로 해당 모드 사용. 새 feedback 경로에서 최신 실제 변위 없이는 반복 클릭하지 않음, 큰 목표와 작은 클릭 분리, 다음 계획이 같은 목표/8px 상한을 사용하는지 검증.
- 실행: python -X utf8 -m unittest discover -s tests => 379개 39.110초 OK (skipped=2), artifacts/feedback-navigation-tests.log. 이후 별도 목표 저장 테스트 추가 후 python -X utf8 -m unittest discover -s tests -p test_main_agent.py => 81개 OK, artifacts/feedback-navigation-final-tests.log. npm run typecheck, npm test(16개), npm run build 통과(기존 번들 크기 안내). JSON 검증 및 변경 파일 재읽기/컴파일/diff 검증.
- 실제 게임 진행은 검증하지 않았음. Agent 재시작 및 웹 새로고침 필요.


### 2026-10-08 사냥 방향 / 근접 목표 연결 / 화면 장애물 회피
- MainAgent: 방향이 포함된 사냥 시작을 로컬 해석하고 지속 우선 방향 저장. 미니맵 탐색 후보는 중앙 여유/미방문/방향/새 지도 노출량 순위. 기존 최종 목표는 유지하며 근접 시 다음 목표 선택.
- ClickJourney: feedback_steps에서 최종 목표 4px 이내, 최신 지도, 클릭 이후 최소 0.25초 조건일 때 near_goal 해제. 실제 정체 1.2초이면 제한된 Qwen 장애물 분석 요청.
- 장애물 요청은 15초 간격, 단일 비동기 작업, 최대 4개/256토큰/512px/6초. 검출 화면 바닥 박스를 공통 지도 좌표로 변환해 8초 임시 경로 비용(30)을 추가. 단축 경로가 해당 칸을 가로지르는 것을 금지. 실제 공격 객체와 영구 지도 근거는 불변. 늦은 세대/구간/포커스 응답 폐기 및 중단 시 비동기 작업 취소.
- 작업 중 현재 navigation.step_fraction이 0.03으로 변경된 것을 확인. 이는 최소 클릭 거리 0.035보다 작아 입력이 막힐 수 있었음. 설정 파일 값은 보존하고 feedback 계획에 최소 반경 0.04 적용(8지도 px 상한과 통로/캐릭터/HUD 가드는 유지). 기존 테스트는 실제 사용자 거리 설정과 독립적인 legacy 0.3을 명시.
- 테스트: 방향 명령이 모델 없이 사냥 시작/경로 우선에 적용, 요청 방향과 미탐색 우선 비교, 가까운 목표 연결, 임시 장애물 투영/공격 객체 불변/만료/단축 경로 교차 금지 및 작은 설정 반경 보완.
- 최초 전체 테스트는 사용자 설정 0.03 때문에 legacy geometry 테스트 2개가 실패했으며 fixture를 독립적으로 설정하고 feedback 최소 반경을 보완. 최종 python -X utf8 -m unittest discover -s tests => 386 tests, 44.839초, OK (skipped=2), artifacts/hunt-direction-obstacle-final-tests.log. 이후 단축 경로 교차 금지 테스트 추가: python -X utf8 -m unittest discover -s tests -p test_minimap_memory.py => 42 tests OK, artifacts/hunt-direction-obstacle-map-tests.log. npm run typecheck, npm test(16개), npm run build 통과(기존 번들 크기 안내). 파일 재읽기/컴파일/diff 검사.
- 실게임의 장애물 식별/우회/명령 수행은 확인하지 않았음. Agent 재시작과 웹 새로고침 필요.


### 2026-10-08 사냥 중단 시 Qwen 요청 취소
- MainAgent: 사냥 중단/중지/그만 및 /stop, /pause, /quit 시 Qwen 중지 플래그, 장면/장애물 작업 취소, 클라이언트 요청 중지. 시작 HUD 보정/동기 장면 분석/자동 장면 예약/모델 대화도 중단 중 호출 금지. 사냥 시작/재개에서 다시 허용하며 기존 포커스 활성화 자동 재개 유지.
- QwenVLClient: GameBot에서 취소 가능한 HTTP 스트리밍 전송을 사용. 활성 소켓 shutdown/close, 신규 요청 차단, 취소 세대 검증으로 빠른 중단/재개 시 이전 요청 전송 방지. SSE 응답은 기존 JSON 검증/토큰 잘림 검사와 동일한 형태로 수집. 기본 비활성 모드는 기존 requests 전송 유지.
- 웹 Qwen 상태에 사냥 중단 · Qwen 처리 중지 표시. 디아블로 IV GAMEPLAY.md에 적용 규칙 기록.
- 새 테스트: 스트리밍 JSON 수집, 진행 중 소켓 취소/재개 전 차단, 빠른 중단/재개 후 이전 요청 차단, 중단 시 시작 보정/장면 분석 금지. 게임 프로필 전환 테스트는 전환 후 명시적으로 /hunt로 재개한 뒤 모델 프롬프트 검증.
- 첫 검증에서 새 테스트 함수명 오류 및 중단된 프로필 전환 테스트 수정. 이후 전체 검사 중 시간에 의존하는 기존 공격 테스트 1개 실패했으나 해당 모듈 재검사 27개 통과. 최종 전체 391 tests, 41.634초, OK (skipped=2): artifacts/hunt-stop-qwen-final-tests.log. 개별 취소 테스트 3개 통과. npm run typecheck, npm test 16개, npm run build 통과(기존 번들 크기 안내). 변경 소스 재읽기/diff 검사.
- 실제 llama-server에서 연결 종료 후 GPU 추론 취소 시점은 확인하지 않았음. 서버 종료 없이 요청 연결만 취소한다. Agent 재시작 및 웹 새로고침 필요.


### 2026-10-08 버프·공격 스킬 미실행 점검
- 실제 diablo4/input.json에서 2번 공격 스킬 enabled=true이지만 disabled_actions에 USE_SKILL과 CAST_BUFF가 함께 있어 두 입력이 차단되는 것을 확인. ProfileStore.apply/revision 검증/백업으로 해당 두 차단만 해제. 1/3/4번 공격 스킬 비활성, 2번 키/10200ms 간격, 버프 키4, 기본 우클릭 tap 설정은 보존.
- 웹 GameSettings에서 공격 스킬 사용 체크 또는 새 활성 스킬 추가 시 USE_SKILL 전체 차단도 해제. 활성 스킬과 전체 차단이 충돌하면 안내 표시. 다른 사용 금지 액션을 유지하며, 단순 키/이름 변경으로 사용 금지를 해제하지 않음.
- 실제 assets/buffs/buff_001.png 41x42 아이콘 템플릿 존재 확인. GameBot의 아이콘 미검출 5초/재시도5초 로직은 유지. 새 통합 테스트는 누락5초 → CAST_BUFF → 컨트롤러4키 → 전송 후 pending 해제, 확인된 적 → USE_SKILL → 등록2키 → 10.2초 재전송 제한을 검증. 실제 장치 입력은 Mock이며 Qwen 요청은 없음.
- 처음 새 공격 테스트는 컨트롤러 진입 이벤트 직후 스케줄러 완료 기록 전 쿨다운을 검사해 실패. 완료 기록을 기다리도록 수정. 최종 python -X utf8 -m unittest discover -s tests: 393 tests, 41.287초, OK (skipped=2), artifacts/buff-attack-skills-tests.log. npm run typecheck, npm test(16개), npm run build 통과(기존 번들 크기 안내). 변경 파일 재읽기/diff 검사.
- 게임에서 실제 아이콘 검출 및 ESP32 키 전송 결과는 확인하지 않았음. Agent 재시작/웹 새로고침 후 사냥 시작으로 적용 확인 필요.


### 2026-10-08 경로 표시 후 실제 클릭 미전송 점검
- 기존 열린 로컬 대시보드를 읽어 게임 전경/자동사냥/HUD 시작 보정 중/캡처 및 처리 미측정/전송한 이동 클릭 없음/GPU100% 상태 확인. 사용자도 클릭 표시가 전혀 없다고 답변. 화면 경로 계산과 별개로 시작 HUD 보정이 _hud_rechecking을 켜 캡처·HP·지도·이동 입력 전체를 막는 코드 확인.
- MainAgent: 시작 보정은 _startup_hud_inflight로 별도 관리. 기존 OpenCV HP가 유효하고 최신 경로/포커스 검사를 통과하면 Qwen 보정 대기 중에도 입력 가능. HP 미확인 자체는 계속 입력 차단. 시작 보정 중복 요청/사냥 중단 처리 유지.
- 시작 HUD 요청은 이미지512px/384토큰/HTTP6초, async 대기6.5초로 제한. 요청 응답 후 현재 캡처에서 OpenCV 바 위치를 다시 얻어 오래된 시작 화면 위치 적용 방지. 보정 요청 실패만으로 현재 추적 영역을 예전 영역으로 되돌리지 않으며, 실제 적용 후 검증 실패 시 직전 영역으로 복원.
- QwenVLClient.discover_hud에 선택적 요청 한도 추가. 취소 가능한 전송의 연결/응답/스트림 읽기 전체가 같은 시간 예산을 사용하고 소켓 읽기 타임아웃을 남은 시간으로 줄임.
- 통합 테스트: 실제 Qwen 호출 대신 응답이 보류되는 Mock 동안 맵핑 경로/좌표 변환/실행 검증/스케줄러/이동 컨트롤러까지 클릭 명령이 전달됨 확인. 별도 요청 시간 예산 검사 추가.
- 전체 python -X utf8 -m unittest discover -s tests: 395 tests, 39.473초 OK (skipped=2), artifacts/move-dispatch-startup-tests.log. 이후 보정 실패 복원 범위 보완 후 관련 Agent 테스트 88개, 12.111초 OK: artifacts/move-dispatch-final-agent-tests.log. 변경 소스 재읽기/컴파일/diff 검사. 웹 코드 변경 없음.
- 실행 중 Agent는 자동 재시작하지 않았음. 수정본 실게임 이동·ESP32 전송/게임 반응·GPU 감소는 확인하지 않았음. Agent 재시작 필요.


### 2026-10-08 등록 버프 아이콘 소실 시 재사용 재확인
- 사용자 요청은 기존 GameBot 동작과 일치: 등록 assets/buffs/*.png가 BUFF 영역에서 5초 연속 미검출이면 CAST_BUFF 키4를 다시 전송. 계속 없으면5초 간격 재시도, 아이콘이 돌아오면 재시도 중지. 현재 CAST_BUFF 금지 없음 확인. 실행 로직/개별 스킬/키 설정 추가 변경 없음.
- 등록 템플릿 이름으로 아이콘 감지 → 소실 → 재사용 대기 → 실제 스케줄러/Mock 컨트롤러4키 전송 → 아이콘 재감지 시 추가 재사용 중지 통합 테스트 추가. Qwen 호출 없음 확인.
- python -X utf8 -m unittest discover -s tests -p test_main_agent.py 실행: 89 tests, 13.990초 OK, artifacts/buff-icon-disappearance-tests.log. 실제 게임 아이콘 검출·물리 키 전송은 검증하지 않음.


### 2026-10-09 디아블로 IV Qwen 규칙 정리 / 버프 소실 1회 발동
- 사용자 적용 요청에 따라 diablo4/GAMEPLAY.md를 현재 규칙으로 통합. 예전 우클릭 유지/시간으로 목표 변경/이전 미니맵 ROI/주기 버프 재시도 안내를 제거하고 tap 공격, 누적 미니맵 feedback_steps, 고정 최종 목표, 최신 입력 피드백 및 포커스/HP/시작 보정 규칙으로 정리. 저장 JSON이 프로필 MD와 공통 규칙보다 우선. 기본 누락 설정에 feedback_steps 추가하며 기존 사용자 JSON 값은 변경하지 않음. BASIC_COMBAT의 버프 반복/시간 목표 교체도 게임 프로필 우선으로 명시.
- Qwen 장면 요청에 현재 공격 방식/키, 실제 전송 클릭/이동량/남은 거리, 확인 적 바 수, 활성 스킬의 실제 전송 쿨다운·시각 준비 상태, 등록 버프 수/활성 아이콘/검사 신선도/발동 허용 여부를 구조화해 전달. 추가 모델 호출이나 이미지 없음.
- BuffMonitor once_per_absence 모드를 GameBot에 적용. 등록 아이콘이 5초 없으면 1회, 성공 전송에서 해당 소실을 소비. 계속 없는 상태에서5초 정기 재시도 제거. 재등장 후 다음 소실에서 재허용. 입력 실패는 소실을 소비하지 않음. 등록 파일 없음/템플릿이 ROI에 맞지 않아 검사 불가일 때는 자동 발동하지 않음. 일반 legacy 모드의 기존 감지 계약 보존.
- MainAgent: Qwen/user intent가 CAST_BUFF를 요청해도 등록 아이콘의 최신 미검출 발동 조건이 없으면 실행 거부. 디아블로 IV 버프 키가 공격 스킬로 중복 등록되어도 인터벌 회전과 USE_SKILL 우회 입력 차단. 현재4번 등록 스킬은 비활성 그대로 보존. 웹 retry_seconds=0/once_per_absence 상태 제공.
- 테스트: 실제 저장 아이콘을 합성 화면에 배치해 존재/소실/전송 후 장기 미검출/너무 작은 ROI 검사, 중복 버프 키 공격 회전 차단, 모델 프롬프트 현재 tap/버프 피드백, 스킬의 실제 시각 준비·쿨다운 반영. 기존5초 재시도 테스트는 최신 요청에 맞게 소실1회/재등장 재허용 검증으로 갱신.
- 첫 전체 검사에서 legacy buff 관찰의 등록필터 호환 실패1개와 시간 의존 공격 테스트1개 실패. 필터는 GameBot 모드에 한정해 호환 수정. 최종 python -X utf8 -m unittest discover -s tests => 401 tests, 42.078초 OK (skipped=2), artifacts/diablo-policy-buff-event-final-tests.log. 이후 공통/프로필 문구 정리 후 관련 Agent 테스트 94개, 13.261초 OK: artifacts/diablo-policy-buff-agent-final-tests.log. 소스 재읽기/컴파일/diff 검사. 웹 소스 변경 없음.
- 실게임 버프 아이콘 비교/ESP32 입력/모델 판단 품질은 검증하지 않았음. Agent 재시작 필요. 이 변경은 이전 동일 소실 중5초 재시도 규칙을 대체함.


### 2026-10-09 버프 등록 이미지 중 하나라도 검출되면 반복 중단
- 사용자 정정 반영: BuffMonitor require_all_absent 모드. assets/buffs에 등록한 이미지 중 하나라도 현재 BUFF 영역에서 검출되면 전체 버프 입력 대기와 미검출 타이머를 취소한다. 다른 등록 이미지가 안 보여도4번 키를 보내지 않음.
- 등록 이미지 모두5초 연속 미검출일 때만4번 키 전송. 모두 없는 동안5초 간격 재시도하며 하나라도 재검출되면 즉시 대기/반복 중지. 미등록/등록 템플릿 일부를 검사할 수 없는 상황은 전체 부재로 단정하지 않고 자동 입력 차단. 기존 버프 키 공격 회전 차단 유지.
- MainAgent Qwen 피드백/웹 상태 및 D4 GAMEPLAY/BASIC_COMBAT 문구를 같은 규칙으로 수정. 이전 동일 소실1회 제한은 최신 요청에 따라 해제.
- 테스트: 등록 이미지2개에서 A만 존재/B만 존재 시 차단, 모두 없는5초 뒤 발동/5초 재시도, 하나 재등장 시 타이머 취소, 다시 모두 사라진 뒤5초 확인, 일부 검사 불가에서 입력 차단. 기존 소실1회 테스트를 최신 반복 조건으로 갱신. python -X utf8 -m unittest discover -s tests 실행: 402 tests, 41.636초 OK (skipped=2), artifacts/buff-any-icon-tests.log. 변경 소스 재읽기/컴파일/diff 검사.
- 실제 게임의 아이콘 매칭과 물리 키 입력은 확인하지 않았음. Agent 재시작 필요.


### 2026-10-09 적 HP 잔량 감소 시 공격 지속 / 검은 바 사망 확인
- 원인: 빨간 연결 요소의 최소 폭 20px 조건 때문에 HP가 줄면 적 바 확인과 공격 허용이 풀림. 빨간 잔량과 검은 사각 배경을 결합해 전체 바를 추적하도록 수정. 잔량 1px도 확인하며 전체 바가 확인된 경우만 관측 HP를 계산. 빨간 부분만 검출되면 HP 비율을 추측하지 않음.
- 이전 1초 내 확인한 적 바와 겹치는 완전 검은 바만 HP 0으로 읽음. 처음부터 존재한 검은 사각형은 새 적으로 생성하지 않으며, 빨간 잔량과 닿은 검은 부분도 0으로 읽지 않음. 감소를 관측하고 0을 연속 3회 확인하면 기존 전투 가드가 종료. 바 소실은 미확인으로 유지. 우클릭 빠른 tap 연타 유지, 추가 Qwen 요청 없음.
- Diablo until_bar_lost 모드는 1% 미만의 실제 관측 감소도 피해 진행으로 인정. 다른 전투 모드의 기존 임계값 유지. 합성 화면에서 잔량 58→38→18→4→1px 동안 같은 추적 ID/공격 허용 유지, 완전 검정 3회에서 종료, 미확인 검은 사각형은 적 생성 안 함 검증.
- 최초 테스트에서 큰 빨간 잔량이 검은 배경 밀도 조건에 걸림. 검정 최소 밀도를 완화하고 빨강+검정 사각 coverage 90% 조건으로 수정. 최종 전체 unittest: 404 tests, 41.817초 OK (skipped=2), artifacts/enemy-bar-shrink-final-tests.log. combat_feedback 단독 18 tests OK. 소스 재읽기/컴파일/diff 검사.
- 실제 게임 화면과 ESP32 입력은 검증하지 않음. 실행 중인 Agent 재시작 필요.


### 2026-10-09 버프 4초 연속 부재 확인 / 클릭 거리 및 연속 이동
- 사용자 요청 반영: GameBot BuffMonitor 부재 확인/재시도 기준을4초로 변경. 등록 이미지 하나라도 BUFF 영역에 보이면 전체 반복 취소. 성공 전송 후 부재 타이머 초기화하여 다음 재시도도 새4초 확인 필요. HUD 버프 검사 공백이1초 이상이면 부재 타이머 초기화. 미등록/검사 불가능은 자동 발동 차단 유지.
- 등록 아이콘의 크기 차이(0.5~1.6배)를 검사하고, 테두리와 변하는 하단 카운트 숫자를 제외한 내부 상단 그림도 매칭. Qwen 추가 요청 없음. 다른 legacy 사용처는 기존 매칭 모드 유지.
- 클릭 거리가 달라지지 않던 원인: feedback_steps가 설정값과 관계없이 최대8지도 px로 제한. 해당 제한 제거. 중앙 클릭 거리(%)를 화면 짧은 변의 픽셀 반경으로 투영하며 검증된 통로/코너/HUD 제외 조건에 따라 필요한 만큼 줄임. 설정20%→40%에서 LocalNavigator의 실제 클릭 반경2배 확인.
- 최신 지도0.4초 이내에서 최소0.12초 간격 step_refresh로 같은 최종 목적지를 재클릭. 클릭 전송 중 중복 큐 금지 유지. 목표 ID/남은 최종 목표/이동 기록 유지하며 반복 클릭으로 정체 측정 타이머를 리셋하지 않아1.2초 실제 정체에서 우회 복구 가능. 웹 목표 유지 상태에 step_refresh 반영. 포커스/HP/지형/전투 검증 유지.
- 테스트 추가/갱신: 확대 아이콘의 하단 숫자 변경 후 검출/반복 취소,4초 전 차단/전송 후 새로운4초 확인,실제 클릭 반경 변화,연속 클릭 시 동일 목표와 정체 검출. 첫 전체 검사에서 새 거리 테스트의 mapping enabled 누락 수정. 관련 Agent99 tests14.405초 OK. 이후 짧은 실시간 대기 기반 기존 공격 테스트가 간헐 실패하여 원인 확인. 공격 회전 테스트는 세 번째 명령 도착 이벤트를 최대0.45초 기다리는 방식으로 검증 변경, 순서 검증 유지.
- 전체 python -X utf8 -m unittest discover -s tests:407 tests41.695초 OK(skipped=2), artifacts/buff-distance-repeat-verified-tests.log. 실제 게임 화면 아이콘/물리 클릭은 검증하지 않음. Agent 재시작 필요.
- 공격 회전 테스트의 이벤트 대기 변경 후 웹 관련 unittest 재검증:72 tests14.662초 OK(skipped=1), artifacts/buff-distance-repeat-web-tests.log. 변경 파일 재읽기/컴파일/diff 검사 완료.


### 2026-10-09 예정 경로/직선/실제 클릭 분리 및 이동 중단 점검
- 실행 중 로컬 Agent(8766) 읽기 점검: GPU100%, 캡처2.5~3.5FPS에서 PATH_BLOCKED와 PAUSED_OR_NO_FRESH_HUD STOP이 반복. 실제 이동 명령 전송과 짧은 이동 기록은 있었으나 이후 경로 차단/HP 측정 신선도 만료로 중단됨. 실행 상태가 buffs.absence_seconds=5를 보고하여 이전4초/step_refresh Python 수정 미반영 확인. 실행 중 Agent는 재시작하지 않았음.
- MainAgent의 최근 HP 측정 유효 시간을0.5→0.8초로 통일. 캡처는1초 제한 유지, health_valid=false/사망/포커스/중단은 기존 입력 차단. 낮은 캡처 FPS에서 정상 측정의 짧은 간격 초과 때문에 STOP이 반복되는 부분 완화. HUD 위치·클릭 제외·웹 HP도 같은0.8초 기준 적용.
- 검증된 코너 waypoint가 짧을 때도 고정 최소 정규화 거리0.035 때문에 실행 불가였던 부분을 최대0.035/화면 폭 기준18px의 작은 값으로 처리. 플레이어/HUD/아군 영역과 전체 통로 검증은 유지. 60px 코너 클릭 전송 허용, 중앙 캐릭터 클릭 거부 테스트.
- 웹 상태가 실제 예정 통로 route를 전송 클릭까지의 직선으로 덮어쓰던 코드 제거. UI:① 하늘색 예정 경로,② 노란 점선 최종 목표 직선,③ 주황색 실제 클릭 방향과×,④ 분홍색 실제 측정 궤적. step_refresh 상태 문구 추가. 최종 목표 고정 유지.
- 화면·미니맵 미리보기는 마지막 캡처+전체 지도. Controller에는 미니맵 원본+초록 플레이어 십자와 요청한 캡션/alt 표시. 중복 전체 지도는 Controller에서 제거. 두 미리보기의 동일 인증 요청은 진행 중/650ms 결과 캐시를 공유하고, 닫았다 열 때 마지막 데이터 유지, 프로필/세션 변경 시 초기화. 인증 정책 변경 없음.
- 테스트:0.65초 정상 HP 사용/0.9초 만료 및 미검출 차단, 안전한 짧은 코너 클릭, 실제 ActionScheduler/Mock 컨트롤러에 같은 최종 목적지로2회 이동 전송, 예정 꺾인 경로 보존. 첫 전체 검사에서 이전 직선 덮어쓰기 기대 테스트1개 실패하여 새로운 경로/클릭 분리 계약으로 수정. 최종 python -X utf8 -m unittest discover -s tests:410 tests43.510초 OK(skipped=2), artifacts/movement-three-directions-final-tests.log.
- 웹 npm run typecheck 통과, npm test16개 통과, npm run build 통과(dist 갱신, 기존 큰 번들 경고). 기존 로컬 탭 새로고침 후 AX 및 화면으로 Controller 원본 이미지/3방향 범례/미리보기 캡처·전체 지도 확인. artifacts/controller-directions.png, artifacts/controller-minimap-preview.png. 새 탭 생성 없음. Python 소스/설정/웹 변경 재읽기·컴파일·diff 검사.
- 실제 게임의 새 이동 로직은 Python 프로세스 재시작 전이므로 검증하지 않았음. Agent 재시작 필요. 온라인 게임 입력을 강제로 재개하지 않았음.


### 2026-10-09 기본 자동사냥 / 목표 도착·전투 전환 / 수동 중단
- 작업 대상: C:/ai/project/Visual-Game-Agent-GameBot. MainAgent 기본 상태를 자동사냥으로 변경하고 main/web 시작 상태와 표시를 일치시킴. 실제 입력은 게임 활성화와 최신 HP·지도 검증을 통과해야 실행. 기존 legacy 기본 상태는 유지.
- 목표 도착 확인 후 기존 목표를 해제하고 다음 탐색 목표를 선택·전송하는 흐름, 이동 중 확정 적이 나타나면 이동보다 공격을 우선하는 흐름을 실제 ActionScheduler와 Mock 컨트롤러 통합 테스트로 확인. 추가 Qwen 요청 없음.
- 명시적 사냥 중단은 manual_control 상태로 전환하고 Qwen 요청·대기 명령을 취소하며 유지 공격을 즉시 해제. STOP은 쿨다운과 무관하게 실행. 수동 상태에서는 입력 루프의 반복 STOP 및 비상키의 반복 STOP 전송을 차단해 사용자 조작을 방해하지 않음. 웹에 사용자 직접 조작 상태 표시.
- 자동사냥 중 포커스 이탈은 중단, 복귀는 재개. 사용자가 사냥 중단을 누른 경우에는 포커스 복귀 후에도 수동 상태를 유지하며 사냥 시작/재개를 눌러야 자동사냥으로 복귀. 이 규칙은 이전 무조건 포커스 복귀 재개 요청을 최신 수동 조작 요청에 맞춰 대체함. diablo4/GAMEPLAY.md에 적용하고 모델 규칙 2500자 한도 확인.
- 검증: 기본 자동사냥 생성, 명시적 중단 후 유지 공격 해제·추가 자동 입력 없음, 포커스 복귀 시 자동/수동 구분, 목표 도착 후 새 목표 전송, 이동 중 확정 적 공격 전환. 최종 python -X utf8 -m unittest discover -s tests: 414 tests, 46.056초 OK(skipped=2), artifacts/auto-hunt-manual-control-final-tests.log. npm run typecheck / npm test(16개) / npm run build 통과. Python 컴파일 및 변경 파일 재읽기/diff 검사.
- 실행 중 Agent는 재시작하지 않았음. 실게임/ESP32 물리 입력은 검증하지 않았으며 변경 적용에는 Agent 재시작 필요.


### 2026-10-09 목표 근접 전환 / 진행 방향 우선 목표
- ClickJourney feedback_steps의 다음 목표 선택 반경을 고정4px에서 전체 예정 이동 거리의15%(최소4px/최대12px)로 변경. 최신0.4초 이내이며 해당 클릭 이후 측정된 지도·0.12초 이상 경과·실제 변위 확인이 필요. 중간 클릭 지점 도착은 최종 목표 근접으로 취급하지 않음.
- MainAgent의 다음 탐색 방향은 마지막 코너 클릭 벡터 대신 최종 목적지와 전체 출발점의 벡터로 이어감. 자동사냥은 명시적인 사용자 방향 지시가 없어도 진행 방향 우선 적용. 사용자 사냥 방향 지시가 있으면 그대로 우선.
- MinimapMemory의 안전한 목표 후보에서 진행 방향을 미방문 여부보다 먼저 평가하고 같은 방향에서 미방문 길·새 지도 공개량을 우선. 장애물·통로 여유 조건 유지, 막힘 복구 단계에는 방향 우선 강제하지 않음. diablo4/GAMEPLAY.md를 동일 규칙으로 갱신(2493자). 추가 Qwen 호출 없음.
- 새 테스트: 장거리 목표의 도착 전 전환/중간 클릭 도착 시 목표 유지, 오래된 지도·변위 없음 시 조기 전환 방지, 코너 뒤 전체 진행 방향 유지, 지나온 전방과 미방문 후방에서 전방 우선. python -X utf8 -m unittest discover -s tests:418 tests42.882초 OK(skipped=2), artifacts/near-goal-forward-priority-tests.log. Python 컴파일/변경 파일 재읽기/git diff --check 통과. 웹 코드 변경 없음.
- 실제 게임/ESP32 물리 입력은 확인하지 않았으며 실행 중 Agent는 재시작하지 않음. 적용에는 Agent 재시작 필요.


### 2026-10-09 미니맵 벽·통로 단절 시 다른 방향 목표
- 작업 대상 GameBot. MinimapMemory.destination_blocked는 최신0.4초 지도에서 목표의 벽 여부와 플레이어 연결 통로를 검사. 4방향 연결 검사로 코너 사이 통과 금지와 일치. 화면 밖 목표/오래된 지도/플레이어 위치 불확실은 미확인으로 유지. 직선에 벽이 있어도 돌아갈 통로가 있으면 기존 목표 우회 유지.
- 자동사냥의 목표가 벽/단절 상태인 새 지도2개(최소0.08초 간격)를 확인하면 ClickJourney goal_blocked로 해제하고 기존 목표·경로를 지움. 마지막 클릭 유지/반복 갱신 상태 모두 처리. 같은 캡처 반복은 확인 횟수로 인정하지 않음. 한 번의 오검출 뒤 정상 지도가 돌아오면 목표 유지.
- 막힌 목표 방향을 새 목표 선정에서 제외하고 다른 연결된 통로 방향을 선택. 실제 지도 좌표와 플레이어 위치로 방향을 계산해 격자 중심 오차 방지. 다른 통로가 없으면 입력하지 않음. 새 통행 가능한 목표가 선택되면 제외 방향을 해제. 사용자 지정 좌표를 임의 변경하지 않고 자동사냥에 적용. 추가 Qwen 요청 없음. D4 GAMEPLAY.md 규칙 갱신(2497자).
- 검증: 벽 목표/벽 너머 단절 바닥/우회 통로/오래된 지도·화면 밖 목표 구분, 새 지도2회 확인 후 다른 안전 방향 선정, 동일 캡처 중복 확인 거부, 일회 오검출 후 목표 유지. 초기 새 테스트의 patch import 누락 수정, 방향 검사에서 격자 좌표와 실제 위치 차이를 확인해 실제 위치 기준으로 수정. 전체 검사에서 기존 legacy 공격 테스트의0.45초 대기 실패1회; 단독 재검사 통과. 최종 python -X utf8 -m unittest discover -s tests:421 tests41.205초 OK(skipped=2), artifacts/blocked-goal-redirect-verified-tests.log. Python 컴파일·소스/테스트/MD 재읽기·git diff --check 통과.
- 웹 변경 없음. 실행 중 Agent 재시작/실게임·ESP32 물리 입력 검증은 하지 않았음. 적용에는 Agent 재시작 필요.


### 2026-10-09 예정 경로와 클릭 방향 불일치 수정
- 사용자 첨부 지도에서 하늘색 경로와 주황색 클릭선 불일치 요청. 코드 점검에서 두 원인 확인: 기존 waypoint 단순화는 통로만 통과하면 코너를 가로지르는 직선 클릭을 허용, UI는 과거 클릭점을 현재 플레이어 위치에 연결해 이동 후 클릭 방향이 뒤집혀 보일 수 있음. 실제 실행 화면에서의 추가 재현은 하지 않음.
- route_geometry.follows_route 추가. MinimapMemory는 waypoint까지의 예정 경로가 직선 클릭에서0.5격자(2지도px)를 넘게 벗어나면 코너 전 지점에서 클릭을 제한. 작은 격자 흔들림만 허용하고 벽 여유/통로/단순화 비용 검증도 유지. 요청 클릭 거리보다 먼 waypoint는 검증된 선분 안에서 보간하여 지도 선정 좌표와 화면 투영 거리 일치. 경로 시작점을 실제 설정 플레이어 위치로 표시.
- 미니맵 주황색 클릭선은 journey.map_start(전송 당시 위치)→map_target(실제 투영 클릭점)으로 표시. 현재 위치에서 과거 클릭점으로 역방향 선을 그리지 않음. 전송 당시 기준 안내와 goal_blocked 상태 문구 추가. 하늘색 경로/노란 목표 직선은 유지. D4 GAMEPLAY.md에 코너 제한/클릭선 기준 적용(2493자).
- 테스트: L자 경로를 자르는 클릭 거부/직선·작은 격자 흔들림 허용, 실제 미니맵 계획에서 경로 근접·요청 반경 제한·시작점 확인, LocalNavigator의1920x1080 화면 클릭을 지도에 역투영한 점이 계획 endpoint와 일치. 전체 python -X utf8 -m unittest discover -s tests:423 tests46.376초 OK(skipped=2), artifacts/route-aligned-click-tests.log. 웹 npm run typecheck / npm test16개 / npm run build 통과(dist 갱신, 기존 번들 크기 안내). 컴파일/변경 파일 재읽기/git diff --check 통과.
- 추가 Qwen 요청 없음. 실행 중 Agent/게임 조작은 하지 않았음. 실제 게임/ESP32 입력·새 UI의 브라우저 화면 검증은 미실시. Agent 재시작 및 웹 새로고침으로 적용 필요.


### 2026-10-09 이동 유지 시간400ms 상한 확장
- GameBot 웹 GameSettings 이동 step_ms 입력창과 runtime_settings 검증의 상한400ms를2000ms로 변경(40~2000ms). 커서 이동 시간 pointer_duration_ms의400ms 상한은 별도 설정으로 유지. 사용자의 저장 step_ms=400 값은 임의로 변경하지 않음.
- 클릭 이동에서 step_ms는 마우스를 누르는 시간이 아니라 클릭 후 컨트롤러 대기 시간임을 ESP32/Win32 move 구현으로 확인. GameBot 미니맵 클릭 자동사냥은 기존 _finalize_navigation_command의duration_ms=0을 유지하여 적 출현·지도 피드백을 긴 대기로 막지 않음. 목적지 근접 여부는 ClickJourney에서 판단하며 같은 최종 목표의 경로 클릭을 계속함. 키 이동은 step_ms 동안 방향 키를 유지. 웹에 의미·40~2000ms 범위 안내 추가.
- 검증: step_ms40/400/800/2000 허용,39/2001 거부, 웹 API1200ms 저장→조회→실행 문서 반영 및2001 거부 후 이전 값 유지. python -X utf8 -m unittest discover -s tests -p test_interactive_agent.py:28 tests4.283초 OK. python -X utf8 -m unittest discover -s tests -p test_web_bridge.py -k extended_movement_hold:1 test0.247초 OK. 웹 npm run typecheck / npm test16개 / npm run build 통과(dist 갱신, 기존 큰 번들 안내). 변경 소스/테스트 재읽기·Python 컴파일·git diff --check 통과.
- 실행 중 Agent 재시작/실게임 입력 검증은 하지 않았음. 새 서버 검증 적용은 Agent 재시작, 새 입력창은 웹 새로고침 필요.


### 2026-10-09 현재 맵 누적 이동 궤적 / 전체 지도50% 축소
- MinimapMemory의 실제 등록 위치 이동에서 travel_edges를 누적. 최근128개 breadcrumbs와 별도로 지나온 지도 격자 간 연결을 유지하며 같은 연결 재방문은 중복 저장하지 않음. 연결된 현재 지도 구간의 처음부터 이동 궤적을 보존. 기존 누적 지형 cells/visits 및 이동 정책은 유지. 위치 연결 실패로 새 구간이 시작되면 이전 지도와 임의로 선을 연결하지 않음.
- atlas_trail은 전체 지도 bounds/resolution에 맞는 연결 좌표를 읽기 전용으로 내보냄. /v1/preview 요청에만 포함하여 주기 웹 상태 데이터에 긴 이동 궤적을 추가하지 않음. 미리보기 PNG에 초록색 실제 궤적, 현재 위치, 기억한 목표를 겹쳐 표시. 등록 위치 연결을 시각화하며 추가 Qwen 요청 없음.
- 누적 지도는 원래 지도 픽셀 기준50% 축소. 긴 전체 지도의 과도한 이미지 생성 방지를 위해 긴 변384px 상한으로 추가 축소. 데이터/원본 누적 셀은 삭제하지 않음. 웹은 자연 크기 표시, 최대 부모 폭50%로 제한하며 현재 맵/이동 길/축소율 안내 갱신.
- 새 테스트:131회 스크롤 뒤 초기 이동 연결과128개 이상의 전체 궤적 유지, 새 구간에서 잘못된 연결 없음, 전체 지도50% PNG 크기/초록 연결선/대형 지도384px 제한/원본 비변경. 초기 전체 검사에서 기존 HUD 보정 테스트가0.5초 대기 안에 완료하지 못함. 완료 상태를 최대2초 기다리도록 테스트만 보완하며 저장/검출 단언 유지. 단독 재검사 통과.
- 최종 python -X utf8 -m unittest discover -s tests:428 tests46.836초 OK(skipped=2), artifacts/accumulated-trail-half-preview-final-tests.log. 웹 npm run typecheck / npm test16개 / npm run build 통과(dist 갱신, 기존 큰 번들 안내). Python 컴파일·수정 파일 재읽기·git diff --check 통과.
- 지도/궤적은 실행 중 누적 데이터이며 디스크 영구 저장은 추가하지 않음. 실행 중 Agent 재시작/실게임·브라우저 새 화면 검증은 하지 않았음. Agent 재시작과 웹 새로고침 후 새 이동부터 적용.


### 2026-10-09 목표 근접 후 다음 목표 전환 누락 수정
- 코드 점검에서 ClickJourney.observe가 중간 클릭 도착을 먼저 해제하면 continues의current=None 조기 반환 때문에 최종 목적지 근접 판단이 빠지는 흐름 확인. 빠른 step_refresh마다 현재 클릭 started 기준0.12초 조건이 재설정되어 최신 측정이 있어도 근접 전환이 늦어질 수 있음.
- _near_destination 공통 조건을 지도 observe(중간 도착 검사보다 먼저)와 continues(현재 클릭 없음 조기 반환보다 먼저)에 적용. 중간 waypoint 도착/갱신/정체로 해제된 같은 목표도 최신 유효 지도에서 최종 목표 근접을 확인. 최초 최종 목표 입력 시각 destination_sent_at을 같은 목표의 반복 클릭에서 유지하며 매 클릭마다 근접 대기 타이머를 다시 시작하지 않음. 기존15%/4~12px 및 실제 변위/epoch/segment/지도 신선도 검증 유지.
- 지도 근접 전환 뒤 아직 처리 중이던 이전 refresh 입력이 늦게 완료되어 같은 최종 목표를 다시 여는 경우 차단. 신규 목표가 실제로 다른 경우 새 goal_id/목적지 기록은 정상 생성. MainAgent 기존 다음 목표 선정/방향 유지 정책으로 연결하며 추가 Qwen 요청 없음.
- 테스트: 지도 처리에서 근접 전환 후 다음 목표 선택/클릭 dispatch/새 goal_id와 목적지 생성, 중간 도착으로current=None인 상태에서도 근접 판정, 클릭 직후에도 최초 목표 시각 기준 전환, 늦은 동일 목표 refresh 완료가 목표 재활성화 안 함. 첫 전체 검사에서 기존 코너 방향 테스트가 마지막 클릭 시각만 임의로 앞당겨 새 최초 목표 시각 조건과 불일치1개; 실제 이동한 설정에 맞게 최초 목표 시각을 명시하여 갱신.
- 최종 python -X utf8 -m unittest discover -s tests:432 tests47.053초 OK(skipped=2), artifacts/near-goal-handoff-verified-tests.log. Python 컴파일·변경 소스/테스트 재읽기·git diff --check 통과. 웹 코드 변경 없음.
- 실행 중 Agent 재시작/실게임 입력 검증은 하지 않았음. 적용에는 Agent 재시작 필요.


### 2026-10-09 이동 우선 점검 / 검증 경로 이중 차단 및 입력 지연 완화
- 현재 D4 저장 설정 확인: step_ms1400, step_fraction0.4, feedback_steps, move_interval120, pointer_duration60, tap40. 마우스 미니맵 자동사냥은 기존duration_ms=0 적용으로1400ms 값이 실제 대기로 쓰이지 않음. 오래 기다리도록 늘리는 설정이 목적지 이동을 개선하지 않는 점 재확인.
- LocalNavigator의strict_route에서 이전 NavigationMonitor.failed_direction이 새 미니맵 경로와 같으면 유일한 허용 방향0도까지 거부하여 PATH_BLOCKED STOP으로 바뀌는 코드 확인. 지도 플래너가 이미 막힘/통로를 처리한 검증 경로에는 별도의 이전 실패 방향 힌트를 다시 적용하지 않음. 최종 경로 마스크·HUD·캐릭터 제외 검사 유지하며 자유 이동의 회피 힌트 동작은 유지.
- MainAgent 지도 처리 후100ms 고정 대기를30ms로 줄여 새 캡처의 통로/위치 반영 지연 완화. 동일 캡처 중복 처리 금지 유지. 입력/지도 센서 검증 및 전투·물약·중단 우선순위는 보존.
- 사용자 설정 수정 요청에 따라 D4 navigation.step_ms1400→120, input.pointer_duration_ms60→40, tap_ms40→20 적용. 클릭 거리40%, 지도 ROI/플레이어 위치/투영배율, 공격/버프 키 및 사용자 스킬 설정은 보존. GAMEPLAY.md 누락 기본 커서 시간도40으로 일치(2493자). JSON 저장 검증 통과. 초기 확인 명령의validate_document 모듈 import 경로 오류를 profile_store로 바로잡고 검증 재실행 성공.
- 검증: strict_route+이전 동일 실패 방향에서도 검증된 이동 클릭 반환, 같은 경로의 실제 벽은 여전히 차단. python -X utf8 -m unittest discover -s tests:433 tests43.693초 OK(skipped=2), artifacts/smooth-movement-priority-tests.log. route_geometry 단독7개 통과. Python 컴파일·수정 소스/설정/테스트/MD 재읽기·git diff --check 통과. 웹 코드 변경 없음.
- 실제 게임의 자연스러움/ESP32 지연·현재 실행 상태는 직접 검증하지 않았음. 실행 중 Agent 재시작/게임 입력은 하지 않음. 코드 적용은 Agent 재시작 필요.


### 2026-10-09 열린 통로의 짧은 클릭 / 격자 꺾임 제거
- 재현: 장애물 없는 grid/mask에서 목표[156,92]/[156,102]/[156,112]/[136,132], 요청36지도px 클릭이 기존 follows_route의0.5격자 편차 제한 때문에 약17~18px로 축소. 격자 최단 경로의 인공 꺾임을 실제 코너처럼 취급한 결과. 저장 클릭 거리40%를 늘리는 문제가 아님.
- MinimapMemory waypoint 선정에서 원시 격자 경로 편차 제한을 제거하고 기존 실제 통로 연결/벽 여유/화면 장애물/경로 비용 검증으로 직선 구간을 단순화. 물리적으로 확인된 열린 바닥에서 요청 거리까지 긴 클릭, 벽·좁은 코너는 통로 검사에 의해 앞에서 제한. 최종 목적지 유지와 클릭 요청 반경 상한 보존.
- 하늘색 표시 경로의 첫 구간을 실제 검증된 직선 waypoint로 갱신하고 이후 경로는 유지. 길게 클릭해도 실제 클릭은 표시한 첫 경로 선분 위에 위치하도록 일치. 단순히 원시 경로를 표시한 채 코너를 잘라 클릭하는 이전 불일치 방식은 사용하지 않음. D4 GAMEPLAY.md에 검증된 통로 직선화 규칙 반영(2498자). Qwen 추가 요청/설정 거리 변경 없음.
- 새 회귀 테스트: 위4개 열린 바닥 방향에서36px 전체 클릭 반경 사용, 클릭점이 표시된 첫 경로 선분 위에 있음, 최종 목표 불변. 기존 L자 벽 통로/화면 장애물/실제 투영/요청 거리 상한 테스트도 유지. minimap_memory47 tests3.500초 OK. 전체 python -X utf8 -m unittest discover -s tests:434 tests47.979초 OK(skipped=2), artifacts/longer-verified-route-click-tests.log. 컴파일·수정 파일/MD 재읽기·git diff --check 통과. 웹 코드 변경 없음.
- 실행 중 Agent 재시작/실게임 물리 입력 검증은 하지 않음. 코드 적용에는 Agent 재시작 필요.


### 2026-10-09 Qwen 백그라운드 장면 분석 8초 타임아웃 수정
- 원인: 백그라운드 분석 요청에 timeout=8이 고정되어 QWEN_VL.timeout=45보다 우선 적용됨. SCENE.request_timeout=15(허용 범위 5~30초), SCENE.max_tokens=384(256~1024)를 추가하고 실제 요청에 사용. 기존 이미지 512px, 객체 최대 2개 제한은 유지하며 사용자 대화 요청 설정은 유지.
- 연속 분석 실패 시 재시도 대기를 12/24/48/60초로 늘리고 성공하면 실패 횟수 초기화. 분석 실패 시 현재 이동 목표와 자동사냥 상태를 유지하는 회귀 테스트 추가. 최초 테스트의 성공 응답 mock에 elapsed_ms가 없어 발생한 로그 오류는 정상 응답처럼 시간을 제공하도록 테스트를 수정하여 해결.
- 검증: 수정 소스/설정/테스트 재읽기, Python 컴파일, git diff --check 통과. 전체 python -X utf8 -m unittest discover -s tests: 435 tests, 47.195초, OK(skipped=2). 로그 artifacts/qwen-background-timeout-tests.log.
- Qwen 서버 http://127.0.0.1:8082/health 읽기 전용 확인: HTTP 200, status=ok. 실제 추론 응답 시간은 검증하지 않았으며 15초 내 응답을 보장하지 않음. 실행 중 Agent 재시작 및 게임 입력은 수행하지 않음. 적용하려면 Agent 재시작 필요.


### 2026-10-09 Qwen 대화 이동 버튼 / 통로 중앙 경로 이동
- Qwen-VL 대화 빠른 명령에 이동 버튼 추가. 이동/이동해/이동해줘 또는 /move는 모델 대화 요청 없이 로컬 제어 명령으로 즉시 지속 이동 시작. 이전 이동 목표와 방향 힌트를 초기화하여 새 통로 경로를 선택하고 기존 자동사냥의 목표 유지/연속 클릭/적 발견 시 공격 및 이동 재개 로직 사용. 게임 비활성 입력 차단 및 수동 사냥 중단 정책 유지.
- 해당 모드의 MinimapMemory 경로 비용은 벽 거리 기반 중앙 선호도를 최소 10으로 적용하고 직선 단축 허용 비용 오차를 10%에서 2%로 줄임. 사용자 프로필 값은 변경하지 않음. 지도상의 점선/아이콘을 그대로 추적하는 방식이 아니라 검증된 통로에서 중앙 경로를 산출. 사냥 중단 시 중앙 이동 모드와 자동 입력 해제. 대화 응답에 해당 이동 모드 설명 추가.
- 변경 파일: app/ai/main_agent.py, app/core/minimap_memory.py, app/web/bridge.py, Visual-Agent-Lab-local/app/live-agent.tsx, tests/test_main_agent.py, tests/test_minimap_memory.py 및 웹 dist 빌드 결과.
- 검증: 소스 및 테스트 재읽기, python -m py_compile app/ai/main_agent.py app/core/minimap_memory.py app/web/bridge.py, git diff --check 통과. 이동 명령의 모델 호출 없는 시작/수동 중단, 연결된 통로 내 경로와 목표 유지 회귀 테스트 2개 통과. python -X utf8 -m unittest discover -s tests: 437 tests 48.637초 OK(skipped=2), artifacts/corridor-move-button-tests.log. 웹 npm run typecheck / npm test(16개) / npm run build 통과. 빌드는 기존 500kB 번들 크기 경고가 있음.
- 실제 게임 입력 및 실행 중 Agent 재시작은 하지 않음. 첨부 지도에서 실제 이동 성공 여부와 화면 표시를 직접 검증하지 않았음. 적용 시 Agent 재시작과 웹 새로고침 필요.


### 2026-10-09 이동 버튼 의미 정정: 실제 미니맵 주황색 선 추종
- 사용자 정정에 따라 이동 버튼은 통로 중앙 탐색이 아닌 실제 캡처에 보이는 주황색 선을 추종. app/vision/orange_route.py에서 HSV 주황색 범위, 길이/면적/두께로 선을 추출하고 작은 마커/프레임 테두리를 제외. 플레이어 근처의 연결된 선을 따라 약 32px 앞 후보를 선택하며 지나온 곳은 후순위. 선을 찾지 못하거나 맵핑이 꺼져 있으면 임의 탐색 이동 대신 대기.
- MinimapMemory는 주황색 선과의 거리 비용 및 직선 클릭의 선 이탈 제한을 적용하면서 지형 통로 검증을 유지. 이동 목표는 기존 ClickJourney로 유지하고 목표 인근에서 다음 선 구간 선택. 이동 버튼의 별도 모드는 _follow_orange_route로 변경, 사용자 중단 시 해제. 대화 응답과 이동 이유에 주황색 선 추종 표시. 기존 자동 전투/포커스 차단/연속 입력 유지.
- 주황색 안내선이 채도가 높아 지형 벽으로 오인되는 문제는 주변 색상으로 해당 얇은 오버레이를 보정하여 처리. 처음 전체 검사에서 색상 보정된 벽 윤곽선 지도 테스트가 실패하여, 보정 적용을 bright_floor/diablo4_auto로 제한. wall_lines 및 hsv 모드의 벽 색상을 유지하도록 수정 후 재검증.
- 변경 파일: app/vision/orange_route.py, app/core/minimap_memory.py, app/ai/main_agent.py, app/web/bridge.py, tests/test_main_agent.py, tests/test_minimap_memory.py. 소스/테스트 재읽기 및 python -m py_compile app/vision/orange_route.py app/core/minimap_memory.py app/ai/main_agent.py app/web/bridge.py, git diff --check 통과.
- 최종 python -X utf8 -m unittest discover -s tests: 439 tests 46.309초 OK(skipped=2), artifacts/orange-route-final-tests.log. 꺾인 주황색 선 방향 우선/목표 유지/선 미검출 대기/작은 마커와 테두리 제외/맵핑 비활성 임의 이동 금지 검증. 실제 첨부 화면 색상과 게임 입력 성공은 직접 검증하지 않았으며 실행 중 Agent 재시작은 하지 않음. 적용은 Agent 재시작 필요.


### 2026-10-09 이동 중 GPU 재추론 차단 / 취소 지연 서버 요청 보호
- 사용자 로그에서 취소 이후 슬롯 해제가 지연되는 상태 확인: task450 취소 36:42.470 → release40:32.051 약230초. 코드상 막힘 발생 시 _request_stall_obstacles가 Qwen 화면 분석을 다시 요청하고 미확정 적 후보에 자동 장면 재분석이 가능했음. 주황색 선 이동 모드는 자동 장면/막힘 Qwen 요청을 차단하고 OpenCV 미니맵 및 적 HP바 처리 유지. 이동 시작 시 진행 중 장면/막힘 작업 취소 및 연결 취소, 오래된 결과는 기존 epoch 검사로 폐기. 시작 1회 HUD 보정과 사용자가 요청한 대화는 기존 Qwen 사용 유지.
- MainAgent의 Qwen 클라이언트에 서버 busy 보호 활성화. 로컬 서버 /slots 읽기에서 is_processing=true이면 이미지 추론을 새로 제출하지 않고 서버 작업 중 상태를 반환. 다른 호환 서버의 /slots 미지원/읽기 실패는 기존 요청 동작 유지. 슬롯 확인과 제출의 원자성이나 다른 외부 클라이언트의 동시 요청은 보장하지 않음.
- 이동 대기 이유에 주황색 선 미검출/통로 단절 및 맵핑 비활성 표시 추가. 연속 클릭이 실제 모의 입력 컨트롤러에 2회 이상 전달되고 최종 목표가 유지되며 Qwen 요청이 없는 회귀 테스트 통과. 이동 중 적 발견→공격→이동 복귀 테스트 유지.
- 읽기 전용 확인 당시 GPU14%, VRAM9171MiB, llama-server -c16384/--parallel1 실행. /health HTTP200 ok, /slots HTTP200 task494 is_processing=true. GPU100%가 발생한 순간의 측정은 없으며 서버 취소 지연의 근본 원인은 확정하지 않음. Agent 실제 게임 입력과 서버 재시작은 하지 않음.
- 변경 파일: app/ai/main_agent.py, app/vision/qwen_vl_client.py, tests/test_main_agent.py, tests/test_qwen_cancel.py. 수정 소스/테스트 재읽기, Python 컴파일, git diff --check 통과. python -X utf8 -m unittest discover -s tests:442 tests47.296초 OK(skipped=2), artifacts/orange-movement-gpu-guard-tests.log. 웹 변경 없음. 코드 적용은 Agent 재시작 필요하며 기존 서버 처리 작업을 즉시 비우려면 Qwen 서버도 재시작해야 함. 실제 GPU 감소/게임 이동 성공은 미검증.


### 2026-10-09 핀 최종 목표 / 자동사냥·이동 경로 통일
- 연결된 중앙 통로 없음에서 멈추는 경로 중 주황색 선 5px 이탈을 통행 금지로 취급하던 제한 확인. 핀 목표가 있을 때는 선을 비용 우선도로 사용하고 연결된 지형으로 우회 허용. 미니맵 흰색/회색 길쭉한 핀의 넓은 머리와 좁은 줄기를 검출하며 플레이어 주변/둥근 아이콘은 제외. 실제 첨부 핀 픽셀에서 검출 성공 여부는 미검증.
- 핀의 줄기 끝을 누적 지도 좌표 pin_world로 기억. 핀이 잠시 가려져도 최종 좌표를 유지하고, 현재 지도에서 도달 가능한 접근점을 선택. 핀 셀 자체가 아이콘/벽으로 가려져도 인접 접근점을 계획. 기존 탐색 목표는 핀 최초 적용 시 해제하고 핀 접근 경로로 전환. 핀 주변 6px 도착 시 이동 완료 표시 및 전투 유지.
- 디아블로 IV의 자동사냥 시작과 이동은 같은 로컬 경로 모드 사용. 핀 이동 중 자동 장면/막힘 Qwen 요청 차단 유지. 다른 게임 프로필의 자동 분석을 무조건 차단해 기존 II 프로필 검사에 실패했던 부분을 D4 프로필로 제한하고 게임 변경 시 이전 경로 모드 초기화. 핀 인식 이후 다른 게임의 실제 전투 지원은 별도 검증 필요.
- 웹 미니맵에 최종 핀 표시, 노란 직선은 핀 목표를 우선 표시. D4 GAMEPLAY.md 이동 규칙 갱신(2483자). 변경 파일: app/vision/orange_route.py, app/core/minimap_memory.py, app/ai/main_agent.py, app/web/bridge.py, tests/test_minimap_memory.py, Visual-Agent-Lab-local/app/minimap-mapping.tsx, app/profiles/diablo4/GAMEPLAY.md 및 dist 빌드 결과.
- 검증: python -X utf8 -m unittest discover -s tests:444 tests47.611초 OK(skipped=2), artifacts/pin-target-navigation-final-tests.log. 이후 최초 핀 전환/프로필 MD 변경의 최종 검사 python -X utf8 -m unittest tests.test_main_agent tests.test_minimap_memory:166 tests18.873초 OK. 핀을 목표로 벽 우회/주황 선 소실 후 진행/아이콘 제외 테스트 포함. 웹 npm run typecheck, npm test16개, npm run build 통과(기존 번들 크기 경고). 수정 소스/테스트/MD 재읽기, 컴파일 및 diff 검사. 첫 MD 초안들은 2500자 초과로 저장 전 거부되어 짧게 다시 작성.
- 실제 게임 이동/새 핀 검출 UI 확인 및 Agent 재시작은 수행하지 않음. 적용하려면 Agent 재시작 및 웹 새로고침 필요. 미확인 지형·벽을 무시하는 강제 클릭은 수행하지 않음.


### 2026-10-09 빠른 연속 이동 클릭 / 저속 지도 갱신 대기 제거
- ClickJourney의 반복 클릭이 sampled_at>=직전 클릭 전송 시각을 요구하여 저속 지도에서는 다음 프레임까지 기다리는 코드 확인. feedback_steps 반복 갱신은 유효한 최근 지도(0.8초 이내)를 사용하여 80ms 이후 가능하게 변경. 실제 도착/목표 근접 및 이동량 판정은 기존 새 측정 조건 유지. 목표 ID/최종 목적지/정체 타이머도 반복 클릭 중 유지하며 포커스·HP·지도 입력 검증은 그대로 적용.
- D4 프로필 pointer_smoothing=false로 커서 보간 대기 제거, tap_ms20→15, steering.move_interval_ms120→100(설정 허용 최솟값). 매핑된 자동 이동의 실제 반복 기준은 REFRESH_SECONDS80ms이며 컨트롤러 처리와 반응 루프 시간이 더해짐. 사용자 step_ms1120와 클릭 거리40%, 스킬/버프 키 및 활성화 설정은 유지. 클릭 이동의 기존 duration_ms=0/cooldown=0 처리 유지.
- 처음 작성한 pointer_duration20 및 설정 간격80은 검증 범위 밖이어서 프로필 검증에 실패. pointer_duration40을 유지하고 보간을 끄는 방식, 설정 간격100과 코드상 반복80으로 수정한 뒤 JSON 검증 통과. 테스트 보조 함수의 기본값 치환 오류도 즉시 수정 후 재검증.
- D4 GAMEPLAY.md의 지도 유효 기간/클릭 반복 주기 및 기본 입력 설정을 일치시킴(2500자). 수정 파일: app/core/click_journey.py, app/ai/main_agent.py, app/profiles/diablo4/input.json, app/profiles/diablo4/navigation.json, app/profiles/diablo4/GAMEPLAY.md, tests/test_click_journey.py, tests/test_main_agent.py.
- 회귀 검증: 동일 지도 표본으로 90ms 단위 반복 클릭 시 목표 유지, 오래된 지도에서는 갱신 금지, 지도 0.5초 갱신 조건에서 0.35초 이내 모의 컨트롤러 클릭 2회 전송. 실제 도착을 새 측정 없이 확정하지 않음. 최종 python -X utf8 -m unittest discover -s tests:446 tests47.166초 OK(skipped=2), artifacts/continuous-fast-move-tests.log. Python 컴파일, 소스/프로필/MD/테스트 재읽기 및 git diff --check 통과. 웹 소스 변경 없음.
- 실제 게임 클릭 간격/이동 부드러움은 미검증. Agent 재시작이나 게임 입력은 하지 않음. 변경한 프로필과 코드는 Agent 재시작 후 적용되며 80ms는 하드웨어 전송까지 보장되는 실측 간격이 아님.


### 2026-10-09 미니맵 경계 밖 핀 검색 확장
- 첨부 화면처럼 핀이 미니맵 사각형 경계 밖에 표시되는 경우를 위해 D4 navigation.json의 minimap.mapping.pin_search_margin=0.12 추가. 원본 지도 너비의12%만큼 상하좌우 검색 여백을 추가하고 전체 캡처 경계에서 잘라낸다. 허용 설정0~0.3. 다른 프로필은 기본0.
- 통로/장애물 판정과 누적 지도 정렬은 기존 미니맵 ROI 및192px 지도 크기를 유지. 핀 검색만 확장된 영역을 원본 지도와 같은 배율로 읽고 검색 여백 오프셋을 빼서 기존 지도 좌표 및 누적 세계 좌표로 환산. 지도 밖 핀을 인식해도 주변 게임 화면을 통로로 추가하지 않음. 핀 목표 접근은 기존 도달 가능한 통로 선택 로직 사용.
- 웹의 미니맵 원본 미리보기는 확장 검색 영역 표시. 내부 기존 지도 사각형과 실제 플레이어 십자 위치도 함께 표시하고 전체 캡처에 추가 검색 영역 사각형 표시. 지형 마스크는 기존 ROI 유지. pin_search_bbox_pixels로 디버그 좌표 제공.
- 변경 파일: app/core/minimap_memory.py, app/web/screen_preview.py, app/profiles/runtime_settings.py, app/profiles/diablo4/navigation.json, tests/test_minimap_memory.py, tests/test_screen_preview.py. 소스/설정/테스트 재읽기, JSON 검증, Python 컴파일, git diff --check 통과.
- 검증: 기존 ROI 밖 핀 검출 및 지도 크기 불변/연결된 통로 접근, 여백0일 때 경계 밖 핀 미검출, 확장 미리보기 십자 좌표/검색경계/원본 캡처 불변 회귀 테스트 통과. python -X utf8 -m unittest discover -s tests:448 tests47.168초 OK(skipped=2), artifacts/expanded-pin-search-tests.log. 웹 소스 변경 없이 응답 이미지에 적용.
- 실제 첨부 화면에서 해당 핀 검출은 직접 검증하지 않았으며 Agent 재시작/게임 입력은 수행하지 않음. Agent 재시작과 웹 새로고침 후 적용. 캡처 밖 핀은 검색 여백 확대만으로 볼 수 없음.


### 2026-10-09 예정 진행 방향의 주황색 선 우선 적용
- 핀이 있는 경우에도 다음 예정 진행 방향을 핀 직선 벡터 대신 플레이어에 연결된 주황색 선으로 선정. 연결된 선에서 최종 핀에 가장 가까운 지점을 찾고 선을 따라 약32px 앞의 구간을 중간 목표로 사용. 먼저 핀 반대 방향으로 꺾이는 선도 해당 굴곡을 따라 진행.
- 최종 pin_world와 기존 활성 이동 목표 유지. 목표 인근에서 다음 선 구간을 선택하며 선 미검출 시 기존 핀 접근 경로와 지형 통행 검증 유지. Qwen 추가 추론 요청 없음.
- 수정 파일: app/vision/orange_route.py, app/core/minimap_memory.py, tests/test_minimap_memory.py. 핀이 오른쪽에 있으나 선이 먼저 왼쪽으로 진행하는 회귀 검사 추가. 수정 소스/테스트 재읽기, Python 컴파일, git diff --check 통과.
- python -X utf8 -m unittest discover -s tests:449 tests47.661초 OK(skipped=2), artifacts/orange-line-planned-direction-tests.log. 실제 게임 이동은 직접 검증하지 않았으며 Agent 재시작/게임 입력은 수행하지 않음. 코드 적용은 Agent 재시작 필요.


### 2026-10-09 이동 명령의 전투 분리
- 이동 버튼/이동 명령은 _move_only 모드로 주황색 선과 핀 경로를 따라 이동. 적/아이템을 전투 정책 대상에서 제외하고 ATTACK/USE_SKILL/TAKE/INTERACT 실행도 차단. 전환 시 기존 공격 유지 해제 및 전투 대상 초기화. 장애물과 클릭 안전 검사 유지. 자동사냥 명령은 이동 전용 모드를 해제하여 기존 적 발견 전투 유지.
- 게임 포커스 복귀 시 이동 전용 모드로 재개. 웹 대화 응답과 control_mode=move_only, D4 GAMEPLAY.md에 의미 반영. 버프/물약의 기존 유지 처리는 유지.
- 변경 파일: app/ai/main_agent.py, app/ai/visual_agent.py, app/web/bridge.py, app/profiles/diablo4/GAMEPLAY.md, tests/test_main_agent.py. 수정 파일 재읽기/컴파일/git diff --check 통과.
- 최초 적 발견 이동 검사에서 적이 실제 클릭 좌표에 겹쳐 안전 검사가 클릭을 차단하여 시간 초과. 적 위치를 경로 밖으로 수정하여 공격 차단과 반복 이동을 독립 검증. 안전 검사는 변경하지 않음.
- python -X utf8 -m unittest discover -s tests:451 tests48.430초 OK(skipped=2), artifacts/move-only-tests.log. 적이 있어도 반복 이동 클릭 및 공격 미전송, 공격 스킬 실행 차단, 포커스 복귀 후 모드 유지, 사냥 지시 후 전투 모드 복귀 확인. 실제 게임 입력/Agent 재시작은 수행하지 않음. 적용은 Agent 재시작 필요.


### 2026-10-09 어두운 안내선 검출 / 뒤쪽 핀에 의한 역방향 계획 보완
- 첨부 화면에는 플레이어에서 우상단으로 이어지는 어두운 적갈색 안내선이 있으나 기존 검출은 HSV H5~35/S110이상/V140이상으로 밝은 주황색만 처리. 선 미검출 시 핀 접근 방향으로 대체되는 코드 확인. 실제 실행 당시 검출 마스크/핀 원본 좌표는 확보하지 않아 첨부 현상의 단일 원인으로 확정하지 않음.
- 어두운 적갈색 H0~18/S75이상/V45~200에 R-G20/R-B25이상 색상 대비 검사를 추가. 기존 선 길이/두께/작은 마커/경계 제외 유지하여 베이지 바닥과 두꺼운 갈색 지형을 선으로 처리하지 않도록 검사.
- 핀과 가장 가까운 연결 선 지점이 플레이어 시작점인 경우, 그 뒤쪽 핀을 향해 이동하는 대신 연결된 전방 선의 다음 구간 선택. 최종 핀 좌표와 선정한 목표 유지 로직은 유지. D4 GAMEPLAY.md 안내선 색상 규칙 갱신(2486자).
- 변경 파일: app/vision/orange_route.py, tests/test_minimap_memory.py, app/profiles/diablo4/GAMEPLAY.md. 수정 파일 재읽기/컴파일/git diff --check 통과. 어두운 선이 1시로 향하고 핀이 왼쪽에 있는 합성 화면에서 우상단 계획 및 목표 유지 검사, 베이지 바닥/갈색 덩어리 제외 검사 추가.
- python -X utf8 -m unittest tests.test_minimap_memory:56 tests4.762초 OK. python -X utf8 -m unittest discover -s tests:453 tests48.874초 OK(skipped=2), artifacts/dim-guide-direction-tests.log. 실제 첨부 이미지 픽셀의 검출 및 게임 이동 결과는 직접 검증하지 않았고 Agent 재시작/게임 입력은 하지 않음. Agent 재시작 후 이동 명령을 새로 내려 기존 목표를 초기화하고 적용.


### 2026-10-09 남아 있는 안내선 기준 예정 진행 방향
- 사용자가 지나온 주황색 선이 게임에서 자동으로 사라진다고 설명한 특성을 방향 선택 기준에 적용. 플레이어 근처에 연결된 현재 안내선 전체를 탐색하고 선을 따라 가장 먼 끝을 선택, 약32px 앞의 선 구간을 중간 이동 목표로 사용. 핀은 동일 길이 후보의 보조 기준이며 가까운 핀 때문에 선 중간을 최종 진행 끝으로 선택하지 않음. 동일 길이에서는 지나온 셀 후순위도 유지.
- 기존 이동 목표 유지/목표 인근 갱신 및 이동 전용 공격 차단 유지. D4 GAMEPLAY.md에 남아 있는 선 끝 우선 규칙 반영. 변경 파일: app/vision/orange_route.py, tests/test_minimap_memory.py, app/profiles/diablo4/GAMEPLAY.md. 수정 소스/테스트/MD 재읽기, 컴파일 및 git diff --check 통과.
- 근처 핀 너머로 연결된 전방 선 추종, 지나온 선 삭제 후 뒤쪽 핀보다 전방 선택 검사 추가. python -X utf8 -m unittest tests.test_minimap_memory:58 tests4.203초 OK.
- 최초 전체 검사455개에서 기존 test_live_known_object_attacks_without_any_vl_call의0.45초 입력 대기가 실패. 해당 테스트 단독 재실행1개1.139초 OK; 코드 변경 없이 전체 재실행455 tests45.595초 OK(skipped=2), artifacts/remaining-guide-direction-final-tests.log. 최초 실패 로그 artifacts/remaining-guide-direction-tests.log 유지. 시간 민감성 가능성은 있으나 실행 부하 원인은 확정하지 않음.
- 실제 게임에서 지나온 선 삭제/새 방향 입력 결과는 직접 검증하지 않음. Agent 재시작 및 이동 명령 재입력 후 적용.


### 2026-10-09 안내선 주변 통로 끊김 / 누적 벽 판정 보정
- 색상 지형 마스크의 좁은 끊김과 과거 evidence<=-3 벽 기억이 현재 통로를 계속 막을 수 있는 코드 확인. 첨부 사진에는 실제 게임 원본 전체 화면과 당시 이동 로그가 없어 멈춤의 단일 원인으로 확정하지 않음.
- bright_floor/diablo4_auto의 유효 지도에서 플레이어20px 이내 연결 안내선만 선택. 해당 선 주변9px 폭 이내이면서 주변15x15 영역의 기존 통로 비율35% 이상인 부분에만 통로 보정. 이 보정이 충분한 셀은 과거 벽 기억보다 현재 통로 증거를 우선. 다른 안내선, 넓은 벽 내부, 지도 경계는 열지 않음. Qwen 추가 요청 없이 OpenCV 로컬 처리.
- 수정 파일: app/core/minimap_memory.py, tests/test_minimap_memory.py. 좁은5px 벽 오판 및 과거 벽 기억 조건에서 연결 목표 계획 검사 추가. 넓은32px 벽의 중심 유지 및 플레이어와 연결되지 않은 안내선 제외 검사 추가.
- 수정 소스/테스트 재읽기, Python 컴파일, git diff --check 통과. python -X utf8 -m unittest tests.test_minimap_memory:60 tests4.908초 OK. 전체 python -X utf8 -m unittest discover -s tests:457 tests48.962초 OK(skipped=2), artifacts/guide-floor-repair-tests.log.
- 실제 게임 원본 화면에서 장애물 유무/이동 입력 성공은 직접 검증하지 않음. 지도 통로 보정은 색상/연결선에 근거한 추정이며 실제 화면 장애물 분석을 추가한 것은 아님. Agent 재시작 후 이동을 다시 지시하여 적용.


### 2026-10-09 주황색 안내선과 가장자리의 벽 오인 제거
- 기존 누적 지도는 안내선 색상 중심 마스크만 inpaint하여 배율 보간/안티앨리어싱의 어둡고 낮은 채도 가장자리가 남을 수 있음. 누적 지도를 사용하지 않는 local_navigation.minimap_mask는 안내선 제거 없이 HSV로 통로를 검사하던 코드 확인.
- route_terrain_image 공통 처리 추가. 검출된 안내선 마스크를7x7로 확장해3px 가장자리까지 주변 지형 색상으로 복원하고 지형 분류에 사용. bright_floor/diablo4_auto 누적 및 일반 이동 검사 모두 적용. 방향 선정용 원본 안내선 마스크는 유지. hsv/wall_lines의 보정된 벽 색상은 보존. 원본 캡처는 수정하지 않음.
- 수정 파일: app/vision/orange_route.py, app/core/minimap_memory.py, app/core/local_navigation.py, tests/test_minimap_memory.py. 어두운 안내선 중심과 저채도5px 가장자리 재현 테스트 추가. 최초5x5 확장으로 가장자리 복원값126이 통로 기준138보다 낮아 실패하여7x7로 수정 후 통과.
- 누적/일반 이동 양쪽의 선 중심·가장자리 통로 판정, 격자 통로, 경계 밖 벽 유지, 원본 캡처 불변, hsv 모드 보존 확인. 소스/테스트 재읽기, Python 컴파일/git diff --check 통과. python -X utf8 -m unittest tests.test_minimap_memory:61 tests4.306초 OK. 전체 python -X utf8 -m unittest discover -s tests:458 tests47.321초 OK(skipped=2), artifacts/route-overlay-terrain-tests.log.
- 실제 사용자 캡처 픽셀/게임 이동 성공은 직접 검증하지 않음. Agent 재시작 후 이동 재지시 필요. 안내선 자체의 색상을 지형 벽으로 판정하지 않도록 보정한 것이며 주변 지형 전체를 통로로 여는 처리는 아님.


### 2026-10-09 안내선 목표 픽셀 / 클릭 아래쪽 격자 오차 수정
- 실제 코드 경로의 재현 테스트에서 y68 안내선 목표를4px 격자 중심으로 치환해 클릭 좌표가y70으로 내려가는2px 지도 오차 확인. 플레이어와 검출된 선 사이25.6px 가림 조건에서 기존20px 제한으로 선을 못 찾는 것도 재현(수정 전2개 검사 실패).
- 핀 접근의 중간 목표가 안내선 셀에 도달 가능하면 셀 중심 대신 실제 안내선 픽셀을 유지. 기존 목표는 기존 정확 좌표로 유지. 계획한 클릭점과6px 이내의 안내선 픽셀에 보정하되 통로 직선 검사/이동 거리/캐릭터 근접 제한을 유지. 클릭 거리 제한과 보정 후 경로 미리보기 끝점을 생성하여 실제 투영 좌표와 일치.
- 안내선 시작 검출 및 통로 보정의 플레이어 가림 허용20→32px(192px 지도 기준). 멀리 떨어진 선은 계속 제외. 변경 파일: app/vision/orange_route.py, app/core/minimap_memory.py, tests/test_minimap_memory.py, tests/test_main_agent.py.
- 안내선 y68 목표/클릭 픽셀 검사, 가림 후 우상단 선 선택 검사, 에이전트의 실제 화면 클릭 투영→지도 역변환 y68 및 미리보기 끝점 일치 검사 추가. python -X utf8 -m unittest tests.test_minimap_memory:63 tests4.368초 OK. 화면 투영 검사1개0.262초 OK. 전체 python -X utf8 -m unittest discover -s tests:461 tests44.869초 OK(skipped=2), artifacts/guide-pixel-click-tests.log.
- 소스/테스트 재읽기, Python 컴파일 및 git diff --check 통과. 실제 첨부 화면 픽셀의 검출과 ESP32/게임 실제 클릭 결과는 직접 검증하지 않음. Agent 재시작 후 이동 재지시 필요. 사용자 클릭 거리/프로필 설정은 변경하지 않음.


### 2026-10-09 안내선 미검출 중 목표 유지 / 선 이탈 통행 금지 제거
- 핀이 없는 주황선 이동은 목표가 있어도 선 미검출 프레임이면 suggest가 바로None을 반환하던 조건 확인. 현재 목표 또는 ClickJourney의 전달받은 목적지가 있으면 선 재검출을 기다리지 않고 최신 유효 지도에서 동일 목적지 경로를 재검증하여 계속 이동. 도착 후 새로운 목표가 없고 선도 없으면 재검출 대기. 원본 안내선 영상 캐시나 지형이 검증되지 않은 강제 이동은 추가하지 않음.
- 핀이 없는 경우 선에서5px 벗어난 셀/직선 구간을 무조건 금지하던2개 조건 제거. 주황색 선은 경로 비용 우선도로 유지하고 실제 통로/벽 검사는 유지하여 연결된 지형 내 우회 허용. 기존 목표 유지 및 선 픽셀 클릭 보정 유지.
- 대기 원인을 guide_missing/guide_disconnected/route_blocked로 구분. 웹 이동 상태에서 새 목표 안내선 재검출, 플레이어 주변 선 연결 확인, 현재 목표 통로 차단을 별도로 표시.
- 변경 파일: app/core/minimap_memory.py, app/ai/main_agent.py, tests/test_minimap_memory.py. 소스/테스트 재읽기, Python 컴파일 및 git diff --check 통과. 기존 선 소실 검사 기대값을 목표 유지로 변경하고 목표 제거 후 재검출 대기 확인. 선 가림 중 목적지 유지와 실제 벽 확인시 차단, 핀 없이5px 이상 선 이탈 우회 경로 검사를 추가.
- python -X utf8 -m unittest tests.test_minimap_memory tests.test_main_agent:182 tests21.031초 OK. 우회 추가 검사1개0.113초 OK. 최종 python -X utf8 -m unittest discover -s tests:463 tests47.098초 OK(skipped=2), artifacts/guide-dropout-continuity-tests.log.
- 실제 사용자 이동 끊김의 완전 해소/게임 입력은 직접 검증하지 않았으며 Agent 재시작도 수행하지 않음. 적용은 Agent 재시작 후 이동 재지시 필요. 새 목표 선정과 현재 통로 검증이 계속 불가능한 경우에는 해당 원인으로 대기할 수 있음.


### 2026-10-09 보이는 흐린 안내선 / 갈색 지형에 붙은 안내선 검출 보완
- 낮은 채도 안내선과 갈색 지형에 붙은 안내선이 기존 색상 기준/큰 연결 성분 제외 조건으로 검출되지 않는 합성 조건2개 재현(수정 전2개 실패). 첨부 이미지의 실제 원본 픽셀/검출 마스크는 확보하지 않아 당시 미검출 원인을 확정하지 않음.
- 기존 밝은 주황/어두운 적갈색 검출에 낮은 채도 후보 H0~25/S35이상/V35~220, R-G14/R-B22이상, 주변9x9 대비18이상 조건 추가. 후보 마스크의11x11 opening으로 큰 갈색 지형을 먼저 분리한 뒤 기존 가는 선 길이/두께/면적 필터 적용하여 지형에 붙은 선을 통째로 버리지 않도록 보완.
- 초기7x7 지형 제거는 가장자리 있는 선과 좁은 통로 검사에 실패하여11x11로 변경. 초기 느슨한 R-G8/R-B12는 회색 좁은 지형을 안내선으로 검출해14/22로 강화. 국소 대비10에서는 잡음 지도 검사 실패가 있어18로 조정. 이후 지도67개 검사5.788초 OK. 기존 베이지 바닥/큰 갈색 영역/프레임/아이콘 제외, 안내선 가장자리 제거 및 좁은 통로 보정 회귀 통과.
- 이동 STOP 명령 자체에 ORANGE_GUIDE_MISSING/ORANGE_GUIDE_DISCONNECTED 원인을 저장해 다음 지도 업데이트의 reason=ready로 대기 설명이 바뀌는 현상 방지. 상태 유지 검사1개0.125초 OK.
- 변경 파일: app/vision/orange_route.py, app/ai/main_agent.py, tests/test_minimap_memory.py, tests/test_main_agent.py. 수정 소스/테스트 재읽기, 컴파일/git diff --check 통과. 전체 python -X utf8 -m unittest discover -s tests:466 tests50.123초 OK(skipped=2), artifacts/faint-attached-guide-tests.log.
- 실제 첨부 화면 선 검출/게임 이동 입력은 직접 검증하지 않았으며 Agent 재시작은 수행하지 않음. Agent 재시작 후 이동 재지시로 적용. Qwen 추가 추론은 사용하지 않음.


### 2026-10-09 그늘진 야외 통로 밝기 자동 판정 / 지도 무효 원인 표시
- D4 프로필 dark_floor138 고정 하한보다 야외 바닥이 어두운 조건(바닥V135/바위V110)에서 outdoor는 바닥을 제거하고 dungeon fallback은 둘 다 바닥으로 보아 유효 면적 제한에 실패하는 조건 재현. 첨부 화면 당시 reason/픽셀은 확보하지 않아 실제 원인 확정은 하지 않음.
- diablo4_auto의 야외 판정은 현재 명암 히스토그램의 Otsu 분리값을 사용하며 고정dark_floor 하한으로 덮어쓰지 않음. bright_floor 수동 모드는 기존 하한 유지. 초기 변경은 낮은 밝기의 던전에서도 야외 판정을 택해 분리된 방을 통로로 포함하는 회귀 실패가 있어, 밝기90퍼센타일100미만 지도는 기존 dungeon 연결성 판정을 우선하도록 수정.
- 미니맵 무효 안내를 지형 명암 분리/플레이어 주변 통로/지도 스크롤 위치 연결/보정 미완료/ROI 미확인/새 측정 대기로 구분. 모든 무효 지도를 무조건 보정 필요로 표시하던 문구 개선.
- 수정 파일: app/core/minimap_memory.py, app/ai/main_agent.py, tests/test_minimap_memory.py, tests/test_main_agent.py. 그늘진 야외 유효성/바위 유지/안내선 이동 검사 추가, 실패 원인별 상태 안내 검사 추가. Python 컴파일/git diff --check 및 수정 소스/테스트 재읽기 통과.
- 최초 그늘 조건 검사는 수정 전 실패. 수정 중 던전 회귀 실패 이후 보완하여 지도68개 검사5.729초 OK. 기존 실제 지도 fixture를diablo4_auto로 별도 읽어 바위5곳/통로3곳 보존 확인. 전체 python -X utf8 -m unittest discover -s tests:468 tests48.757초 OK(skipped=2), artifacts/adaptive-outdoor-floor-tests.log.
- 현재 사용자 화면 자체의 유효 지도 판정/게임 이동 결과는 직접 검증하지 않았으며 Agent 재시작/게임 입력은 수행하지 않음. Agent 재시작 후 이동 재지시 필요. 프로필 JSON 및 클릭 거리 설정은 변경하지 않음.


### 2026-10-09 미니맵 분석 해상도 / 경로 격자 세밀화
- 기존 원본 미니맵을192px 폭으로 먼저 축소하여 색상을 판정하고4px 격자로 경로화하던 구조 확인. D4 mapping에 analysis_width384, grid_cell_px2 적용. 원본 ROI 폭 이내(기존 좌표 기준 최소192px)에서 세밀하게 색상을 판정한 후192px 표준 좌표로 변환. 경로 격자는 각 축2배, 전체 셀 수4배로 증가. 클릭 투영/이동 거리/누적 세계 좌표는 기존192px 기준 유지.
- 미니맵 원본 미리보기도 분석 해상도에 맞춰 출력. 미리보기의 지형 마스크를 실제 이동의 안내선 제거/지형 판정/안내선 통로 보정과 동일 파이프라인으로 변경. 분석 해상도와 격자 크기 검증/상태 정보 추가. 기존 벽 여유와 플레이어 마커 크기는 분석 배율에 맞춰 유지.
- 격자 증가에 따른 계산 지연 보완: 노드 비용을 탐색 전 계산하고 고정 목표가 있는 경우 새로운 후보 목표 점수/지도 확장 점수를 생략. 동일 fixture에서2px 격자 고정 목표 경로 계산 약42~52ms(수정 전 약150ms), 새 후보 탐색 약106~119ms. 로컬 단일 측정이며 실제 게임 처리 시간 보장 아님.
- 합성 좁은 통로는 기존4px 격자에서 단절,2px 격자에서 목표 경로 연결 확인. 고해상도 원본 미리보기와 실제 이동 지형 판정 일치 검사 추가. python -X utf8 -m unittest tests.test_minimap_memory tests.test_screen_preview:75 tests4.327초 OK. 전체 python -X utf8 -m unittest discover -s tests:470 tests50.666초 OK(skipped=2), artifacts/high-detail-minimap-tests.log. Python 컴파일/git diff --check 통과 및 수정 파일 재읽기.
- 첨부 화면은 마지막 캡처2.8초 전/게임 활성화 대기 상태로 동일 시점의 지도 비교가 아닐 수 있음. 실제 게임의 새 프레임/이동 입력은 검증하지 않았고 Agent 재시작은 수행하지 않음. 재시작 후D4 프로필을 적용해야 변경 반영. 원본에 없는 세부 지형은 해상도 확대로 복원하지 못하며 색상 판정 오류가 남을 수 있음.


### 2026-10-09 누적 전체지도 표시 제거 / 화면캡처 미리보기 명칭 변경
- 화면 미리보기의 누적 전체지도 이미지/범례/설명 제거. 사용하지 않는 AccumulatedMap 컴포넌트와 UI atlas 타입 제거. 미리보기 요청에서 누적 지도 복사/이동 경로 생성/목표 투영을 생략하여 이미지 생성도 수행하지 않음. 이동용 내부 지도 기억 기능 유지.
- 버튼과 접근성 이름을 화면캡처 미리보기로 변경하고 마지막 화면캡처/HP·SP·MP/스킬/버프 영역 안내로 수정. Controller 원본 미니맵 표시는 유지.
- 수정 소스 재읽기/git diff --check 통과. npm run typecheck 및 npm run build 성공(기존500kB 번들 경고). dist 갱신. python -X utf8 -m unittest tests.test_screen_preview:6 tests0.156초 OK. python -X utf8 -m unittest tests.test_web_bridge:73 tests15.459초 OK(skipped=1). 실제 브라우저 확인/Agent 재시작은 수행하지 않음.


### 2026-10-09 원본과 지형 지도 불일치 / 야외 배경 밝기 보정
- 해상도 증가 후에도 야외 지형에서 단일 Otsu 밝기 기준이 그림자와 바위 색상을 혼동할 수 있음을 확인. 위치별 밝기가120~205로 변하고 바위는 주변 통로의76% 밝기인 합성 지도에서 수정 전 어두운 쪽 통로를 장애물로 판정하는 실패 재현. 첨부 화면 자체의 원본 픽셀은 확보하지 못하여 당시 원인을 확정한 것은 아님.
- D4 자동 야외 판정에서 주변 배경 밝기를 추정하여 원본 밝기를 정규화한 후 Otsu 지형 분리. 배경만96px로 계산하고 최종 지형은 설정된 분석 해상도를 유지. 수동 bright_floor 하한과 어두운 던전 연결성 판정 유지. 작은 아이콘 구멍/플레이어 오버레이/벽 여유 처리는 유지.
- 최초384px 직접 배경 연산은 평균91.5ms로 느려 배경 추정을 축소하여24.4ms로 개선(저장된 지도 확대본5회 로컬 측정). 수정 파일 app/core/minimap_memory.py, tests/test_minimap_memory.py. 수정 소스/테스트 재읽기, Python 컴파일 및 git diff --check 통과.
- 관련 검사 python -X utf8 -m unittest tests.test_minimap_memory tests.test_screen_preview:76 tests4.359초 OK. 실제 저장 지도 fixture 자동 모드에서 바위5곳/통로3곳 보존 확인. 실제 게임 실행/Agent 재시작은 수행하지 않았으며 재시작 후 적용 필요.
- 최종 전체 python -X utf8 -m unittest discover -s tests:471 tests48.078초 OK(skipped=2), artifacts/illumination-minimap-final-tests.log.


### 2026-10-09 넓은 바위가 통로가 되는 밝기 보정 수정
- 이전 배경 추정의49px 닫기 연산은 커널보다 큰 갈색 지형 내부를 배경 밝기로 삼아, 밝기 정규화 후 넓은 바위까지 통로로 만드는 조건 확인.144x192 합성 지도에서 오른쪽 경계에 붙은84x113px 바위/왼쪽70x77px 바위 조건이 수정 전 실패. 첨부 화면 자체의 픽셀 판정을 직접 재현한 것은 아님.
- 국소 닫기 연산 기반 배경 추정을 밝은 통로의 상위 밝기 평면 추정으로 교체.96px 배경에서 최대6회 최소제곱/어두운 잔차 제외를 수행해 넓은 바위 내부를 조명 기준으로 사용하지 않도록 변경. 원본 지형 분석 해상도/던전 처리/벽 여유 유지.
- 수정 소스/테스트 재읽기, Python 컴파일 및 git diff --check 통과. python -X utf8 -m unittest tests.test_minimap_memory tests.test_screen_preview:77 tests4.179초 OK. 넓은 바위 보존과 그늘진 통로 보존 검사 모두 통과. 저장된 실제 지도 fixture 자동 판정에서 바위5곳/통로3곳 확인.384px 확대fixture5회 지형 분석 평균28.1ms(로컬 단일 측정).
- 실제 게임 입력/Agent 재시작은 수행하지 않았고 첨부 화면의 정확한 원본 판정은 검증하지 않음. Agent 재시작 후 새 지도 측정으로 적용 확인 필요.
- 전체 python -X utf8 -m unittest discover -s tests:472 tests45.808초 OK(skipped=2), artifacts/broad-rock-minimap-tests.log.


### 2026-10-09 통로 중앙 우선 이동 / 벽 쪽 안내선 클릭 방지
- 기존 follow_centerline은 테스트에서만 직접 설정되며 프로필에서 읽지 않았음. 주황선 거리 비용3이 벽 여유 비용보다 강해 벽 쪽 선으로 치우치는 합성 조건 재현:64px 높이 통로에서 중앙72px 대비 실제 클릭58px(14px 편차), 수정 전 검사 실패.
- D4 navigation mapping에 follow_centerline=true, center_weight4→15, preferred_clearance_px10→14 적용. update에서 중앙 우선 설정을 경로 계산에 연결. 중앙 우선일 때 안내선 거리 비용3→0.03으로 낮춰 목표 방향 선정은 안내선에 맡기고 실제 경로는 벽 여유를 우선. 중앙 우선일 때 직선 단축의 기존1.02 비용 제한 사용.
- 실제 클릭을 안내선 픽셀에 맞추는 단계는 중앙 우선일 때 벽 여유가0.25격자보다 더 줄어드는 보정을 차단. 목표 좌표 유지/벽·HUD 검사/좁은 통로 통행 유지. runtime 설정에 중앙 우선 boolean 검증 추가.
- 추가 검사에서 벽 쪽 안내선이 있어도 클릭 중앙 편차7px 이내 및 동일 목적지 유지 확인. python -X utf8 -m unittest tests.test_minimap_memory tests.test_screen_preview:78 tests4.783초 OK. 수정 소스/프로필/테스트 재읽기, Python 컴파일 및 git diff --check 통과.
- 실제 게임 이동/Agent 재시작은 수행하지 않았음. Agent 재시작 후D4 프로필 적용 필요. 지형 인식이 틀린 곳에서는 계산된 중앙도 실제 통로 중앙과 다를 수 있음.
- 전체 python -X utf8 -m unittest discover -s tests:473 tests48.565초 OK(skipped=2), artifacts/center-priority-movement-tests.log.


### 2026-10-09 플레이어 / NPC 미니맵 아이콘 지형 제외
- D4 mapping exclude_entity_icons=true 추가. 밝은 저채도 또는 선명한 유색 내부/어두운 테두리를 가진24px 이하(192px 좌표 기준)의 작은 연결 성분을 아이콘 후보로 검출. 테두리 여유를 포함해 인페인팅 후 지형을 판정하여 주변 바위/통로를 추정. 단순 밝은 바닥 무늬와 길게 이어진 안내선은 제외 후보로 삼지 않음. 의미 기반 NPC 식별이 아닌 아이콘 색상/크기/테두리 검출임.
- 지형 분석과 미리보기 마스크 모두 공통 정제 파이프라인 사용. 지도 스크롤 정합도 아이콘을 제거한 회색 영상 사용. 원본 프레임/원본 미니맵 표시는 변경하지 않음. 아이콘에 가린 셀은 누적 벽/통로 증거를 갱신하지 않으며 안내선 보정도 해당 셀을 영구 통로로 저장하지 않음.
- 검증 중 실제 fixture의 아이콘/안내선 중첩에서 새 미등록 셀 접근 KeyError 확인 후 해당 셀의 증거 갱신을 모두 생략하도록 수정. 중첩 재현/아이콘 셀 미저장 검사 추가. 합성 플레이어/NPC 아이콘 제거, 바위 위 아이콘 제거 후 실제 바위 유지, 입력 영상 불변 및 경로 연결 검사 추가.
- 수정 파일 app/vision/minimap_overlays.py(신규), app/core/minimap_memory.py, app/profiles/runtime_settings.py, app/profiles/diablo4/navigation.json, tests/test_minimap_memory.py. 파일 재읽기/Python 컴파일/git diff --check 통과. 관련 검사80 tests4.610초 OK. 실제 저장 지도 바위5곳 유지 확인.
- 첨부 이미지 원본 픽셀 자체 및 게임 입력은 직접 검증하지 않음. Agent 재시작 후 적용하며 기존 잘못 누적된 아이콘 벽 증거도 새 세션에서 초기화됨. 아이콘 아래 실제 지형이 가려져 있으므로 주변으로 추정하며 완전 복원을 보장하지 않음.
- 최종 전체 python -X utf8 -m unittest discover -s tests:475 tests45.583초 OK(skipped=2), artifacts/minimap-entity-exclusion-final-tests.log.


### 2026-10-09 예정 진행 방향 변경 버튼 / 경로·클릭 지도 일치
- Qwen 대화의 북쪽 이동/남쪽 이동 버튼 제거, 예정 진행 방향 변경 버튼 추가. 명령 /replan 또는 예정 진행 방향 변경을 Qwen 호출 없이 로컬 처리. 현재 목표/ClickJourney/예약 이동/이전 명령 epoch를 해제하고 최신 유효 지도에서 이전 방향을 피한 대체 목표 우선 탐색. 대체 경로가 없으면 유효한 기존 방향 허용. 최종 핀은 유지하며 일회 대체 목표 선택 후 기존 경로 모드 복원. 중단 상태/이동 전용/사냥 모드 유지.
- PATH_BLOCKED는 LocalNavigator가 클릭 후보를 거절할 때 발생. 지도 worker가 memory를 갱신한 뒤 별도 _minimap_mask를 게시하기 전이면 새 경로가 오래된 마스크로 검증될 수 있음. stale mask 전부0 조건으로 유효한 경로의 클릭이 거부되는 조건 확인. 경로 계산 lock 안에서 승인 격자/플레이어 위치의 동일 시점 사본을 클릭 설정에 묶어 전달. 격자 셀을 원래 크기로 확대하고 나머지 픽셀은0으로 패딩해 홀수 높이 지도에서도 투영 좌표 유지.
- 승인 격자 검사는 임의0.5px 오프셋과 중복3x3 점유율 기준을 적용하지 않고 동일 경로의 실제 픽셀 선분을 확인. 벽/지도 경계/화면 경계/플레이어·NPC/HUD 클릭 금지는 유지. 기존 일반 미니맵 검사는 유지. PATH_BLOCKED 안내를 실제 원인에 맞춰 계획 경로 클릭 검증 실패 · 새 미니맵 측정 대기로 수정.
- 새 목표 변경/최신지도 대기/이동전용과 중단 유지/무VL호출 검사 추가. 최신 경로와 오래된 게시 마스크 분리 검사, 승인 지도 실제 차단 시 클릭 금지 확인. 버튼 source/백엔드/프로필 재읽기 및 Python 컴파일/git diff --check 통과. npm run typecheck/build 성공(dist 갱신, 기존 큰 번들 경고). 소규모 초기 검사는 이동기록이 없는 상태의 last_release 기대값으로 실패하여 실제 초기화 상태/재계획 요청 검사로 수정.
- 중간 전체477 tests49.759초 OK(skipped=2), 경로마스크 결합 후478 tests46.915초 OK(skipped=2). 홀수 픽셀 패딩 수정 후 관련 단일 검사0.283초 OK. 실제 사용자 게임 입력/브라우저 검증/Agent 재시작은 수행하지 않음.
- 최종 전체 python -X utf8 -m unittest discover -s tests:478 tests46.411초 OK(skipped=2), artifacts/manual-replan-route-mask-final-tests.log.


### 2026-10-09 사냥 / 이동 안내선 선택적 사용 및 분석 해상도 축소
- 사냥 시작과 이동 명령의 로컬 경로에 explore_without_guide=true 적용. 안내선·핀 존재 시 기존 우선 경로를 유지하고, 둘 다 없으면 현재 지도에서 연결된 통로의 탐색 목표를 선택. 기존 목표는 유지하며 도착 후 다음 목표 선택. 지도 자체가 무효이거나 실제 통로가 차단되면 계속 차단한다. 안내선 존재하나 연결이 확인되지 않은 경우 무리한 우회 목표를 생성하지 않음.
- 사냥은 기존 EncounterCombatPolicy가 확정 적을 만나면 이동보다 공격을 우선하고 적 소실 후 이동을 재개하는 정책 유지. 이동 전용은 적 정책 제외 및 ATTACK/USE_SKILL 차단 유지. 로컬 전환에 VL 추가 요청 없음. 이동 상태/채팅 응답/웹 안내를 핀 경로·주황선·미니맵 통로 탐색으로 구분.
- GAMEPLAY.md에 두 모드의 안내선 선택적 사용 규칙 추가. 기본값도 현재 통로 중앙 우선/아이콘 제외/안내선 없는 탐색 설정과 맞춤. JSON이 우선하는 기존 기본값 병합 정책 유지.
- 사용자 후속 요청으로 D4 분석 폭384→192px, 격자2→4px로 낮춤. navigation.json과 GAMEPLAY.md 기본값 모두 동일하게 변경. 밝기 보정/아이콘 제외/중앙 우선/클릭 검증 일치는 유지. 해상도가 당시 인식 실패의 확정 원인인 것은 아님.
- 신규 안내선 없는 탐색·목표 유지·핀/선 우선·무효 지도 차단 검사와 사냥/이동 두 모드 실제 prepare 및 전투 정책/전투 후 이동/무VL 요청 검사 통과. 신규2 tests0.589초 OK. 해상도 변경 전 전체480 tests49.677초 OK(skipped=2), artifacts/optional-guide-hunt-move-tests.log. 해상도 변경 후 관련82 tests4.672초 OK. npm typecheck/build 성공(dist 갱신). 파일 재읽기/Python 컴파일/git diff --check 통과.
- 게임 실제 인식/이동 입력 및 Agent 재시작은 수행하지 않음. 재시작하고 웹 새로고침 후 적용 확인 필요.
- 최종 낮은 해상도 설정 전체 python -X utf8 -m unittest discover -s tests:480 tests48.642초 OK(skipped=2), artifacts/low-resolution-optional-guide-tests.log.


### 2026-10-09 핀 방향 기준 / 연결된 통로 목표 선정
- D4 mapping pin_direction_priority=true 추가 및 실행 경로에 반영. 핀을 기본 방향 기준으로 삼고 예정 목표는 현재 플레이어에서 경로 탐색으로 연결된 격자/실제 통로 픽셀 안에 선택. 핀이 벽 위·맵 밖·분리된 지형에 있으면 핀 접근 거리와 벽 여유를 함께 고려한 통로 목표 사용. 연결된 실제 통로에 핀이 있으면 해당 안전 셀을 골라 핀 근처에서 반복 정지하지 않도록 함.
- 핀 방향 우선에서는 반대 방향 안내선 끝을 예정 목표로 고정하지 않고 안내선은 경로 비용 참고로 사용. 안내선 거리 비용은 중앙 우선 모드와 동일한0.03으로 반영. 핀이 없으면 기존 안내선/지도 탐색 유지. 검증된 기존 목표 유지하되 실제 목표 픽셀이나 연결성이 무효이면 새 접근점 선택. 기억한 핀 좌표는 유지.
- 수정 파일 app/core/minimap_memory.py, app/ai/main_agent.py, app/profiles/runtime_settings.py, app/profiles/diablo4/navigation.json, app/profiles/diablo4/GAMEPLAY.md, tests/test_minimap_memory.py. 소스/설정 재읽기 및 git diff --check 통과. GAMEPLAY 기본값/문서 규칙도 일치시킴.
- 핀이 벽·맵 밖·분리된 섬에 있는3조건의 연결된 통로 목표/방향/목표 유지 검사, 반대 안내선보다 핀 방향 우선 및 실제 클릭 방향/핀 도착 가능 거리 검사 추가. 중간 전체482 tests50.414초 OK(skipped=2), artifacts/pin-reachable-floor-goals-tests.log. 핀 도착 셀/안내선 비용 최종 보완 후 경로·미리보기·화면투영·두 모드 전투전환·컨트롤러 반복클릭 관련87 tests5.984초 OK.
- 실제 게임 입력/Agent 재시작은 수행하지 않음. 재시작 후D4 프로필로 적용 확인 필요. 지형 자체가 잘못 분류된 곳은 예정 목표도 실제 게임 통행 가능 여부와 다를 수 있음.


### 2026-10-09 미니맵 스크롤 연결 안정화 / 재확인 로그 반복 억제
- 기존 정합 검증이 원시 밝기 차이를 그대로 사용하여 노출 변화도 정합 실패 요인이 될 수 있음을 확인. 현재/이전 이미지 차이의 중앙값을 제거한 잔차로 정지·광류·위상 상관 결과를 검증하여 전체 밝기 변화에 대응.
- 특징점/위상 상관이 실패하면 최대24px 범위에서 작은 지도 이동을 템플릿 상관으로 보완. 점수0.88 이상, 주변 대체 후보와0.015 이상 차이, 보정 후 잔차8 미만 조건을 모두 만족해야 연결. 실제로 불명확한 지도를 임의의0 이동으로 승인하거나 이전 지도로 계속 클릭하지 않음.
- 지도 스크롤 위치 연결 재확인 로그는1초 이상 연속 실패할 때 표시하며 같은 실패는 중복 출력하지 않음. 회복 후 재발해도 이전 재확인 출력에서10초 이내에는 생략. 다른 중단/포커스/HP 메시지는 즉시 상태 변경을 반영. 웹 상태의 실제 유효성 판정은 유지.
- 밝기25 증가에서 정지 유지, 특징점/위상 정합 불가 조건에서 실제(-5,+3) 스크롤 복구, 무관한 영상 연결 거절 검사 추가. 짧은 실패 로그 생략/지속 실패 표시/10초 내 반복 억제/다른 상태 즉시 표시 검사 추가. 관련80 tests5.683초 OK. 소스/테스트 재읽기, Python 컴파일/git diff --check 통과.
- 변경 파일 app/core/minimap_memory.py, app/ai/main_agent.py, tests/test_minimap_memory.py, tests/test_main_agent.py. 실제 사용자 프레임의 빈번한 실패 원인은 직접 재현하지 않았으며 Agent 재시작/게임 입력도 수행하지 않음. 재시작 후 적용 확인 필요.
- 최종 전체 python -X utf8 -m unittest discover -s tests:484 tests50.907초 OK(skipped=2), artifacts/minimap-registration-stability-tests.log.


### 2026-10-09 통로 목표 차단 반복 대기 / 직선 목표 표시 수정
- 노란 직선이 planned_target보다 pin_target을 우선하여 벽 위 핀까지 표시하던 문제 수정. 직선은 실제 통로 목표를 표시하고 핀은 별도 흰색 표식으로 유지. 클릭 전 웹 목표도 짧은 클릭 끝점 대신 memory.goal 좌표로 표시. 직선 자체는 장애물을 우회하는 경로가 아니며 실제 이동 경로는 하늘색.
- 새 안내선 끝점이 벽 픽셀 또는 플레이어와 단절된 격자에 있으면 경로 탐색으로 연결된 실제 통로 픽셀의 접근점으로 변경. 명시적으로 고정한 목표는 자동 대체하지 않음.
- 아직 클릭하지 않았거나 이전 클릭 이후 유지 중인 자동 이동 목표가 막힌 경우에도, 80ms 이상 떨어진 두 유효 지도에서 차단 확인 후 목표 해제/같은 처리 내 재탐색. 기본 안내선/핀 기준 재탐색이 실패하면 연결된 대체 통로 탐색. 위치 미측정/오래된 지도/한 번의 차단은 목표 교체 근거로 사용하지 않음.
- ORANGE_ROUTE_NOT_FOUND 및 안내선 대기 STOP은 ClickJourney를 지우지 않도록 유지. 경로 재확인만으로 진행 중 목표 기록을 잃어 차단 복구 대상에서 빠지던 조건 보완.
- 변경: app/core/minimap_memory.py, app/core/click_journey.py, app/ai/main_agent.py, Visual-Agent-Lab-local/app/minimap-mapping.tsx 및 dist, tests/test_minimap_memory.py, tests/test_main_agent.py. 새 안내선 벽 끝점/미전송 목표의 두 지도 차단 확인/대기 시 목표 유지/직선 표시 좌표 검사 추가.
- npm run typecheck, npm run build 성공. 기존 500kB 초과 번들 경고 유지. 실제 게임 입력/Agent 재시작은 수행하지 않음.
- 최종 python -X utf8 -m unittest discover -s tests:488 tests47.858초 OK(skipped=2), artifacts/blocked-route-goal-tests.log. 초기 추가 테스트의 불완전한 지도 초기화(center_weight 누락)를 보완한 뒤 전체 검증 통과. 소스/테스트/빌드 산출물/문서 재읽기 및 git diff --check 확인.


### 2026-10-09 캐릭터 주변 이동 클릭 제외
- 자동 이동(HUNT_EXPLORE/MINIMAP_QWEN/USER_COMMAND)의 최소 클릭 거리를 실제 게임 화면 높이의10%로 적용(1080p에서108px). 기존 최소18px 허용과 짧은 지도 경유점 투영 때문에 캐릭터 주변 클릭이 만들어지는 조건 보완.
- 짧은 경유점은 동일 진행 방향으로 최소 거리까지 확장하되 전체 확장 구간을 승인된 미니맵 마스크로 검사. 벽/HUD/객체 제외 영역에 걸리는 클릭은 전송하지 않으며 최소 거리보다 짧은 축소 클릭도 사용하지 않음. 원래 더 긴 클릭은 유지. 이동 반복 전송 주기는 기존80ms 유지.
- 변경 파일 app/ai/main_agent.py, app/core/local_navigation.py, tests/test_main_agent.py. 기존 짧은 클릭 검사에60px 경유점을108px로 확장하는 결과와 확장 구간 벽 통과 거절 검사 반영. 소스/테스트 재읽기 및 git diff --check 확인.
- 실제 게임에서 이동 연속성은 아직 확인하지 않았으며 Agent 재시작은 수행하지 않음. 좁은 모퉁이에서 최소 거리만큼 직선 통로가 없으면 안전한 클릭을 찾을 때까지 대기할 수 있음.
