# Pinned by digest so a rebuild reproduces the same base rather than whatever `3.12-slim`
# points at that day. This is the multi-arch manifest list (OCI image index) for python:3.12-slim
# as served by the Docker Hub registry API on 2026-09-04:
#   GET https://registry-1.docker.io/v2/library/python/manifests/3.12-slim
#   docker-content-digest: sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
# The tag is kept in front of the digest for readability only; the digest is what is pulled.
# To bump: re-run that GET (bearer token from auth.docker.io, scope repository:library/python:pull)
# and paste the new digest.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# trimesh needs nothing heavy for GLB export; keep the image lean.
WORKDIR /app

# Runtime deps PLUS the scale backends. The public deploy uses S3-compatible object storage
# (deploy/README.md) — BIO3D_STORAGE_BACKEND=s3 — and boto3 does not ship in requirements.txt.
# An image built from requirements.txt alone booted and could not reach its assets. psycopg is
# still installed so a Postgres BIO3D_DATABASE_URL keeps working, though production has been a
# SQLite file on the volume since 2026-08-09.
#
# This does NOT undo the runtime/research split: requirements-scale.txt is psycopg + boto3 +
# redis, a few MB. The split exists to keep torch and the NVIDIA CUDA stack (5.6 GB) off the web
# host, and requirements-research.txt is still deliberately absent here.
COPY requirements.txt requirements-scale.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-scale.txt

COPY app ./app

# Runtime data (SQLite DB + assets) lives on a mounted volume.
ENV BIO3D_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000

# Do not serve as root. A fixed uid so the volume can be chowned to a number that does not
# depend on the image's /etc/passwd.
#
# NEEDS-VERIFY-ON-DEPLOY: the Fly volume `bio3d_data` (fly.toml [[mounts]]) was created and has
# only ever been written while the app ran as root, so /data and /data/arena.db are root-owned
# there. The first deploy of this image must be preceded by, on the running (root) machine:
#     flyctl ssh console -a bio3d-arena -C "chown -R 10001:10001 /data"
# and then verified after the deploy with `flyctl ssh console -C "id -u"` -> 10001 and a
# /readyz that reports `database: ok`. Without the chown, SQLite opens the file read-only and
# every vote 500s. A local bind mount (`docker run -v $PWD/data:/data`) needs the same chown.
# The mount point itself is created here so it has the right owner when nothing is mounted.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data /app
USER app

# Serve. Nothing else.
#
# This used to run `python -m app.seed` first, with a comment calling the seed idempotent. It
# was not: that entry point defaulted to force=True, which WIPES every seeded table. Containers
# restart for ordinary reasons — deploys, failed health checks, host migrations — so a public
# instance would have destroyed its own votes on every restart, and votes are the one
# irreplaceable thing here. The entry point now defaults to non-destructive (app/seed.py), and
# the boot path does not seed at all.
#
# A public instance gets its data from the import bundle (deploy/README.md), never from the demo
# seeder, so seeding on boot would be wrong even now that it is safe. Seeding is an explicit
# operator action: `python -m app.seed` for a local demo, `--force` to reset one.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
