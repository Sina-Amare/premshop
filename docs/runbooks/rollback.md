# Rollback

`scripts/deploy.py rollback` puts the previous release back: it switches `web` and `worker` to the image the state file records as previous and waits for `/healthz` to report that image's commit. It touches neither the database nor its schema. Migrations only add, so the previous image runs on the schema the newer one left; no rollback ever runs a down-migration. That holds only while every migration follows the rule in [deploy.md](deploy.md), "Migrations the previous image survives": a new column is nullable or has a `db_default`, and nothing is renamed or dropped in the same deploy that stops using it.

A deploy whose health check fails already returns by itself ([deploy.md](deploy.md)). This is for the release that passed its check and then misbehaved: a page that breaks, a wrong price, a flow that stops.

## Steps

1. Run `python3 scripts/deploy.py rollback`, with the settings described in [deploy.md](deploy.md).
2. It prints `premshop:<previous> is running and healthy` and exits with status 0. The state file now records the two images the other way round, so a second rollback returns to the release you left.
3. Find the fault in the release you left, fix it on `main`, and deploy the fixed commit; deploy the release you left again only once its fault is understood.

## When it stops

- **`no previous image is recorded`**: only one release has run on this server. Deploy a commit that fixes the fault instead.
- **`is not on this machine`**: the previous image was removed. Load it again (`docker load --input <archive>`, or pull it) and rerun.
- **`returning to premshop:<current>`**: the previous image failed its health check, and the script went back to the release you started on. The site is as it was before the rollback.

## When the return fails

`is not healthy either` means neither image answers `/healthz`, and visitors see the maintenance page. A cause that defeats both images is rarely in either:

1. `docker compose ps` (with the flags in [deploy.md](deploy.md)): `db` and `redis` should be running and healthy.
2. `docker compose logs --tail 100 web db redis`: the first error usually names the cause, a full disk, a changed password in the environment file, a database that will not start.
3. Fix that cause. The image the script went back to is already running, so `/healthz` answers once the cause is gone: open `PREMSHOP_HEALTH_URL` and look for `"status": "ok"` with that image's commit before deploying again.

When data has to go back as well, restore the backup the deploy took ([restore.md](restore.md)). That loses every order written since the backup, so it is a last resort, never part of a rollback.
