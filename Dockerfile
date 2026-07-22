# Runs the Free/Pro gate in any CI or via `docker run`. The docker:cli base
# ships the docker CLI and the compose plugin; add python3 for lifeguard.
#
# Needs the host docker socket and the installation directory at run time:
#   docker run --rm \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -v /srv/immich-app:/srv/immich-app \
#     -e LIFEGUARD_LICENSE_KEY=... \
#     ghcr.io/OWNER/selfhost-lifeguard \
#     --directory /srv/immich-app --command preflight
FROM docker:27-cli

RUN apk add --no-cache python3 docker-cli-compose

COPY lifeguard.py gate.py /opt/lifeguard/

ENTRYPOINT ["python3", "/opt/lifeguard/gate.py"]
