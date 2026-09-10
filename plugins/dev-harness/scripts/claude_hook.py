#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code 훅 스크립트 — 개발 하네스 공통 규약 기계적 강제 (dev-harness 플러그인)

hooks/hooks.json 에서 호출된다. 프로젝트 고유 경로를 알지 못하며,
현재 프로젝트 폴더는 CLAUDE_PROJECT_DIR 환경변수(없으면 훅 stdin 의 cwd)로 받는다.

모드 (argv[1]):
  bak        PreToolUse(Write|Edit|Bash|PowerShell)
             — .bak 백업 파일 생성/수정 차단. Write/Edit 은 file_path 로,
               Bash/PowerShell 은 명령 문자열의 복사·이동·리다이렉트 패턴으로 판정.
  pycompile  PostToolUse(Write|Edit)
             — 프로젝트 폴더 안의 .py 편집 직후 문법 검사, 실패 시 편집 주체(Claude)에게 즉시 피드백

stdin: Claude Code 훅 JSON ({tool_name, tool_input, cwd, ...})
stdout: 훅 제어 JSON (통과 시 출력 없음)
"""
import json
import os
import py_compile
import re
import sys
import tempfile

# 명령 문자열에서 ".bak 를 만드는" 동작으로 볼 패턴. 단순히 .bak 를 언급(grep 등)하는 것은 통과.
_BAK_WRITE_RE = re.compile(
    r"(?:\b(?:cp|mv|copy|move|ren|rename|tee|install)\b|Copy-Item|Move-Item|Rename-Item|"
    r"Set-Content|Out-File|sed\s+-i|>{1,2})[^\n;&|]*\.bak\b",
    re.IGNORECASE,
)


def _project_dir(data: dict) -> str:
    return (os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)).replace("\\", "/")


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> None:
    # Claude Code 는 utf-8 로 주고받는다. Windows 파이프 기본값(cp949)이면
    # 한글 경로가 깨져 파일 검사가 조용히 건너뛰어지므로 반드시 고정.
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tool = data.get("tool_name") or ""
    tin = data.get("tool_input") or {}
    fp = tin.get("file_path") or ""
    cmd = tin.get("command") or ""

    if mode == "bak":
        reason = (
            "프로젝트 규약(CLAUDE.md §0): .bak 백업 파일은 만들지 않는다 — "
            "롤백은 git 히스토리로 하고, 변경 전후 diff 를 요약해 보고할 것."
        )
        if fp and ".bak" in os.path.basename(fp).lower():
            _deny(reason)
        elif tool in ("Bash", "PowerShell") and cmd and _BAK_WRITE_RE.search(cmd):
            _deny(reason + " (명령에서 .bak 생성 패턴 감지)")
        return

    if mode == "pycompile":
        if not fp or not fp.lower().endswith(".py") or not os.path.isfile(fp):
            return
        root = _norm(_project_dir(data)).rstrip("/") + "/"
        if not _norm(fp).startswith(root):
            return  # 프로젝트 밖 파일은 검사하지 않는다
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyc")
        tmp.close()
        try:
            py_compile.compile(fp, cfile=tmp.name, doraise=True)
        except py_compile.PyCompileError as e:
            msg = str(e).strip().splitlines()[-1]
            print(json.dumps({
                "decision": "block",
                "reason": (
                    f"[훅] py_compile 실패 — {os.path.basename(fp)}: {msg} "
                    "(방금 편집으로 문법 오류 발생. 즉시 수정할 것.)"
                ),
            }, ensure_ascii=False))
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


main()
