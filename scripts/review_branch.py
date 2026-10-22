"""What a branch changes that a second reader must look at before it is merged.

    .venv/Scripts/python.exe scripts/review_branch.py <branch> [--base main]

Run it from a checkout of main, so the rules applied are main's and not the branch's
own copy of this file. Each finding is STOP (the branch must not be merged as it
stands) or LOOK (fine if it was part of the approved step). Exit status: 0 nothing
found, 1 LOOK only, 2 at least one STOP.

The rules cover the ways a change can pass every check while making the checks weaker
or the data less safe: an applied migration edited, tests removed or skipped, the gate
itself changed, a query budget raised, a decision record rewritten, a secret added, a
data migration with no way back, and code where money, sign-in or security live. It
also asks GitHub whether main is still protected, since the same credentials that push
a branch can switch that protection off.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Changing any of these changes what "green" means.
GATE_FILES = re.compile(
    r"^(\.github/|scripts/check\.py$|scripts/review_branch\.py$|(.*/)?conftest\.py$)"
)
# Money, sign-in, credentials, operator HTML and settings: a careful review every time.
SENSITIVE = re.compile(
    r"^(apps/accounts/|apps/orders/|apps/payments/|apps/catalog/pricing\.py$"
    r"|apps/core/(richtext|email|ratelimit)\.py$|config/settings/)"
)
MIGRATION = re.compile(r"^apps/[^/]+/migrations/(?!__init__\.py$)[^/]+\.py$")
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def (test_\w+)", re.MULTILINE)
SKIP = re.compile(r"pytest\.mark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail)\(|unittest\.skip")
SUPPRESSION = re.compile(r"#\s*(noqa|type:\s*ignore|nosec)\b|pragma:\s*no cover")
BUDGET = re.compile(r"^\s*([A-Z][A-Z0-9_]*BUDGET[A-Z0-9_]*)\s*=\s*(\d+)\s*$")
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}"
    r"|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
)
SECRET_FILE = re.compile(r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|id_rsa.*)$")
DEPENDENCY_FILES = {"pyproject.toml", "uv.lock", "package.json", "package-lock.json"}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
TOO_BIG = 600
STRING = re.compile(r"""(['"]).*?(?<!\\)\1""")
REQUIRED_CHECKS = {
    "Lint, format, types",
    "Tests (real PostgreSQL, real Chromium)",
    "Dependency and security audit",
}


@dataclass
class Report:
    stop: list[str] = field(default_factory=list)
    look: list[str] = field(default_factory=list)

    @property
    def status(self) -> int:
        return 2 if self.stop else 1 if self.look else 0


@dataclass
class FileDiff:
    status: str = "M"
    old: str = ""
    path: str = ""
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    hunks: list[tuple[int, int, int, int]] = field(default_factory=list)


def git(*args: str, repo: Path = REPO) -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "-c", "core.quotepath=off", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return out.stdout.decode("utf-8", errors="replace")


def show(rev: str, path: str, repo: Path) -> str:
    try:
        return git("show", f"{rev}:{path}", repo=repo)
    except subprocess.CalledProcessError:
        return ""


def parse_diff(text: str) -> list[FileDiff]:
    """One `git diff -M -U0`, split by file: status, paths, added and removed lines, hunks."""
    files: list[FileDiff] = []
    current = FileDiff()
    header = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            a, _, b = line[len("diff --git a/") :].rpartition(" b/")
            current, header = FileDiff(old=a, path=b), True
            files.append(current)
        elif header and line.startswith("new file mode"):
            current.status = "A"
        elif header and line.startswith("deleted file mode"):
            current.status = "D"
        elif header and line.startswith("rename from "):
            current.status, current.old = "R", line[len("rename from ") :]
        elif header and line.startswith("rename to "):
            current.path = line[len("rename to ") :]
        elif header and line.startswith("--- a/"):
            current.old = line[len("--- a/") :]
        elif header and line.startswith("+++ b/"):
            current.path = line[len("+++ b/") :]
        elif line.startswith("@@"):
            header = False
            match = HUNK.match(line)
            if match:
                a, b, c, d = match.groups()
                current.hunks.append((int(a), int(b or 1), int(c), int(d or 1)))
        elif not header and line.startswith("+"):
            current.added.append(line[1:])
        elif not header and line.startswith("-"):
            current.removed.append(line[1:])
    for diff in files:
        if diff.status == "D":
            diff.path = diff.old
    return files


def test_names(revs: tuple[str, str], repo: Path) -> tuple[Counter[str], Counter[str]]:
    """Every test function at each revision, by name, so a test that moves files is not lost."""
    found: dict[str, Counter[str]] = {rev: Counter() for rev in revs}
    try:
        out = git(
            "grep",
            "-I",
            "-E",
            r"^\s*(async\s+)?def test_\w+",
            *revs,
            "--",
            ":(glob)**/test_*.py",
            repo=repo,
        )
    except subprocess.CalledProcessError:
        out = ""
    for line in out.splitlines():
        rev, _, rest = line.partition(":")
        name = TEST_DEF.search(rest.partition(":")[2])
        if name and rev in found:
            found[rev][name[1]] += 1
    return found[revs[0]], found[revs[1]]


def irreversible(source: str) -> list[str]:
    """RunPython and RunSQL calls given no way back."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ["the file does not parse"]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            back = "reverse_code" if name == "RunPython" else "reverse_sql"
            if name in ("RunPython", "RunSQL") and len(node.args) < 2:
                if not any(k.arg == back for k in node.keywords):
                    found.append(f"{name} on line {node.lineno}")
    return found


def sections_by_line(text: str) -> list[str]:
    """The [section] each line of a TOML file sits in."""
    section, out = "", []
    for line in text.splitlines():
        header = re.match(r"^\s*\[\[?([^\]]+)\]", line)
        if header:
            section = header[1].strip()
        out.append(section)
    return out


def touched_sections(diff: FileDiff, before: str, after: str) -> set[str]:
    old, new = sections_by_line(before), sections_by_line(after)
    touched = set()
    for a, b, c, d in diff.hunks:
        for first, count, sections in ((a, b, old), (c, d, new)):
            for number in range(max(first, 1), first + count):
                if number <= len(sections):
                    touched.add(sections[number - 1])
    return touched


def review(head: str, base: str = "main", repo: Path = REPO) -> Report:
    report = Report()
    merge_base = git("merge-base", base, head, repo=repo).strip()
    head_rev = git("rev-parse", head, repo=repo).strip()
    diffs = parse_diff(
        git("diff", "-U0", "-M", "--no-color", "--no-ext-diff", merge_base, head_rev, repo=repo)
    )

    before, after = test_names((merge_base, head_rev), repo)
    missing = before - after
    names = ", ".join(sorted(missing))
    if sum(after.values()) < sum(before.values()):
        total = f"{sum(before.values())} to {sum(after.values())}"
        report.stop.append(f"Tests were removed: the count fell from {total}. Missing: {names}.")
    elif missing:
        report.look.append(f"Tests disappeared by name while the count held (renamed?): {names}.")

    for diff in diffs:
        status, old, path = diff.status, diff.old or diff.path, diff.path
        if MIGRATION.match(old) and status in "MDR":
            report.stop.append(
                f"An applied migration was changed ({old}). Applied migrations are never edited; a new migration makes the change."
            )
        if status == "A" and MIGRATION.match(path):
            for call in irreversible(show(head, path, repo)):
                report.stop.append(f"{path} cannot be reversed: {call} has no reverse step.")
            report.look.append(
                f"New migration {path}: was a schema change part of the approved step?"
            )
        if GATE_FILES.match(old) or GATE_FILES.match(path):
            report.stop.append(f"The gate itself was changed: {path}.")
        if SENSITIVE.match(old) or SENSITIVE.match(path):
            report.stop.append(
                f"Money, sign-in or security code changed: {path}. Wait for a careful review."
            )
        if old.startswith("docs/decisions/") and old != "docs/decisions/README.md":
            if status in "DR" or any(line.strip() for line in diff.removed):
                report.stop.append(
                    f"A decision record was rewritten ({old}). ADRs get a dated amendment at the end, never an edit."
                )
        if path == "pyproject.toml" and status == "M":
            tools = sorted(
                s
                for s in touched_sections(
                    diff, show(merge_base, path, repo), show(head, path, repo)
                )
                if s.startswith("tool.")
            )
            if tools:
                report.stop.append(f"Tool settings changed in pyproject.toml: {', '.join(tools)}.")
        if path in DEPENDENCY_FILES:
            report.look.append(f"Dependencies changed ({path}): was a new package agreed?")
        if status == "D":
            continue
        if SECRET_FILE.search(path) and not path.endswith(".env.example"):
            report.stop.append(f"A file that holds secrets was added: {path}.")
        if any(SECRET.search(line) for line in diff.added):
            report.stop.append(f"Something that looks like a secret was added in {path}.")
        if path.endswith(".py"):
            for line in diff.added:
                # A marker quoted in a string is data, not a marker.
                code = STRING.sub("", line)
                if SKIP.search(code):
                    report.stop.append(
                        f"A test is skipped or expected to fail in {path}: {line.strip()}"
                    )
                if SUPPRESSION.search(code):
                    report.look.append(f"A check is silenced in {path}: {line.strip()}")
            budgets = {m[1]: int(m[2]) for m in map(BUDGET.match, diff.removed) if m}
            for m in filter(None, map(BUDGET.match, diff.added)):
                if m[1] in budgets and int(m[2]) > budgets[m[1]]:
                    report.stop.append(
                        f"{m[1]} was raised from {budgets[m[1]]} to {m[2]} in {path}."
                    )

    visual = sorted(
        d.path for d in diffs if d.status != "D" and d.path.startswith(("templates/", "static/"))
    )
    if visual:
        report.look.append(
            f"Templates or styles changed ({len(visual)} files): ask for screenshots at 390 and 1440, each with its address."
        )
    size = sum(len(d.added) + len(d.removed) for d in diffs)
    if size > TOO_BIG:
        report.look.append(f"The change is {size} lines: more than one step usually is.")
    report.stop = list(dict.fromkeys(report.stop))
    report.look = list(dict.fromkeys(report.look))
    return report


def gh_api(path: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["gh", "api", path], capture_output=True)  # noqa: S603, S607


def protection(report: Report, branch: str = "main", repo: Path = REPO) -> None:
    """Whether GitHub still guards `branch` the way the gate depends on."""
    url = git("remote", "get-url", "origin", repo=repo).strip()
    slug = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not slug:
        return
    try:
        out = gh_api(f"repos/{slug[1]}/branches/{branch}/protection")
    except OSError:
        report.look.append("GitHub's protection of main could not be checked: gh is not installed.")
        return
    if out.returncode != 0:
        if b"not protected" in out.stderr.lower() or b"not protected" in out.stdout.lower():
            report.stop.append(
                f"{branch} is no longer protected on GitHub. Ask before merging anything."
            )
        else:
            report.look.append(f"GitHub's protection of {branch} could not be read (offline?).")
        return
    rules = json.loads(out.stdout)
    missing = []
    if not rules.get("required_pull_request_reviews"):
        missing.append("a pull request")
    checks = set((rules.get("required_status_checks") or {}).get("contexts") or [])
    if not REQUIRED_CHECKS <= checks:
        missing.append("the checks " + ", ".join(sorted(REQUIRED_CHECKS - checks)))
    if not (rules.get("enforce_admins") or {}).get("enabled"):
        missing.append("the same rules for administrators")
    if (rules.get("allow_force_pushes") or {}).get("enabled"):
        missing.append("a refusal of force-pushes")
    if missing:
        report.stop.append(
            f"GitHub no longer requires {'; '.join(missing)} on {branch}. Ask before merging anything."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="What a branch changes that must be looked at.")
    parser.add_argument("branch")
    parser.add_argument("--base", default="main")
    args = parser.parse_args()
    here = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if here != args.base:
        print(
            f"Warning: these rules are {here}'s copy, not {args.base}'s. Run this from a checkout of {args.base}.\n"
        )
    report = review(args.branch, args.base)
    protection(report, args.base.removeprefix("origin/"))
    stat = git(
        "diff", "--shortstat", git("merge-base", args.base, args.branch).strip(), args.branch
    ).strip()
    print(f"{args.branch} against {args.base}: {stat or 'no changes'}\n")
    for item in report.stop:
        print(f"STOP  {item}")
    for item in report.look:
        print(f"LOOK  {item}")
    verdict = {
        0: "Nothing found. Merge once every CI check on the pull request is green.",
        1: "Nothing blocking. Check each LOOK item was part of the approved step, then merge once CI is green.",
        2: "Do not merge. Each STOP item needs a second reader first.",
    }[report.status]
    print(f"\nVerdict: {verdict}")
    return report.status


if __name__ == "__main__":
    sys.exit(main())
