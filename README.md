# Selfhost Lifeguard

Read-only safety checks for self-hosted applications. The first supported target is an official Docker Compose installation of [Immich](https://docs.immich.app/install/docker-compose/).

## Current version

The first check reads an Immich directory and reports:

- missing `docker-compose.yml` or `.env` files;
- missing or unsafe core settings;
- whether storage paths exist;
- available disk space;
- whether a database backup is visible in `UPLOAD_LOCATION/backups` or a verified `BACKUP_LOCATION` mount.

It does not run Docker, create backups, update containers, or change files.

```console
python lifeguard.py /path/to/immich-app
```

Exit codes: `0` ready, `1` warnings found, `2` blocking failures found.

With Immich's documented defaults, Lifeguard intentionally warns about the moving `v3` image tag, the example database password, and the absence of a visible database backup. These warnings do not modify or stop the installation.

## Next

1. Add a safe database-backup command.
2. Restore into an isolated disposable environment.
3. Add upgrade and automatic rollback only after three successful pilot restores.
