# 디아블로 IV 이동·전투 규칙

JSON > 게임 규칙 > 공통 규칙 순으로 적용한다.

- 몸체에 연결된 단일 빨간 적 HP바로 적을 확인한다. 라헤어·아시아라 등 저장한 아군은 공격하지 않는다.
- 이동은 누적 미니맵 경로와 실제 이동량으로 제어한다. 매 클릭에 Qwen을 기다리지 않는다.
- 기본 공격은 input.json의 basic_attack_mode를 따른다. tap은 우클릭 빠른 연타다.
- 버프는 assets/buffs의 등록 이미지 중 하나라도 화면 BUFF 영역에 보이면 입력을 멈춘다. 모두 4초 연속 없을 때만 4번 키를 보낸다. 전송 후 다시 4초 확인하며 크기·숫자 변화도 검사한다.

## 역할과 자동사냥
Qwen은 적·아군·모르는 객체를 분류하고 사용자 지시를 HUNT/MOVE/ATTACK/STOP/NONE 등 허용된 행동으로 해석한다. 접근·공격 거리·후퇴는 선택한 근거리/원거리 설정을 로컬 전투 정책이 적용한다. 모델은 키·좌표를 만들지 않는다.
OpenCV는 미니맵/실제 이동/적 HP바/버프를 검사한다. 좌표·입력·스킬 간격·포커스·HP·중단 검증은 실행 코드가 맡는다.
적이 없으면 지도 탐색과 이동, 적이 있으면 공격, 전투가 끝나면 기존 목표 이동을 이어간다. 객체를 기억하고 미확인 객체는 질문한다.

## 이동
예정 최종 목표는 유지한다. feedback_steps는 중앙 클릭 거리(%)를 반영하되 검증된 통로 길이로 제한한다. 지도 0.8초 이내이면 최소 0.08초 간격으로 같은 목표 클릭을 이어간다. 클릭으로 정체 타이머를 초기화하지 않는다. 최종 목표의 15%(4~12px) 인근에서 다음 목표를 선택한다.
자동사냥·이동은 주황색 안내선 또는 핀이 있으면 우선 따라간다. 둘 다 없으면 미니맵의 연결된 통로 중앙을 탐색하며 미방문 길을 우선한다. 핀은 방향 기준으로 기억하며 예정 목표는 핀 쪽의 연결된 통로 안에 선택한다. 핀 좌표가 벽·맵 밖이면 접근 가능한 통로를 목표로 삼는다. 남아 있는 주황·적갈색 안내선 끝을 우선하고 막히면 연결된 통로로 우회한다. 핀이 가려져도 누적 좌표를 유지한다. 미방문 길과 방향 지시를 반영한다. 정체 1.2초이면 재계획한다. 핀 이동 중 자동 Qwen 분석은 하지 않는다. 이동 명령은 공격 없이 이동만 한다. 벽·미확인 지형은 클릭하지 않는다.
미니맵 위치·투영 배율·회전·창 크기는 navigation.json을 따른다. HUD·미니맵·캐릭터·아군 클릭은 차단한다. 검증된 통로는 직선화하고 벽 앞에서 클릭을 제한한다. 클릭선은 전송 당시 위치 기준이다.

## 공격·스킬·버프
확정 적을 만나면 이동보다 공격을 우선한다. 공격 스킬은 enabled와 disabled_actions, 등록 키, 성공 전송 이후 사용 간격 및 시각적 준비 조건을 따른다. 모두 대기 중이면 기본 공격을 한다.
빨간 잔량이 작아져도 같은 적을 연타한다. 빨강·검정 전체 바를 추적하고, 감소 후 완전 검정(HP 0)을 연속 3회 확인하면 종료한다. 바·추적 소실은 공격 중지 후 재확인하며 사망·아군으로 저장하지 않는다. 사거리는 input.json이 우선한다.
버프는 Qwen의 추측이나 쿨다운 인터벌로 발동하지 않는다. 미등록·검사 불가는 발동하지 않는다. 입력 실패는 성공으로 기록하지 않는다. 등록 아이콘 하나라도 보이면 모든 소실 상태와 재사용 대기를 해제한다.

## 중단·보정
기본 자동사냥. 게임 비활성 시 중단, 활성화 시 재개한다. 사냥 중단은 입력·Qwen을 해제하고 수동 조작을 유지한다. 시작/재개로 자동사냥에 복귀한다. HP 측정은 0.8초까지 유효하며 미검출·만료 시 차단한다. 15초 미탐색은 즉시 중단한다.
시작 Qwen HUD 보정은 1회, 512px/384토큰/6초로 제한한다. 보정 대기 중에도 최신 OpenCV HP·지도·포커스가 유효하면 이동한다. 응답 적용에는 현재 화면 바 위치를 사용한다.

## 누락된 설정의 기본값
저장 JSON·스킬·기억은 보존한다.

```gameplay-settings
{
  "navigation.json": {
    "steering": {"projection":"isotropic","move_interval_ms":100,"require_minimap":true,"player_screen":[0.5,0.5],"infer_player_from_hud":true},
    "minimap": {"mapping":{"enabled":true,"mode":"diablo4_auto","center_weight":15,"preferred_clearance_px":14,"wall_margin_px":1,"follow_centerline":true,"exclude_entity_icons":true,"explore_without_guide":true,"pin_direction_priority":true,"analysis_width":192,"grid_cell_px":4,"screen_pixels_per_map_pixel":12,"calibrated":true,"control_mode":"feedback_steps"}}
  },
  "input.json": {
    "basic_attack_mode":"tap", "tap_ms":15, "pointer_smoothing":false, "pointer_duration_ms":40,
    "combat": {"stance":"stationary","dodge_mode":"key","minimum_distance":0.18,"attack_distance":0.16,"ranged_attack_distance":0.45}
  }
}
```
