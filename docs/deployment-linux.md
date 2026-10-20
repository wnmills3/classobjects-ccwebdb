# Deploying on an Ubuntu server

How to run the system on an Ubuntu server the public can reach over
`https://` at a domain of your own: PostgreSQL and the API as systemd
services, Caddy in front of them, the routine backup on a systemd timer. The
Linux counterpart of [deployment-windows.md](deployment-windows.md), in the
same steps; the reasons given there for the shape of the site hold here.

Tested on **Ubuntu 26.04 LTS**. Every command here is bash. WSL is for
development only: hosting the public site needs a real Ubuntu machine.

**The example installation.** Where a value has to be shown, it is the
owner's: the domain `classobjects.com` with a DigiCert certificate. Put
your own domain, certificate files and folders where those appear.

**On the server, do not use `ccweb_startup.sh` or `ccweb_shutdown.sh` once
step 6 is done.** The shutdown script stops whatever holds the API's port
-- there, the live API -- and stops PostgreSQL. The server's own controls are
`systemctl` (*Keeping it running*).

## The shape

```
browser --https:443--> Caddy --+-- /api/...   --> uvicorn CCWEB_API_HOST:CCWEB_API_PORT --> PostgreSQL (DATABASE_URL)
                               +-- everything else --> CCWEB_SITE_ROOT (a copy of the built frontend)
```

Caddy, installed from its own apt repository, is the only thing that
listens to the outside; the API listens on `CCWEB_API_HOST` (`127.0.0.1`)
only. Each server runs as the account that owns the repository, from the
conda environment.

## 1. The certificate

Make the key and request, or carry them in, exactly as in
[deployment-windows.md](deployment-windows.md), step 1 -- `openssl` is the
same program -- but keep the files in `/etc/caddy/certs`, readable by root
and by Caddy's own account, `caddy`, alone. That account exists once Caddy
is installed (step 7), so make the folder then. The example's commands, from
step 7 on, with the certificate authority's two files and the key in the
current folder:

```console
sudo mkdir -p /etc/caddy/certs
cat classobjects.com.pem GeoTrustTLSRSACAG1.pem | sudo tee /etc/caddy/certs/fullchain.pem > /dev/null
sudo cp classobjects.com.key /etc/caddy/certs/
sudo chown -R root:caddy /etc/caddy/certs
sudo chmod 750 /etc/caddy/certs
sudo chmod 640 /etc/caddy/certs/*
```

`grep -c BEGIN /etc/caddy/certs/fullchain.pem` should say `2`. Keep every
certificate and key file out of the repository.

## 2. Install the system

Follow [environment-setup-linux.md](environment-setup-linux.md) steps 0 to
7, as the account that will own the system. Then make PostgreSQL a service.
First stop a server started by hand in step 5 (`pg_ctl -D .pgdata -m fast
stop`), then render the unit from the settings and install it:

```console
cd ~/dev/classobjects-ccwebdb/backend
conda activate ccwebdb
python -m app.runtime_settings --render ../deploy/linux/ccwebdb-postgres.service | sudo tee /etc/systemd/system/ccwebdb-postgres.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ccwebdb-postgres
```

`systemctl status ccwebdb-postgres` says `active (running)`, and
`pg_isready -h localhost -p 5432` says `accepting connections`. The unit
runs `pg_ctl` from the conda environment by its full path: re-creating the
environment needs the unit rendered and installed again.

## 3. Move the collection across

As in [deployment-windows.md](deployment-windows.md), step 3: a `pg_dump`
and the photographs from the development machine, restored into an empty
database here, or the backup workbook restored as
[environment-setup-linux.md](environment-setup-linux.md) step 8 does. Carry
the files with `scp` or `rsync`. Then check the photographs, with
`MEDIA_FOLDER` replaced by the folder `MEDIA_ROOT` names:

```console
cd ~/dev/classobjects-ccwebdb/backend
uv run python -m app.media_backup check MEDIA_FOLDER
```

## 4. Settings for the server

The server's `.env` takes the settings in
[deployment-windows.md](deployment-windows.md), step 4, with Linux values
for the paths -- the example's:

| Setting | On the server |
|---|---|
| `CCWEB_TLS_CERT`, `CCWEB_TLS_KEY` | `/etc/caddy/certs/fullchain.pem`, `/etc/caddy/certs/classobjects.com.key` |
| `CCWEB_SITE_ROOT` | `/srv/ccwebdb/dist`: a copy Caddy can read (step 5) |
| `MEDIA_ROOT` | the photographs' folder, as a full path |
| `CCWEB_BACKUP_DIR` | a folder that leaves the machine -- a mounted network share, or one a sync client carries off |
| `TIME_ZONE` | the business's zone (`America/New_York`) when the server's clock runs on UTC, as most servers' do |

Change both database passwords as that step says. The commands are the
same in bash, starting from the repository:

```console
cd ~/dev/classobjects-ccwebdb
psql -h localhost -U postgres -d postgres
```

then `\password ccwebdb` and `\password postgres`, the new `ccwebdb` one
written into `DATABASE_URL` with `nano .env`, and the check:

```console
cd ~/dev/classobjects-ccwebdb/backend && uv run alembic current
```

## 5. Build the frontend

```console
cd ~/dev/classobjects-ccwebdb/frontend
npm ci
npm run build
```

Set `VITE_DIGICERT_SEAL_ID` before building, or the shop has no seal. Ubuntu
makes each home folder readable by its owner alone, so Caddy cannot serve
`frontend/dist` where it is. Copy it where it can, to `CCWEB_SITE_ROOT`:

```console
sudo mkdir -p /srv/ccwebdb
sudo rsync -a --delete ~/dev/classobjects-ccwebdb/frontend/dist/ /srv/ccwebdb/dist/
```

Copy it again after every build.

## 6. Run the API at start-up

Render and install the API's unit. It runs as the repository's owner, reads
`.env` itself, and listens on `CCWEB_API_HOST` behind Caddy:

```console
cd ~/dev/classobjects-ccwebdb/backend
python -m app.runtime_settings --render ../deploy/linux/ccwebdb-api.service | sudo tee /etc/systemd/system/ccwebdb-api.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now ccwebdb-api
```

Check it answers (the default address):

```console
curl -s -f -o /dev/null http://127.0.0.1:8000/docs && echo "The API answers" || echo "The API is not answering: journalctl -u ccwebdb-api"
```

## 7. Caddy

Install Caddy from its own apt repository, which keeps it current; this is
caddyserver.com's install for Ubuntu:

```console
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get install -y caddy
```

It installs and starts the `caddy` service with a placeholder site. Put the
certificate in place (step 1), then write the configuration from the
repository's template and the settings, check it, and load it:

```console
cd ~/dev/classobjects-ccwebdb/backend
python -m app.runtime_settings --render ../deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile > /dev/null
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

`validate` says `Valid configuration` (it runs as root because it reads the
key, which only root and Caddy may). Render again after changing any of
the site's settings, then reload. The rendered file is the one
[deployment-windows.md](deployment-windows.md) step 7 shows, with this
server's paths.

## 8. Open the server to the internet

DNS and the router as in [deployment-windows.md](deployment-windows.md),
step 8. With the `ufw` firewall:

```console
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

Do not open the API's port or PostgreSQL's.

## 9. Check it

As in [deployment-windows.md](deployment-windows.md), step 9, with
`-o /dev/null` for `-o nul`:

```console
curl -sI https://classobjects.com/
curl -s -o /dev/null -w "%{http_code}\n" "https://classobjects.com/api/catalog?limit=1"
curl -sI http://classobjects.com/
```

## Keeping it running

```console
sudo systemctl stop ccwebdb-api caddy ccwebdb-postgres
```

```console
sudo systemctl start ccwebdb-postgres ccwebdb-api caddy
```

`systemctl status` names each one's state; `journalctl -u ccwebdb-api` (or
`-u ccwebdb-postgres`, `-u caddy`) shows its log. All three start with the
machine.

- **The certificate:** renew it as the Windows guide says, replace the files
  in `/etc/caddy/certs` as in step 1, and `sudo systemctl reload caddy`.
- **An update:** `git pull`, `uv sync`, a schema release as in
  [system-administration.md](system-administration.md) (*Applying a schema
  release*) when the update brings a migration, `npm ci` and `npm run build`
  in `frontend`, the copy of step 5, then `sudo systemctl restart
  ccwebdb-api`.
- **Backups:** once, as the system's owner:

  ```console
  cd ~/dev/classobjects-ccwebdb
  ./scripts/ccweb_backup.sh --schedule
  ```

  It installs a systemd user timer that runs the backup every day at
  `CCWEB_BACKUP_TIME`, into `CCWEB_BACKUP_DIR`, whether or not anyone is
  signed in ([system-administration.md](system-administration.md), *Backing
  up and restoring*). `./scripts/ccweb_backup.sh --scheduled` shows it.
