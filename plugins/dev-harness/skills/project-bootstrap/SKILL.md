---
name: project-bootstrap
description: 새 프로젝트에 개발 하네스를 적용하는 절차 — CLAUDE.md 골격 생성(템플릿 기반, 소스 구조를 훑어 초안 채움), .claude/verify.toml 작성, .claude/skills 폴더 준비. 새 프로젝트 폴더에서 "하네스 적용", "CLAUDE.md 만들어", "/project-bootstrap" 요청 시 사용.
---

# project-bootstrap — 새 프로젝트에 하네스 적용

훅(.bak 차단·py_compile·세션 기록)은 플러그인이 켜져 있으면 이미 동작한다.
이 스킬은 **프로젝트 고유 지식을 담는 파일**을 만드는 절차다.

## 절차

1. **기존 파일 확인.** 프로젝트 루트에 `CLAUDE.md` 가 이미 있으면 덮어쓰지 않는다.
   빠진 섹션만 제안하고 멈춘다.

2. **소스 구조 파악.** `ls`, 주요 설정 파일(`package.json`, `requirements.txt`, `docker-compose.yml`,
   `pyproject.toml`, `*.csproj`, `deploy*.sh` 등)을 읽어 다음을 파악한다.
   - 서비스 개요(무엇을 하는 시스템인지 한 문단)
   - 소스 루트와 git 저장소 위치(프로젝트 폴더 = 저장소인지, 하위 폴더인지)
   - 기술 스택과 의존성 파일 위치
   - 배포 방식(스크립트·CI·수동)과 시크릿 파일(gitignore 대상)
   - 테스트/검증 명령과 **성공 기준**(무엇이 통과하면 끝인지 — 테스트·헬스체크·재현 스크립트)

3. **CLAUDE.md 생성.** `templates/CLAUDE.md.tmpl` (이 플러그인 폴더의 `templates/`) 을 읽어
   `{{...}}` 자리를 2단계에서 파악한 내용으로 채운다. 모르는 항목은 지우지 말고
   `(확인 필요)` 로 남겨 사용자가 채우게 한다. 백서·설계문서가 있으면 "원본 근거" 에 경로를 적는다.

4. **`.claude/verify.toml` 생성** (Python 프로젝트일 때).
   ```toml
   [verify]
   repo              = "."                 # git 저장소가 하위 폴더면 그 이름
   requirements_dirs = []                  # requirements.txt 가 있는 디렉터리 (repo 기준). 컨테이너 배포면 꼭 채운다
   env_known_files   = ["CLAUDE.md", ".env"]
   env_hint          = "배포 환경 .env 수동 추가 필요 여부 확인"
   ```

5. **`.claude/skills/` 폴더 생성**과 첫 스킬 후보 안내. CLAUDE.md 가 길어질 항목(환경변수 실측값,
   배포 트리거 표, 버전 이력)은 처음부터 `.claude/skills/<프로젝트약칭>-env/SKILL.md` 식으로 분리한다.
   SKILL.md 는 frontmatter 의 `name` 과 `description` 이 필수이며, description 에
   "언제 사용" 을 반드시 적는다.

6. **verify 1회 실행**으로 설정이 맞는지 확인하고, 만든 파일 목록과 사용자가 채워야 할
   `(확인 필요)` 항목을 보고한다.

## 원칙

- CLAUDE.md 에는 **"어디를 봐야 하는지"와 "지켜야 하는 규약"** 만 압축해 둔다.
  코드로 알 수 있는 것(디렉터리 목록, 함수 시그니처)은 적지 않는다.
- 공통 규약(워크플로우·.bak 금지·env 플래그)과 **코드 작성 태도 4원칙**(생각 먼저·단순함·외과적
  변경·목표 기반)은 `harness-workflow` 스킬에 있으므로 CLAUDE.md 에는 참조 한 줄만 두고
  중복 기술하지 않는다. CLAUDE.md 5절에는 **이 프로젝트에서만 성립하는 규약**만 적는다.
- 부트스트랩 자체도 외과적으로 한다. 기존 파일을 재작성하지 말고 빠진 섹션만 덧붙인다.
- 상대 날짜는 절대 날짜로 적는다.
