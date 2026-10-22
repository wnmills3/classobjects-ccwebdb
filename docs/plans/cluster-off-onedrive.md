# Moving the live cluster off OneDrive

*Written 2026-10-22.* A temporary plan: deleted when this branch merges.

The collection's cluster lives in `%USERPROFILE%\OneDrive\coins_db\pgdata`,
with `.pgdata` a junction to it on both computers, so the
`ccwebdb-postgres` service on classobjects has to start by hand and the
site is down after every reboot until someone starts it. This moves the
cluster onto classobjects' own disk, sets the service to start with the
machine, and leaves the development computer without a live cluster: it
reaches the collection through https://classobjects.com, and its tests use
its own test cluster as they always have. The nightly backup keeps going
to `OneDrive\coins_backup`.

Everything below runs on **classobjects** in an **administrator cmd**
unless it says otherwise. Each block's output is checked before the next.
Nothing deletes the OneDrive copy until step 8, so steps 1 to 6 can be
undone with *Going back*, at the end.

## 1. Count the collection and back it up

The count and sum are what step 5 compares against; write them down.

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
..\scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where split_at is null and deleted_at is null;"
```

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
.\scripts\ccweb_backup.cmd
```

It ends `backup proved`.

## 2. Stop the API and PostgreSQL

```console
schtasks /end /tn "ccwebdb api"
sc stop ccwebdb-postgres
```

Then check that it has stopped and left no `postmaster.pid` (run it again
after a few seconds if it still says `STOP_PENDING`):

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
sc query ccwebdb-postgres | findstr STATE
if exist .pgdata\postmaster.pid echo STOP: postmaster.pid is still there
```

The first line says `STOPPED`; the second prints nothing.

## 3. Copy the cluster onto the local disk

First check that `.pgdata` is the junction to OneDrive and that the copy's
folder is free:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
dir /al
if exist .pgdata.moving echo STOP: .pgdata.moving already exists
```

`dir /al` lists `<JUNCTION>  .pgdata [C:\Users\wnmil\OneDrive\coins_db\pgdata]`;
the `if` prints nothing. Then copy:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
robocopy "%USERPROFILE%\OneDrive\coins_db\pgdata" .pgdata.moving /E /COPY:DAT /DCOPY:T /R:3 /W:5 /NP /NFL /NDL
```

In its summary the `Failed` column is `0` for both `Dirs` and `Files`.
Then compare the two: this lists what a second copy would still have to do,
and it should be nothing -- `Copied`, `Mismatch`, `FAILED` and `Extras` all
`0`:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
robocopy "%USERPROFILE%\OneDrive\coins_db\pgdata" .pgdata.moving /E /L /NP /NFL /NDL /NJH
```

## 4. Put the copy where the service looks

`rmdir` without `/s` removes the junction and leaves the folder it points
to untouched; on a real folder with files in it, it refuses.

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
rmdir .pgdata
ren .pgdata.moving .pgdata
dir /al
dir .pgdata\PG_VERSION
```

`dir /al` no longer lists `.pgdata`; `PG_VERSION` is there.

## 5. Start it with the machine, and start it now

```console
sc config ccwebdb-postgres start= auto
sc start ccwebdb-postgres
```

Then check it answers and holds the same collection as in step 1:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
call ..\scripts\ccweb_env.cmd
"%PGBIN%\pg_isready.exe" -h localhost -p 5432
..\scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where split_at is null and deleted_at is null;"
```

`accepting connections`, and the same count and sum. Then the API:

```console
schtasks /run /tn "ccwebdb api"
```

```console
curl -s -f -o nul http://127.0.0.1:8000/docs
if errorlevel 1 echo The API is not answering: read logs\backend.log
```

## 6. Prove it

A backup restores the export into a scratch database on this cluster and
drops it, which on OneDrive used to leave an orphaned folder in `base`:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
.\scripts\ccweb_backup.cmd
dir /b .pgdata\base
```

`backup proved`, and `base` holds only `1`, `4`, `5` and `16390`.

Then restart classobjects and **do not sign in**. Claude checks from
outside that the site answers with the database up (a request for an
unknown photograph returns 404, not 500), which shows the service started
with the machine before anyone signed in.

## 7. The development computer

On the development computer, in a plain cmd, remove its junction to the
OneDrive cluster, after checking it is one:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
dir /al
rmdir .pgdata
```

`dir /al` lists `<JUNCTION>  .pgdata [...\OneDrive\coins_db\pgdata]`.
Afterwards `ccweb_claude.cmd` reports `no cluster at ...\.pgdata`, and
`ccweb_check.cmd` runs the tests on the test cluster as before.

## 8. Retire the OneDrive copy

After a few nightly backups have proved, on classobjects:

```console
cd /d %USERPROFILE%\OneDrive\coins_db
ren pgdata pgdata-retired
```

and some days later delete `pgdata-retired`. OneDrive then asks, on each
computer's desktop, to confirm removing that many files: confirm it.

## Going back

Before step 8, from an administrator cmd on classobjects:

```console
schtasks /end /tn "ccwebdb api"
sc stop ccwebdb-postgres
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
ren .pgdata .pgdata.moving
mklink /J .pgdata %USERPROFILE%\OneDrive\coins_db\pgdata
sc config ccwebdb-postgres start= demand
sc start ccwebdb-postgres
schtasks /run /tn "ccwebdb api"
```

Anything entered on the site since step 5 is then only in `.pgdata.moving`
and the backups.
