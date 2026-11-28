# docs-local backup

This branch holds `docs-local.bundle.gpg`: the owner's private working notes for PremShop, as a
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
{
  git clone --quiet --depth 1 --branch docs-local-backup https://github.com/Sina-Amare/premshop.git fetched
  read -rsp 'Passphrase: ' P && echo
  printf '%s\n' "$P" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --output docs-local.bundle --decrypt fetched/docs-local.bundle.gpg
  unset P
  git clone --quiet docs-local.bundle docs-local
  git -C docs-local log -1 --format='Restored docs-local at %h, %cd: %s'
}
```

`docs-local` is then the notes folder with its history. Put it inside a premshop checkout
(it is gitignored there). To resume backups with the same passphrase, save it as one line
in `~/.premshop-backup/passphrase` before running `scripts/backup_docs_local.py setup`.
