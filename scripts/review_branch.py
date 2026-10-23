"""What a change does that a reviewer must look at before it goes into main.

    .venv/Scripts/python.exe scripts/review_branch.py <branch> [--base main]
    .venv/Scripts/python.exe scripts/review_branch.py HEAD --base <commit before the change>

Each finding is STOP (needs an answer before the change goes into main) or LOOK (fine if
it was meant). Exit status: 0 nothing found, 1 LOOK only, 2 at least one STOP.

The rules cover the ways a change can pass every check while making the checks weaker
or the data less safe: an applied migration edited or a new one that removes, moves or
reshapes data, tests removed, skipped or with assertions changed, the gate changed, a
query budget raised, a decision record rewritten, a secret, a text file turned binary,
money or sign-in code touched, and a commit crediting an assistant.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Changing any of these changes what "green" means.
GATE_FILES = re.compile(
    r"^(\.github/|scripts/check\.py$|scripts/review_branch\.py$|(.*/)?conftest\.py$"
    r"|(pytest|tox|mypy)\.ini$|\.mypy\.ini$|setup\.cfg$|\.?ruff\.toml$|\.coveragerc$"
    r"|\.pre-commit-config\.yaml$|apps/core/tests/test_(written_voice|safe_markup|env_example"
    r"|persian_register|review_branch|accessibility_browser)\.py$)"
)
# Money, sign-in, credentials, operator HTML and settings: a careful review every time.
SENSITIVE = re.compile(
    r"^(apps/accounts/|apps/orders/|apps/payments/|apps/catalog/pricing\.py$"
    r"|apps/core/(richtext|email|ratelimit)\.py$|config/settings/)"
)
MIGRATION = re.compile(r"^apps/[^/]+/migrations/(?!__init__\.py$)[^/]+\.py$")
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def (test_\w+)", re.MULTILINE)
SKIP = re.compile(
    r"\bmark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail|importorskip)\(|unittest\.skip"
    r"|\bskipTest\(|\bexpectedFailure\b"
)
SUPPRESSION = re.compile(r"#\s*(noqa|type:\s*ignore|nosec)\b|pragma:\s*no cover")
BUDGET = re.compile(r"^\s*([A-Z][A-Z0-9_]*BUDGET[A-Z0-9_]*)\s*(?::\s*\w+\s*)?=\s*(\d+)\s*(?:#.*)?$")
TEST_FILE = re.compile(r"(^|/)tests?/.*\.py$|(^|/)test_[^/]*\.py$")
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}"
    r"|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
)
SECRET_FILE = re.compile(r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|id_rsa.*)$")
# No list of names: a rule that names no tool cannot be outlived by a rename.
ATTRIBUTION = re.compile(r"(?im)^\s*co-authored-by:|\bgenerated (with|by)\b|\U0001f916")
DEPENDENCY_FILES = {"pyproject.toml", "uv.lock", "package.json", "package-lock.json"}
KNOWN_HIDDEN = {".github", ".gitignore", ".gitattributes", ".python-version", ".env.example"}
# Operations that remove or reshape what is already stored.
DESTRUCTIVE = {
    "RemoveField",
    "DeleteModel",
    "RenameField",
    "RenameModel",
    "RemoveConstraint",
    "RemoveIndex",
    "AlterUniqueTogether",
}
# Code that rewrites rows, whatever it does: never judged from its name.
MOVES_DATA = {"RunPython", "RunSQL"}
TEXT_SUFFIXES = (".py", ".html", ".txt", ".md", ".css", ".js", ".json", ".po", ".toml", ".svg")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
STRING = re.compile(r"""(['"]).*?(?<!\\)\1""")
TOO_BIG = 600


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


@dataclass
class Commit:
    sha: str
    author: str
    author_email: str
    committer_email: str
    message: str


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


def commits(merge_base: str, head: str, repo: Path) -> Iterator[Commit]:
    out = git(
        "log", "--format=%H%x1f%an%x1f%ae%x1f%ce%x1f%B%x1e", f"{merge_base}..{head}", repo=repo
    )
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, author, author_email, committer_email, message = record.split("\x1f", 4)
            yield Commit(sha, author, author_email, committer_email, message)


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
            # pytest's testpaths: a test moved out of apps/ is never collected again.
            ":(glob)apps/**/test_*.py",
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


def migration_calls(source: str) -> dict[str, list[str]]:
    """A new migration's calls, sorted by what they can do to stored data."""
    calls: dict[str, list[str]] = {"one_way": [], "destructive": [], "moves": [], "alters": []}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        calls["one_way"].append("the file does not parse")
        return calls

    def is_none(node: ast.expr | None) -> bool:
        return node is None or (isinstance(node, ast.Constant) and node.value is None)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name, where = node.func.attr, f"{node.func.attr} on line {node.lineno}"
            if name in MOVES_DATA:
                calls["moves"].append(where)
                back = "reverse_code" if name == "RunPython" else "reverse_sql"
                given = node.args[1] if len(node.args) > 1 else None
                given = next((k.value for k in node.keywords if k.arg == back), given)
                if is_none(given):
                    calls["one_way"].append(where)
            if name in DESTRUCTIVE:
                calls["destructive"].append(where)
            if name == "AlterField":
                calls["alters"].append(where)
    return calls


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
    binary = {
        line.split("\t")[-1]
        for line in git("diff", "--numstat", "-M", merge_base, head_rev, repo=repo).splitlines()
        if line.startswith("-\t-\t")
    }
    top_level = set(git("ls-tree", "--name-only", merge_base, repo=repo).splitlines())
    for commit in commits(merge_base, head_rev, repo):
        short = commit.sha[:7]
        if ATTRIBUTION.search(commit.message):
            report.stop.append(f"Commit {short} credits someone else in its message.")

    before, after = test_names((merge_base, head_rev), repo)
    missing = before - after
    names = ", ".join(sorted(missing))
    if sum(after.values()) < sum(before.values()):
        total = f"{sum(before.values())} to {sum(after.values())}"
        report.stop.append(f"Tests were removed: the count fell from {total}. Missing: {names}.")
    elif missing:
        report.look.append(f"Tests disappeared by name while the count held (renamed?): {names}.")

    test_lines = {line.strip() for d in diffs if TEST_FILE.search(d.path) for line in d.added}
    for diff in diffs:
        status, old, path = diff.status, diff.old or diff.path, diff.path
        if MIGRATION.match(old) and status in "MDR":
            report.stop.append(
                f"An applied migration was changed ({old}). Applied migrations are never edited; a new migration makes the change."
            )
        if status == "A" and MIGRATION.match(path):
            calls = migration_calls(show(head_rev, path, repo))
            for call in calls["one_way"]:
                report.stop.append(f"{path} cannot be reversed: {call} has no reverse step.")
            if calls["destructive"]:
                report.stop.append(
                    f"{path} removes or reshapes stored data ({', '.join(calls['destructive'])}): back up, and have it reviewed, before migrating real data."
                )
            if calls["moves"]:
                report.stop.append(
                    f"{path} moves data ({', '.join(calls['moves'])}): back up, and have it reviewed, before migrating real data."
                )
            if calls["alters"]:
                report.look.append(
                    f"{path} alters a field ({', '.join(calls['alters'])}): check it changes only labels or limits; a new type rewrites the column."
                )
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
                    diff, show(merge_base, path, repo), show(head_rev, path, repo)
                )
                if s.startswith("tool.")
            )
            if tools:
                report.stop.append(f"Tool settings changed in pyproject.toml: {', '.join(tools)}.")
        if path in DEPENDENCY_FILES:
            report.stop.append(
                f"Dependencies changed ({path}): ask whether a new package was agreed."
            )
        if status == "D":
            continue
        if status in "AR":
            parts = path.split("/")
            if parts[0] not in top_level:
                report.stop.append(f"A new top-level path appeared: {parts[0]}.")
            hidden = [p for p in parts if p.startswith(".") and p not in KNOWN_HIDDEN]
            if hidden:
                report.stop.append(f"A hidden path was added: {path}.")
        if status == "M" and path in binary and path.endswith(TEXT_SUFFIXES):
            report.stop.append(
                f"{path} turned from text into binary: its encoding was likely damaged."
            )
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
            if TEST_FILE.search(path) and status in "MR":
                # Lines that reappear anywhere in the tests were moved, not lost.
                lost = [line.strip() for line in diff.removed if line.strip() not in test_lines]
                lost = [line for line in lost if line]
                asserts = [line for line in lost if "assert" in line]
                # A LOOK, not a STOP: half of all steps change an assertion on purpose.
                if asserts:
                    report.look.append(
                        f"Assertions removed or changed in {path} ({len(asserts)}): check none was loosened, starting with {asserts[0]}"
                    )
                elif lost:
                    report.look.append(
                        f"Test code removed or changed in {path} ({len(lost)} lines): read them."
                    )
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


def main() -> int:
    parser = argparse.ArgumentParser(description="What a change does that must be looked at.")
    parser.add_argument("branch", help="the branch or commit to review")
    parser.add_argument("--base", default="main")
    args = parser.parse_args()
    report = review(args.branch, args.base)
    stat = git(
        "diff", "--shortstat", git("merge-base", args.base, args.branch).strip(), args.branch
    ).strip()
    print(f"{args.branch} against {args.base}: {stat or 'no changes'}\n")
    for item in report.stop:
        print(f"STOP  {item}")
    for item in report.look:
        print(f"LOOK  {item}")
    verdict = {
        0: "Nothing found.",
        1: "Nothing blocking. Check each LOOK item was meant to be part of the change.",
        2: "Each STOP item needs an answer before this goes into main.",
    }[report.status]
    print(f"\nVerdict: {verdict}")
    return report.status


if __name__ == "__main__":
    sys.exit(main())
