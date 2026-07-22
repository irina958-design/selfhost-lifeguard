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

The tiers apply to this Action only. `lifeguard.py` run by hand has no license
check — every command, including restore verification and upgrade rehearsal, is
free on the command line.

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
      - uses: irina958-design/selfhost-lifeguard@v0.3.5
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
        uses: irina958-design/selfhost-lifeguard@v0.3.5
        with:
          directory: /srv/immich-app
          command: backup

      - name: Find newest backup
        id: newest
        shell: bash
        run: echo "path=$(ls -t /srv/immich-app/backups/*.sql.gz | head -1)" >> "$GITHUB_OUTPUT"

      - name: Verify restore (Pro)
        uses: irina958-design/selfhost-lifeguard@v0.3.5
        with:
          directory: /srv/immich-app
          command: verify-restore
          backup: ${{ steps.newest.outputs.path }}
          license-key: ${{ secrets.LIFEGUARD_LICENSE_KEY }}
```

`rehearse-upgrade` needs no backup path — it picks the newest backup itself:

```yaml
      - name: Rehearse upgrade (Pro)
        uses: irina958-design/selfhost-lifeguard@v0.3.5
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

Pilot operators get a Pro key for free: say so in the
[pilot tracker](https://github.com/irina958-design/selfhost-lifeguard/issues/5)
and a key is issued at no cost while the pilot is open. Nothing is needed for
the command-line pilot itself, which is free either way.

## How licensing works (Lemon Squeezy)

A key is tied to installations, not to runs. The first Pro command activates
this installation and stores the resulting instance id in
`~/.lifeguard/instances.json`; every later run validates that same activation.
When the key's activation limit is reached, further installations fail closed
with the merchant's message.

Two values leave the runner: the key, and an opaque installation name of the
form `lifeguard-<12 hex>` — the SHA-256 of the repository slug on a GitHub
runner, or of the hostname otherwise. Nothing else about the installation is
transmitted, and the name cannot be reversed into a hostname or path.

Point `LIFEGUARD_STATE` at another file if the runner's home directory is not
writable or not persistent; without a stored instance id each run activates
again and consumes the key's activation limit.

Seller setup, one time:

1. In Lemon Squeezy, create a **Product** and a **Variant** for Lifeguard Pro.
2. Enable **License keys** on the variant (Settings → License keys → *Generate license keys*)
   and set the activation limit to the number of installations one key may cover.
3. Publish. Buyers receive a key by email at checkout — no extra integration.

The gate calls the public `activate` and `validate` endpoints under
`https://api.lemonsqueezy.com/v1/licenses`. Override that base with the
`LIFEGUARD_LICENSE_URL` environment variable for testing. No API token is
required. The runner needs network access at run time.

Key administration, including how pilot and paid keys are told apart, is in
[`LICENSING.md`](LICENSING.md).
