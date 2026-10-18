"""Back up docs-local/, encrypted, to a branch of this repository and to a local disk.

docs-local/ holds costs, margins and supplier notes and exists only on this laptop, while this
repository is public. So what leaves the laptop is a git bundle of docs-local's whole history,
encrypted with a passphrase that is kept off the laptop too (password manager and paper).

    .venv/Scripts/python.exe scripts/backup_docs_local.py setup   # once: passphrase + commit hook
    .venv/Scripts/python.exe scripts/backup_docs_local.py run     # what the hook runs
    .venv/Scripts/python.exe scripts/backup_docs_local.py verify  # restore drill, empty folder
    .venv/Scripts/python.exe scripts/backup_docs_local.py verify --ask   # …typing the passphrase

`verify` feeds a shell the restore block of the branch's README word for word and then the
passphrase, the way a person pastes the block and types, so the instructions a new machine
would follow are the thing being tested. Any failure exits non-zero and says what
did not happen; the commit hook repeats it on the commit's output.
"""

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from base64 import b32encode
from functools import partial
from getpass import getpass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs-local"
PASSPHRASE = Path.home() / ".premshop-backup" / "passphrase"
MIRROR_CONFIG = PASSPHRASE.parent / "mirror"
DEFAULT_MIRROR = Path.home() / "premshop-backups"
KEEP = 5
MIN_PASSPHRASE = 20  # `setup` writes 39 characters; a shorter file is a truncated or empty one
BRANCH = "docs-local-backup"
ENCRYPTED = "docs-local.bundle.gpg"
GIT_BIN = Path(r"C:\Program Files\Git")

README = f"""# docs-local backup

This branch holds `{ENCRYPTED}`: the owner's private working notes for PremShop, as a
git bundle of the whole `docs-local/` history, encrypted with a passphrase that is not in
this repository. Without the passphrase it is unreadable. Every backup replaces it, so the
branch has one commit. It has no workflows, so pushing it runs no CI. It is written by
`scripts/backup_docs_local.py` on `main`.

## Restore

In an empty folder, in a terminal that has git and gpg (on Windows: Git Bash, which comes
with Git for Windows), paste the whole block below at once. The braces make the shell read
all of it before running any of it, so the prompt waits for you. At `Passphrase:` type the
passphrase exactly as stored, capitals and dashes included, then Enter; it does not show.

```bash
{{
  git clone --quiet --depth 1 --branch {BRANCH} https://github.com/Sina-Amare/premshop.git fetched
  read -rsp 'Passphrase: ' P && echo
  printf '%s\\n' "$P" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --output docs-local.bundle --decrypt fetched/{ENCRYPTED}
  unset P
  git clone --quiet docs-local.bundle docs-local
  git -C docs-local log -1 --format='Restored docs-local at %h, %cd: %s'
}}
```

`docs-local` is then the notes folder with its history. Put it inside a premshop checkout
(it is gitignored there). To resume backups with the same passphrase, save it as one line
in `~/.premshop-backup/passphrase` before running `scripts/backup_docs_local.py setup`.
"""
RESTORE = README.split("```bash\n")[1].split("```")[0]

HOOK = """#!/bin/sh
# Installed by premshop's scripts/backup_docs_local.py setup: back up after every commit.
"{python}" "{script}" run || {{
  echo 'docs-local BACKUP INCOMPLETE: the line above says where it failed.'
  echo 'Each backup holds the whole history, so the next commit catches up. To retry now:'
  echo '"{python}" "{script}" run'
}} >&2
"""


class BackupError(Exception):
    pass


def _run(cmd: list[str], *, cwd: Path = REPO, stdin: bytes | None = None, env=None) -> str:
    # No command here ever carries the passphrase as an argument; it goes through stdin.
    result = subprocess.run(cmd, cwd=cwd, input=stdin, env=env, capture_output=True)  # noqa: S603
    if result.returncode != 0:
        # The last line is the reason; gpg and git put their setup chatter before it.
        error = (result.stderr.decode(errors="replace").strip().splitlines() or ["no output"])[-1]
        raise BackupError(f"{Path(cmd[0]).stem} {cmd[1]} exited {result.returncode}: {error}")
    return result.stdout.decode(errors="replace").strip()


def git(*args: str, cwd: Path = REPO, stdin: bytes | None = None, env=None) -> str:
    return _run(["git", *args], cwd=cwd, stdin=stdin, env=env)


def _git_bash_path(path: Path | str) -> str:
    # Git for Windows' gpg cannot reach its agent through a GNUPGHOME or HOME written as C:\…
    text = str(path).replace("\\", "/")
    return f"/{text[0].lower()}{text[2:]}"


def gpg(passphrase: str, *args: str) -> None:
    """Run gpg with a fresh, empty home, so nothing cached on this machine takes part."""
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ, GNUPGHOME=_git_bash_path(home))
        gpg_cmd = [str(GIT_BIN / "usr/bin/gpg.exe"), "--batch", "--yes", "--pinentry-mode"]
        try:
            _run(
                [*gpg_cmd, "loopback", "--passphrase-fd", "0", *args],
                stdin=f"{passphrase}\n".encode(),
                env=env,
            )
        finally:
            _kill_agent(env)


def _kill_agent(env: dict[str, str]) -> None:
    # The agent gpg started holds files in the temporary home; stop it before it is deleted.
    subprocess.run(  # noqa: S603
        [str(GIT_BIN / "usr/bin/gpgconf.exe"), "--kill", "gpg-agent"], env=env, capture_output=True
    )


def _mirror() -> Path:
    """Where the second, local copy of the snapshots goes.

    A drive letter is a fact about one machine, not about this project, and
    this was hardcoded to `G:` until a Windows reinstall left no such drive
    and every backup failed its local half in silence. The path now lives
    beside the passphrase, outside the repository, so a new machine states
    where its own disk is rather than the repository pretending to know.

    Put the second copy on a different physical disk from the project, or it
    only protects you against deleting the folder, not against the disk dying.
    """
    if MIRROR_CONFIG.exists():
        written = MIRROR_CONFIG.read_text(encoding="utf-8-sig").strip()
        if written:
            return Path(written)
    return DEFAULT_MIRROR


MIRROR = _mirror()


def _normalise(passphrase: str) -> str:
    """The one place a passphrase becomes a key, whether it was typed or read from the file.

    The generated passphrase is upper-case base32 in dash-separated groups. Stored and typed
    copies must normalise identically, or a drill passes for a value a real restore would
    refuse. `\\ufeff` is the byte-order mark Windows tools put at the start of a hand-written
    file: it is not whitespace, so `.strip()` leaves it in and it silently joins the key.
    """
    return passphrase.replace("﻿", "").strip().upper()


def _stored_passphrase() -> str:
    if not PASSPHRASE.exists():
        raise BackupError(f"no passphrase at {PASSPHRASE}; run `setup` first")
    passphrase = _normalise(PASSPHRASE.read_text(encoding="utf-8-sig"))
    # Nothing inside this script can tell a wrong key from the right one — every check it runs
    # uses the same value — so the only defence is refusing what cannot be the real passphrase.
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(
            f"{PASSPHRASE} holds {len(passphrase)} characters, fewer than {MIN_PASSPHRASE}:"
            " it must hold the whole passphrase on one line, nothing else"
        )
    return passphrase


def setup() -> None:
    if PASSPHRASE.exists():
        print(f"Passphrase already at {PASSPHRASE}; left as it is.")
    else:
        groups = b32encode(secrets.token_bytes(20)).decode()  # 160 bits; A-Z and 2-7, no 0/O or 1/I
        PASSPHRASE.parent.mkdir(exist_ok=True)
        passphrase = "-".join(groups[i : i + 4] for i in range(0, 32, 4))
        PASSPHRASE.write_text(passphrase + "\n", encoding="utf-8", newline="\n")
        print(f"New passphrase written to {PASSPHRASE}. It is not printed here.")
    hook = DOCS / ".git" / "hooks" / "post-commit"
    if hook.exists() and "backup_docs_local" not in hook.read_text(encoding="utf-8"):
        raise BackupError(f"{hook} exists and is not this script's; merge it by hand")
    script = HOOK.format(
        python=Path(sys.executable).as_posix(), script=Path(__file__).resolve().as_posix()
    )
    hook.write_text(script, encoding="utf-8", newline="\n")  # a shell script: LF, not CRLF
    print(f"Commit hook installed: {hook}")


def _to_mirror(encrypted: Path, head: str) -> str:
    MIRROR.mkdir(parents=True, exist_ok=True)
    target = MIRROR / f"docs-local-{time.strftime('%Y%m%d-%H%M%S')}-{head[:7]}.bundle.gpg"
    # Written under another name and renamed: a copy that dies mid-write (the disk drops out,
    # an antivirus lock, a full disk) must not leave a half file holding the newest name, which
    # the pruning below would then keep in preference to a whole older one.
    partial = target.with_suffix(".part")
    try:
        shutil.copyfile(encrypted, partial)
        if partial.read_bytes() != encrypted.read_bytes():
            raise BackupError(f"{partial} does not match what was written")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    for old in sorted(MIRROR.glob("docs-local-*.bundle.gpg"))[:-KEEP]:
        old.unlink()
    return str(target)


def _to_branch(encrypted: Path, head: str) -> str:
    # Built with git plumbing, so neither the working tree nor any local branch is touched.
    blob = git("hash-object", "-w", "--no-filters", str(encrypted))
    readme = git("hash-object", "-w", "--stdin", stdin=README.encode())
    entries = f"100644 blob {readme}\tREADME.md\n100644 blob {blob}\t{ENCRYPTED}\n"
    commit = git(
        "commit-tree",
        git("mktree", stdin=entries.encode()),
        "-m",
        f"docs-local backup at {head[:7]}",
    )
    git("push", "--force", "--quiet", "origin", f"{commit}:refs/heads/{BRANCH}")
    published = git("ls-remote", "origin", f"refs/heads/{BRANCH}").split()
    if published[:1] != [commit]:
        raise BackupError(
            f"GitHub's {BRANCH} is {published[:1]}, not the commit just pushed ({commit[:7]})"
        )
    return f"GitHub, branch {BRANCH} ({commit[:7]})"


def run() -> None:
    passphrase = _stored_passphrase()
    head = git("rev-parse", "HEAD", cwd=DOCS)
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp, "b.bundle")
        encrypted = Path(tmp, ENCRYPTED)
        check = Path(tmp, "check.bundle")
        git("bundle", "create", str(bundle), "--all", cwd=DOCS)
        encrypt = ["--symmetric", "--cipher-algo", "AES256", "--output", str(encrypted)]
        gpg(passphrase, *encrypt, str(bundle))
        gpg(passphrase, "--decrypt", "--output", str(check), str(encrypted))
        if check.read_bytes() != bundle.read_bytes():
            raise BackupError(
                "the encrypted file does not decrypt back to the bundle; nothing copied"
            )
        # A round trip alone proves nothing: gpg also "decrypts" a file that was never encrypted.
        try:
            gpg(f"not-{passphrase}", "--decrypt", "--output", str(Path(tmp, "x")), str(encrypted))
        except BackupError:
            pass
        else:
            raise BackupError(
                "the file opens without the passphrase: not encrypted; nothing copied"
            )
        for name, copy in ((str(MIRROR), _to_mirror), ("GitHub", _to_branch)):
            try:
                print(f"docs-local {head[:7]} backed up to {copy(encrypted, head)}")
            except (BackupError, OSError) as error:
                failures.append(f"{name}: {error}")
    if git("status", "--porcelain", cwd=DOCS):
        print("Not in this backup: uncommitted changes in docs-local. Commit them to back them up.")
    if failures:
        raise BackupError("; ".join(failures))


def verify(ask: bool = False) -> None:
    # --ask tests the copy kept off the laptop: the stored file takes no part. On Windows
    # getpass reads the console and ignores a pipe, so without a terminal it would hang.
    if ask:
        prompt = "Passphrase, exactly as stored (it does not show): "
        typed = getpass(prompt) if sys.stdin.isatty() else input(prompt)
        passphrase = _normalise(typed)
    else:
        passphrase = _stored_passphrase()
    expected = git("rev-parse", "HEAD", cwd=DOCS)
    with tempfile.TemporaryDirectory() as tmp:
        home, folder = Path(tmp, "home"), Path(tmp, "restore")
        home.mkdir()
        folder.mkdir()
        # A stranger's machine: an empty home (no git config, no gpg keys or agent), no
        # stored GitHub login, and nothing from this repository.
        env = dict(
            os.environ,
            HOME=_git_bash_path(home),
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="credential.helper",
            GIT_CONFIG_VALUE_0="",
        )
        env.pop("GNUPGHOME", None)
        print(f"Restoring into an empty folder, {folder}, with the README's commands:")
        # The block, then the passphrase, on one input stream: what a terminal receives when a
        # person pastes the block and types. -e: stop at the first failure, so the error names it.
        typed = f"{RESTORE}{passphrase}\n".encode()
        try:
            print(
                _run([str(GIT_BIN / "bin/bash.exe"), "-e", "-s"], cwd=folder, stdin=typed, env=env)
            )
        finally:
            _kill_agent(env)
        restored = git("rev-parse", "HEAD", cwd=folder / "docs-local")
        files = len(git("ls-files", cwd=folder / "docs-local").splitlines())
        # The drill runs this script's copy of the instructions. The ones a new machine would
        # follow are on the branch, and they are only republished by a backup — so compare.
        published = (folder / "fetched" / "README.md").read_text(encoding="utf-8")
        stale = published.split("```bash\n")[1].split("```")[0] != RESTORE
    if restored != expected:
        raise BackupError(f"GitHub restores {restored[:7]}, but docs-local is at {expected[:7]}")
    print(f"GitHub: restored {files} files at {restored[:7]}, the same commit as docs-local here.")

    copies = sorted(MIRROR.glob("docs-local-*.bundle.gpg"))
    if not copies:
        raise BackupError(f"no copies in {MIRROR}")
    # Every copy, not just the newest: a count of filenames says nothing about the archives
    # behind them. Only the newest holds the current commit; the older ones are older snapshots.
    for copy in copies:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp, "b.bundle")
            gpg(passphrase, "--decrypt", "--output", str(bundle), str(copy))
            heads = git("bundle", "list-heads", str(bundle), cwd=DOCS)
        if copy is copies[-1] and expected not in heads:
            raise BackupError(f"the newest G: copy, {copy.name}, does not hold {expected[:7]}")
    print(
        f"{MIRROR}: all {len(copies)} copies decrypt, and the newest, {copies[-1].name},"
        f" holds {expected[:7]}."
    )
    if stale:
        raise BackupError(
            "the restore instructions published on the branch are not this script's."
            " A new machine would follow the published ones: run `backup_docs_local.py run`"
            " (or make any docs-local commit) to republish them."
        )


if __name__ == "__main__":
    args = sys.argv[1:]
    commands = {"setup": setup, "run": run, "verify": verify}
    if args == ["verify", "--ask"]:
        chosen = partial(verify, ask=True)
    elif len(args) == 1 and args[0] in commands:
        chosen = commands[args[0]]
    else:
        sys.exit("usage: backup_docs_local.py setup | run | verify [--ask]")
    # Git runs a hook with GIT_DIR and friends pointing at docs-local; without dropping
    # them, the commands meant for this repository would act on docs-local instead.
    for name in _run(["git", "rev-parse", "--local-env-vars"]).split():
        os.environ.pop(name, None)
    try:
        chosen()
    except BackupError as error:
        sys.exit(f"BACKUP FAILED: {error}")
