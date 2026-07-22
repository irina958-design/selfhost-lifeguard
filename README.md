# Selfhost Lifeguard

[![CI](https://github.com/irina958-design/selfhost-lifeguard/actions/workflows/ci.yml/badge.svg)](https://github.com/irina958-design/selfhost-lifeguard/actions/workflows/ci.yml)

Safety checks, database backups, and isolated restore verification for an official Docker Compose installation of [Immich](https://docs.immich.app/install/docker-compose/).

## Pilot status

Version `0.1.3` is ready for controlled pilots. Backups are scoped to the selected installation's Compose project, and three simulated user scenarios plus the official Immich v3.0.3 database Compose configuration pass backup and isolated restore checks. Verification against three real Immich installations is still required before any production upgrade or rollback feature is added.

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
curl -fLO https://github.com/irina958-design/selfhost-lifeguard/releases/download/v0.1.3/lifeguard.py
python lifeguard.py --version
```

The [release page](https://github.com/irina958-design/selfhost-lifeguard/releases/tag/v0.1.3) publishes the file's SHA-256 checksum. No installation step is required.

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

## Safety boundary

- No production container, volume, or database is modified during restore verification.
- Cleanup is scoped to a randomly generated Compose project.
- Automatic upgrades and production rollback are intentionally unavailable during the pilot.
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
2. Add upgrade and automatic rollback only after three successful pilot restores.

## License

[MIT](LICENSE)
