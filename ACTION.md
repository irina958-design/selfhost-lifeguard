# Selfhost Lifeguard — GitHub Action

Run Lifeguard on a schedule from GitHub Actions: back up the database, prove the
backup actually restores, and rehearse an upgrade in disposable containers —
without touching the production installation.

| Command | Tier | What it does |
|---|---|---|
| `preflight` | Free | Config, storage and backup checks. No Docker, no writes. |
| `backup` | Free | Create a compressed PostgreSQL backup in the verified backup directory. |
| `verify-restore` | **Pro** | Restore a backup into an isolated disposable database and check it. |
| `rehearse-upgrade` | **Pro** | Start the target release against a restored backup in disposable containers. |

## Requirements

Because Lifeguard drives the installation's own Compose project, the Action runs
on a **self-hosted runner installed on the Immich host**. That runner needs:

- Python 3.10+;
- Docker with the `docker compose` plugin;
- read access to the installation directory (`docker-compose.yml`, `.env`, backups).

GitHub-hosted runners cannot see your installation and will not work.

## Free example — weekly backup

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
      - uses: irina958-design/selfhost-lifeguard@v0.3.3
        with:
          directory: /srv/immich-app
          command: backup
```

## Pro example — back up, then prove the backup restores

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
        uses: irina958-design/selfhost-lifeguard@v0.3.3
        with:
          directory: /srv/immich-app
          command: backup

      - name: Find newest backup
        id: newest
        shell: bash
        run: echo "path=$(ls -t /srv/immich-app/backups/*.sql.gz | head -1)" >> "$GITHUB_OUTPUT"

      - name: Verify restore (Pro)
        uses: irina958-design/selfhost-lifeguard@v0.3.3
        with:
          directory: /srv/immich-app
          command: verify-restore
          backup: ${{ steps.newest.outputs.path }}
          license-key: ${{ secrets.LIFEGUARD_LICENSE_KEY }}
```

`rehearse-upgrade` needs no backup path — it picks the newest backup itself:

```yaml
      - name: Rehearse upgrade (Pro)
        uses: irina958-design/selfhost-lifeguard@v0.3.3
        with:
          directory: /srv/immich-app
          command: rehearse-upgrade
          version: 3.0.3
          license-key: ${{ secrets.LIFEGUARD_LICENSE_KEY }}
```

Exit codes propagate: `0` clean, `1` warnings, `2` blocking failure, `3` a Pro
command was called without a valid license.

## Get a license

Buy a key at the [store](https://selfhost-lifeguard.lemonsqueezy.com), then add it
to the repository:

**Settings → Secrets and variables → Actions → New repository secret**
Name `LIFEGUARD_LICENSE_KEY`, value the key from your purchase email.

## How licensing works (Lemon Squeezy)

Pro commands validate the key online, then run. Nothing about your installation
leaves the runner — only the key string is sent for a valid/invalid answer.

Seller setup, one time:

1. In Lemon Squeezy, create a **Product** and a **Variant** for Lifeguard Pro.
2. Enable **License keys** on the variant (Settings → License keys → *Generate license keys*).
3. Publish. Buyers receive a key by email at checkout — no extra integration.

The gate calls the public endpoint
`https://api.lemonsqueezy.com/v1/licenses/validate` with the key and reads
`valid`. Override it for testing with the `LIFEGUARD_LICENSE_URL` environment
variable. No API token is required for validation.

> Validation is online (the runner needs network at run time). For air-gapped
> runners, the upgrade path is an offline Ed25519-signed key verified against a
> bundled public key — see the `ponytail:` note in [`gate.py`](gate.py).
