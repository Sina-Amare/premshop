# Deploy

`scripts/deploy.py deploy` puts one image on the server: it backs up the database, migrates with the new image, switches `web` and `worker` to it while nginx shows the maintenance page, and waits for `/healthz` to report the new commit, then checks that the worker runs without a restart or a traceback in its log. When that check fails it switches back to the image that was running and exits with status 1. It never runs a down-migration: migrations only add, so the previous image runs on the new schema, as long as each migration follows the rule below. CI rehearses this on every code change (`deploy/rehearse.sh`).

## Once per server

The script runs with the server's own `python3` and needs nothing installed beyond Docker. It reads four settings, as flags or from the environment:

| Setting | Holds |
|---|---|
| `PREMSHOP_ENV_FILE` | the environment file with every secret, readable by root alone (`docs/development.md` lists what it needs) |
| `PREMSHOP_STATE_FILE` | the record of the image running now and the one before it; the script writes it, nobody edits it |
| `PREMSHOP_BACKUP_DIR` | where each deploy's database backup goes, one `premshop-<time>.dump` per deploy, readable by its owner alone; `python3 scripts/deploy.py backup` writes one at any time |
| `PREMSHOP_HEALTH_URL` | the address of `/healthz` the script checks, on a host `ALLOWED_HOSTS` accepts |

Keep every image the state file names: `docker image prune` would remove the one a failed deploy returns to.

## Migrations the previous image survives

A failed deploy and a rollback both run the previous image on the schema the new one left, so every migration has to keep that image working. Nothing checks this yet; the review of the change that adds the migration does:

- A new column is nullable, or carries a database default (`db_default`). `AddField` with `default=` fills the existing rows and then drops the database default, so the previous image's inserts, which never name the column, would fail.
- Nothing is renamed or dropped in the deploy that stops using it. The code stops reading it first; a later deploy, once no image that reads it can return, renames or drops it.

## Each deploy

1. Waiting for the server step: take the provider's snapshot of the server.
2. Find the commit: a green run of `main` in CI. Its image is `premshop:<commit>` with the full 40-character hash, the value `/healthz` reports; how the image reaches the server is decided in the server step, as an archive or a pull.
3. Put the checkout on that commit, so the compose file and nginx configuration match the image: `git fetch` then `git checkout <commit>`. nginx reads `deploy/nginx/` from the checkout: the deploy tests those files with `nginx -t` before it backs up or migrates anything and reloads nginx after the switch, so a changed file takes effect with the deploy that brings it.
4. Run `python3 scripts/deploy.py deploy premshop:<commit>`, adding `--load <archive>` when the image came as a file.
5. Read the lines it prints, each starting `deploy:`. A deploy that worked ends with `premshop:<commit> is running and healthy` and status 0, and names the backup it took.
6. Waiting for the server step: copy that backup offsite.

## When it stops

- **`another command holds`**: nothing changed. A deploy, rollback or restore is already running on this server, and only one runs at a time: wait for it to end.
- **`changes the db or redis service`**: nothing changed. The compose file defines the database or Redis differently from the containers running, and a deploy never recreates either: see "Changing db or redis" below.
- **`the backup came out empty, cut short or not a pg_dump archive`**: nothing changed. Every backup is read whole by `pg_restore` before the deploy goes on. The database container is down or refused `pg_dump`, or the disk is full: `docker compose ps db`, then its logs, and `df -h`.
- **`nginx refuses the files in deploy/nginx/`**: nothing changed, and the old image still serves under nginx's old configuration: a deploy, a rollback and a restore test the files before anything else, and a switch back to the old image never tests them. nginx's error is printed above. Put the checkout back on the running image's commit at once, since nginx would read the refused files at its next restart, then fix the file on `main` and deploy that commit.
- **`stopped: ... migrate --noinput exited with 1`**: nothing switched, and the old image is still serving. The migration's error is printed above. A migration that failed part way leaves the tables it already added, which the old image ignores.
- **`returning to premshop:<old>` then `back on premshop:<old>`**: the new image failed its health check and the site is back on the old one; the status is 1. The last `/healthz` answer, or the worker's state, is printed. Read the new image's logs before trying again.
- **`is not healthy either`**: neither image answers, and visitors see the maintenance page. The cause is usually outside the image: the database, Redis or the environment file. Follow "When the return fails" in [rollback.md](rollback.md).
- **Interrupted** (a closed terminal, a dropped connection): run the same deploy again. The state file changes only after a health check passes, so it still names the image that ran before, and `rollback` would go one release further back.

## Changing db or redis

A change to the `db` or `redis` service in `deploy/compose.yaml` (a new Postgres version, a new setting) is made by hand, because recreating the database on its old data directory may leave it unable to start:

1. Take the provider's snapshot of the server and a backup ([restore.md](restore.md), step 1).
2. A new major version of Postgres cannot read the old data directory: restore that backup into a new volume with the new compose file ([restore.md](restore.md)).
3. Any other change: `docker compose up --detach --wait db redis` recreates both on their volumes. Check that `docker compose ps` shows them healthy and that `/healthz` answers, then run the deploy.

Every `docker compose` command by hand needs the same flags the script passes: `docker compose --env-file "$PREMSHOP_ENV_FILE" --file deploy/compose.yaml`, with `PREMSHOP_IMAGE` set to the image recorded as current.
