# Deploy

`scripts/deploy.py deploy` puts one image on the server: it backs up the database, migrates with the new image, switches `web` and `worker` to it while nginx shows the maintenance page, and waits for `/healthz` to report the new commit. When that check fails it switches back to the image that was running and exits with status 1. It never runs a down-migration: migrations only add, so the previous image runs on the new schema. CI rehearses this on every code change (`deploy/rehearse.sh`).

## Once per server

The script runs with the server's own `python3` and needs nothing installed beyond Docker. It reads four settings, as flags or from the environment:

| Setting | Holds |
|---|---|
| `PREMSHOP_ENV_FILE` | the environment file with every secret, readable by root alone (`docs/development.md` lists what it needs) |
| `PREMSHOP_STATE_FILE` | the record of the image running now and the one before it; the script writes it, nobody edits it |
| `PREMSHOP_BACKUP_DIR` | where each deploy's database backup goes, one `premshop-<time>.dump` per deploy |
| `PREMSHOP_HEALTH_URL` | the address of `/healthz` the script checks, on a host `ALLOWED_HOSTS` accepts |

Keep every image the state file names: `docker image prune` would remove the one a failed deploy returns to.

## Each deploy

1. Waiting for the server step: take the provider's snapshot of the server.
2. Find the commit: a green run of `main` in CI. Its image is `premshop:<commit>`; how the image reaches the server is decided in the server step, as an archive or a pull.
3. Put the checkout on that commit, so the compose file and nginx configuration match the image: `git fetch` then `git checkout <commit>`.
4. Run `python3 scripts/deploy.py deploy premshop:<commit>`, adding `--load <archive>` when the image came as a file.
5. Read the lines it prints, each starting `deploy:`. A deploy that worked ends with `premshop:<commit> is running and healthy` and status 0, and names the backup it took.
6. Waiting for the server step: copy that backup offsite.

## When it stops

- **`the backup came out empty`**: nothing changed. The database container is down or refused `pg_dump`: `docker compose ps db`, then its logs.
- **`stopped: ... migrate --noinput exited with 1`**: nothing switched, and the old image is still serving. The migration's error is printed above. A migration that failed part way leaves the tables it already added, which the old image ignores.
- **`returning to premshop:<old>` then `back on premshop:<old>`**: the new image failed its health check and the site is back on the old one; the status is 1. The last `/healthz` answer is printed. Read the new image's logs before trying again.
- **`is not healthy either`**: neither image answers, and visitors see the maintenance page. The cause is usually outside the image: the database, Redis or the environment file. Follow "When the return fails" in [rollback.md](rollback.md).
- **Interrupted** (a closed terminal, a dropped connection): run the same deploy again. The state file changes only after a health check passes, so it still names the image that ran before, and `rollback` would go one release further back.

Every `docker compose` command by hand needs the same flags the script passes: `docker compose --env-file "$PREMSHOP_ENV_FILE" --file deploy/compose.yaml`, with `PREMSHOP_IMAGE` set to the image recorded as current.
