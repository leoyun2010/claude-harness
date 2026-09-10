---
name: verify
description: 커밋 전 자가 검증 실행 — 변경 .py py_compile, requirements.txt ↔ import 대조, .bak 감지, 신규 env 키 경고. 소스 수정을 마치고 커밋을 요청하기 전, 또는 사용자가 "/verify", "검증해", "커밋 전 확인" 을 요청할 때 사용.
---

# verify — 커밋 전 자가 검증

현재 프로젝트 루트(대화를 시작한 폴더)를 기준으로 아래를 실행하고 결과를 그대로 보고한다.

```
python "C:\Users\user\claude-harness\plugins\dev-harness\scripts\verify.py" --root "<프로젝트 루트>"
```

- `--all` 을 붙이면 변경 파일이 아니라 저장소 전체 .py 를 컴파일 검사한다.
- 설정 파일 `<루트>/.claude/verify.toml` 이 없으면 기본 검사(py_compile·.bak·env 키)만 돈다.
  requirements 대조가 필요한 프로젝트면 `project-bootstrap` 스킬의 4단계대로 toml 을 만든다.
- 결과가 FAIL 이면 오류를 고치고 다시 실행한다. PASS 여도 경고(특히 **신규 env 키**)는
  사용자에게 반드시 전달한다 — env 는 push 만으로 배포 환경에 반영되지 않는다.
- 통과 후 보고 형식은 `harness-workflow` 스킬 §5 를 따른다.
