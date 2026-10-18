# Deploying on a Windows server

*Written 2026-10-14.*

How to run the system on a server the public can reach as
`https://classobjects.com`. The development machine runs everything from
`scripts\ccweb_startup.cmd` with Vite serving the frontend; a server runs
none of that. Every command here is cmd; none is PowerShell.

## The shape

```
browser --https:443--> Caddy --+-- /api/...   --> uvicorn 127.0.0.1:8000 --> PostgreSQL localhost:5432
                               +-- everything else --> frontend\dist (static files)
```

- **Caddy** is the only thing that listens to the outside. It holds the TLS
  certificate, sends `http://` visitors to `https://`, serves the built
  frontend, and passes `/api/...` to the API. It is open source, one
  `caddy.exe`, and configured by one short text file. IIS can do the same
  job, but needs the URL Rewrite and Application Request Routing modules and
  a `.pfx` in the Windows certificate store; nginx's Windows build is not
  meant for production.
- **The API** (FastAPI under uvicorn) listens on `127.0.0.1` only. The
  application never reads the certificate: TLS ends at Caddy.
- **The frontend** is built once into static files (`frontend\dist`): no
  Vite and no Node run on the server after the build. The shop is
  `index.html`; the console is `management.html`.
- **Photographs** are served by the API (`/api/images/...`, by content hash),
  so the `/api` rule covers them. Their bytes live under `MEDIA_ROOT`, never
  in the database.
- Browser and API share one origin, so no cross-origin request is ever made
  and `CORS_ORIGINS` needs no change.

## 1. The certificate

A server needs three things; the zip DigiCert sends holds only two of them.

| File | In DigiCert's zip | Needed by Caddy |
|---|---|---|
| `classobjects.com.pem` -- the site's certificate (`classobjects.com` and `www.classobjects.com`) | yes | yes |
| `GeoTrustTLSRSACAG1.pem` -- the intermediate that signed it | yes | yes, sent with the certificate |
| `DigiCertGlobalRootG2.pem` -- the root | yes | no: browsers already hold it |
| **the private key** | **no** | **yes** |

The private key was made wherever the certificate request (CSR) was made,
and never leaves there on its own. Two ways to get one onto the server:

**Reissue from the server (simplest when unsure where the key is).** Make a
new key and request on the server, then use *Reissue* on the order in
DigiCert's portal and paste in the request. DigiCert reissues at no charge
and sends a new zip; the key never travels.

```console
mkdir C:\caddy\certs
cd /d C:\caddy\certs
openssl req -new -newkey rsa:2048 -nodes -keyout classobjects.com.key -out classobjects.com.csr -subj "/C=US/ST=Connecticut/L=Coventry/O=ClassObjects LLC/CN=classobjects.com"
type classobjects.com.csr
```

`openssl` is in the conda environment (`<env>\Library\bin\openssl.exe`).
Ask for `www.classobjects.com` as an additional name in the portal.

**Or export the key from the machine that made the request.** If the
request was made with IIS or `certreq`, complete it on that machine, export
the certificate *with its private key* as a `.pfx`
(`certlm.msc` -> Personal -> Certificates -> the certificate -> All Tasks ->
Export), carry the `.pfx` across, and take the key out of it:

```console
cd /d C:\caddy\certs
openssl pkcs12 -in classobjects.com.pfx -nocerts -nodes -out classobjects.com.key
```

Either way, Caddy wants the certificate and the intermediate in one file,
site certificate first:

```console
cd /d C:\caddy\certs
copy /b classobjects.com.pem + GeoTrustTLSRSACAG1.pem fullchain.pem
type fullchain.pem
```

`type` should show two `BEGIN CERTIFICATE` blocks, each on its own line; if
the two run together on one line, add a line break between them in a text
editor. Then allow only the administrators and the account Caddy runs as to
read the key (Caddy as a service runs as `SYSTEM`):

```console
icacls C:\caddy\certs\classobjects.com.key /inheritance:r /grant:r SYSTEM:R Administrators:F
```

Keep every certificate and key file out of the repository.

## 2. Install the system

On the server, follow [environment-setup.md](environment-setup.md) steps 1
to 7: Miniforge, the `ccwebdb` conda environment, uv, Node and PostgreSQL in
the environment, the database cluster, `.env`, and the dependencies.

PostgreSQL on a server has to start with the machine, not from a console.
Register the cluster as a Windows service once, from an administrator cmd:

```console
pg_ctl register -N ccwebdb-postgres -D "%USERPROFILE%\dev\classobjects-ccwebdb\.pgdata" -S auto
sc start ccwebdb-postgres
```

## 3. Move the collection across

Restore the current collection; never run `app.seed` on the server. On the
development machine, with nothing writing to the database:

```console
pg_dump -Fc -f ccwebdb_to_server.dump ccwebdb
python -m app.media_backup copy D:\to_server\media
python -m app.media_backup check D:\to_server\media
```

Carry `ccwebdb_to_server.dump` and the `media` folder to the server. There,
restore into an empty database (`createdb`, then `pg_restore -d ccwebdb
ccwebdb_to_server.dump`), as in
[system-administration.md](system-administration.md) (*Applying a schema
release*, step 1), and copy `media` to the folder `MEDIA_ROOT` will name.
Then, on the server, with `MEDIA_FOLDER` replaced by the folder `MEDIA_ROOT`
names:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
python -m app.media_backup check MEDIA_FOLDER
```

`check` exits 1 naming any photograph missing or damaged.

## 4. Settings for the server

The server's `.env` (template: `.env.example`; every setting is in
[system-administration.md](system-administration.md), *Settings*):

| Setting | On the server |
|---|---|
| `JWT_SECRET` | a new value (`python -c "import secrets; print(secrets.token_hex(32))"`), never the development one. The API will not start without one |
| `DATABASE_URL` | the server's database, with a password that is not `devpassword`: change it first with `ALTER ROLE ccwebdb PASSWORD '...'` in `psql` |
| `MEDIA_ROOT` | the folder the photographs were copied to |
| `SALES_TAX_RATE`, `SALES_TAX_INCLUDES_SHIPPING` | as on the development machine |

`FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_PASSWORD` are read only by `app.seed`,
which is never run on the server: the restored database already holds its
accounts. Sign in with one of them, and change the password of any account
still using a development password (`admin@example.com` among them).

## 5. Build the frontend

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\frontend
npm ci
npm run build
```

This writes `frontend\dist`: `index.html`, `management.html`,
`favicon-lock.png` and `assets\`. Build again after every update.

## 6. Run the API at start-up

The API is started by a scheduled task that runs at boot, whether or not
anyone is signed in. `python -m uvicorn` rather than `uvicorn.exe`: Smart
App Control blocks unsigned `.exe` wrappers on some machines.

Save as `C:\ccwebdb-run\api.cmd` (outside the repository, so an update
never overwrites it). `%USERPROFILE%` is the folder of the account the task
runs as, which is the account that installed the system in step 2:

```console
@echo off
cd /d "%USERPROFILE%\dev\classobjects-ccwebdb\backend"
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 >> "%USERPROFILE%\dev\classobjects-ccwebdb\logs\backend.log" 2>&1
```

`--proxy-headers` with `--forwarded-allow-ips 127.0.0.1` makes the API see
the visitor's address and `https` from Caddy's headers, and trust those
headers from Caddy alone. Register it from an administrator cmd, signed in
as the account that installed the system, which can read the repository and
`MEDIA_ROOT`. The command asks for that account's password, so it is given
alone -- pasted with others, the next line would be taken as the password:

```console
schtasks /create /tn "ccwebdb api" /sc onstart /ru "%USERDOMAIN%\%USERNAME%" /rp * /tr "C:\ccwebdb-run\api.cmd"
```

Then start it and check it answers:

```console
schtasks /run /tn "ccwebdb api"
curl -s -f -o nul http://127.0.0.1:8000/docs
if errorlevel 1 echo The API is not answering: read logs\backend.log
```

To restart it after an update:
`schtasks /end /tn "ccwebdb api"`, then `schtasks /run /tn "ccwebdb api"`.

## 7. Caddy

Download `caddy.exe` for Windows from caddyserver.com into `C:\caddy`, and
save this as `C:\caddy\Caddyfile` (forward slashes in Windows paths):

```
classobjects.com {
	tls C:/caddy/certs/fullchain.pem C:/caddy/certs/classobjects.com.key
	encode gzip

	handle /api/* {
		reverse_proxy 127.0.0.1:8000
	}

	handle /management* {
		root * <repo>/frontend/dist
		try_files {path} /management.html
		file_server
	}

	handle {
		root * <repo>/frontend/dist
		try_files {path} /index.html
		file_server
	}
}

www.classobjects.com {
	tls C:/caddy/certs/fullchain.pem C:/caddy/certs/classobjects.com.key
	redir https://classobjects.com{uri} permanent
}
```

The two `try_files` rules are the server's half of the two applications: a
console address that is not a file is answered with `management.html`, any
other with the shop's `index.html`, as Vite's development fallback does.
Given its own certificate, Caddy asks no one else for one; it still answers
port 80 by sending visitors to `https://`.

Check the file, try it in the foreground, then make it a service:

```console
cd /d C:\caddy
caddy validate --config Caddyfile
caddy run --config Caddyfile
```

Stop it with Ctrl+C once the site answers, then:

```console
sc.exe create caddy start= auto binPath= "C:\caddy\caddy.exe run --config C:\caddy\Caddyfile"
sc.exe start caddy
```

After changing the Caddyfile or the certificate files:
`caddy reload --config C:\caddy\Caddyfile`.

## 8. Open the server to the internet

- **DNS:** `A` records for `classobjects.com` and `www.classobjects.com` at
  the server's public address.
- **Router:** forward ports 80 and 443 to the server, and nothing else.
- **Windows firewall**, from an administrator cmd:

  ```console
  netsh advfirewall firewall add rule name="Caddy HTTP" dir=in action=allow protocol=TCP localport=80
  netsh advfirewall firewall add rule name="Caddy HTTPS" dir=in action=allow protocol=TCP localport=443
  ```

  Do not open 8000 or 5432: the API and the database answer only on the
  server itself.

## 9. Check it

```console
curl -sI https://classobjects.com/
curl -s -o nul -w "%{http_code}\n" "https://classobjects.com/api/catalog?limit=1"
curl -sI http://classobjects.com/
```

Expect `200`, `200`, and a redirect to `https://`. Then in a browser: the
shop loads with the padlock favicon, the DigiCert seal appears in the
footer (it shows only on the domain the certificate names), and
`https://classobjects.com/management` signs in.

## Keeping it running

- **The certificate expires on 2027-04-30**, though the purchase covers
  five years. A public certificate may now last no more than 200 days, so a
  multi-year purchase is a subscription: DigiCert issues a new certificate
  within it, at no charge, before each one ends. Ahead of each expiry,
  reissue in DigiCert's portal (the request made in step 1 can be used
  again, so the key stays where it is), replace `classobjects.com.pem` and
  `fullchain.pem` in `C:\caddy\certs`, and `caddy reload`. This comes round
  about twice a year; the date is in the certificate itself
  (`openssl x509 -in classobjects.com.pem -noout -enddate`).
- **An update:** `git pull`, `uv sync`, then a schema release as in
  [system-administration.md](system-administration.md) (*Applying a schema
  release*) when the update brings a migration, then `npm ci` and
  `npm run build` in `frontend`, then restart the API task. Caddy needs no
  restart for a new build.
- **Backups:** `.\scripts\ccweb_backup.cmd /schedule` on the server, with
  `CCWEB_BACKUP_DIR` naming a folder that leaves the machine
  ([system-administration.md](system-administration.md), *Backing up and
  restoring*).

## Not yet done before the public can reach it

This is known and open, and worth settling before the shop is announced:

- the shop footer says "Prices and inventory are sample data".
