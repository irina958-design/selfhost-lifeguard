# Selfhost Lifeguard

Read-only safety checks for self-hosted applications. The first supported target is an official Docker Compose installation of [Immich](https://docs.immich.app/install/docker-compose/).

## Current version

The first check reads an Immich directory and reports:

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

To explicitly create a new compressed PostgreSQL backup in the verified backup directory:

```console
python lifeguard.py /path/to/immich-app --backup
```

This is the first write operation in Lifeguard. It runs `pg_dump` inside `immich_postgres`, never puts the database password on the command line, refuses unsafe publication, and removes failed temporary output.

With Immich's documented defaults, Lifeguard intentionally warns about the moving `v3` image tag, the example database password, and the absence of a visible database backup. These warnings do not modify or stop the installation.

## Next

1. Restore into an isolated disposable environment.
2. Add upgrade and automatic rollback only after three successful pilot restores.
