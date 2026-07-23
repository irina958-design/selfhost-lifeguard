# Runs the checks in any CI or via `docker run`. The docker:cli base
# ships the docker CLI and the compose plugin; add python3 for lifeguard.
#
# Needs the host docker socket and the installation directory at run time:
#   docker run --rm \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -v /srv/immich-app:/srv/immich-app \
#     ghcr.io/irina958-design/selfhost-lifeguard \
#     --directory /srv/immich-app --command preflight
FROM docker:27-cli

RUN apk add --no-cache python3 docker-cli-compose

COPY lifeguard.py run.py /opt/lifeguard/

ENTRYPOINT ["python3", "/opt/lifeguard/run.py"]
