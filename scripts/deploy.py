#!/usr/bin/env python3
"""Deploy, roll back and restore the production stack (docs/runbooks/).

    python3 scripts/deploy.py deploy premshop:<commit> [--load <image archive>]
    python3 scripts/deploy.py rollback
    python3 scripts/deploy.py restore <dump> premshop:<commit> [--load <image archive>]

It runs on the server with the system Python and its standard library alone, and
drives deploy/compose.yaml through docker compose. A deploy backs up the database,
migrates with the new image, switches web and worker to it and checks /healthz;
when the check fails it switches back to the image that was running. Migrations
only go forward and the previous image runs on the new schema, so going back never
needs a down-migration, and nothing here runs one.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

COMPOSE_FILE = Path(__file__).resolve().parent.parent / "deploy" / "compose.yaml"
# pg_dump --format custom starts every archive with this; an error message, an empty
# file or a plain SQL dump does not.
DUMP_SIGNATURE = b"PGDMP"
HEALTH_POLL_SECONDS = 2
STORES = ("db", "redis")


class Failed(Exception):
    """A step failed; the message says what was left running."""


def say(message: str) -> None:
    print(f"deploy: {message}", flush=True)


def fetch_health(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 the operator's own URL
        return json.load(response)


def read_state(path: str) -> dict[str, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"current": None, "previous": None}


def write_state(path: str, current: str, previous: str | None) -> None:
    state = Path(path)
    state.parent.mkdir(parents=True, exist_ok=True)
    partial = state.with_name(state.name + ".partial")
    partial.write_text(json.dumps({"current": current, "previous": previous}) + "\n", "utf-8")
    os.replace(partial, state)


class Stack:
    def __init__(self, options: argparse.Namespace, run: Callable, fetch: Callable) -> None:
        self.env_file = str(Path(options.env_file).resolve())
        self.health_url, self.health_timeout = options.health_url, options.health_timeout
        self.run, self.fetch = run, fetch

    def compose(self, image: str, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        command = ["docker", "compose", "--env-file", self.env_file, "--file", str(COMPOSE_FILE)]
        env = {**os.environ, "PREMSHOP_IMAGE": image, "PREMSHOP_ENV_FILE": self.env_file}
        return self.run([*command, *args], env=env, **{"check": True, **kwargs})

    def database(self, image: str, program: str, *args: str, **kwargs: Any) -> Any:
        """One of Postgres's programs inside the db container, on the shop's database."""
        connect = ["--username", "premshop", "--dbname", "premshop"]
        return self.compose(image, "exec", "-T", "db", program, *connect, *args, **kwargs)

    def manage(self, image: str, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        """A one-off container from the image, beside whatever web is running."""
        command = ["run", "--rm", "--no-deps", "web", "python", "manage.py", *args]
        return self.compose(image, *command, **kwargs)

    def require(self, image: str, reason: str) -> None:
        inspect = ["docker", "image", "inspect", image]
        if self.run(inspect, check=False, capture_output=True).returncode:
            raise Failed(f"{image} is not on this machine, and {reason}")

    def fetch_image(self, image: str, archive: str | None) -> None:
        if archive:
            self.run(["docker", "load", "--input", archive], check=True)
        inspect = ["docker", "image", "inspect", image]
        if self.run(inspect, check=False, capture_output=True).returncode:
            self.run(["docker", "pull", image], check=True)

    def start_stores(self, image: str) -> None:
        """Start db and redis as they run now: a deploy never recreates either."""
        # Recreating the database on its old data directory under a changed
        # definition (a new Postgres version) may leave it unable to start.
        output = {"capture_output": True, "text": True}
        defined = self.compose(image, "config", "--hash", ",".join(STORES), **output)
        label = '{{.Service}} {{.Label "com.docker.compose.config-hash"}}'
        running = self.compose(image, "ps", "--all", "--format", label, *STORES, **output)
        if not set(running.stdout.splitlines()) <= set(defined.stdout.splitlines()):
            raise Failed(
                "the compose file changes the db or redis service, which a deploy never "
                "recreates; nothing was changed (docs/runbooks/deploy.md)"
            )
        self.compose(image, "up", "--detach", "--wait", "--no-recreate", *STORES)

    def switch(self, image: str) -> None:
        # nginx reads deploy/nginx/ from this checkout: a file it refuses stops
        # here, before anything switches, rather than at nginx's next restart.
        test = ["run", "--rm", "--no-deps", "nginx", "nginx", "-t"]
        if self.compose(image, *test, check=False).returncode:
            raise Failed("nginx refuses the files in deploy/nginx/; nothing was switched")
        # Static files are copied beside the ones already there, so a page loaded
        # before the switch still finds its stylesheet. While web is replaced,
        # nginx answers with the maintenance page.
        self.compose(image, "run", "--rm", "--no-deps", "static")
        self.compose(image, "up", "--detach", "--no-deps", "web", "worker", "nginx")
        # The official image reloads its configuration on SIGHUP.
        self.compose(image, "kill", "--signal", "HUP", "nginx")

    def healthy(self, image: str) -> bool:
        commit = image.rpartition(":")[2]
        deadline = time.monotonic() + self.health_timeout
        while True:
            try:
                report = self.fetch(self.health_url)
            except (OSError, ValueError) as error:
                report = {"status": f"unreachable ({type(error).__name__})"}
            if isinstance(report, dict) and report.get("status") == "ok":
                if report.get("commit") == commit:
                    return True
            if time.monotonic() >= deadline:
                say(f"/healthz never reported {commit} healthy; last answer: {report}")
                return False
            time.sleep(HEALTH_POLL_SECONDS)

    def switch_or_return(self, image: str, previous: str | None) -> bool:
        try:
            self.switch(image)
            if self.healthy(image):
                return True
        except subprocess.CalledProcessError as error:
            say(f"switching to {image} failed: {error}")
        if previous is None:
            say(f"no previous image to return to; {image} is left as it is")
            return False
        say(f"returning to {previous}")
        try:
            self.switch(previous)
            back = self.healthy(previous)
        except subprocess.CalledProcessError as error:
            say(f"switching to {previous} failed: {error}")
            back = False
        say(f"back on {previous}" if back else f"{previous} is not healthy either")
        return False


def backup(stack: Stack, image: str, directory: str) -> Path:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"premshop-{stamp}.dump"
    kept = False
    try:
        # The dump holds personal data and ciphertext: readable by its owner alone.
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            stack.database(image, "pg_dump", "--format", "custom", stdout=file)
        with path.open("rb") as file:
            kept = file.read(len(DUMP_SIGNATURE)) == DUMP_SIGNATURE
    finally:
        if not kept:
            path.unlink(missing_ok=True)
    if not kept:
        raise Failed("the backup came out empty or is not a pg_dump archive; nothing was changed")
    return path


def deploy(stack: Stack, options: argparse.Namespace) -> int:
    image, current = options.image, read_state(options.state_file).get("current")
    if image == current:
        raise Failed(f"{image} is already the running image")
    if current:
        stack.require(current, "a failed health check would have nothing to return to")
    stack.start_stores(image)
    say(f"database backed up to {backup(stack, image, options.backup_dir)}")
    stack.fetch_image(image, options.load)
    stack.manage(image, "migrate", "--noinput")
    if not stack.switch_or_return(image, current):
        return 1
    write_state(options.state_file, image, current)
    say(f"{image} is running and healthy")
    return 0


def rollback(stack: Stack, options: argparse.Namespace) -> int:
    state = read_state(options.state_file)
    current, previous = state.get("current"), state.get("previous")
    if not previous:
        raise Failed("no previous image is recorded, so there is nothing to roll back to")
    stack.require(previous, "rollback runs it")
    if not stack.switch_or_return(previous, current):
        return 1
    write_state(options.state_file, previous, current)
    say(f"{previous} is running and healthy")
    return 0


def restore(stack: Stack, options: argparse.Namespace) -> int:
    image, dump = options.image, Path(options.dump)
    with dump.open("rb") as file:
        if file.read(len(DUMP_SIGNATURE)) != DUMP_SIGNATURE:
            raise Failed(f"{dump} is not a pg_dump archive")
    stack.fetch_image(image, options.load)
    stack.start_stores(image)
    count = "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    tables = stack.database(
        image,
        "psql",
        "--tuples-only",
        "--no-align",
        "--command",
        count,
        capture_output=True,
        text=True,
    )
    if tables.stdout.strip() != "0":
        raise Failed("the database already holds tables; a restore goes into a fresh volume")
    with dump.open("rb") as file:
        once = ["--no-owner", "--exit-on-error", "--single-transaction"]
        stack.database(image, "pg_restore", *once, stdin=file)
    stack.manage(image, "migrate", "--noinput")
    # Before the site opens: under the wrong key it would also write new values
    # that the right key cannot read. A backup with no encrypted value proves no
    # key, so it fails too unless the operator says it holds none.
    decrypt = ["verify_decrypt", *(["--allow-empty"] if options.allow_empty else [])]
    if stack.manage(image, *decrypt, check=False).returncode:
        raise Failed("the restored data does not prove this key decrypts it: a failed restore")
    if not stack.switch_or_return(image, None):
        return 1
    write_state(options.state_file, image, None)
    say(f"restored {dump.name}; {image} is running and healthy")
    return 0


def parse(argv: list[str] | None) -> argparse.Namespace:
    def setting(parser: argparse.ArgumentParser, flag: str, name: str, help: str) -> None:
        parser.add_argument(
            flag,
            default=os.environ.get(name),
            required=name not in os.environ,
            help=f"{help} (default: ${name})",
        )

    shared = argparse.ArgumentParser(add_help=False)
    setting(shared, "--env-file", "PREMSHOP_ENV_FILE", "the environment file with the secrets")
    setting(shared, "--state-file", "PREMSHOP_STATE_FILE", "the record of the image running")
    setting(shared, "--health-url", "PREMSHOP_HEALTH_URL", "the /healthz address to check")
    shared.add_argument("--health-timeout", type=float, default=90, help="seconds to wait")
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    commands = parser.add_subparsers(required=True)

    command = commands.add_parser("deploy", parents=[shared], help="deploy an image")
    command.add_argument("image", help="premshop:<commit>")
    command.add_argument("--load", help="an image archive to load first")
    setting(command, "--backup-dir", "PREMSHOP_BACKUP_DIR", "where the database backup goes")
    command.set_defaults(action=deploy)

    command = commands.add_parser("rollback", parents=[shared], help="run the previous image")
    command.set_defaults(action=rollback)

    command = commands.add_parser("restore", parents=[shared], help="restore a database dump")
    command.add_argument("dump", help="a pg_dump archive")
    command.add_argument("image", help="premshop:<commit>")
    command.add_argument("--load", help="an image archive to load first")
    command.add_argument(
        "--allow-empty", action="store_true", help="accept a backup with no encrypted value"
    )
    command.set_defaults(action=restore)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    run: Callable = subprocess.run,
    fetch: Callable = fetch_health,
) -> int:
    options = parse(argv)
    try:
        return options.action(Stack(options, run, fetch), options)
    except Failed as error:
        say(f"stopped: {error}")
    except subprocess.CalledProcessError as error:
        say(f"stopped: {' '.join(map(str, error.cmd))} exited with {error.returncode}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
