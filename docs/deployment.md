# Deploying on a Windows server

*Written 2026-10-14.*

How to run the system on a server the public can reach as
`https://classobjects.com`. The development machine runs everything from
`scripts\ccweb_startup.cmd` with Vite serving the frontend; a server runs
none of that. Every command here is cmd; none is PowerShell.

**On the server, do not use `ccweb_startup.cmd` or `ccweb_shutdown.cmd`
once step 6 is done.** The shutdown script kills whatever listens on port
8000 -- there, the live API -- and stops PostgreSQL, taking the site down.
The startup script finds the API and PostgreSQL already running and leaves
them, but starts Vite as well, which the server has no use for. The server's
own controls are under *Keeping it running*.

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

`openssl` is in the conda environment
(`%USERPROFILE%\miniforge3\envs\ccwebdb\Library\bin\openssl.exe`), so run
these in a window where `conda activate ccwebdb` has been run.
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

PostgreSQL on a server has to run apart from any console. It becomes a
Windows service, started by hand because its cluster is shared with the
development machine (*Keeping it running*). A service cannot start while
another server holds the cluster -- one started by `ccweb_startup.cmd` or by
hand in environment-setup.md step 5. First stop any that is running (it prints
`server stopped`, or `Is server running?` when none was):

```console
conda activate ccwebdb
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
pg_ctl -D .pgdata -m fast stop
```

Then register the cluster as a service once, from an administrator cmd with
`conda activate ccwebdb` run in it, and start it:

```console
pg_ctl register -N ccwebdb-postgres -D "%USERPROFILE%\dev\classobjects-ccwebdb\.pgdata" -S demand
```

```console
sc start ccwebdb-postgres
pg_isready -h localhost -p 5432
```

`pg_isready` says `accepting connections` within a few seconds; run it again
if it says `no response` at first. `sc qc ccwebdb-postgres` shows what was
registered. The service runs as `LocalSystem`, which can read a cluster in
the user's folder, and runs `pg_ctl.exe` from the conda environment by its
full path: re-creating the `ccwebdb` environment breaks it until the service
is registered again (`pg_ctl unregister -N ccwebdb-postgres`, then the
`register` line above).

## 3. Move the collection across

Skip this step if the collection is already on the server: restored from
the backup workbook by environment-setup.md step 8 (*This collection, on a
new machine*), it is complete.

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
| `DATABASE_URL` | the server's database, with a password that is not `devpassword`: change it first, as below |
| `MEDIA_ROOT` | the folder the photographs were copied to |
| `SALES_TAX_RATE`, `SALES_TAX_INCLUDES_SHIPPING` | as on the development machine |
| `SHOP_OPEN` | `false` until opening day: visitors who are not signed in see a coming-soon page, and the API refuses them the catalog and registration. Set it to `true` and restart the API to open the shop |

`FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_PASSWORD` are read only by `app.seed`,
which is never run on the server: the restored database already holds its
accounts. Sign in with one of them, and change the password of any account
still using a development password (`admin@example.com` among them).

### The database passwords

environment-setup.md step 5 gives both database accounts the password
`devpassword`: `ccwebdb`, which the application signs in as, and
`postgres`, the administrator. Change both on the server.

**1. Make a new password**, once for each account. This prints 32 random
characters -- letters, digits, `-` and `_` -- that go into `.env` as they
are. A password with `@ # : / %` in it would break `DATABASE_URL`, which is
a web-style address, unless those characters were encoded.

```console
conda activate ccwebdb
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

**2. Start psql as the administrator.** It asks for the `postgres`
password, still `devpassword`:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
psql -h localhost -U postgres -d postgres
```

**3. At the `postgres=#` prompt, set each password.** `\password` asks for
the new one twice and shows nothing as it is typed or pasted. It keeps the
password out of psql's history file and the server's log, which
`ALTER ROLE ... PASSWORD '...'` would not.

```console
\password ccwebdb
```

```console
\password postgres
```

Then `\q` to leave. The application never uses the `postgres` password:
keep it somewhere safe, for psql and for schema releases.

**4. Put the `ccwebdb` password into `.env`.** Change only the part between
`ccwebdb:` and `@`, so the line reads
`DATABASE_URL=postgresql+psycopg://ccwebdb:NEWPASSWORD@localhost:5432/ccwebdb`
with the new password in place of `NEWPASSWORD`:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
notepad .env
```

**5. Check that the application signs in.** This prints the database's
schema revision, ending `(head)`; a `password authentication failed` means
`.env` does not match what was typed in 3:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend && uv run alembic current
```

An API already running read the old `.env` when it started: restart it
(*Keeping it running*, below). The backup and the application read `.env`
and need nothing more. `scripts\ccweb_psql.cmd` assumes `devpassword`
unless `PGPASSWORD` is set in the window first, so it no longer signs in;
plain `psql`, as in 2, asks for the password.

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

Caddy is one program, `caddy.exe`. Download it into `C:\caddy` (this is the
address behind the Windows amd64 button on caddyserver.com's download page,
which saves the same file as `caddy_windows_amd64.exe`; renamed to
`caddy.exe`, that one does as well):

```console
mkdir C:\caddy
curl -L -o C:\caddy\caddy.exe "https://caddyserver.com/api/download?os=windows&arch=amd64"
C:\caddy\caddy.exe version
```

`version` prints a `v2.` version line. Every command here names
`C:\caddy\caddy.exe` in full: on a machine with
`NoDefaultCurrentDirectoryInExePath` set, as the development machine has, a
bare `caddy` is not found even in `C:\caddy`.

Then save this as `C:\caddy\Caddyfile`. Caddy wants forward slashes in Windows
paths. The two `root` lines name the repository installed in step 2 as
`C:\Users\wnmil\dev\classobjects-ccwebdb`; installed under another account
or folder, change them to match. They must be written out: Caddy runs as
`SYSTEM`, whose own `USERPROFILE` is not the installing account's.

```
classobjects.com {
	tls C:/caddy/certs/fullchain.pem C:/caddy/certs/classobjects.com.key
	encode gzip

	handle /api/* {
		reverse_proxy 127.0.0.1:8000
	}

	handle /management* {
		root * C:/Users/wnmil/dev/classobjects-ccwebdb/frontend/dist
		try_files {path} /management.html
		file_server
	}

	handle {
		root * C:/Users/wnmil/dev/classobjects-ccwebdb/frontend/dist
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

The two `tls` lines must name the key file Caddy is really to use, and that
is the copy to lock down with `icacls` (step 1). Keep one copy of the key in
`C:\caddy\certs`, not several under different names.

Check the file. It says `Valid configuration`, and that it is skipping
automatic certificates because the files given are loaded:

```console
C:\caddy\caddy.exe validate --config C:\caddy\Caddyfile
```

### Caddy as a service

Caddy runs as a Windows service by itself -- no wrapper program -- so that
it starts with the machine and keeps running when no one is signed in.
Caddy started by hand in a window (`caddy run`) holds ports 80 and 443 until
that window closes; stop it with Ctrl+C first, and check that none is left:

```console
tasklist /fi "imagename eq caddy.exe"
```

It should say `No tasks are running`. Then, from a cmd window run **as
administrator** (Start, type `cmd`, right-click *Command Prompt*, *Run as
administrator*; its title starts `Administrator:`), create the service --
copy the line rather than retype it:

```console
sc.exe create caddy start= auto binPath= "C:\caddy\caddy.exe run --config C:\caddy\Caddyfile"
```

It prints `[SC] CreateService SUCCESS`. `sc` wants a space after each `=`
and nothing else there: `start= auto`, exactly; a misspelt value is refused
as an `Invalid start= field`. Start it and check it:

```console
sc.exe start caddy
sc query caddy
curl -s -o nul -w "%{http_code}\n" https://classobjects.com/
```

`RUNNING` and `200`. Caddy reads the Caddyfile only when it starts, so after
changing it or the certificate files, restart the service:

```console
sc.exe stop caddy
sc.exe start caddy
```

**When the service will not start or cannot be created:**

| What it says | What it means |
|---|---|
| `StartService FAILED 2: The system cannot find the file specified` | the program named in the service is not there. `sc qc caddy` shows the path it runs (`BINARY_PATH_NAME`); put `caddy.exe` there, or point the service at where it is with `sc.exe config caddy binPath= "..."` |
| `CreateService FAILED 5: Access is denied` | in a window that is not elevated, that is all it means: open one as administrator. In an elevated one, the name is still held by a service deleted while something had it open -- see below |
| `CreateService FAILED 1073: The specified service already exists` | `sc.exe delete caddy` first |
| `FAILED 1072: The specified service has been marked for deletion` | the same held name, said plainly -- see below |
| copying `caddy.exe` into `C:\caddy` fails with `Access is denied` | the old `caddy.exe` was deleted while still open, and its name is held -- see below |

**Close the Services window before deleting or re-creating a service.** A
service deleted with `sc.exe delete` while the Services window (or anything
else) has it open is only *marked* for deletion: it lingers, and its name
cannot be used again. A `caddy.exe` deleted while a process still has it open
is held the same way -- gone from `dir`, but its name refused to anything
that tries to create it. Both clear when whatever holds them lets go; a
restart always does it. Until then, a service under another name
(`caddy-web`, say) running a copy of `caddy.exe` from another folder keeps
the site up.

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

The server's three parts are controlled on their own, never with the
`ccweb_` start-up and shut-down scripts. Caddy and the API start with the
machine; PostgreSQL does not, because its cluster is shared with the
development machine through OneDrive and only one of them may run it
([runtime-operations.md](runtime-operations.md), *One database, two
computers*). After a reboot, with the database not running elsewhere, start
it and then restart the API (the steps below). From an
administrator cmd, stop the site, API first:

```console
schtasks /end /tn "ccwebdb api"
sc stop caddy
sc stop ccwebdb-postgres
```

Start it again, database first:

```console
sc start ccwebdb-postgres
schtasks /run /tn "ccwebdb api"
sc start caddy
```

`sc query ccwebdb-postgres` and `sc query caddy` say `RUNNING` when they
are up; step 9 checks the whole site.

- **The certificate expires on 2027-04-30**, though the purchase covers
  five years. A public certificate may now last no more than 200 days, so a
  multi-year purchase is a subscription: DigiCert issues a new certificate
  within it, at no charge, before each one ends. Ahead of each expiry,
  reissue in DigiCert's portal (the request made in step 1 can be used
  again, so the key stays where it is), replace `classobjects.com.pem` and
  `fullchain.pem` in `C:\caddy\certs`, and restart the `caddy` service. This comes round
  about twice a year; the date is in the certificate itself
  (`openssl x509 -in classobjects.com.pem -noout -enddate`).
- **An update:** `git pull`, `uv sync`, then a schema release as in
  [system-administration.md](system-administration.md) (*Applying a schema
  release*) when the update brings a migration, then `npm ci` and
  `npm run build` in `frontend`, then restart the API task. Caddy needs no
  restart for a new build.
- **Backups:** the server runs `ccweb_backup.cmd` every day at 20:00
  (`.\scripts\ccweb_backup.cmd /schedule`), into
  `%USERPROFILE%\OneDrive\coins_backup`, which OneDrive carries off the
  machine ([system-administration.md](system-administration.md), *Backing
  up and restoring*). The task runs only while the owner is signed in.
