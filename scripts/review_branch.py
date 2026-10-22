"""What a branch changes that a second reader must look at before it is merged.

    .venv/Scripts/python.exe scripts/review_branch.py <branch> [--base main] [--scope FILE]
    .venv/Scripts/python.exe scripts/review_branch.py --pin

Run it from a checkout of main, so the rules applied are main's and not the branch's
own copy of this file. Each finding is STOP (the branch must not be merged as it
stands) or LOOK (fine if it was part of the approved step). Exit status: 0 nothing
found, 1 LOOK only, 2 at least one STOP.

The rules cover the ways a change can pass every check while making the checks weaker
or the data less safe: an applied migration edited or a new one that removes or
reshapes data, tests removed or skipped, the gate changed, a query budget raised, a
decision record rewritten, a secret, a text file turned binary, money or sign-in code
touched, a commit crediting an assistant or made under another identity, and paths
outside those the step was approved to change. It also asks GitHub whether main is
still protected, and compares this clone's hooks and ignore rules with their pins.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PINS = REPO / "docs-local" / "git-state.json"
SCOPES = REPO / "docs-local" / "scopes"

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
SKIP = re.compile(r"pytest\.mark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail)\(|unittest\.skip")
SUPPRESSION = re.compile(r"#\s*(noqa|type:\s*ignore|nosec)\b|pragma:\s*no cover")
BUDGET = re.compile(r"^\s*([A-Z][A-Z0-9_]*BUDGET[A-Z0-9_]*)\s*=\s*(\d+)\s*$")
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}"
    r"|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
)
SECRET_FILE = re.compile(r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|id_rsa.*)$")
ATTRIBUTION = re.compile(r"(?im)^\s*co-authored-by:|\bgenerated (with|by)\b|\U0001f916")
DEPENDENCY_FILES = {"pyproject.toml", "uv.lock", "package.json", "package-lock.json"}
KNOWN_HIDDEN = {".github", ".gitignore", ".gitattributes", ".python-version", ".env.example"}
# Operations that remove or reshape what is already stored.
DESTRUCTIVE = {
    "RemoveField",
    "DeleteModel",
    "RenameField",
    "RenameModel",
    "AlterField",
    "RunSQL",
    "RemoveConstraint",
    "RemoveIndex",
    "AlterUniqueTogether",
}
TEXT_SUFFIXES = (".py", ".html", ".txt", ".md", ".css", ".js", ".json", ".po", ".toml", ".svg")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
STRING = re.compile(r"""(['"]).*?(?<!\\)\1""")
TOO_BIG = 600
GITHUB_BOT = "noreply@github.com"
REQUIRED_CHECKS = {
    "Lint, format, types",
    "Tests (real PostgreSQL, real Chromium)",
    "Dependency and security audit",
}
PINNED_FILES = ("hooks/pre-commit", "hooks/commit-msg", "hooks/pre-push", "info/exclude")


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


def git_config(key: str, repo: Path = REPO) -> str:
    try:
        return git("config", "--get", key, repo=repo).strip()
    except subprocess.CalledProcessError:
        return ""


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


def migration_calls(source: str) -> tuple[list[str], list[str]]:
    """(calls with no way back, calls that remove or reshape stored data) in a migration."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ["the file does not parse"], []
    one_way, destructive = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            back = "reverse_code" if name == "RunPython" else "reverse_sql"
            if name in ("RunPython", "RunSQL") and len(node.args) < 2:
                if not any(k.arg == back for k in node.keywords):
                    one_way.append(f"{name} on line {node.lineno}")
            if name in DESTRUCTIVE:
                destructive.append(f"{name} on line {node.lineno}")
    return one_way, destructive


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


def review(
    head: str, base: str = "main", repo: Path = REPO, scope: list[str] | None = None
) -> Report:
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
    identity = git_config("user.email", repo).lower()
    if not identity:
        report.stop.append("This clone has no user.email, so no commit's identity can be checked.")

    for commit in commits(merge_base, head_rev, repo):
        short = commit.sha[:7]
        if ATTRIBUTION.search(commit.message):
            report.stop.append(f"Commit {short} credits someone else in its message.")
        foreign = {commit.author_email.lower(), commit.committer_email.lower()} - {
            identity,
            GITHUB_BOT,
        }
        if identity and foreign:
            report.stop.append(
                f"Commit {short} was made as {commit.author} <{commit.author_email}>, not as the owner."
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
            one_way, destructive = migration_calls(show(head_rev, path, repo))
            for call in one_way:
                report.stop.append(f"{path} cannot be reversed: {call} has no reverse step.")
            if destructive:
                report.stop.append(
                    f"{path} removes or reshapes stored data ({', '.join(destructive)}): back up, and have it reviewed, before migrating real data."
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
            budgets = {m[1]: int(m[2]) for m in map(BUDGET.match, diff.removed) if m}
            for m in filter(None, map(BUDGET.match, diff.added)):
                if m[1] in budgets and int(m[2]) > budgets[m[1]]:
                    report.stop.append(
                        f"{m[1]} was raised from {budgets[m[1]]} to {m[2]} in {path}."
                    )

    if scope is not None:
        # Both ends of a rename: moving a file out of a folder changes that folder too.
        touched = sorted({p for d in diffs for p in (d.old, d.path) if p})
        outside = [p for p in touched if not any(fnmatch.fnmatch(p, g) for g in scope)]
        if outside:
            shown = ", ".join(outside[:8]) + (" and more" if len(outside) > 8 else "")
            report.stop.append(f"Changed outside the approved paths: {shown}.")
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


def read_scope(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()]


def rewritten(path: Path) -> bool:
    """Whether the file changed after its first commit, or was never committed at all.

    The scope file sits where the agent under review can write, so a STOP for a path
    outside it is one edit away from going quiet.
    """
    try:
        commits = git("log", "--format=%h", "--", path.name, repo=path.parent).split()
        dirty = git("status", "--porcelain", "--", path.name, repo=path.parent).strip()
    except subprocess.CalledProcessError:
        return True
    return len(commits) != 1 or bool(dirty)


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


def git_state(repo: Path = REPO) -> dict[str, str]:
    """Fingerprints of this clone's hooks and private ignore rules, which every worktree shares."""
    common = Path(git("rev-parse", "--git-common-dir", repo=repo).strip())
    common = common if common.is_absolute() else repo / common
    state = {}
    for name in PINNED_FILES:
        target = common / name
        state[name] = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else ""
    state["core.hooksPath"] = git_config("core.hooksPath", repo)
    # Both folders share this config, and the identity checks compare against it.
    state["user.email"] = git_config("user.email", repo)
    return state


def pinned(report: Report, pins: Path = PINS, repo: Path = REPO) -> None:
    if not pins.exists():
        report.stop.append(
            f"No pinned hooks to compare with ({pins.name} is missing): read .git/hooks, then --pin."
        )
        return
    expected = json.loads(pins.read_text(encoding="utf-8"))
    now = git_state(repo)
    # Every key either side knows: a key dropped from the file must not drop its check.
    changed = sorted(k for k in set(expected) | set(now) if expected.get(k, "") != now.get(k, ""))
    if changed:
        report.stop.append(
            f"This clone's {', '.join(changed)} changed since they were pinned. Ask before merging anything."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="What a branch changes that must be looked at.")
    parser.add_argument("branch", nargs="?")
    parser.add_argument("--base", default="main")
    parser.add_argument("--scope", type=Path, help="the paths the step was approved to change")
    parser.add_argument("--pin", action="store_true", help="record the hooks as they are now")
    args = parser.parse_args()
    if args.pin:
        PINS.write_text(json.dumps(git_state(), indent=1) + "\n", encoding="utf-8")
        print(f"pinned {', '.join(git_state())} in {PINS}")
        return 0
    if not args.branch:
        parser.error("name the branch to review")
    here = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if here != args.base:
        print(
            f"Warning: these rules are {here}'s copy, not {args.base}'s. Run this from a checkout of {args.base}.\n"
        )
    scope_file = args.scope or SCOPES / (
        args.branch.removeprefix("origin/").replace("/", "-") + ".txt"
    )
    scope = read_scope(scope_file) if scope_file.exists() else None
    report = review(args.branch, args.base, scope=scope)
    if scope is None:
        report.look.append(f"No list of approved paths for this step ({scope_file.name}).")
    elif rewritten(scope_file):
        report.look.append(
            f"The approved paths changed after the go, or were never committed: "
            f"git -C {scope_file.parent} log -p -- {scope_file.name}"
        )
    protection(report, args.base.removeprefix("origin/"))
    pinned(report)
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
