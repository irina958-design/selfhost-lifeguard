# Selfhost Lifeguard

[![CI](https://github.com/irina958-design/selfhost-lifeguard/actions/workflows/ci.yml/badge.svg)](https://github.com/irina958-design/selfhost-lifeguard/actions/workflows/ci.yml)

Safety checks, database backups, and isolated restore verification for an official Docker Compose installation of [Immich](https://docs.immich.app/install/docker-compose/).

## Pilot status

Version `0.2.0` is ready for controlled pilots. Backups are scoped to the selected installation's Compose project, three simulated user scenarios plus the official Immich v3.0.3 database Compose configuration pass backup and isolated restore checks, and upgrade prerequisites can be planned read-only. Production upgrade remains gated on isolated rehearsal and real-installation pilots.

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
curl -fLO https://github.com/irina958-design/selfhost-lifeguard/releases/download/v0.2.0/lifeguard.py
python lifeguard.py --version
```

The [release page](https://github.com/irina958-design/selfhost-lifeguard/releases/tag/v0.2.0) publishes the file's SHA-256 checksum. No installation step is required.

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

## Safety boundary

- No production container, volume, or database is modified during restore verification.
- Cleanup is scoped to a randomly generated Compose project.
- Production upgrades remain unavailable until isolated rehearsal is implemented and validated.
- Version downgrade is intentionally unavailable because Immich does not support it.
- Back up the media library separately; this version verifies PostgreSQL backups, not a full media restore.

With Immich's documented defaults, Lifeguard intentionally warns about the moving `v3` image tag, the example database password, and the absence of a visible database backup. These warnings do not modify or stop the installation.

## Development

Run the fast tests:

```console
python -m unittest -v test_lifeguard
```

Run the simulated pilot matrix:

```console
LIFEGUARD_DOCKER_TEST=1 python -m unittest -v test_restore_docker
```

It covers an invalid dump, a custom backup path with spaces, and two parallel installations with different data. Every scenario creates a backup, performs an isolated restore, and checks resource cleanup.

## Roadmap

1. Run restore verification against three real Immich installations.
2. Rehearse same-major upgrades against an isolated restored database.
3. Add production upgrade only after successful rehearsal and real-installation pilots; recovery uses a verified backup, never an unsupported downgrade.

## License

[MIT](LICENSE)
