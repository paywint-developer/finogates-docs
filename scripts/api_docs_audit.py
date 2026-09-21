#!/usr/bin/env python3
"""Keep the Mintlify API reference in step with the Platform API.

The docs site renders every ``api-reference/**/*.mdx`` page from the OpenAPI
spec named in ``docs.json``. This script compares a spec (sandbox, local,
production build, file or URL) against those pages and, on request, writes
the missing stubs and sidebar entries.

    python scripts/api_docs_audit.py audit    --source sandbox
    python scripts/api_docs_audit.py audit    --source local --brief
    python scripts/api_docs_audit.py audit    --source app@v1.42.0        # backend at a git ref
    python scripts/api_docs_audit.py scaffold --source sandbox --tags payments
    python scripts/api_docs_audit.py scaffold --source sandbox --plan plan.json
    python scripts/api_docs_audit.py snapshot --source sandbox
    python scripts/api_docs_audit.py check    --source sandbox            # CI: exit 1 on gaps

Sources
    sandbox | local            aliases (see SOURCES)
    http(s)://...              any URL serving the spec
    path/to/spec.json          a saved spec
    app[:<backend-dir>][@<ref>] build from the backend code; defaults to the
                               sibling ``python-finogates.com`` checkout, ``@ref``
                               uses a temporary git worktree at that ref

Endpoints that are live on purpose but not public go in
``scripts/api_docs_ignore.txt`` (one ``METHOD /path`` per line, ``*`` allowed).

Stdlib only.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DOCS_JSON = ROOT / "docs.json"
API_DIR = ROOT / "api-reference"
SNAPSHOT = ROOT / "open-api-collection-test.json"
IGNORE_FILE = ROOT / "scripts" / "api_docs_ignore.txt"
DEFAULT_BACKEND = ROOT.parent / "python-finogates.com"

SOURCES = {
    "sandbox": "https://api-sandbox.fg.wintpay.com/openapi-v1-platform.json",
    "local": "http://localhost:8008/openapi-v1-platform.json",
}
METHODS = ("get", "post", "put", "patch", "delete")
# Importing the backend validates Settings; these placeholders satisfy the
# required fields so the spec builds without a .env (env vars beat dotenv).
_DB = "postgresql+asyncpg://x:x@localhost:5432/x"
_SECRET = "docs-audit-placeholder-secret-0123456789abcdef"  # ≥32 chars
PLACEHOLDER_ENV = {
    "APP_ENV": "local",
    "REDIS_URL": "redis://localhost:6379/0",
    "CONTROL_DATABASE_URL": _DB,
    "SANDBOX_DATABASE_URL": _DB,
    "PRODUCTION_DATABASE_URL": _DB,
    "S3_BUCKET_NAME": "placeholder",
    "JWT_SECRET_KEY": _SECRET,
    "ADMIN_JWT_SECRET_KEY": _SECRET,
    "CLIENT_JWT_SECRET_KEY": _SECRET,
    "MODE_EXCHANGE_SECRET": _SECRET,
}
API_TAB = "API Reference"

Op = tuple[str, str]  # ("POST", "/v1/platform/users")


# ── spec loading ──────────────────────────────────────────────────────────


def load_spec(source: str) -> dict:
    source = SOURCES.get(source, source)
    if source.startswith(("http://", "https://")):
        req = Request(source, headers={"Accept": "application/json"})
        with urlopen(req, timeout=30) as resp:
            return json.load(resp)
    if source == "app" or source.startswith(("app:", "app@")):
        return _spec_from_app(source)
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _spec_from_app(source: str) -> dict:
    """Import the backend and call its platform-spec builder."""
    rest = source[3:]
    ref = None
    if "@" in rest:
        rest, ref = rest.split("@", 1)
    backend = Path(rest.lstrip(":")) if rest.lstrip(":") else DEFAULT_BACKEND
    if not backend.is_dir():
        sys.exit(f"backend checkout not found: {backend}")
    python = _venv_python(backend)
    code = (
        "import json,sys;from app.main import openapi_v1_platform;"
        "json.dump(openapi_v1_platform(), sys.stdout)"
    )
    workdir = backend
    tmp = None
    if ref:
        tmp = Path(tempfile.mkdtemp(prefix="api-docs-"))
        subprocess.run(
            ["git", "-C", str(backend), "worktree", "add", "--detach", str(tmp), ref],
            check=True,
            capture_output=True,
        )
        workdir = tmp
    try:
        out = subprocess.run(
            [str(python), "-c", code],
            cwd=workdir,
            env={**_os_env(), **PLACEHOLDER_ENV},
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            sys.exit(f"spec build failed:\n{out.stderr[-2000:]}")
        return json.loads(out.stdout)
    finally:
        if tmp is not None:
            subprocess.run(
                ["git", "-C", str(backend), "worktree", "remove", "--force", str(tmp)],
                capture_output=True,
            )
            shutil.rmtree(tmp, ignore_errors=True)


def _venv_python(backend: Path) -> Path:
    for cand in (
        backend / ".venv" / "Scripts" / "python.exe",
        backend / ".venv" / "bin" / "python",
    ):
        if cand.exists():
            return cand
    sys.exit(f"no .venv found under {backend}")


def _os_env() -> dict:
    import os

    return dict(os.environ)


# ── the three inventories ─────────────────────────────────────────────────


def spec_ops(spec: dict) -> dict[Op, dict]:
    ops: dict[Op, dict] = {}
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method in METHODS and isinstance(op, dict):
                ops[(method.upper(), path)] = op
    return ops


_FRONT = re.compile(r"^openapi:\s*['\"]?([A-Z]+)\s+(\S+?)['\"]?\s*$", re.M)


def page_ops() -> dict[Op, str]:
    """Every ``api-reference`` page → the operation it renders."""
    found: dict[Op, str] = {}
    for f in sorted(API_DIR.rglob("*.mdx")):
        m = _FRONT.search(f.read_text(encoding="utf-8"))
        if m:
            found[(m.group(1), m.group(2))] = f.relative_to(ROOT).with_suffix("").as_posix()
    return found


def all_pages() -> set[str]:
    return {
        f.relative_to(ROOT).with_suffix("").as_posix()
        for f in ROOT.rglob("*.mdx")
        if ".git" not in f.parts and "node_modules" not in f.parts
    }


def docs_json() -> dict:
    return json.loads(DOCS_JSON.read_text(encoding="utf-8"))


def _walk_pages(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [p for n in node for p in _walk_pages(n)]
    if isinstance(node, dict):
        return [p for k in ("pages", "groups", "tabs") for p in _walk_pages(node.get(k, []))]
    return []


def nav_pages(cfg: dict) -> list[str]:
    return _walk_pages(cfg.get("navigation", {}))


def ignored() -> list[str]:
    if not IGNORE_FILE.exists():
        return []
    return [
        ln.strip()
        for ln in IGNORE_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def is_ignored(op: Op, patterns: list[str]) -> bool:
    key = f"{op[0]} {op[1]}"
    return any(fnmatch.fnmatchcase(key, p) for p in patterns)


# ── audit ─────────────────────────────────────────────────────────────────


def audit(spec: dict, tags: set[str] | None = None) -> dict:
    ops = spec_ops(spec)
    pages = page_ops()
    cfg = docs_json()
    nav = nav_pages(cfg)
    nav_set = set(nav)
    files = all_pages()
    ign = ignored()

    missing = [
        (op, meta)
        for op, meta in ops.items()
        if op not in pages and not is_ignored(op, ign)
    ]
    if tags:
        wanted = {t.lower() for t in tags}
        missing = [(op, m) for op, m in missing if _tag(m).lower() in wanted]
    return {
        "source_ops": len(ops),
        "pages": len(pages),
        "missing": missing,
        "stale": sorted(op for op in pages if op not in ops),
        "unlisted": sorted(p for p in files if p not in nav_set and p.startswith("api-reference/")),
        "unlisted_other": sorted(p for p in files if p not in nav_set and not p.startswith("api-reference/")),
        "dangling": sorted(p for p in nav if p not in files),
        "duplicates": sorted(p for p, n in Counter(nav).items() if n > 1),
    }


def _tag(meta: dict) -> str:
    tags = meta.get("tags") or ["untagged"]
    return str(tags[0])


def print_audit(res: dict, brief: bool, as_json: bool) -> None:
    if as_json:
        out = {
            **{k: v for k, v in res.items() if k not in ("missing", "stale")},
            "missing": [
                {"op": f"{op[0]} {op[1]}", "tag": _tag(m), "summary": m.get("summary", "")}
                for op, m in res["missing"]
            ],
            "stale": [f"{m} {p}" for m, p in res["stale"]],
        }
        print(json.dumps(out, indent=2))
        return

    print(f"spec ops {res['source_ops']}  pages {res['pages']}  "
          f"missing {len(res['missing'])}  stale {len(res['stale'])}  "
          f"unlisted {len(res['unlisted'])}  dangling {len(res['dangling'])}")
    if res["missing"]:
        by_tag: dict[str, list] = defaultdict(list)
        for op, m in res["missing"]:
            by_tag[_tag(m)].append((op, m))
        print("\nmissing pages:")
        for tag in sorted(by_tag):
            print(f"  [{tag}] ({len(by_tag[tag])})")
            for (method, path), m in by_tag[tag]:
                summary = (m.get("summary") or "").strip()
                print(f"    {method:<6} {path}  - {summary[:60]}")
    if brief:
        return
    for key, label in (
        ("stale", "pages whose endpoint is gone from the spec"),
        ("unlisted", "api-reference pages not in docs.json"),
        ("unlisted_other", "other pages not in docs.json (may be linked inline)"),
        ("dangling", "docs.json entries without a file"),
        ("duplicates", "docs.json entries listed twice"),
    ):
        if res[key]:
            print(f"\n{label}:")
            for item in res[key]:
                print("   ", item if isinstance(item, str) else f"{item[0]} {item[1]}")


# ── scaffold ──────────────────────────────────────────────────────────────


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "page"


def group_for_tag(cfg: dict, pages: dict[Op, str], ops: dict[Op, dict]) -> dict[str, dict]:
    """Map a spec tag → the docs.json group that already documents that tag."""
    groups = _api_groups(cfg)
    folder_to_group = {}
    for g in groups:
        for p in g.get("pages", []):
            if isinstance(p, str):
                folder_to_group.setdefault(p.split("/")[1] if p.count("/") >= 2 else "", g)
    votes: dict[str, Counter] = defaultdict(Counter)
    for op, page in pages.items():
        meta = ops.get(op)
        if meta:
            votes[_tag(meta)][page.split("/")[1]] += 1
    mapping = {}
    for tag, c in votes.items():
        folder = c.most_common(1)[0][0]
        if folder in folder_to_group:
            mapping[tag] = folder_to_group[folder]
    return mapping


def _api_groups(cfg: dict) -> list[dict]:
    for tab in cfg.get("navigation", {}).get("tabs", []):
        if tab.get("tab") == API_TAB:
            return tab.setdefault("groups", [])
    sys.exit(f'docs.json has no "{API_TAB}" tab')


def _group_folder(group: dict) -> str:
    for p in group.get("pages", []):
        if isinstance(p, str) and p.count("/") >= 2:
            return p.split("/")[1]
    return slug(group["group"])


def scaffold(spec: dict, plan: list[dict], dry_run: bool) -> None:
    ops = spec_ops(spec)
    pages = page_ops()
    cfg = docs_json()
    groups = _api_groups(cfg)
    tag_group = group_for_tag(cfg, pages, ops)
    created: list[str] = []

    for entry in plan:
        method, path = entry["op"].split(" ", 1)
        op = (method.upper(), path)
        meta = ops.get(op)
        if meta is None:
            print(f"skip (not in spec): {entry['op']}")
            continue
        if op in pages:
            print(f"skip (already documented): {entry['op']} → {pages[op]}")
            continue
        tag = _tag(meta)
        group = tag_group.get(tag)
        if group is None:
            name = tag if any(c.isupper() for c in tag) else tag.title()
            group = {"group": entry.get("group") or name, "expanded": False, "pages": []}
            groups.append(group)
            tag_group[tag] = group
            print(f"new group: {group['group']}")
        folder = entry.get("folder") or _group_folder(group)
        title = entry.get("title") or (meta.get("summary") or path).strip()
        name = entry.get("file") or slug(title)
        rel = f"api-reference/{folder}/{name}"
        target = ROOT / f"{rel}.mdx"
        if target.exists():
            print(f"skip (file exists): {rel}.mdx")
            continue
        body = f"---\ntitle: '{title.replace(chr(39), chr(39) * 2)}'\nopenapi: '{method.upper()} {path}'\n---\n"
        if dry_run:
            print(f"would write {rel}.mdx  [{group['group']}]  title={title!r}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            if rel not in group["pages"]:
                before = entry.get("before")
                if before in group["pages"]:
                    group["pages"].insert(group["pages"].index(before), rel)
                else:
                    group["pages"].append(rel)
            created.append(rel)
            print(f"wrote {rel}.mdx  [{group['group']}]")

    if created and not dry_run:
        DOCS_JSON.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\ndocs.json updated ({len(created)} entries). Titles come from the spec "
              f"summary unless the plan overrode them - review before committing.")


def plan_from_tags(spec: dict, tags: set[str]) -> list[dict]:
    res = audit(spec, tags)
    return [{"op": f"{op[0]} {op[1]}"} for op, _ in res["missing"]]


# ── snapshot ──────────────────────────────────────────────────────────────


def snapshot(spec: dict) -> None:
    SNAPSHOT.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT.relative_to(ROOT)} ({len(spec_ops(spec))} ops)")


# ── cli ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_source(p):
        p.add_argument("--source", default="sandbox",
                       help="sandbox | local | URL | file | app[:dir][@ref] (default: sandbox)")

    p = sub.add_parser("audit", help="report gaps between the spec and the pages")
    add_source(p)
    p.add_argument("--tags", help="comma-separated spec tags to restrict the missing list to")
    p.add_argument("--brief", action="store_true", help="only the missing list")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("check", help="like audit, exit 1 when anything is off (CI)")
    add_source(p)

    p = sub.add_parser("scaffold", help="write stub pages + docs.json entries")
    add_source(p)
    p.add_argument("--tags", help="scaffold every missing op under these tags")
    p.add_argument("--ops", help="comma-separated 'METHOD /path' list")
    p.add_argument("--plan", help="JSON file: [{op, title?, file?, before?, group?, folder?}]")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("snapshot", help="refresh open-api-collection-test.json")
    add_source(p)

    a = ap.parse_args(argv)
    spec = load_spec(a.source)

    if a.cmd in ("audit", "check"):
        tags = {t.strip() for t in a.tags.split(",")} if getattr(a, "tags", None) else None
        res = audit(spec, tags)
        print_audit(res, getattr(a, "brief", False), getattr(a, "json", False))
        if a.cmd == "check":
            bad = any(res[k] for k in ("missing", "stale", "dangling", "duplicates"))
            return 1 if bad else 0
        return 0

    if a.cmd == "scaffold":
        plan: list[dict] = []
        if a.plan:
            plan += json.loads(Path(a.plan).read_text(encoding="utf-8"))
        if a.tags:
            plan += plan_from_tags(spec, {t.strip() for t in a.tags.split(",")})
        if a.ops:
            plan += [{"op": o.strip()} for o in a.ops.split(",") if o.strip()]
        if not plan:
            sys.exit("nothing to scaffold: pass --plan, --tags or --ops")
        scaffold(spec, plan, a.dry_run)
        return 0

    if a.cmd == "snapshot":
        snapshot(spec)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
