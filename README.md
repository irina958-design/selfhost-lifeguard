# Selfhost Lifeguard

[![CI](https://github.com/irina958-design/selfhost-lifeguard/actions/workflows/ci.yml/badge.svg)](https://github.com/irina958-design/selfhost-lifeguard/actions/workflows/ci.yml)

Safety checks, database backups, isolated restore verification, and upgrade rehearsal for an official Docker Compose installation of [Immich](https://docs.immich.app/install/docker-compose/).

## Pilot status

Version `0.4.0` is ready for controlled pilots. Backups are scoped to the selected installation's Compose project, temporary output is staged privately, disposable services use internal networks, and real Immich v2 and v3 patch rehearsals pass migrations and schema validation. Production upgrade remains gated on real-installation pilots.

The internal engineering gate additionally exercises a multi-chunk database backup and restore, interrupts a live `pg_dump`, rejects a non-writable backup directory without a traceback, and injects an out-of-space staging error. These maintainer-controlled checks can advance implementation work but do not count as independent user pilots.

Track the pilot gate in [issue #5](https://github.com/irina958-design/selfhost-lifeguard/issues/5).

## Requirements

- Python 3.10 or newer;
- Docker with the `docker compose` plugin;
- the official Immich Docker Compose layout;
- access to the Immich installation directory.

No Python packages are required.

## Get the pilot release

Download the standalone script and confirm its version:

```console
curl -fLO https://github.com/irina958-design/selfhost-lifeguard/releases/download/v0.4.0/lifeguard.py
python lifeguard.py --version
```

The [release page](https://github.com/irina958-design/selfhost-lifeguard/releases/tag/v0.4.0) publishes the file's SHA-256 checksum. No installation step is required.

## Preflight

The default command reads the installation and reports:

- missing `docker-compose.yml` or `.env` files;
- missing or unsafe core settings;
- whether storage paths exist;
- available disk space;
- whether a database backup is visible in `UPLOAD_LOCATION/backups` or a verified `BACKUP_LOCATION` mount.

The default check does not run Docker, create backups, update containers, or change files.

```console
python lifeguard.py /path/to/immich-app
```

Exit codes: `0` ready, `1` warnings found, `2` blocking failures found.

## Create a database backup

To explicitly create a new compressed PostgreSQL backup in the verified backup directory:

```console
python lifeguard.py /path/to/immich-app --backup
```

This is the first write operation in Lifeguard. It runs `pg_dump` through the `database` service in the selected installation's Compose project, never puts the database password on the command line, refuses unsafe publication, and removes failed temporary output.

## Verify restore

To verify a backup without touching the production installation:

```console
python lifeguard.py /path/to/immich-app --verify-restore /path/to/backup.sql.gz
```

Lifeguard reads the database image from the installation's normalized Compose configuration, creates a randomly named Compose project and volume, restores the SQL in one transaction, checks PostgreSQL, and removes only those disposable resources.

## Plan an upgrade

Validate the local prerequisites for a same-major upgrade without pulling images or changing files or containers:

```console
python lifeguard.py /path/to/immich-app --plan-upgrade v3.0.3
```

The current and target versions must use exact `X.Y.Z` syntax, the target must be newer in the same major series, and a database backup must exist in the verified backup directory. Major-version changes remain a manual review because Immich requires reading their breaking-change notes. [Immich does not support downgrades](https://docs.immich.app/install/upgrading/), so recovery must use a verified backup rather than an automatic version rollback.

## Rehearse an upgrade

Start the target Immich release against the newest database backup without changing the production installation:

```console
python lifeguard.py /path/to/immich-app --rehearse-upgrade v3.0.3
```

Lifeguard reads the database and Redis images from the normalized installation Compose file, restores the backup into a random disposable project, starts the exact target `immich-server` image, waits for its official healthcheck, confirms the reported version, and runs `immich-admin schema-check`. It creates empty storage markers in a disposable media volume so Immich can validate its mounts without receiving the user's media files. All rehearsal containers, networks, and volumes are removed afterward, including on failure.

## Safety boundary

- No production container, volume, database, media directory, or secret is given to restore verification or upgrade rehearsal.
- Cleanup is scoped to a randomly generated Compose project.
- Upgrade rehearsal pulls the target image and creates disposable Docker resources; it never changes `IMMICH_VERSION` or performs the production upgrade.
- Production upgrades remain unavailable until real-installation pilots validate the workflow.
- Version downgrade is intentionally unavailable because Immich does not support it.
- Back up the media library separately; this version verifies PostgreSQL backups, not a full media restore.

With Immich's documented defaults, Lifeguard intentionally warns about the moving `v3` image tag, the example database password, and the absence of a visible database backup. These warnings do not modify or stop the installation.

## GitHub Action

Run these checks on a schedule from a self-hosted runner on the Immich host. See
[`ACTION.md`](ACTION.md) for workflow examples.

## Development

Run the fast tests:

```console
python -m unittest discover -s tests -p "test_*.py" -v
```

Run the simulated pilot matrix:

```console
LIFEGUARD_DOCKER_TEST=1 python -m unittest discover -s tests -p "test_restore_docker.py" -v
```

It covers an invalid dump, a custom backup path with spaces, and two parallel installations with different data. Every scenario creates a backup, performs an isolated restore, and checks resource cleanup.

Run the real Immich patch-upgrade rehearsal:

```console
LIFEGUARD_UPGRADE_TEST=1 python -m unittest discover -s tests -p "test_upgrade_docker.py" -v
```

This runs real v2.7.4 → v2.7.5 and v3.0.2 → v3.0.3 rehearsals with the official database and Redis images, validates migrations and schema drift, and checks cleanup.

Run the resource-intensive engineering acceptance on a POSIX Docker host:

```console
LIFEGUARD_ENGINEERING_TEST=1 python -m unittest discover -s tests -p "test_engineering_docker.py" -v
```

It creates and restores a database containing 64 MiB of generated payload, interrupts a second live backup after streaming begins, checks that no partial backup or disposable restore resource remains, and verifies safe handling of a non-writable backup directory. The fast unit suite separately injects an out-of-space write error.

## Roadmap

1. Keep the internal engineering gate green while implementation continues.
2. Run restore and upgrade rehearsal against three independent real Immich installations.
3. Keep production upgrade unavailable in releases until both external pilot gates reach 3/3.
4. Keep recovery based on a verified backup, never an unsupported downgrade.

## License

[MIT](LICENSE)
