# Deploying on a Windows server

*Written 2026-10-14; made general 2026-10-20.*

How to run the system on a Windows server the public can reach over
`https://` at a domain of your own. The development machine runs everything
from `scripts\ccweb_startup.cmd` with Vite serving the frontend; a server
runs none of that. Every command here is cmd; none is PowerShell. For an
Ubuntu server, see [deployment-linux.md](deployment-linux.md).

**The example installation.** Where a value has to be shown, it is the
owner's: a server named `classobjects` serving `https://classobjects.com`,
with a DigiCert certificate, the repository in
`%USERPROFILE%\dev\classobjects-ccwebdb`, and backups into a OneDrive
folder. Put your own domain, certificate files and folders where those
appear; the settings they belong to are named alongside.

**On the server, do not use `ccweb_startup.cmd` or `ccweb_shutdown.cmd`
once step 6 is done.** The shutdown script kills whatever listens on the
API's port -- there, the live API -- and stops PostgreSQL, taking the site
down. The startup script finds the API and PostgreSQL already running and
leaves them, but starts Vite as well, which the server has no use for. The
server's own controls are under *Keeping it running*.

## The shape

```
browser --https:443--> Caddy --+-- /api/...   --> uvicorn CCWEB_API_HOST:CCWEB_API_PORT --> PostgreSQL (DATABASE_URL)
                               +-- everything else --> CCWEB_SITE_ROOT (the built frontend)
```

- **Caddy** is the only thing that listens to the outside. It holds the TLS
  certificate, sends `http://` visitors to `https://`, serves the built
  frontend, and passes `/api/...` to the API. It is open source, one
  `caddy.exe`, and configured by one short text file, rendered from
  `deploy\Caddyfile` in the repository. IIS can do the same job, but needs
  the URL Rewrite and Application Request Routing modules and a `.pfx` in
  the Windows certificate store; nginx's Windows build is not meant for
  production.
- **The API** (FastAPI under uvicorn) listens on `CCWEB_API_HOST`
  (`127.0.0.1` unless `.env` says otherwise) only. The application never
  reads the certificate: TLS ends at Caddy.
- **The frontend** is built once into static files (`frontend\dist`): no
  Vite and no Node run on the server after the build. The shop is
  `index.html`; the console is `management.html`.
- **Photographs** are served by the API (`/api/images/...`, by content hash),
  so the `/api` rule covers them. Their bytes live under `MEDIA_ROOT`, never
  in the database.
- Browser and API share one origin, so no cross-origin request is ever made
  and `CORS_ORIGINS` needs no change.

## 1. The certificate

A server needs the site's certificate, the intermediate certificate that
signed it, and the private key. A certificate authority's download holds the
first two, and often the root as well, which browsers already have; it never
holds the key. The example's DigiCert zip:

| File | In the zip | Needed by Caddy |
|---|---|---|
| `classobjects.com.pem` -- the site's certificate (`classobjects.com` and `www.classobjects.com`) | yes | yes |
| `GeoTrustTLSRSACAG1.pem` -- the intermediate that signed it | yes | yes, sent with the certificate |
| `DigiCertGlobalRootG2.pem` -- the root | yes | no: browsers already hold it |
| **the private key** | **no** | **yes** |

The private key was made wherever the certificate request (CSR) was made,
and never leaves there on its own. Two ways to get one onto the server.

**Reissue from the server (simplest when unsure where the key is).** Make a
new key and request on the server, then use *Reissue* on the order in the
certificate authority's portal and paste in the request; DigiCert reissues
at no charge and sends a new zip, and the key never travels. The example's
commands follow: put your own domain where `classobjects.com` appears, and
your organisation's country, state, town and name in `-subj`.

```console
mkdir C:\caddy\certs
cd /d C:\caddy\certs
openssl req -new -newkey rsa:2048 -nodes -keyout classobjects.com.key -out classobjects.com.csr -subj "/C=US/ST=Connecticut/L=Coventry/O=ClassObjects LLC/CN=classobjects.com"
type classobjects.com.csr
```

`openssl` is in the conda environment
(`%USERPROFILE%\miniforge3\envs\ccwebdb\Library\bin\openssl.exe`), so run
these in a window where `conda activate ccwebdb` has been run. Ask for the
`www.` name as an additional name in the portal.

**Or export the key from the machine that made the request.** If the
request was made with IIS or `certreq`, complete it on that machine, export
the certificate *with its private key* as a `.pfx`
(`certlm.msc` -> Personal -> Certificates -> the certificate -> All Tasks ->
Export), carry the `.pfx` across, and take the key out of it (the example's
file names):

```console
cd /d C:\caddy\certs
openssl pkcs12 -in classobjects.com.pfx -nocerts -nodes -out classobjects.com.key
```

Either way, Caddy wants the certificate and the intermediate in one file,
site certificate first (the example's files):

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

Keep every certificate and key file out of the repository. The certificate
and key files are named to Caddy by two settings, `CCWEB_TLS_CERT` and
`CCWEB_TLS_KEY` (step 4).

## 2. Install the system

On the server, follow [environment-setup-windows.md](environment-setup-windows.md) steps 1
to 7: Miniforge, the `ccwebdb` conda environment, uv, Node and PostgreSQL in
the environment, the database cluster, `.env`, and the dependencies.

PostgreSQL on a server has to run apart from any console, so it becomes a
Windows service. A service cannot start while another server holds the
cluster -- one started by `ccweb_startup.cmd` or by hand in
environment-setup-windows.md step 5. First stop any that is running (it
prints `server stopped`, or `Is server running?` when none was):

```console
conda activate ccwebdb
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
pg_ctl -D .pgdata -m fast stop
```

Then register the cluster as a service once, from an administrator cmd with
`conda activate ccwebdb` run in it. `-S auto` starts it with the machine:

```console
pg_ctl register -N ccwebdb-postgres -D "%USERPROFILE%\dev\classobjects-ccwebdb\.pgdata" -S auto
```

(The example installation registers it with `-S demand` instead and starts
it by hand, because its cluster folder is shared with the development
computer through OneDrive and only one of them may run it:
[runtime-operations-windows.md](runtime-operations-windows.md), *One
database, two computers*.) Then start it:

```console
sc start ccwebdb-postgres
pg_isready -h localhost -p 5432
```

`pg_isready` says `accepting connections` within a few seconds; run it again
if it says `no response` at first. (The cluster folder and port shown are
the defaults; with `CCWEB_DB_CLUSTER_DIR` or another port in `DATABASE_URL`,
use those.) `sc qc ccwebdb-postgres` shows what was registered. The service
runs as `LocalSystem`, which can read a cluster in the user's folder, and
runs `pg_ctl.exe` from the conda environment by its full path: re-creating
the `ccwebdb` environment breaks it until the service is registered again
(`pg_ctl unregister -N ccwebdb-postgres`, then the `register` line above).

## 3. Move the collection across

Skip this step if the collection is already on the server: restored from
the backup workbook by environment-setup-windows.md step 8 (*This collection, on a
new machine*), it is complete.

Restore the current collection; never run `app.seed` on the server. On the
development machine, with nothing writing to the database, dump it and copy
its photographs to a folder you can carry across -- the example uses
`D:\to_server`:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
if not exist D:\to_server mkdir D:\to_server
pg_dump -Fc -f D:\to_server\ccwebdb_to_server.dump ccwebdb
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

The server's `.env` (template: `.env.example`, which lists every setting
with its default; [system-administration.md](system-administration.md),
*Settings*, says what each one does):

| Setting | On the server |
|---|---|
| `JWT_SECRET` | a new value (`python -c "import secrets; print(secrets.token_hex(32))"`), never the development one. The API will not start without one |
| `DATABASE_URL` | the server's database, with a password that is not `devpassword`: change it first, as below |
| `MEDIA_ROOT` | the folder the photographs were copied to, as a full path |
| `SALES_TAX_RATE`, `SALES_TAX_INCLUDES_SHIPPING` | as on the development machine |
| `SHOP_OPEN` | `false` until opening day: visitors who are not signed in see a coming-soon page, and the API refuses them the catalog and registration. Set it to `true` and restart the API to open the shop |
| `CCWEB_SITE_DOMAIN` | the domain, without `https://` or `www.` (the example: `classobjects.com`) |
| `CCWEB_TLS_CERT`, `CCWEB_TLS_KEY` | the full paths of `fullchain.pem` and the key from step 1 (the example: `C:\caddy\certs\fullchain.pem`, `C:\caddy\certs\classobjects.com.key`) |
| `CCWEB_SITE_ROOT` | unset: the repository's `frontend\dist`, which Caddy, running as `SYSTEM`, can read |
| `VITE_DIGICERT_SEAL_ID` | DigiCert's seal for the certificate, the eight characters after `DigiCertClickID_` in DigiCert's seal code; unset, the shop shows no seal. Read when the frontend is built (step 5) |
| `CCWEB_BACKUP_DIR`, `CCWEB_BACKUP_TIME` | where and when the daily backup runs (*Keeping it running*); the example: its OneDrive folder, 20:00 |
| `TIME_ZONE` | unset keeps the server's own zone; set it (`America/New_York`) if the server's clock runs on another zone than the business's |

`FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_PASSWORD` are read only by `app.seed`,
which is never run on the server: the restored database already holds its
accounts. Sign in with one of them, and change the password of any account
still using a development password.

### The database passwords

environment-setup-windows.md step 5 gives both database accounts the password
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
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
uv run alembic current
```

An API already running read the old `.env` when it started: restart it
(*Keeping it running*, below). The backup, `scripts\ccweb_psql.cmd` and the
application read `.env` and need nothing more.

## 5. Build the frontend

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\frontend
npm ci
npm run build
```

This writes `frontend\dist`: `index.html`, `management.html`,
`favicon-lock.png` and `assets\`. The build reads `VITE_DIGICERT_SEAL_ID`
from `.env`, so set it (step 4) before building, or the shop has no seal;
the build then ends with a `WARNING` naming it.
Build again after every update.

## 6. Run the API at start-up

The API is started by a scheduled task that runs at boot, whether or not
anyone is signed in. `python -m uvicorn` rather than `uvicorn.exe`: Smart
App Control blocks unsigned `.exe` wrappers on some machines.

Save as `C:\ccwebdb-run\api.cmd` (outside the repository, so an update
never overwrites it). `%USERPROFILE%` is the folder of the account the task
runs as, which is the account that installed the system in step 2. It takes
the API's address and the log folder from the settings, as the
repository's own scripts do:

```console
@echo off
cd /d "%USERPROFILE%\dev\classobjects-ccwebdb\backend"
call ..\scripts\ccweb_env.cmd || exit /b 2
call ..\scripts\ccweb_settings.cmd || exit /b 2
"%ENVDIR%\python.exe" -m uvicorn app.main:app --host %CCWEB_API_HOST% --port %CCWEB_API_PORT% --proxy-headers --forwarded-allow-ips %CCWEB_API_HOST% >> "%CCWEB_LOG_DIR%\backend.log" 2>&1
```

`--proxy-headers` with `--forwarded-allow-ips` set to the API's own address
makes the API see the visitor's address and `https` from Caddy's headers,
and trust those headers from Caddy alone. (An `api.cmd` written before
these settings existed, naming `127.0.0.1` and `8000` itself, behaves the
same while the settings keep those defaults.) Register it from an
administrator cmd, signed in as the account that installed the system,
which can read the repository and `MEDIA_ROOT`. The command asks for that
account's password, so it is given alone -- pasted with others, the next
line would be taken as the password:

```console
schtasks /create /tn "ccwebdb api" /sc onstart /ru "%USERDOMAIN%\%USERNAME%" /rp * /tr "C:\ccwebdb-run\api.cmd"
```

Then start it and check it answers (at the default address; use
`CCWEB_API_HOST` and `CCWEB_API_PORT` if `.env` sets them):

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
`NoDefaultCurrentDirectoryInExePath` set, as the example's development
computer has, a bare `caddy` is not found even in `C:\caddy`.

Then write its configuration, `C:\caddy\Caddyfile`, from the repository's
template `deploy\Caddyfile` and the settings of step 4. The Windows service
has no environment of its own, so the values are filled in now, with full
paths -- Caddy runs as `SYSTEM`, whose own `USERPROFILE` is not the
installing account's -- and forward slashes, which Caddy wants on Windows
too. In a window where `conda activate ccwebdb` has been run:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
python -m app.runtime_settings --render ..\deploy\Caddyfile > C:\caddy\Caddyfile.new
```

It goes to `Caddyfile.new` first, so that a refused setting -- an empty
`CCWEB_SITE_DOMAIN`, say, which it names -- never empties the file a
running Caddy reads. Render it again after changing any of those settings,
check it and put it in place as below, then restart the service. For the
example installation the result, comments left out, is:

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
C:\caddy\caddy.exe validate --config C:\caddy\Caddyfile.new
```

Only when it does, put it in place:

```console
move /y C:\caddy\Caddyfile.new C:\caddy\Caddyfile
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
as an `Invalid start= field`. Start it and check it (the example's domain):

```console
sc.exe start caddy
sc query caddy
curl -s -o nul -w "%{http_code}\n" https://classobjects.com/
```

`RUNNING` and `200`. Caddy reads the Caddyfile only when it starts, so after
rendering it again or changing the certificate files, restart the service:

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

- **DNS:** `A` records for the domain and its `www.` name (the example:
  `classobjects.com`, `www.classobjects.com`) at the server's public
  address.
- **Router:** forward ports 80 and 443 to the server, and nothing else.
- **Windows firewall**, from an administrator cmd:

  ```console
  netsh advfirewall firewall add rule name="Caddy HTTP" dir=in action=allow protocol=TCP localport=80
  netsh advfirewall firewall add rule name="Caddy HTTPS" dir=in action=allow protocol=TCP localport=443
  ```

  Do not open the API's port or PostgreSQL's: the API and the database
  answer only on the server itself.

## 9. Check it

With the example's domain:

```console
curl -sI https://classobjects.com/
curl -s -o nul -w "%{http_code}\n" "https://classobjects.com/api/catalog?limit=1"
curl -sI http://classobjects.com/
```

Expect `200`, `200`, and a redirect to `https://`. Then in a browser: the
shop loads with the padlock favicon, the certificate's seal appears in the
footer if `VITE_DIGICERT_SEAL_ID` is set (it shows only on the domain the
certificate names), and the domain's `/management` signs in.

## Keeping it running

The server's three parts are controlled on their own, never with the
`ccweb_` start-up and shut-down scripts. All three start with the machine
(the example's PostgreSQL excepted: see step 2). From an administrator
cmd, stop the site, API first:

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

- **The certificate expires** on the date it carries
  (`openssl x509 -in classobjects.com.pem -noout -enddate`, with your
  certificate's file name). A public certificate may now last no more than
  200 days, so a multi-year purchase is a subscription: the authority issues
  a new certificate within it, at no charge, before each one ends -- about
  twice a year. Ahead of each expiry, reissue in the portal (the request
  made in step 1 can be used again, so the key stays where it is), replace
  the certificate and `fullchain.pem` in `C:\caddy\certs`, and restart the
  `caddy` service. The example's current certificate expires on
  2027-04-30.
- **An update:** `git pull`, `uv sync`, then a schema release as in
  [system-administration.md](system-administration.md) (*Applying a schema
  release*) when the update brings a migration, then `npm ci` and
  `npm run build` in `frontend`, then restart the API task. Caddy needs no
  restart for a new build.
- **Backups:** schedule the daily backup once with
  `.\scripts\ccweb_backup.cmd /schedule`. It runs every day at
  `CCWEB_BACKUP_TIME`, into `CCWEB_BACKUP_DIR`, whether or not anyone is
  signed in ([system-administration.md](system-administration.md), *Backing
  up and restoring*). Put that folder somewhere that leaves the machine: the
  example's is a OneDrive folder, whose files OneDrive carries off at the
  owner's next sign-in.
