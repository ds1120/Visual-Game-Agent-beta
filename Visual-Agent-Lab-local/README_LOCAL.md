# Visual Agent Lab 로컬 실행

기존 대시보드의 튜닝 화면과 Agent 연결 화면을 로컬 서버에서 실행하는 소스입니다. 빌드된 화면(dist)도 포함합니다.

## 통합 실행 (권장)

이 폴더를 Visual Game Agent 프로젝트의 app 폴더와 같은 위치에 둡니다. 통합 업데이트 ZIP에는 Python 웹 서버 수정도 포함합니다.

```powershell
py -m app.main
```

Agent·대시보드·실험 저장 API가 함께 실행되며 http://127.0.0.1:8765/#tuning 이 자동으로 열립니다.
화면은 Agent에 자동 연결합니다. 시작/재개를 누르면 게임 입력을 재개합니다.
Node.js, npm install, start-local.cmd는 통합 실행에 필요하지 않습니다. Ctrl+C로 함께 종료합니다.
`--no-chat`은 콘솔 대화를 끄고 `--no-browser`는 브라우저 자동 열기를 끕니다. `--no-web`은 웹 서버를 끕니다.
실험 기록은 app/profiles/<게임>/tuning_experiments.json에 저장합니다.

## 별도 웹 서버 실행 (개발·호환용, Windows)

1. Node.js 22.13 이상을 설치합니다.
2. ZIP을 압축 해제합니다.
3. `start-local.cmd`를 실행합니다. 또는 해당 폴더에서 `node server.mjs`를 실행합니다.
4. 브라우저에서 http://127.0.0.1:3000/#tuning 을 엽니다.

미리 빌드된 화면은 npm install 없이 실행할 수 있습니다. 서버 종료는 Ctrl+C입니다. 서버는 127.0.0.1에만 바인딩됩니다.

## 튜닝과 저장

OpenCV HUD, YOLO 탐지, Qwen-VL 판단 역할, 캡처 FPS·ROI·YOLO 주기·이미지 크기·게이팅 설정 및 예상 부하 비교를 제공합니다. GPU 60% 이하, 추론 20~30ms, 캡처 60FPS 기준선과 실측 입력·실험 저장·비교 기능을 그대로 제공합니다.

예상치는 가정 기반 계산입니다. 실제 GPU·추론 시간은 입력하거나 실행 중인 Agent에서 가져와야 합니다.

별도 Node 서버의 실험 기록은 실행 폴더의 `data/experiments.json`에 저장되며 재시작해도 유지됩니다. 백업하려면 data 폴더를 복사하세요. 사이트의 기존 클라우드 실험 기록은 자동으로 복사되지 않습니다. 로컬판에는 클라우드 로그인이나 DB 연결이 필요하지 않습니다.

## 기존 Python Agent 연결

이 ZIP의 agent-patch 폴더에는 기존 웹 연동 Agent에 적용할 변경 파일만 들어 있습니다.

1. 기존 Visual Game Agent 프로젝트를 종료합니다.
2. `agent-patch` 안의 app 폴더와 tests 폴더를 기존 프로젝트의 동일한 경로에 덮어씁니다. 몹 판정·웹 설정 적용 수정과 로컬 화면 3000 포트 허용을 포함합니다. 자세한 변경 내용은 agent-patch/README_RECOGNITION_SETTINGS.md를 확인하세요.
3. 기존 Python 프로젝트에서 `py -m app.main --no-chat`을 실행합니다.
4. 대시보드에서 Agent 연결을 누릅니다. 기본 주소는 `http://127.0.0.1:8765`이며 토큰 입력은 없습니다.

이 패치는 앞서 제공한 웹 연동 버전을 기준으로 합니다. 이전 버전에는 웹 연동 업데이트가 먼저 필요합니다. Agent 기본 입력 백엔드는 mock입니다. 실제 게임 입력, 모델, 캡처 장치는 기존 Agent 설정을 사용합니다. 별도 Node 서버 실행은 Python Agent나 모델을 실행하지 않습니다. 통합 실행은 py -m app.main을 사용하세요.

## 소스 수정

```sh
npm install
npm run dev
```

개발 화면도 http://127.0.0.1:3000 에서 엽니다. 개발 서버와 일반 서버를 동시에 같은 포트에서 실행하지 마세요.

```sh
npm run typecheck
npm test
npm run build
npm start
```

app/dashboard.tsx는 튜닝 화면, app/live-agent.tsx는 Agent 연결 화면, lib/tuning.ts는 계산과 입력 검증, server.mjs는 로컬 API와 파일 저장을 담당합니다. public과 dist에는 아이콘과 빌드된 정적 화면이 있습니다. pnpm 사용 시 제공된 pnpm-lock.yaml을 사용할 수 있습니다.

배포 사이트는 이번 로컬 소스 다운로드 작업으로 변경되지 않았습니다.
