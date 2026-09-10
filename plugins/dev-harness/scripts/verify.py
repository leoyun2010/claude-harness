#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
커밋 전 자가 검증 스크립트 (dev-harness 플러그인 공용)

용도: 소스 수정 후 커밋을 요청하기 전에 로컬에서 실행하는 검증. 배포와 무관.

프로젝트 고유 설정은 <프로젝트 루트>/.claude/verify.toml 에서 읽는다 (없어도 동작).
  [verify]
  repo              = "."                    # git 저장소 경로 (루트 기준 상대)
  requirements_dirs = ["rag-service"]        # requirements.txt 가 있는 디렉터리 — import 대조 대상 (repo 기준)
  env_known_files   = ["CLAUDE.md", ".env"]  # 알려진 env 키가 적힌 파일 (루트 기준)
  env_hint          = "서버 .env 수동 추가 필요"
  transitive_ok     = ["numpy", "torch"]     # requirements 에 없지만 전이 의존성으로 설치되는 모듈
  [verify.pkg_import_map]                    # pip 패키지명 → import 모듈명 추가 규칙
  "my-pkg" = ["mypkg"]

검사 항목
  1. py_compile   변경된 .py 전체 문법 검사 (--all 시 저장소 전체)
  2. import 대조  requirements_dirs 의 *.py import 가 requirements.txt 에 있는지 (설정 시에만)
  3. .bak 감지    git 에 올라간 .bak(오류) + 마지막 커밋 이후 생성된 .bak(경고)
  4. env 키 검사  변경으로 추가된 줄의 os.getenv 키가 env_known_files 에 없으면 경고

종료 코드: 0 = 통과(경고는 통과) / 1 = 오류
사용법:   python verify.py [--root <프로젝트 루트>] [--all]
          (--root 생략 시 CLAUDE_PROJECT_DIR → 현재 폴더 순)
"""
import argparse
import ast
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

PKG_IMPORT_MAP: dict[str, set[str]] = {
    "sentence-transformers": {"sentence_transformers"},
    "python-multipart": {"multipart"},
    "python-docx": {"docx"},
    "python-pptx": {"pptx"},
    "pillow": {"PIL"},
    "pyyaml": {"yaml"},
    "pyjwt": {"jwt"},
    "beautifulsoup4": {"bs4"},
    "scikit-learn": {"sklearn"},
    "opencv-python": {"cv2"},
    "python-dotenv": {"dotenv"},
    "psycopg2-binary": {"psycopg2"},
}
SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", "venv"}


# ── 설정 ────────────────────────────────────────────────────────────

class Cfg:
    def __init__(self, root: Path):
        self.root = root
        raw: dict = {}
        self.source = None
        for cand in (root / ".claude" / "verify.toml", root / "verify.toml"):
            if cand.is_file():
                raw = tomllib.loads(cand.read_text(encoding="utf-8")).get("verify", {})
                self.source = cand
                break
        self.repo = (root / raw.get("repo", ".")).resolve()
        self.requirements_dirs = [self.repo / d for d in raw.get("requirements_dirs", [])]
        self.env_known_files = [root / f for f in raw.get("env_known_files", ["CLAUDE.md", ".env"])]
        self.env_hint = raw.get(
            "env_hint", "배포 환경 .env 에 수동 추가 필요 여부 확인 (push 만으로 반영 안 됨)")
        self.transitive_ok = set(raw.get("transitive_ok", []))
        self.pkg_import_map = dict(PKG_IMPORT_MAP)
        for k, v in raw.get("pkg_import_map", {}).items():
            self.pkg_import_map[k.lower()] = set(v)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout


def changed_files(repo: Path) -> list[Path]:
    """HEAD 대비 변경(스테이징 포함) + 신규(untracked, gitignore 제외) 파일."""
    lines = git(repo, "diff", "--name-only", "HEAD").splitlines()
    lines += git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    files = []
    for line in dict.fromkeys(l.strip() for l in lines):
        if line and (repo / line).is_file():
            files.append(repo / line)
    return files


# ── 검사 1: py_compile ──────────────────────────────────────────────

def check_compile(cfg: Cfg, files: list[Path], all_mode: bool):
    errors = []
    if all_mode:
        targets = [p for p in cfg.repo.rglob("*.py") if not SKIP_DIRS & set(p.parts)]
    else:
        targets = [p for p in files if p.suffix == ".py"]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyc")
    tmp.close()
    for p in targets:
        try:
            py_compile.compile(str(p), cfile=tmp.name, doraise=True)
        except py_compile.PyCompileError as e:
            msg = str(e).strip().splitlines()[-1]
            errors.append(f"py_compile 실패: {p.relative_to(cfg.repo)}\n      {msg}")
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return targets, errors


# ── 검사 2: import ↔ requirements.txt ──────────────────────────────

def _requirements_modules(cfg: Cfg, req: Path) -> set[str]:
    mods = set()
    for line in _read(req).splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[=<>!~\[;@ ]", line)[0].strip().lower()
        if name:
            mods |= cfg.pkg_import_map.get(name, {name.replace("-", "_")})
    return mods


def _local_modules(dirpath: Path) -> set[str]:
    mods = {p.stem for p in dirpath.glob("*.py")}
    mods |= {p.name for p in dirpath.iterdir()
             if p.is_dir() and (p / "__init__.py").exists()}
    return mods


def _handler_catches_importerror(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    elts = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for e in elts:
        name = e.id if isinstance(e, ast.Name) else getattr(e, "attr", "")
        if name in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException"):
            return True
    return False


def _collect_imports(tree: ast.AST):
    imports: dict[str, int] = {}
    guarded: set[str] = set()

    def _names(node):
        if isinstance(node, ast.Import):
            return [a.name.split(".")[0] for a in node.names]
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            return [node.module.split(".")[0]]
        return []

    for node in ast.walk(tree):
        for name in _names(node):
            imports.setdefault(name, node.lineno)
        if isinstance(node, ast.Try) and any(map(_handler_catches_importerror, node.handlers)):
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    guarded.update(_names(sub))
    return imports, guarded


def check_imports(cfg: Cfg):
    errors, warnings = [], []
    for d in cfg.requirements_dirs:
        req = d / "requirements.txt"
        if not req.is_file():
            warnings.append(f"requirements.txt 없음: {d} (verify.toml requirements_dirs 확인)")
            continue
        allowed = (set(sys.stdlib_module_names) | _requirements_modules(cfg, req)
                   | _local_modules(d) | cfg.transitive_ok)
        for p in sorted(d.glob("*.py")):
            try:
                tree = ast.parse(_read(p))
            except SyntaxError:
                continue  # 검사 1에서 이미 잡힘
            imports, guarded = _collect_imports(tree)
            for mod, lineno in sorted(imports.items(), key=lambda x: x[1]):
                if mod in allowed:
                    continue
                loc = f"{p.relative_to(cfg.repo)}:{lineno}"
                if mod in guarded:
                    warnings.append(f"조건부 import (try/except 가드): {loc} — '{mod}'")
                else:
                    errors.append(f"requirements.txt 에 없는 import: {loc} — '{mod}'"
                                  " (컨테이너 기동 실패 위험)")
    return errors, warnings


# ── 검사 3: .bak 파일 ───────────────────────────────────────────────

def check_bak(cfg: Cfg):
    errors, warnings = [], []
    tracked = (git(cfg.repo, "ls-files").splitlines()
               + git(cfg.repo, "diff", "--cached", "--name-only").splitlines())
    for f in tracked:
        if ".bak" in Path(f).name:
            errors.append(f"git 에 올라간 .bak: {f} (gitignore 정책 위반)")
    ts_raw = git(cfg.repo, "log", "-1", "--format=%ct").strip()
    last_commit_ts = int(ts_raw) if ts_raw.isdigit() else 0
    for p in cfg.repo.rglob("*.bak*"):
        if SKIP_DIRS & set(p.parts) or not p.is_file():
            continue
        if p.stat().st_mtime > last_commit_ts:
            warnings.append(f"마지막 커밋 이후 생성/수정된 백업 파일: {p.relative_to(cfg.repo)}"
                            " (규약: .bak 금지 — 롤백은 git 히스토리로)")
    return errors, warnings


# ── 검사 4: 신규 env 키 ─────────────────────────────────────────────

ENV_RE = re.compile(
    r"os\.(?:getenv|environ\.get)\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]"
    r"|os\.environ\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]\]")


def check_env_keys(cfg: Cfg, files: list[Path]):
    """이번 변경으로 **추가된 줄**의 env 키만 검사한다 (전체 스캔은 오탐 폭주)."""
    warnings = []
    known = "".join(_read(f) for f in cfg.env_known_files if f.exists())
    tracked = set(git(cfg.repo, "ls-files").splitlines())
    seen = set()
    for p in files:
        if p.suffix != ".py":
            continue
        rel = p.relative_to(cfg.repo).as_posix()
        if rel in tracked:
            diff = git(cfg.repo, "diff", "-U0", "HEAD", "--", rel)
            text = "\n".join(l[1:] for l in diff.splitlines()
                             if l.startswith("+") and not l.startswith("+++"))
        else:
            text = _read(p)
        for m in ENV_RE.finditer(text):
            key = m.group(1) or m.group(2)
            if key and key not in known and (rel, key) not in seen:
                seen.add((rel, key))
                warnings.append(f"신규 env 키: {rel} — {key} → {cfg.env_hint}")
    return warnings


# ── main ────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="커밋 전 자가 검증 (dev-harness)")
    ap.add_argument("--root", type=Path, default=None,
                    help="프로젝트 루트 (기본: CLAUDE_PROJECT_DIR → cwd)")
    ap.add_argument("--all", action="store_true", help="저장소 전체 .py 를 컴파일 검사")
    args = ap.parse_args()

    root = (args.root or Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())).resolve()
    cfg = Cfg(root)
    if not (cfg.repo / ".git").exists():
        print(f"[error] git 저장소가 아님: {cfg.repo}  (verify.toml 의 repo 설정 확인)")
        return 1

    files = changed_files(cfg.repo)
    errors, warnings = [], []
    compiled, e1 = check_compile(cfg, files, args.all)
    errors += e1
    e2, w2 = check_imports(cfg)
    errors += e2
    warnings += w2
    e3, w3 = check_bak(cfg)
    errors += e3
    warnings += w3
    warnings += check_env_keys(cfg, files)

    print("=" * 64)
    print(f" 커밋 전 자가 검증  —  루트: {root}")
    print(f" 설정: {cfg.source or '(없음 → 기본 검사만)'}   저장소: {cfg.repo}")
    print("=" * 64)
    print(f" 변경 파일 {len(files)}개 / py_compile {len(compiled)}개 / "
          f"import 대조 {len(cfg.requirements_dirs)}디렉터리")
    if errors:
        print(f"\n ✘ 오류 {len(errors)}건")
        for e in errors:
            print(f"   - {e}")
    if warnings:
        print(f"\n ⚠ 경고 {len(warnings)}건")
        for w in warnings:
            print(f"   - {w}")
    print()
    print(" 결과:", "FAIL — 수정 후 다시 실행" if errors else "PASS — 커밋 요청 가능")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
