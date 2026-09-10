# claude-harness — Claude Code 개발 하네스 (로컬 플러그인 마켓플레이스)

ISAC 프로젝트에서 쓰던 하네스(훅·자가 검증·세션 기록·CLAUDE.md 규약)를 어느 프로젝트에서나
쓸 수 있게 플러그인으로 묶은 것. 프로젝트 고유 지식(환경변수·배포 경로·함정)은 여기 두지 않고
각 프로젝트의 `CLAUDE.md` 와 `.claude/skills/` 에 둔다.

```
claude-harness/
├── .claude-plugin/marketplace.json     ← 마켓플레이스 선언 (이름: local-harness)
└── plugins/dev-harness/
    ├── .claude-plugin/plugin.json
    ├── hooks/hooks.json                ← PreToolUse(.bak 차단) · PostToolUse(py_compile) · SessionEnd(기록 저장)
    ├── scripts/claude_hook.py          ← 훅 본체. 프로젝트 경로는 CLAUDE_PROJECT_DIR 로 받음
    ├── scripts/verify.py               ← 커밋 전 자가 검증. <프로젝트>/.claude/verify.toml 로 프로젝트별 설정
    ├── scripts/export_session.py       ← 세션 JSONL → md. ~/claude-logs/sessions/<프로젝트명>/ 에 저장·커밋
    ├── skills/harness-workflow         ← 공통 작업 규약
    ├── skills/verify                   ← /verify 실행 절차
    ├── skills/project-bootstrap        ← 새 프로젝트에 CLAUDE.md·verify.toml 만드는 절차
    └── templates/CLAUDE.md.tmpl
```

## 설치 (한 번만)

```powershell
claude plugin marketplace add C:\Users\user\claude-harness
claude plugin install dev-harness@local-harness      # 범위를 물으면 user 선택 → 모든 프로젝트 적용
```

설치 후 VSCode / Claude Code 를 재시작한다. 확인: Claude Code 안에서 `/plugin` → dev-harness 가 enabled.

## 새 프로젝트에 적용

그 프로젝트 폴더에서 Claude Code 를 열고 `/project-bootstrap` 을 실행한다.
훅은 이미 전역으로 돌고 있으므로, 이 단계는 CLAUDE.md 골격과 `.claude/verify.toml` 을 만드는 것이다.

## 수정 후 반영

스크립트·스킬을 고치면 이 폴더 한 곳만 고치고:

```powershell
claude plugin update dev-harness@local-harness
```

## 세션 기록 위치

기본 `~/claude-logs` (git 저장소, 처음 실행 시 자동 init). 바꾸려면 시스템 환경변수
`CLAUDE_HARNESS_LOG_DIR` 지정. ISAC 의 기존 기록(`10.업무도구및아이작(ISAC)/claude-logs/`)은
그대로 두거나 `~/claude-logs/sessions/10.업무도구및아이작(ISAC)/` 로 옮겨 합칠 수 있다.

## 훅 동작 확인 방법

- Claude 에게 "test.bak 파일을 만들어 줘" → 거부 메시지가 나와야 함
- 문법이 틀린 .py 를 저장시키면 → "py_compile 실패" 피드백이 돌아와야 함
- 세션 종료 후 `~/claude-logs/sessions/<프로젝트명>/` 에 md 생성 + `git log` 커밋 확인
"# claude-harness" 
