"""`scripts/deploy.py` against a stand-in for Docker that records every call.

The stand-in plays the stack: it remembers which image web runs, writes what
pg_dump would, and answers /healthz with the running image's commit, or not at
all for an image marked broken. CI runs the same script against the real stack
(`deploy/rehearse.sh`).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
from django.conf import settings

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "deploy.py"
OLD, NEW = "premshop:aaaa111", "premshop:bbbb222"
DUMP = b"PGDMP custom-format dump"


def _module():
    spec = importlib.util.spec_from_file_location("deploy_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy = _module()


class Docker:
    def __init__(self, running=None, broken=(), dump=DUMP, tables="0", decrypts=True):
        self.calls: list[list[str]] = []
        self.running, self.broken = running, set(broken)
        self.dump, self.tables, self.decrypts = dump, tables, decrypts
        self.restored = b""

    def run(self, args, *, check=False, env=None, stdin=None, stdout=None, **kwargs):
        args = list(args)
        self.calls.append(args)
        assert "migrate" not in args or args[-2:] == ["migrate", "--noinput"], args
        returncode, output = 0, ""
        if "pg_dump" in args:
            stdout.write(self.dump)
        elif "pg_restore" in args:
            self.restored = stdin.read()
        elif "psql" in args:
            output = self.tables + "\n"
        elif "verify_decrypt" in args:
            returncode = 0 if self.decrypts else 1
        elif "up" in args and "web" in args:
            self.running = env["PREMSHOP_IMAGE"]
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, args)
        return subprocess.CompletedProcess(args, returncode, stdout=output)

    def fetch(self, url):
        self.calls.append(["health", url])
        if self.running is None or self.running in self.broken:
            raise urllib.error.URLError("connection refused")
        return {"status": "ok", "commit": self.running.rpartition(":")[2]}

    def steps(self, image=NEW):
        """The calls as named steps, in order, each run of one step counted once."""
        names: list[str] = []
        for args in self.calls:
            if "pg_dump" in args:
                name = "backup"
            elif args[1] in ("load", "pull", "image") and image in args:
                name = "image"
            elif "pg_restore" in args:
                name = "restore"
            elif "migrate" in args:
                name = "migrate"
            elif "verify_decrypt" in args:
                name = "decrypt"
            elif "up" in args and "web" in args:
                name = "switch"
            elif args[0] == "health":
                name = "health"
            else:
                continue
            if not names or names[-1] != name:
                names.append(name)
        return names


@pytest.fixture
def paths(tmp_path):
    return {
        "state": tmp_path / "state" / "deploy.json",
        "backups": tmp_path / "backups",
        "dump": tmp_path / "premshop.dump",
    }


def main(docker, paths, *args):
    options = [
        "--env-file",
        "premshop.env",
        "--state-file",
        str(paths["state"]),
        "--health-url",
        "https://localhost/healthz",
        "--health-timeout",
        "0",
    ]
    if args[0] == "deploy":
        options += ["--backup-dir", str(paths["backups"])]
    return deploy.main([*args, *options], run=docker.run, fetch=docker.fetch)


def record(paths, current, previous=None):
    paths["state"].parent.mkdir(parents=True, exist_ok=True)
    paths["state"].write_text(json.dumps({"current": current, "previous": previous}))


def recorded(paths):
    return json.loads(paths["state"].read_text())


def test_a_deploy_backs_up_fetches_migrates_switches_then_checks_health(paths):
    record(paths, OLD)
    docker = Docker(running=OLD)
    assert main(docker, paths, "deploy", NEW) == 0
    assert docker.steps() == ["backup", "image", "migrate", "switch", "health"]
    assert docker.running == NEW
    assert recorded(paths) == {"current": NEW, "previous": OLD}
    [backup] = paths["backups"].iterdir()
    assert backup.read_bytes() == DUMP


def test_a_first_deploy_needs_no_previous_image(paths):
    docker = Docker()
    assert main(docker, paths, "deploy", NEW) == 0
    assert docker.running == NEW
    assert recorded(paths) == {"current": NEW, "previous": None}


def test_a_failed_health_check_returns_to_the_previous_image_and_fails(paths):
    record(paths, OLD)
    docker = Docker(running=OLD, broken={NEW})
    assert main(docker, paths, "deploy", NEW) != 0
    assert docker.steps() == ["backup", "image", "migrate", "switch", "health", "switch", "health"]
    assert docker.running == OLD
    assert recorded(paths) == {"current": OLD, "previous": None}


@pytest.mark.parametrize("dump", [b"", b"pg_dump: error: connection refused\n"])
def test_a_backup_that_is_not_a_dump_stops_before_anything_changes(paths, dump):
    record(paths, OLD)
    docker = Docker(running=OLD, dump=dump)
    assert main(docker, paths, "deploy", NEW) != 0
    assert docker.steps() == ["backup"]
    assert docker.running == OLD
    assert not list(paths["backups"].iterdir())
    assert recorded(paths) == {"current": OLD, "previous": None}


def test_deploying_the_running_image_again_is_refused(paths):
    record(paths, NEW, OLD)
    docker = Docker(running=NEW)
    assert main(docker, paths, "deploy", NEW) != 0
    assert docker.calls == []


def test_rollback_switches_to_the_recorded_previous_image(paths):
    record(paths, NEW, OLD)
    docker = Docker(running=NEW)
    assert main(docker, paths, "rollback") == 0
    assert docker.steps() == ["switch", "health"]
    assert docker.running == OLD
    assert recorded(paths) == {"current": OLD, "previous": NEW}
    assert not any("migrate" in args or "pg_dump" in args for args in docker.calls)


def test_a_rollback_that_fails_its_health_check_returns_to_the_image_it_left(paths):
    record(paths, NEW, OLD)
    docker = Docker(running=NEW, broken={OLD})
    assert main(docker, paths, "rollback") != 0
    assert docker.running == NEW
    assert recorded(paths) == {"current": NEW, "previous": OLD}


def test_rollback_without_a_recorded_previous_image_changes_nothing(paths):
    record(paths, OLD)
    docker = Docker(running=OLD)
    assert main(docker, paths, "rollback") != 0
    assert docker.calls == []


def test_a_restore_checks_decryption_before_the_site_opens(paths):
    paths["dump"].write_bytes(DUMP)
    docker = Docker()
    assert main(docker, paths, "restore", str(paths["dump"]), NEW) == 0
    assert docker.restored == DUMP
    assert docker.steps() == ["image", "restore", "migrate", "decrypt", "switch", "health"]
    assert recorded(paths) == {"current": NEW, "previous": None}


def test_a_restore_that_cannot_decrypt_fails_and_the_site_stays_closed(paths):
    paths["dump"].write_bytes(DUMP)
    docker = Docker(decrypts=False)
    assert main(docker, paths, "restore", str(paths["dump"]), NEW) != 0
    assert "switch" not in docker.steps()
    assert docker.running is None
    assert not paths["state"].exists()


def test_a_restore_refuses_a_database_that_already_holds_tables(paths):
    paths["dump"].write_bytes(DUMP)
    docker = Docker(tables="42")
    assert main(docker, paths, "restore", str(paths["dump"]), NEW) != 0
    assert not any("pg_restore" in args for args in docker.calls)


def test_a_restore_refuses_a_file_that_is_not_a_dump(paths):
    paths["dump"].write_bytes(b"")
    docker = Docker()
    assert main(docker, paths, "restore", str(paths["dump"]), NEW) != 0
    assert docker.calls == []


def test_the_script_runs_on_the_servers_python_with_its_standard_library_alone():
    """Ubuntu 24.04's system Python is 3.12; the project's interpreter is newer."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 12))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= sys.stdlib_module_names | {"__future__"}
