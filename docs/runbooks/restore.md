# Restore

`scripts/deploy.py restore` brings the shop back from a database backup: it reads a `pg_dump` archive into an empty database, applies any migration the image has and the backup lacks, then decrypts every encrypted value with the configured key (`manage.py verify_decrypt`, which prints counts and never a value). Only when every value decrypts does it start `web`, `worker` and nginx and wait for `/healthz`. A restore that cannot decrypt is a failed restore (ADR-0007), so the site stays closed: under a wrong key it would also write new input that the right key could never read.

## What you need

1. The backup: a `premshop-<time>.dump` from `PREMSHOP_BACKUP_DIR`, or, waiting for the server step, its offsite copy.
2. The environment file with the field-encryption key that wrote the data: `FIELD_ENCRYPTION_KEY`, and in `FIELD_ENCRYPTION_KEY_FALLBACKS` any older key the backup still holds values under. On a new machine these come from the offline escrow copy (ADR-0007).
3. The image to run: the one the state file records as current, or the latest green `main`.

## Steps

1. Give the restore an empty database. On a new machine there is nothing to do. On the server, first keep a copy of what is there (a `pg_dump`, or waiting for the server step, a snapshot), then stop the stack and remove the database volume: `docker compose down` (with the flags in [deploy.md](deploy.md)), then `docker volume rm premshop_db`. Removing the volume deletes the database for good: never skip the copy.
2. Run `python3 scripts/deploy.py restore <dump> premshop:<commit>`, adding `--load <archive>` when the image came as a file.
3. Read the counts: one line per encrypted column, such as `orders.OrderItem.customer_input: 12 decrypted, 0 failed`, then `All 12 encrypted values decrypt.`
4. A restore that worked ends with `restored premshop-<time>.dump; premshop:<commit> is running and healthy` and status 0.
5. Sign in as a customer whose order the backup holds, and open that order.

## When it stops

- **`the database already holds tables`**: the database is not empty, and nothing was restored. Go back to step 1.
- **`N decrypted, M failed`** then **`a failed restore`**: the key in the environment file did not write those values. The site stays closed. Find the right key in the escrow, put it in the environment file, remove the database volume again (it holds only the copy this attempt restored) and rerun.
- **`nothing proves the key`** then **`a failed restore`**: the backup holds no encrypted value, so decrypting proved nothing and the site stays closed. Only a backup taken before the first order holds none: remove the database volume again and rerun with `--allow-empty`. Any later backup without one is the wrong file.
- **`stopped: ... pg_restore ... exited with 1`**: the archive is damaged or not from this shop's database. The restore runs in one transaction, so the database is still empty; try an older backup.

## As a drill

The same steps on a machine that is not the production server, monthly from the launch step. The drill passes only when at least one value decrypts, none fails and the sign-in in step 5 works. CI rehearses the restore on every code change (`deploy/rehearse.sh`): one seeded input decrypts with the right key, and a different key fails the restore with the site closed.
