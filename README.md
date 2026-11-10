# CMIP Output Tracking

Tracking the availability of CMIP data retrieved from ESGF.

This repository runs one job, in two halves. It searches ESGF for the datasets we
are tracking, emails whoever is on the list with what has changed since the last
time it ran, and then downloads whatever is new and emails what arrived.

Both halves are one process and one cron entry, deliberately. The download plans
from what the search just recorded, so "search first" is a fact about the code
rather than a convention in the crontab — which matters more the more questions we
track. The search report is emailed *before* the download starts, so a long
download cannot make the morning mail late.

## Status

- development: the project is actively being worked on

## Installation

We do all our environment management using [uv](https://docs.astral.sh/uv/).
To get started, you will need to make sure that uv is installed
([instructions here](https://docs.astral.sh/uv/getting-started/installation/),
we found that using uv's standalone installer was best on a Mac).

To create the virtual environment, run

```sh
uv sync
uv run pre-commit install
```

These steps are also captured in the `Makefile` so if you want a single
command, you can instead simply run `make virtual-enviroment`.

Having installed your virtual environment, you can now run commands in your
virtual environment using

```sh
uv run <command>
```

For example, to run Python within the virtual environment, run

```sh
uv run python
```

As another example, to run a notebook server, run

```sh
uv run jupyter lab
```

## Running the daily search

Everything about the job lives in two places:

- `scripts/drive-daily.py`, which holds **all** the configuration — every question
  we track, which endpoints to ask, what to download, who gets emailed — and is the
  only entry point;
- the `.env` file at the root of the repository, which holds the **secrets** (how
  to reach the mail server) and the **machine-specific** settings (where downloaded
  data goes), and is never committed.

One consequence worth stating: because the search and the download are handed the
*same* `TrackedRequirement`, they cannot disagree about what the question is.
Searching for `tas` while downloading `pr` would look like a working morning email
and a directory quietly filling with the wrong files, and there is no longer any
way to express it.

### Run it once, by hand

```sh
make run-daily
```

That will:

1. create and migrate `dbs/002_db.sqlite` if it is not there yet;
2. search ESGF for every requirement in the driver script, at every endpoint;
3. save every result into the database, and record each search itself;
4. for the questions which ask about where their runs came from, **walk back up the
   parent chain** to the control they branched from, searching for the ancestors we
   do not already hold;
5. work out what there is to download — a database read, no network;
6. **email** what changed since the last complete run, what the walk found, and what
   is about to be fetched;
7. download it, checking each file's published size and checksum as it lands;
8. **email** what arrived.

Step 4 is where it is because it is the only place it fits. A walk writes rows for
datasets the search never returned, and both the plan in step 5 and the email in
step 6 have to be able to see them. It costs a handful of searches rather than a
download, so it does not make the morning mail late.

The first run has nothing to compare against, so it reports everything it found
as "added". Every run after that reports the difference.

Either half can be run on its own, which is what you want when retrying a download
that failed without waiting for tomorrow:

```sh
make run-search-only       # search, walk the ancestry, email; download nothing
make run-downloads-only    # download what the last search found; do not search
make downloads-dry-run     # say what would be fetched, fetch nothing, email nothing
```

There is also `--skip-parents`, for re-running a search without paying for the walk
again:

```sh
uv run python scripts/drive-daily.py --search-only --skip-parents
```

Note what it costs you: a download planned afterwards does not know about ancestors
nobody has walked to yet, so it will not fetch them.

### Why there are two emails and not one

The search report goes out the instant the search finishes, carrying a table of what
is about to be fetched — one row per dataset version, with its file count and size,
and the running total in the heading:

```text
TO DOWNLOAD (CMIP7 tas: 1 dataset, 3 files, 149.7 MB)
  id   model         variant    grid  version   files      size
  108  CanESM6-0-MR  r26i1p1f1  g150  20261103      3  149.7 MB
  into /data/cmip7-scratch
```

It sits inside its own question's section, before that question's model-support
table, because that table reads as the section's closing line and something
actionable underneath it would be read last or not at all.

Both emails are subject-lined `cmip-tracking: ...` — the job's name, not any one
question's. There are four questions now, in two different scientific registers, and
a subject built from one of their stems would read as a mail about the pattern effect
on a morning when only the Gregory search moved. The constant is `SUBJECT_STEM`;
**it is what a mail filter matches on**, so changing it is a thing to go and fix in
Gmail too.

> It was `pattern-effect` until 2026-11-10. **If a Gmail filter is matching on that,
> it has stopped catching these emails** and wants pointing at `cmip-tracking`
> instead.

The `id` is the same number the `ADDED` table shows, which is the point of it being
there: a dataset that turned up overnight can be followed straight into the download
table. And a total on its own is not much use — *"21.2 GB is coming"* does not say
whether that is one model you were waiting for or forty you already had.

The download report follows whenever the fetching is done, which on a normal morning
is seconds later and on the first run is hours.

That split is doing a specific job. One email after everything would be hostage to
the download, and on a bulk morning would arrive long after you wanted the search
news. Two emails where the first says nothing about downloading would leave a
download that starts and never finishes completely invisible. This way the first
mail always arrives on time and always says what is coming, so a gap between the
two is something you can notice.

The second email is skipped when nothing was fetched and nothing failed
(`EMAIL_WHEN_NOTHING_DOWNLOADED`), because the first one has already said there was
nothing to do.

## The ancestry half

Some questions are not answered by what their own query matches. A Gregory
regression is of the *anomaly* against the control, so the control is not a nice
extra — it is half the measurement. A `TrackedRequirement` says so by carrying a
`lineage`:

```python
lineage=Ancestors(until=UNTIL_PICONTROL, role="control"),
```

and that is the whole declaration. Where it stops is read back off the requirement
rather than passed separately (`local.parents.until_experiments`), so the thing the
walk is told and the thing the requirement asks for cannot drift apart.

A search still searches for `abrupt-4xCO2` and nothing else — a lineage does not
widen a query. Climbing the chain is a separate, explicit call, and it reports like
this:

```text
CMIP7 gregory -- ancestry
  Walked back from 24 dataset version(s): 24 link(s) written, 1 hop(s), 6 search(es) fired
  Chains stopped at (6):
    model          experiment  variant
    ACCESS-ESM1-6  piControl   r1i1p1f1
    CanESM5-1      piControl   r1i1p2f1
    ...
```

That block rides in the question's own section, above its download plan, because the
plan is partly a consequence of it: *"we walked back to six controls"*, and then
*"and here is the 35 GB that costs"*.

### What to actually read in that block

The counts are the easy part. The sections worth the space are the ones that only
appear when something went sideways — and **none of them is a failure**, which is
exactly why they have to be visible:

- **"Parent release decided by us"** — the parent dataset was published more than
  once and a rule of ours picked which release to use. It is the only figure in
  either email that rests on a judgement we made rather than on something the
  archive published. Note that the releases passed over are often all *newer* than
  the child, which reads as a run branching from data that did not exist yet; a
  version string is a publication date, not a run date.
- **"Ancestor does not publish the variable"** — the control exists, the link is
  written, and that model is short one of the four fluxes. The walk succeeded; the
  science did not.
- **"Could not be chosen, so not climbed through"** — nothing could tell two
  candidate parents apart, so the walk did not climb, so that ancestor is missing.

A walk which raises says so and carries on. It raises once at the end with every
failed chain on it, and it commits once per hop — so the links it managed are
already in the database and the next run picks up where it left off. The failure is
reported and the download goes ahead, for the same reason one endpoint being down
does not cost us the other eight.

### Downloading what you walked back to

**This is the part that is easy to get wrong, and it has a tripwire.** A download is
planned from a *query*, and the Gregory search's query names `abrupt-4xCO2`. So
without a second query the controls would be searched for, linked, reported in the
morning email — and never fetched, with nothing raised and nothing logged. Hence
`download_query`:

```python
query=Query(..., experiment=("abrupt-4xCO2",)),
download_query=Query(..., experiment=("abrupt-4xCO2", "piControl")),
```

`TrackedRequirement.__post_init__` refuses a question which has a lineage and
`download=True` but no `download_query`, so the failure mode is a `ValueError` at
startup rather than data you do not have.

One thing to know about what ends up in the database. The walk's search for a parent
can only name the simulation and the variables its children need — not the frequency
or the branded variable — so it ingests every frequency of those four variables for
each control it finds: about 45 dataset versions and 3000 file rows where we download
24. That is rows, not bytes; the download query is what keeps it to the monthly
`tavg-*` ones. If `download_query` ever loses its `reporting_interval` or
`processing_id`, the dry run says 646 GB instead of 57 GB, which is the number to
watch for.

## The download half

Where the search finds out what exists, this fetches the bytes. It runs in the same
process, straight after the search report is emailed, and can also be run on its own:

```sh
make run-downloads-only     # fetch, record, report, email
make downloads-dry-run      # say what it would fetch, and fetch nothing
```

It reports like this:

```text
pattern-effect-tas-mon-historical-g2 · CMIP7 tas
  0 file(s): 0 fetched, 0 already here, 0 failed
  0 B moved into /data/cmip7-scratch

pattern-effect-sst-mon-historical-g2 · CMIP7 tos
  270 file(s): 270 fetched, 0 already here, 0 failed
  18.4 GB moved into /data/cmip7-scratch

FETCHED (43 datasets, 270 files, 18.4 GB)
  id   model          variant    grid  version   files      size  from
  201  ACCESS-ESM1-6  r10i1p1f1  g147  20261009      6  408.6 MB  crd-esgf-drc.ec.gc.ca
  ...
```

Titled by the **requirement**, not just the project: two questions now share CMIP7,
so the project alone no longer says which one asked for the bytes. The quiet one is
still listed rather than left out, because an absent block cannot be told from a
block that fell over.

The `In the database` figures at the end are for the whole database, not per
question — they answer "is anything stuck anywhere", which is not a per-question
thing.

Both tables count **dataset versions, not files**, because a file is an arbitrary
slice of one — CMIP7 publishes ACCESS-ESM1-6 as eighteen files per variant and
CanESM6-0-MR as three, which says something about the publisher and nothing about
the science. Failures are the exception and are listed per file, because there the
particular file is the point. A dataset that half-arrived therefore appears in both
tables, which is correct and is the thing you would want to notice.

Two details that only show up when they matter: a `from` column reading
`mixed (2 nodes)` when one dataset's files came from several mirrors, and a line
saying how many files needed a fallback mirror at all. One node being down says
nothing; all of them being down says a lot.

`run-downloads-only` will:

1. refuse to start unless `DOWNLOAD_ROOT` is set (see below);
2. migrate the database, which is where the download tables come from;
3. work out which files the search has found that we have not already got;
4. fetch them, checking each one's published size and checksum as it lands;
5. record every file, and every attempt against every data node;
6. print what arrived, and email it.

### "Automatically download anything new" needs no extra machinery

This is worth understanding, because it is the whole design and it is less than it
sounds. There is no diff, no watermark, no "since last time" to keep track of.

A file is left out of the plan when its download row says it is done **and** it is
still on disk at the published size. So "download everything that satisfies the
requirement" is already incremental: the first run fetches all 325 files, and every
run after it fetches only what has appeared since. A new model or a new variant
shows up in the morning search, and the download half of the same run picks it up
minutes later, because it is simply a file we have not got.

Both halves of that check matter. The row alone would miss a file deleted behind our
back; the disk alone would mean re-verifying everything we already know about, every
night. Together they mean a deleted file is noticed and fetched again, rather than
being assumed present forever.

Being interrupted is therefore safe, and so is running it twice. Each file is
recorded in its own transaction as it finishes, so a run killed on day two keeps
everything from day one, and a part-downloaded file is resumed rather than restarted.

### Which questions it fetches

`download=True` on a `TrackedRequirement` in `scripts/drive-daily.py`. Today that is
**all three CMIP7 questions** — `tas`, `tos` and the Gregory search — and neither
CMIP6 one.

| question | variable | branded as | experiment | how much, at east | |
| --- | --- | --- | --- | --- | --- |
| CMIP7 `tas` | `tas` | `tavg-h2m-hxy-u` | `historical` | 47 datasets, 325 files, 22.7 GB | fetched |
| CMIP7 `tos` | `tos` | `tavg-u-hxy-sea` | `historical` | 43 datasets, 270 files, 18.4 GB | fetched |
| CMIP7 gregory | `tas`, `rsdt`, `rsut`, `rlut` | `tavg-h2m-hxy-u`, `tavg-u-hxy-u` | `abrupt-4xCO2` **and its `piControl` parents** | 48 datasets, 934 files, 56.6 GB | fetched |
| CMIP6 `tas` | `tas` | `Amon` | `historical` | ~1140 dataset versions | watched |

(The first two measured against the live archive on 2026-11-03, the Gregory question
on 2026-11-10.)

Every CMIP7 question downloads into the same `DOWNLOAD_ROOT`. Nothing has to be done
to keep them apart: esmporium's layout puts a file under its own dataset id, and the
variable and experiment are part of that, so they never collide and
`make data-node-health` covers them all at once.

**The Gregory question is more than half of that total, and most of it is the
controls.** 21.2 GB is `abrupt-4xCO2` and the other ~35 GB is the `piControl` runs
the walk goes and finds — a control is a long run, and four variables of a long run
is a lot of monthly data. One model, `UKCM2a-0-HH`, is on its own about 39 GB of the
61 GB those four variables come to across both experiments; it is high-resolution and
there is nothing wrong with it, but it is the thing to look at first if the disk
becomes a problem.

**`tos` is the lighter of the two, which was not the expectation going in.** Each
file is within a per-cent of a `tas` file's size; there are simply fewer of them.

Searching and downloading are not the same appetite: we watch CMIP6 across seven
endpoints to see what exists, and we have no intention of pulling all of it onto
this disk.

**There is also a trap in turning CMIP6 on, and it is not the obvious one.** Five of
its seven endpoints are Solr, which publishes no per-file URLs on a dataset document
— only its `type=File` documents carry them, which is a second request that is not
yet wired into esmporium's tracked search. But `east` and `west` are ESGF-NG, whose
STAC assets *are* their files. So of CMIP6's ~1140 dataset versions, roughly 460
have files recorded and the other ~680 have none.

A download plans from the files. So setting `download=True` on CMIP6 would quietly
fetch the 460 and say nothing whatsoever about the rest — a partial download that
reads as a complete one, which is worse than one that refuses. That is what is
waiting on the esmporium side, and it is why this stays off rather than being
"CMIP6 downloads do not work".

### Credentials

Downloads are anonymous, and that is enough for both CMIP7 questions today.

esmporium fetches with an `httpx` client that the caller supplies, and we supply
none, so it builds an anonymous one. A node which refuses an anonymous request
answers 401, which is reported as `needs credentials` and **not** retried — asking
again, the same way, gets the same answer. If that shows up in the report, the fix
is an ESGF X.509 certificate passed as `httpx.Client(cert=...)`; that is the
documented seam, and nothing else has to change to use it.

### Which data nodes are actually serving us

The download records every HTTP attempt it makes -- which node, which URL, how it
answered, how many bytes moved, how long it took -- because `run_downloads` passes
esmporium's `record_data_node_calls` as its observer. That is a row per *attempt*,
not per file, so a file fetched on the third try from the second mirror left four of
them.

Nothing in the daily emails reads them yet. This prints them instead:

```sh
make data-node-health                      # everything we know
make data-node-health ARGS="--days 7"      # how they are behaving lately
```

```text
DATA NODE HEALTH (all history)
  node                   attempts   ok  failed   ok%   served  MB/s
  esgf.ceda.ac.uk              27   22       5   81%   1.3 GB   4.4
      5 refused us (403, 429 or 503)
  esgf.nci.org.au             302  300       2   99%  18.2 GB  18.5
      2 timed out
  crd-esgf-drc.ec.gc.ca         9    0       0     -      0 B     -
      9 had nothing there (404), which is not the node's fault

  3 nodes, 338 attempts, 19.5 GB served
  25m 3s spent fetching, summed over every attempt
```

Worst first, rather than alphabetically like every other table here: the reason to
open this is to find out whether a node has gone bad, and in a list of nine the one
that has should not be eighth. Ties -- which on a clean run is every row -- break on
how much we asked of the node.

Speed is per node and time is not. How long one node took is only interesting divided
by what it served, which is what `MB/s` already is, so a per-node time column would
be the same fact twice -- while the *total* is the figure somebody actually asks for.

**There are two honest answers to "how long did it take", and they can be days
apart.** The one at the foot is transfer time, summed over every attempt including the
failed ones -- each measured from just before the request to just after the checksum,
so it leaves out the retry backoff and everything that is not a fetch. It is
wall-clock time only because esmporium's download loop is sequential on purpose
today; if that is ever parallelised it becomes worker-time, and the wording has to
change with it.

The other answer is the span from the first attempt to the last, and over a window
covering several nightly runs that is mostly the idle days between them. So the span
is only printed once it differs from the fetching time by more than a minute, and
when it is, it is given as two dates with a note rather than as a single duration that
would get quoted as the download time:

```text
  3h 53m spent fetching, summed over every attempt
  first attempt 2026-10-30 21:00 UTC, last 2026-11-02 21:03 UTC (3d apart, which includes any time between runs)
```

The time comes from a second read of the same table over the same window, because
esmporium's `DataNodeHealth` keeps `median_throughput_mb_per_s` and `total_bytes` but
throws the time away -- so `local.node_health.read_fetch_time` goes to
`DataNodeCallRecord.seconds` itself. The outcome classification stays upstream.

It is read-only and deliberately does **not** migrate the database, unlike every
other entry point, so it is safe to point at a copy of the production one. It has to
run on the machine that downloaded, though: `dbs/` is git-ignored, so a clone has the
table and none of the rows.

Three things to know before quoting the numbers at anybody.

**`failed` is narrower than it sounds, and `ok%` is ours.** A 404 means a route we
recorded has gone away, a 401 means we did not present credentials, and a size
mismatch means the *index* published the wrong size: in all three the node answered
us correctly, and esmporium excludes them from its health on purpose. They are still
counted, on the explanation lines under each row, which is why `attempts` is not
`ok + failed`.

The percentage is computed in `local.node_health.ok_rate` rather than read off
`DataNodeHealth.failure_rate`, because that field does not currently mean what its
docstring says: `n_judged` leaves successes out, and `_summarise` then divides by it,
so `failure_rate` can only ever come out as exactly `0.0` or `1.0`. A node that
succeeded nineteen times in twenty reports `1.0`. `n_success` and `n_judged` are each
sound on their own, so the rate is built from those two instead, and
`tests/unit/test_node_health_formatting.py` pins the upstream behaviour it relies on
so that a fix fails a test rather than silently halving every number.

**A node absent from the table is one we have never fetched from, not a healthy one.**
"We have no evidence" stays distinguishable from "we tried and it failed" throughout,
which is also why a node whose every attempt was a 404 shows `-` for `ok%` instead of
0% or 100%.

**These are the data nodes, not the search endpoints.** An index node and a data node
are different machines doing different jobs, and being good at one says nothing about
being good at the other -- a search is judged on latency, a download on throughput.
Nothing here says whether `search.east.esgf.io` is answering; the per-endpoint table
in the morning email is what covers that.

### Several files at once, and why there is a cap per node

A run fetches `DOWNLOAD_WORKERS` files at a time (8), but **no more than
`PER_DATA_NODE` of them against any one data node** (4). Both are constants at the
top of `scripts/drive-daily.py`.

The cap is the part that matters, and it is not about our bandwidth. Most files have
exactly one node serving them, so an uncapped pool of workers becomes a pile of
simultaneous requests against a single host — which is how a node starts refusing
us, and these are other people's institutions' servers. A node that refuses records
`blocked` attempts, which `make data-node-health` reports; that is the number to
look at before raising anything here.

Two properties worth knowing:

- a worker whose file's best node is already at the cap **waits for a slot** rather
  than being sent to a node ranked lower. So the cap paces a run and never silently
  changes which node served us.
- a file that failed for a reason that might not recur gets one more go before the
  run ends. Files whose every route is gone, or needs credentials, are not retried —
  nothing about them will have changed.

`DOWNLOAD_WORKERS` is a ceiling, not a promise. CMIP7 `tos` is spread over four data
nodes (`crd-esgf-drc.ec.gc.ca` holds 25 of its 43 dataset versions, `esgf.nci.org.au`
10, `ceda.ac.uk` 7, `esg-earthlab.iap.ac.cn` 1), so eight workers can keep every node
busy; a question concentrated on one host would move four at a time whatever the
number says.

**This is also why `tos` was a good first case for it.** Every `tos` dataset version
is published to exactly one node, so there is nothing for node *ranking* to choose
between — but the requirement as a whole is spread across four, so there is plenty
for node *parallelism* to do.

### The health is recorded but not yet used to choose a node

Worth knowing, because it looks from the above as though it must be. `run_downloads`
calls esmporium's `download` without a `selector`, so route order is whatever ESGF
published rather than what we have learnt. esmporium ships
`build_refreshing_data_node_health_selector`, which ranks reliability first and
throughput second and re-reads every few hours so that a multi-day run does not keep
using Monday's opinion on Thursday. Turning it on is a one-line change, and the
feedback loop it needs -- the observer -- is already wired up.

### Which Python it runs on

`.python-version` pins the interpreter to 3.13, and unlike most projects **that
file is committed** (`.gitignore` has an explicit exception for it).

This matters, so it is worth knowing why rather than discovering it the hard way.
Left unpinned, `uv` picks whatever interpreter the machine happens to offer, which
is not the same answer on two machines. On a machine offering CPython 3.14,
`uv sync` fails: the `pydantic-core` in our lock file has no 3.14 wheel, so uv
falls back to building it from source, and that build needs a PyO3 which does not
support 3.14 yet. The failure arrives after several minutes of downloading and
compiling Rust, which makes it look like a much deeper problem than it is.

`uv` will download a managed CPython 3.13 by itself if the machine has not got
one; this needs no root.

To move to a newer Python later, the interpreter and the lock file have to move
together: bump `.python-version` and run `uv lock --upgrade-package pydantic-core`.

### Set up the mail server

Copy `.env.example` to `.env` and fill it in. There are two sensible shapes.

**An external provider** (Gmail, Office 365, a departmental server). Set
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME` and `SMTP_PASSWORD`, and leave
`SMTP_STARTTLS=true`. For Gmail the password must be an
[app password](https://support.google.com/accounts/answer/185833), not the account
password. Note that many University Office 365 tenants have basic SMTP
authentication turned off, in which case this route will not work without asking
IT to enable it.

**A relay on the server itself** (`postfix`, `msmtp`, or whatever the box already
has). Set `SMTP_HOST=localhost`, `SMTP_PORT=25` and `SMTP_STARTTLS=false`, and
leave the username and password blank: a relay that only listens on the loopback
interface authenticates nothing, because nothing off the machine can reach it.
This is usually the least trouble on a shared server, and there is no password to
leak.

> **Warning**
> `.env` is in `.gitignore`, and it must stay that way. If a password does get
> committed, rotate it rather than trying to rewrite history.

### Say where downloaded data goes

The same `.env` file carries one more setting, and the job refuses to start without
it whenever anything is set to download:

```sh
DOWNLOAD_ROOT=/data/azehrung/cmip-data
```

It is not a secret. It is in `.env` because it is **machine-specific** in exactly
the way the mail settings are: a laptop and a shared server do not have the same
data volume, so a path committed to the repository would be wrong on one of them.

There is deliberately no default. The only one available would be somewhere inside
this repository, and the first you would know of it is a `git status` full of
netCDF, or a full disk on whichever volume the repository happens to sit on.
`DownloadSettings.from_env()` raises `MissingDownloadSettingError` naming the
variable, which is the message you want when you are reading it out of a cron log.

Check there is room before the first run. It is ~98 GB today, across the three
CMIP7 questions — 22.7 GB of `tas`, 18.4 GB of `tos` and 56.6 GB for the Gregory
search and the controls it walks back to:

```sh
df -h /data
make downloads-dry-run
```

`downloads-dry-run` plans the whole download and fetches none of it, so it is also
how to check a widened search before it is turned loose on a shared volume.

### Run it every day

`cron` is the right tool here: the job is a single command, it runs once a day,
and it does not need to be told anything about when it last ran (the database
knows that). A `systemd` timer would also work and is a bit more robust — it logs
to the journal, and `Persistent=true` catches up a run missed because the server
was down — but it is more to set up and cannot be installed without root.

`scripts/run-daily.sh` is the wrapper to point cron at. It exists because cron is
not a login shell: it starts with a nearly empty `PATH`, reads no profile, and has
no useful working directory. The wrapper puts all of that back, logs each run to
`logs/`, and exits non-zero if the job fails.

#### What time is 7am in Melbourne?

This is the one part worth doing slowly, because Melbourne is not on one offset all
year and cron does not know that:

| | local time | in UTC |
| --- | --- | --- |
| 7am **AEST** (April–October) | 07:00 | **21:00 the previous day** |
| 7am **AEDT** (October–April) | 07:00 | **20:00 the previous day** |

So there is no single UTC time that is 7am in Melbourne. A job fixed at 21:00 UTC
is 7am through winter and 8am through summer; a job fixed at 20:00 UTC is the
other way round. Either is fine if you only care that the mail is waiting when you
start work, and neither needs touching twice a year.

Some crons can do the conversion for you, if you name the zone instead of the
offset: `CRON_TZ=Australia/Melbourne` applies to the entries after it and tracks
daylight saving by itself. RHEL's `cronie` has this; the `cron` on our server
(`cmip6-crunching`, Debian) does **not**, so we use the fixed UTC time. Check
before relying on it, and check the binary rather than the man page, because a
server with no man pages installed looks exactly like a cron without the feature:

```sh
grep -ac CRON_TZ /usr/sbin/cron   # 0 means it is genuinely not supported
```

Note that `TZ` is not an alternative: Debian's cron passes it to the job's
environment but ignores it when deciding when to fire.

#### Install it

Everything in this repository reaches the server by `git pull`. Two things never
do, and they are the whole of the setup: the `.env` file, which is git-ignored on
purpose, and the crontab, which lives in cron's own storage rather than in any
file you can commit.

```sh
cd /path/to/CMIP-Output-Tracking
git pull

# 1. The secrets, and where downloaded data goes. This is the step that is easy to
#    forget, and the job cannot start without it: `DownloadSettings.from_env()`
#    raises before anything reaches a network, and `EmailSettings.from_env()`
#    before anything reaches the mail server.
mkdir -p /data/azehrung/cmip-data
cp .env.example .env
$EDITOR .env

# 2. The search half only, by hand. This really does email the recipients, which
#    is the point: it tests the mail path, not just the search. It also writes the
#    file records the download plans from, which nothing else does.
make run-search-only

# 3. How big the first download is, and whether the disk can take it.
df -h /data
make downloads-dry-run

# 4. The initial download. ~98 GB over several hours the first time (22.7 GB of
#    `tas`, 18.4 GB of `tos`, and 56.6 GB for the Gregory search including the
#    `piControl` runs it walks back to); a handful of files, in seconds, every
#    time after. Eight files move at once, capped at four per data node. Run it
#    under `tmux` -- it is resumable, so losing the ssh session is survivable, but
#    there is no reason to find out.
tmux new -s download
make run-downloads-only          # detach with ctrl-b then d

# 5. Only once both halves work, schedule the whole thing.
crontab -e
```

`crontab -e` opens an editor (`EDITOR=nano crontab -e` if you want nano). On a
server which has never had a crontab you will find it either empty or full of
commented-out explanation; either way nothing in it needs changing. The lines go
at the bottom. This is what is installed on `cmip6-crunching`, which keeps its
clock in UTC:

```cron
MAILTO=azehrung@student.unimelb.edu.au

# Daily CMIP search, email, download, email. 21:00 UTC = 7am AEST
0 21 * * * /data/azehrung/CMIP-Output-Tracking/scripts/run-daily.sh
```

**One line, and it stays one line however many questions we track.** There used to
be two entries, with the download an hour behind the search so that it planned from
rows the search had already written. That ordering now lives in the script, where it
cannot be got wrong by editing a crontab, and adding a third requirement does not
mean finding a third slot for it.

Saving installs it; `crontab: installing new crontab` is the confirmation, and
`crontab -l` reads it back.

`MAILTO` is cron's own notification, and it is the only one left if the job dies
*before* it can send its own email — if the server cannot reach ESGF, say, or if
the mail settings are wrong. Without it a broken job is perfectly silent.

#### The first run on a new server starts from scratch

`dbs/` is not committed, so a server which has never run the job has no history to
compare against and will report everything it finds as "added". That is correct and
it only happens once: that run *is* the baseline, and every run after it reports
the difference. Do not read the first morning's email as news.

#### Keeping up with a moving esmporium

`make run-daily` calls `uv run`, which syncs the environment
from `uv.lock` before running anything. So the whole of deploying a new esmporium
pin — or a new email layout — is:

```sh
git pull
```

The next scheduled run picks it up. There is no separate `uv sync` step to
remember, and nothing to restart. What this does mean is that a commit which
breaks the job breaks tomorrow's email, so it is worth having `make test` pass
before pushing: `tests/unit/test_esmporium_contract.py` is there precisely so that
a rename upstream fails by name rather than as an email that never arrives.

### Check that it is working

```sh
# What the last runs did -- both halves log to the one file
tail -80 logs/search-and-notifications-$(date +%Y-%m).log

# What cron thinks it is scheduled to do
crontab -l

# How much has actually landed
du -sh /data/azehrung/cmip-data
```

The **search report** arrives on a morning when nothing has changed. That is
deliberate: without it, "nothing changed" and "the server is on fire" both look
like an empty inbox. Turn it off by setting `EMAIL_WHEN_NOTHING_CHANGED = False`
in the driver script once the job has been boring for long enough to be trusted.

The **download report** does the opposite: `EMAIL_WHEN_NOTHING_DOWNLOADED` is
`False`, so a morning with nothing fetched and nothing failed is silent. It does not
need to speak, because the search report went out minutes earlier and already said
`nothing new to fetch`. It does still mail you when something arrived, and when
something failed.

One asymmetry worth knowing, because it is a deliberate choice and not an
oversight: if the **search report cannot be emailed**, the failure is logged, cron's
`MAILTO` hears about it, and the job carries on and downloads anyway. A mail server
being down is not a reason to miss a day of data, and the download records
everything it does in the database whether or not anybody was told at the time.

The logs are not rotated. They are small — a few kilobytes a day — and there is
one file per month, so this is fine for a long while. Delete old ones when it
starts to bother you.

### Change what is searched for

Edit the `query` of the relevant `TrackedRequirement` in
`scripts/drive-daily.py`. That is also, by construction, what gets downloaded: the
search and the download are handed the same object.

The requirement's **name** is the key its whole history hangs off: it is what the
database files the search under, and what the summary is read back out by.
esmporium deliberately refuses to let a search change meaning without changing
name, so if you edit the query you must either change that requirement's stem
(`TAS_STEM`, `SST_STEM` or `GREGORY_STEM`) or bump `DB_VERSION_ID` to start a fresh
database. If you do not, the next run will stop with a `RequirementNameInUseError`
rather than quietly comparing apples with oranges.

**Reach for the stem, not the database version, and know why.** A new stem restarts
that one question's search history and nothing else: the dataset, file and download
rows are keyed by the data rather than by the question, so everything already on disk
stays accounted for. A bumped `DB_VERSION_ID` restarts all of it — including the
`Download` rows, which are the only record of what we hold. `plan_downloads` needs
the row *and* the file on disk and treats a file with no row as one to fetch again,
so bumping the version on a server that has downloaded anything means downloading it
all over again.

That is the whole reason every stem here carries a `-g2`. Pointing at the esmporium
commit that gave `Leaf` a `lineage` changed every stored requirement's hash, because
`requirement_hash` covers the whole tree — so all eleven existing searches had to be
renamed or the job would have stopped working. Search history reset, download
bookkeeping kept, and `DB_VERSION_ID` still `002`.

Run `make downloads-dry-run` after widening anything. A query that matches more
than you expected is a search that reports a longer table and a download that
quietly fills a disk; the dry run is how you see the second one coming.

Adding a facet value is the easy case — each facet is a tuple:

```python
variable=("tas", "pr"),
```

This still changes the requirement's meaning, so it still needs a new name.

### Add a whole new question

Adding another `TrackedRequirement` to `REQUIREMENTS`, which is a cheaper change
than editing an existing one: a new name is a new history, so nothing has to be
reset and `DB_VERSION_ID` stays where it is. The search loop, the download loop,
both emails and both reports all iterate that list, so nothing else has to learn
about it. CMIP7 `tos` was added this way on 2026-11-03, and the Gregory question on
2026-11-10.

Five things to get right, all of which have a test to catch them:

1. **Its own stem.** A new question means a new `*_STEM` constant. Sharing one with
   an existing question would file both under the same requirement name, and
   esmporium would refuse the second as a name already in use.
2. **A distinct `label`.** The download plan is matched back to its search results
   by label, so two requirements sharing one quietly drops a download block out of
   the morning email, with nothing raised and nothing logged. Both CMIP7 questions
   would naturally have been labelled `"CMIP7"`; they are `"CMIP7 tas"` and
   `"CMIP7 tos"` because of this.
3. **The right branded-variable suffix.** CMIP7 `processing_id` values are easy to
   get nearly right. Monthly `tos` is published both as `tavg-u-hxy-sea` (the
   gridded field, which is what we want) and `tavg-u-hm-sea` (its zonal mean, which
   is a different quantity under the same variable name). A query naming neither
   fetches both and reports them as one requirement. Check against the live archive
   rather than guessing.

   The Gregory question is the same trap twice over. Monthly `tas` is published as
   `tavg-h2m-hxy-u` *and* as `tminavg-h2m-hxy-u` and `tmaxavg-h2m-hxy-u` — the
   monthly means of the daily minimum and maximum — so naming no suffix returns 10
   `tas` dataset versions where 6 are wanted.
4. **Its own report columns, if it spans more than one of anything.**
   `report_facets` on the requirement. The defaults are model, variant and grid,
   which tell one row from another only while a question asks about one variable in
   one experiment. The Gregory question asks for four variables, so without
   `variable` its `ADDED` table is four identical lines per model, distinguishable
   only by the `id` column — a table that reads as a bug in the search. One knob
   feeds both reports.
5. **A first download done by hand.** See below.

And if the new question carries a `lineage`, a sixth: **a `download_query` that
names where the lineage stops.** See "Downloading what you walked back to" above.
That one is refused at startup rather than merely tested, because the symptom is
otherwise data you simply do not have.

Then `make test`, which will fail in `tests/unit/test_driver_config.py` until the
new requirement's stored names are written down there — that file is the record of
what the server's database already contains, so adding to it is the step that says
"these names are new, and the old ones are untouched".

#### The first run of a new question

A new question's first download is its whole archive — 18.4 GB for `tos`. Do that
one by hand rather than letting cron find it:

```sh
make downloads-dry-run      # how big is it, and can the disk take it
tmux new -s download
make run-downloads-only     # resumable, but there is no reason to find out
```

Leaving it to the scheduled run is safe but makes the download email as late as the
download is long, after the search mail has already gone out saying it was starting.

### Change what the email looks like

This is the part of the job we expect to keep changing, and it is why
`src/local/summary_formatting.py` lives in this repository rather than in
esmporium.

The division of labour is: **esmporium knows the database, we know the layout.**
`summarise_search` reads a window of runs and hands back a `SearchSummary`;
everything in `summary_formatting.py` is a pure function of that object. So the
email can be rewritten as often as you like without touching the library, without
a release and without a changelog.

The same goes for `src/local/download_formatting.py`, which is the download half's
share of it: `esmporium.db.download` hands back a result per file and
`download_summary` hands back the totals, and everything in that module is a pure
function of those. Where it goes slightly further than "layout" is in rendering
esmporium's own `__str__` output itself — a byte count as `21.2 GB` rather than as
`22716867033`, a status as `given up` rather than as `given_up`. Counting upstream,
wording here.

The workflow is:

1. edit `src/local/summary_formatting.py` or
   `src/local/download_formatting.py`;
2. run `make test`. The files under `tests/regression/test_summary_rendering/`,
   `tests/regression/test_report_rendering/` and
   `tests/regression/test_download_rendering/` are checked-in copies of the two
   emails, so the failure *is* the before-and-after diff. Read it;
3. if the new version is the one you want, run `make accept-layout` to rewrite
   those files, and commit them with the change.

Committing a regenerated fixture without reading its diff defeats the only
purpose it has. The point of them is not that the text is right -- we do not know
yet what right looks like -- but that a change to it is impossible to miss.

The handful of tests in `tests/unit/test_summary_formatting.py` are deliberately
few: they pin the invariants a regression diff *cannot* tell you about (that the
tables have no trailing whitespace, that the `id` column keeps rows distinct
however few facets are shown, that a long "what moved" line cannot widen a table).
Everything about what the output *says* is covered by the regression files.

If you find yourself wanting a **field** esmporium does not compute, as opposed to
a different arrangement of the fields it does, that is the signal the change
belongs upstream in esmporium instead. Pulling a layout back later is cheap;
pulling back a field you have been computing here is not.

### Point esmporium at a different commit

esmporium is pinned to an exact commit in `[tool.uv.sources]`. To move it:

```sh
make pin-esmporium REV=<commit sha, branch or tag>
```

That updates `pyproject.toml` and `uv.lock`, re-syncs, and then runs the tests --
which is the whole point of doing it through the target. To go back, discard the
two files and re-sync:

```sh
git checkout pyproject.toml uv.lock && uv sync
```

**Pin to a commit sha, not a branch or a tag.** A tag can be moved onto different
work, and a branch moves by definition, so either one makes `uv.lock` the only
record of what you actually tested against. The target prints the resolved sha
when it finishes; that is the thing to pin.

`tests/unit/test_esmporium_contract.py` is what makes this safe to do often. It
names every piece of esmporium's public surface this repository relies on -- the
functions we import, and every field of a `SearchSummary`, a download result or a
parent walk the renderers read -- and fails by name the moment one of them moves.
Without it, the first symptom of a rename upstream is a cron job that sends no
email, which looks exactly like a morning on which nothing changed.

It cannot catch everything, and it is worth knowing what it misses. A bump which
changes a stored requirement's **fingerprint** rather than a name passes every test
here and then refuses to run against the real database: that is what happened on
2026-11-10, when `Leaf` gained a `lineage`. The symptom is a
`RequirementNameInUseError` per endpoint, reported in the morning email as every
endpoint having failed. The fix is a new generation on every stem -- see "Change
what is searched for" -- and the thing *not* to do is bump `DB_VERSION_ID`, which
also forgets every file already on disk.

When a name there does legitimately change upstream, fix the renderer and that
file in the same commit. Loosening the tripwire to make a bump pass is worse than
not having one, because it still looks like one.

### Where the data lives

`dbs/002_db.sqlite` holds every dataset the search has ever returned, every run it
has ever done, and every file we have downloaded. It is deliberately **not**
committed: it is local state, it changes every day, and the copy on the server is
the one that matters.

The `002` is our own version, bumped by hand when a search changes enough that its
history is no longer comparable. It is not the schema version — esmporium migrates
the schema itself, on every run.
Bumping it leaves the old database on disk, so nothing is thrown away.

**Bumping it now costs more than it used to, and it is almost never what you
want.** The database is what remembers which files we already have — specifically
the `Download` rows, which `plan_downloads` requires *as well as* the file being on
disk before it will skip one. A fresh database has none, so it does not know about
the ~98 GB of netCDF sitting under `DOWNLOAD_ROOT` and the next run re-fetches all
of it.

So if what you actually want is for a search's history to start again — which is the
usual reason to reach for this — **change that question's stem instead**. The stem
resets one question's history and keeps every dataset, file and download row. See
"Change what is searched for". If you really do need to bump the version, either
point `DOWNLOAD_ROOT` somewhere new as well, or expect the first download against
the new database to be the bulk run again.

### Where the downloaded data lives

Under `DOWNLOAD_ROOT`, laid out as:

```text
<dataset id, dots turned into slashes>/v<version>/<filename>
```

which for CMIP7 comes out as:

```text
MIP-DRS7/CMIP7/CMIP/UKNCSP/UKESM1-3-LL/historical/r2i1p1f2/glb/mon/tas/tavg-h2m-hxy-u/g110/v20261020/tas_...nc
MIP-DRS7/CMIP7/CMIP/UKNCSP/UKESM1-3-LL/historical/r2i1p1f2/glb/mon/tos/tavg-u-hxy-sea/g126/v20261020/tos_...nc
MIP-DRS7/CMIP7/CMIP/UKNCSP/UKESM1-3-LL/abrupt-4xCO2/r1i1p1f1/glb/mon/rlut/tavg-u-hxy-u/g110/v20260901/rlut_...nc
MIP-DRS7/CMIP7/CMIP/UKNCSP/UKESM1-3-LL/piControl/r1i1p1f1/glb/mon/rlut/tavg-u-hxy-u/g110/v20261002/rlut_...nc
```

The experiment, the variable and its branding are all in the path, which is why
every CMIP7 question can share one `DOWNLOAD_ROOT` with nothing done to keep them
apart — including the Gregory question's `abrupt-4xCO2` runs and the `piControl`
runs it walks back to, which are the last two lines above.

This is DRS-*shaped* — navigable by eye, sorts the way a climate scientist expects —
but it is **esmporium's own layout and not any project's real DRS**, deliberately.
Reproducing each project's actual DRS needs per-project logic (CMIP5 puts the
variable after the version; Solr publishes no equivalent path at all), and one
layout that works for every project is worth more here than a faithful one that
works for some.

Two consequences worth knowing:

- **Do not reorganise the tree by hand.** It is how a download run recognises what
  it already has. Moving a file means fetching it again.
- `tests/unit/test_esmporium_contract.py` pins the layout as a *path*, not as a
  name, for the same reason: if esmporium ever changes it, we find out from a test
  rather than from a ~98 GB re-download.

## Development

Install and run instructions are the same as the above. The test dependencies
(`pytest`, `pytest-regressions`) live in the `dev` dependency group, which
`uv sync` installs by default; the job itself does not need them, so a server
running only the cron job can install without them.

Before pushing, run the checks:

```sh
make pre-commit
make test
```

The tests need no database, no network and no ESGF: every one of them builds the
summary it renders by hand. The whole suite runs in well under a second, so there
is no reason not to run it.

### Contributing

This is a thin repository. If you would like to contribute, it is best to raise
an issue to discuss what you want to do (without a discussion, we can't guarantee
that any contribution can actually be used).

### Repository structure

The repository is very basic and imposes no structure on you, so you can lay your
Python files, notebooks etc. out in any way you wish. As it stands:

```
scripts/drive-daily.py            the whole job, and all of its configuration
scripts/run-daily.sh              cron wrapper around the above
scripts/data-node-health.py       a read-only report on how the data nodes behave
src/local/                        the local package, shared helpers
src/local/endpoints.py            a tracked requirement, and asking one endpoint
src/local/database.py             opening and migrating the database
src/local/notifications.py        the mechanics of sending an email
src/local/downloads.py            where downloads go, and what came of one
src/local/parents.py              walking back to the control a run branched from
src/local/summary_formatting.py   how the search report is laid out
src/local/download_formatting.py  how a download run is laid out
src/local/parent_formatting.py    how a parent walk is laid out
src/local/node_health.py          how the data node report is laid out
tests/conftest.py                 builders for summaries and download runs, so no
                                  test needs a database or a network
tests/regression/                 checked-in copies of both emails
tests/unit/                       the invariants, and the esmporium contract
tests/unit/test_driver_config.py  what the one job we really run is pointed at
tests/unit/test_download_pacing.py   that the worker settings reach esmporium
dbs/                              SQLite databases (not committed)
logs/                             output from the cron job (not committed)
```

Downloaded data does **not** live in here. It goes wherever `DOWNLOAD_ROOT` points,
which on a server is a data volume and not this directory.

We do have a basic `Makefile` which captures key commands in one place (for more
thoughts on why this makes sense, see
[general principles: automation](https://gitlab.com/znicholls/mullet-rse/-/blob/main/book/general-principles/automation.md)).
For an introduction to `make`, see
[this introduction from Software Carpentry](https://swcarpentry.github.io/make-novice/).
Run `make help` to see what is available.

### Tools

In this repository, we use the following tools:

- git for version-control (for more on version control, see
  [general principles: version control](https://gitlab.com/znicholls/mullet-rse/-/blob/main/book/theory/version-control.md))
    - for these purposes, git is a great version-control system so we don't
      complicate things any further. For an introduction to Git, see
      [this introduction from Software Carpentry](http://swcarpentry.github.io/git-novice/).
- [uv](https://docs.astral.sh/uv/) for environment management
  (for more on environment management, see
  [general principles: environment management](https://gitlab.com/znicholls/mullet-rse/-/blob/main/book/theory/environment-management.md))
    - there are lots of environment management systems.
      uv works well in our experience.
    - we track the `uv.lock` file so that the environment
      is completely reproducible on other machines or by other people
      (e.g. if you want a colleague to take a look at what you've done)
- [pre-commit](https://pre-commit.com/) with some very basic settings to get some
  easy wins in terms of maintenance, specifically:
    - code formatting with [ruff](https://docs.astral.sh/ruff/formatter/)
    - basic file checks (removing unneeded whitespace, not committing large
      files etc.)
    - (for more thoughts on the usefulness of pre-commit, see
      [general principles: automation](https://gitlab.com/znicholls/mullet-rse/-/blob/main/book/general-principles/automation.md)
    - track your notebooks using
    [jupytext](https://jupytext.readthedocs.io/en/latest/index.html)
    (for more thoughts on the usefulness of Jupytext, see
    [tips and tricks: Jupytext](https://gitlab.com/znicholls/mullet-rse/-/blob/main/book/tips-and-tricks/managing-notebooks-jupytext.md))
        - this avoids nasty merge conflicts and incomprehensible diffs

## Original template

This project was generated from this template:
[basic python repository](https://gitlab.com/openscm/copier-basic-python-repository).
[copier](https://copier.readthedocs.io/en/stable/) is used to manage and
distribute this template.
