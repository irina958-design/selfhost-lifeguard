# Selfhost Lifeguard — GitHub Action

Run Lifeguard on a schedule from GitHub Actions: back up the database, prove the
backup actually restores, and rehearse an upgrade in disposable containers —
without touching the production installation.

| Command | What it does |
|---|---|
| `preflight` | Config, storage and backup checks. No Docker, no writes. |
| `backup` | Create a compressed PostgreSQL backup in the verified backup directory. |
| `verify-restore` | Restore a backup into an isolated disposable database and check it. |
| `rehearse-upgrade` | Start the target release against a restored backup in disposable containers. |

## Requirements

Because Lifeguard drives the installation's own Compose project, the Action runs
on a **self-hosted runner installed on the Immich host**. That runner needs:

- Python 3.10+;
- Docker with the `docker compose` plugin;
- read access to the installation directory (`docker-compose.yml`, `.env`, backups).

GitHub-hosted runners cannot see your installation and will not work.

## Weekly backup

```yaml
name: Lifeguard backup
on:
  schedule:
    - cron: "0 3 * * 1"   # 03:00 every Monday
  workflow_dispatch:

jobs:
  backup:
    runs-on: [self-hosted, immich]
    steps:
      - uses: irina958-design/selfhost-lifeguard@v0.4.0
        with:
          directory: /srv/immich-app
          command: backup
```

## Back up, then prove the backup restores

```yaml
name: Lifeguard backup + verify
on:
  schedule:
    - cron: "0 3 * * 1"
  workflow_dispatch:

jobs:
  backup-and-verify:
    runs-on: [self-hosted, immich]
    steps:
      - name: Create backup
        uses: irina958-design/selfhost-lifeguard@v0.4.0
        with:
          directory: /srv/immich-app
          command: backup

      - name: Find newest backup
        id: newest
        shell: bash
        run: echo "path=$(ls -t /srv/immich-app/backups/*.sql.gz | head -1)" >> "$GITHUB_OUTPUT"

      - name: Verify restore
        uses: irina958-design/selfhost-lifeguard@v0.4.0
        with:
          directory: /srv/immich-app
          command: verify-restore
          backup: ${{ steps.newest.outputs.path }}
```

`rehearse-upgrade` needs no backup path — it picks the newest backup itself:

```yaml
      - name: Rehearse upgrade
        uses: irina958-design/selfhost-lifeguard@v0.4.0
        with:
          directory: /srv/immich-app
          command: rehearse-upgrade
          version: 3.0.3
```

Exit codes propagate: `0` clean, `1` warnings, `2` blocking failure.
