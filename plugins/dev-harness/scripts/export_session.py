# -*- coding: utf-8 -*-
"""
Claude Code 세션 기록(JSONL) → Markdown 변환기  v1.1 (dev-harness 플러그인 공용)

Claude Code 는 세션마다 전체 대화를 아래 경로에 JSONL 로 남긴다.

    C:\\Users\\<user>\\.claude\\projects\\<encoded-cwd>\\<session-id>.jsonl

이 스크립트는 그 원본을 읽어 "프롬프트 + 최종 답변 + 도구 호출 1줄 요약" 만 뽑아
읽기 좋은 Markdown 으로 만들고, 민감정보를 마스킹한 뒤 git 저장소에 커밋한다.
원본 JSONL 은 절대 수정하지 않으므로 규칙을 바꿔 언제든 재생성할 수 있다.

v1.1 — 프로젝트 고정 경로 제거. 어느 프로젝트에서 실행되든 자동으로 자리를 찾는다.
  · 기록 저장소(git): 환경변수 CLAUDE_HARNESS_LOG_DIR, 없으면 ~/claude-logs
                       (git 저장소가 아니면 처음 한 번 git init)
  · md 출력 폴더:      <기록 저장소>/sessions/<프로젝트 폴더명>/
  · JSONL 소스 폴더:   현재 폴더(또는 훅 stdin 의 cwd)를 Claude Code 규칙으로 인코딩해 유도
                       (영숫자 이외 문자를 전부 '-' 로 치환)

사용 예:
    python export_session.py --latest                 # 현재 프로젝트의 최근 세션 1개 변환
    python export_session.py --all --commit           # 현재 프로젝트 전체 백필 + 커밋
    python export_session.py --session 4d244593-...   # 특정 세션
    python export_session.py --hook --commit          # SessionEnd 훅용(stdin JSON)
    python export_session.py --cwd "C:\\proj" --all   # 다른 프로젝트를 지정해 변환

의존성 없음(표준 라이브러리만). Python 3.9+.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 경로 기본값

LOG_REPO_DIR = Path(
    os.environ.get("CLAUDE_HARNESS_LOG_DIR") or (Path.home() / "claude-logs")
).expanduser().resolve()

KST = dt.timezone(dt.timedelta(hours=9))


def encode_project_dir(cwd: str) -> str:
    """Claude Code 가 cwd → ~/.claude/projects/<이름> 으로 쓰는 인코딩.

    관찰 규칙: 영숫자 이외의 모든 문자(':' '\\' '.' 공백 한글 괄호)를 '-' 하나로 치환.
      c:\\Users\\user\\WORK_Proj\\10.업무도구및아이작(ISAC)
        → c--Users-user-WORK-Proj-10----------ISAC-
    """
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def resolve_source_dir(cwd: str) -> Path | None:
    """cwd 에 대응하는 Claude Code JSONL 폴더. 드라이브 문자 대소문자 양쪽을 시도한다."""
    base = Path.home() / ".claude" / "projects"
    cands = [cwd]
    if len(cwd) > 1 and cwd[1] == ":":
        cands += [cwd[0].lower() + cwd[1:], cwd[0].upper() + cwd[1:]]
    for c in cands:
        p = base / encode_project_dir(c)
        if p.is_dir():
            return p
    return None


def sessions_dir_for(cwd: str) -> Path:
    """프로젝트 폴더명(경로 마지막 요소)으로 md 출력 하위 폴더를 만든다."""
    name = Path(cwd).name or "root"
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", name).strip() or "root"
    return LOG_REPO_DIR / "sessions" / name

# ---------------------------------------------------------------- 마스킹 규칙
# md 를 파일로 쓰기 직전, 전체 문자열에 한 번 적용한다.

_REDACTIONS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.S), "[REDACTED-PRIVATE-KEY-BLOCK]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"), "[REDACTED-ANTHROPIC-KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED-GH-TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED-AWS-KEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"), "Bearer [REDACTED]"),
    # KEY=VALUE / KEY: VALUE — 키 이름은 남기고 값만 가린다.
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|api[_-]?token|access[_-]?token|"
                r"secret|password|passwd|pwd)[A-Za-z0-9_]*)(\s*[:=]\s*)"
                r"[\"']?([^\s\"',<>|]{8,})"),
     r"\1\2[REDACTED]"),
]


def redact(text: str) -> tuple[str, int]:
    """민감정보를 마스킹하고 (결과, 치환건수) 를 돌려준다."""
    total = 0
    for pattern, repl in _REDACTIONS:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


# ---------------------------------------------------------------- 파싱 도우미

_SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _to_kst(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None


def _slug(text: str, limit: int = 40) -> str:
    s = _BAD_FILENAME.sub("", text or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = s.strip("-.")
    return (s[:limit].rstrip("-.") or "untitled")


def _blocks(msg) -> list[dict]:
    """message.content 를 항상 블록 리스트로 정규화."""
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _tool_summary(block: dict) -> str:
    """tool_use 블록 → `> 🔧 **Read**(`file_path=…`)` 한 줄."""
    name = block.get("name") or "tool"
    args = block.get("input") or {}
    key = ""
    if isinstance(args, dict) and args:
        # 대표 파라미터 1개만 고른다.
        for cand in ("file_path", "path", "command", "pattern", "query",
                     "prompt", "url", "skill", "description"):
            if cand in args:
                key = f"{cand}={args[cand]}"
                break
        else:
            first = next(iter(args.items()))
            key = f"{first[0]}={first[1]}"
    key = re.sub(r"\s+", " ", str(key)).strip()
    if len(key) > 120:
        key = key[:117] + "…"
    key = key.replace("`", "'")
    return f"> 🔧 **{name}**(`{key}`)" if key else f"> 🔧 **{name}**"


def load_rows(jsonl: Path) -> list[dict]:
    rows = []
    with jsonl.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[warn] {jsonl.name}:{lineno} JSON 파싱 실패 — 건너뜀 ({exc})",
                      file=sys.stderr)
    return rows


# ---------------------------------------------------------------- 변환 본체

def build_markdown(rows: list[dict], include_tools: bool = True) -> tuple[str, str, dt.datetime | None]:
    """rows → (markdown, 제목, 첫 시각). 대상 턴이 없으면 ('', '', None)."""
    title = ""
    for r in rows:
        if r.get("type") == "ai-title" and r.get("aiTitle"):
            title = r["aiTitle"]                      # 마지막 값이 최신 제목

    turns: list[tuple[str, dt.datetime | None, list[str]]] = []
    session_id = cwd = branch = version = ""
    first_ts = last_ts = None

    for r in rows:
        rtype = r.get("type")
        if rtype not in ("user", "assistant"):
            continue
        if r.get("isSidechain"):                      # 서브에이전트 내부 대화 제외
            continue
        # isMeta: 하네스가 주입한 항목(스킬 본문·슬래시 명령 출력 등). 사람이 친 프롬프트가 아니다.
        if rtype == "user" and r.get("isMeta"):
            continue

        session_id = session_id or r.get("sessionId", "")
        cwd = cwd or r.get("cwd", "")
        branch = branch or r.get("gitBranch", "")
        version = version or r.get("version", "")

        when = _to_kst(r.get("timestamp"))
        if when:
            first_ts = first_ts or when
            last_ts = when

        parts: list[str] = []
        if rtype == "user":
            for b in _blocks(r.get("message")):
                if b.get("type") != "text":           # tool_result 등은 버림
                    continue
                text = _SYS_REMINDER.sub("", b.get("text") or "").strip()
                if text:
                    parts.append(text)
            if not parts:
                continue
            turns.append(("user", when, parts))
        else:
            for b in _blocks(r.get("message")):
                btype = b.get("type")
                if btype == "text":
                    text = (b.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif btype == "tool_use" and include_tools:
                    parts.append(_tool_summary(b))
                # thinking 은 기록하지 않는다.
            if not parts:
                continue
            # 한 응답이 여러 줄로 쪼개져 저장되므로 연속 assistant 는 한 섹션으로 병합.
            if turns and turns[-1][0] == "assistant":
                turns[-1][2].extend(parts)
            else:
                turns.append(("assistant", when, parts))

    if not turns:
        return "", "", None

    if not title:
        head = turns[0][2][0] if turns[0][0] == "user" else ""
        title = re.sub(r"\s+", " ", head).strip()[:40] or session_id[:8]

    n_prompts = sum(1 for t in turns if t[0] == "user")
    out: list[str] = [
        f"# {title}",
        "",
        f"- **세션 ID**: `{session_id}`",
        f"- **일시(KST)**: {first_ts:%Y-%m-%d %H:%M} ~ {last_ts:%H:%M}"
        if first_ts and last_ts else "- **일시(KST)**: -",
        f"- **작업 폴더**: `{cwd}`",
        f"- **git 브랜치**: `{branch or '-'}`  |  **Claude Code**: `{version or '-'}`",
        f"- **프롬프트 수**: {n_prompts}",
        "",
        "---",
        "",
    ]
    for role, when, parts in turns:
        stamp = f" — {when:%H:%M}" if when else ""
        head = "## 👤 사용자" if role == "user" else "## 🤖 Claude"
        out.append(f"{head}{stamp}")
        out.append("")
        out.append("\n\n".join(parts).strip())
        out.append("")

    return "\n".join(out).rstrip() + "\n", title, first_ts


def export(jsonl: Path, out_dir: Path, *, do_redact: bool = True,
           include_tools: bool = True, dry_run: bool = False) -> Path | None:
    rows = load_rows(jsonl)
    md, title, first_ts = build_markdown(rows, include_tools=include_tools)
    if not md:
        print(f"[skip] {jsonl.name} — 기록할 대화 턴이 없음")
        return None

    session_id = jsonl.stem
    date = (first_ts or dt.datetime.now(KST)).strftime("%Y-%m-%d")
    # 같은 세션은 항상 같은 파일 → 재실행해도 중복 생성 없이 갱신된다.
    target = out_dir / f"{date}_{session_id[:8]}_{_slug(title)}.md"

    if do_redact:
        md, hits = redact(md)
        if hits:
            print(f"[redact] {target.name} — {hits}건 치환")

    n_turns = md.count("\n## ")
    if dry_run:
        print(f"[dry-run] {target}  (섹션 {n_turns}개, {len(md):,}자)")
        return target

    out_dir.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    print(f"[ok] {target}  (섹션 {n_turns}개, {len(md):,}자)")
    return target


# ---------------------------------------------------------------- git 커밋

def git_commit(repo: Path) -> None:
    """sessions/ 만 스테이징해 커밋. push 는 하지 않는다."""
    def run(*args: str) -> subprocess.CompletedProcess:
        # quotepath=false 가 없으면 한글 파일명이 "\354\227\220" 처럼 8진수로 escape 되어
        # 아래 제목 추출이 깨진다.
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, encoding="utf-8")

    if not (repo / ".git").exists():
        # 기록 저장소는 처음 한 번 자동 초기화한다 (사용자 소스 저장소가 아닌 전용 폴더).
        repo.mkdir(parents=True, exist_ok=True)
        init = run("init", "-q")
        if init.returncode != 0:
            print(f"[warn] {repo} git init 실패 — 커밋 생략: {init.stderr.strip()}", file=sys.stderr)
            return
        (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        print(f"[git] 기록 저장소 초기화: {repo}")

    run("add", "--", "sessions")
    if run("diff", "--cached", "--quiet").returncode == 0:
        print("[git] 변경 없음 — 커밋 생략")
        return

    # 커밋 메시지는 "변환한 것" 이 아니라 "실제로 바뀐 것" 에서 뽑는다.
    # (--all 로 전수 변환해도 내용이 그대로면 스테이징되지 않으므로 제목이 어긋나지 않는다)
    staged = [line for line in
              run("diff", "--cached", "--name-only").stdout.splitlines() if line.strip()]
    titles = [Path(p).stem.split("_", 2)[-1] for p in staged]
    if len(titles) == 1:
        subject = titles[0]
    elif titles:
        subject = f"{titles[0]} 외 {len(titles) - 1}건"
    else:
        subject = "session"

    stamp = dt.datetime.now(KST).strftime("%Y-%m-%d")
    msg = f"chat: {stamp} {subject}"[:120]
    res = run("commit", "-m", msg)
    if res.returncode == 0:
        print(f"[git] 커밋 완료 — {msg}")
    else:
        print(f"[warn] 커밋 실패: {res.stderr.strip()}", file=sys.stderr)


# ---------------------------------------------------------------- 엔트리포인트

def main() -> int:
    # 콘솔이 cp949 여도 한글이 깨지지 않게.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Claude Code 세션 JSONL → Markdown")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--latest", action="store_true", help="가장 최근 세션 1개 (기본값)")
    src.add_argument("--all", action="store_true", help="모든 세션 (백필용)")
    src.add_argument("--session", metavar="ID", help="특정 세션 ID")
    src.add_argument("--hook", action="store_true",
                     help="stdin 의 훅 JSON에서 transcript_path 를 읽음")
    ap.add_argument("--commit", action="store_true", help="변환 후 git 커밋")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 대상만 출력")
    ap.add_argument("--no-redact", action="store_true", help="민감정보 마스킹 끄기")
    ap.add_argument("--no-tools", action="store_true", help="도구 호출 요약 제외")
    ap.add_argument("--cwd", default=None,
                    help="대상 프로젝트 폴더 (기본: CLAUDE_PROJECT_DIR → 훅 cwd → 현재 폴더)")
    ap.add_argument("--project-dir", type=Path, default=None,
                    help="JSONL 소스 디렉터리 (기본: --cwd 에서 자동 유도)")
    ap.add_argument("--out", type=Path, default=None,
                    help="md 출력 디렉터리 (기본: <기록 저장소>/sessions/<프로젝트 폴더명>)")
    args = ap.parse_args()

    payload: dict = {}
    if args.hook:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            payload = {}

    cwd = (args.cwd or os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    project_dir = args.project_dir or resolve_source_dir(cwd)
    out_dir = args.out or sessions_dir_for(cwd)

    targets: list[Path] = []
    if args.hook:
        path = payload.get("transcript_path")
        if path and Path(path).exists():
            targets = [Path(path)]
        elif payload.get("session_id") and project_dir:
            cand = project_dir / f"{payload['session_id']}.jsonl"
            targets = [cand] if cand.exists() else []
        if not targets:
            print("[warn] 훅 입력에서 transcript 를 찾지 못함 — 종료", file=sys.stderr)
            return 0                                  # 세션 종료를 막지 않는다
    else:
        if not project_dir or not project_dir.is_dir():
            print(f"[error] 소스 디렉터리 없음 (cwd={cwd} → {project_dir})", file=sys.stderr)
            return 0 if args.commit else 1
        files = sorted(project_dir.glob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if args.session:
            targets = [p for p in files if p.stem == args.session]
            if not targets:
                print(f"[error] 세션 없음: {args.session}", file=sys.stderr)
                return 1
        elif args.all:
            targets = files
        else:
            targets = files[:1]

    for jsonl in targets:
        try:
            export(jsonl, out_dir,
                   do_redact=not args.no_redact,
                   include_tools=not args.no_tools,
                   dry_run=args.dry_run)
        except Exception as exc:                      # 훅에서 세션 종료를 막지 않도록
            print(f"[error] {jsonl.name} 변환 실패: {exc}", file=sys.stderr)

    if args.commit and not args.dry_run:
        git_commit(LOG_REPO_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
