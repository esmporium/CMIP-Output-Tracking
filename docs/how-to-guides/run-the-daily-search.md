# How to run the daily search

This project runs one job: it searches ESGF for the datasets we are tracking,
records what it found, and emails whoever is on the list
with what has changed since the last time it ran.

Everything about that job lives in two places:

- `scripts/drive-search-and-notifications.py`, which holds **all** the configuration
  (which search, which database, who gets emailed) and is the only entry point;
- the `.env` file at the root of the repository, which holds the **secrets**
  (how to reach the mail server) and is never committed.

## Run it once, by hand

```sh
make run-search-and-notifications
```

That will:

1. create and migrate `dbs/001_db.sqlite` if it is not there yet;
2. search ESGF for the requirement in the driver script;
3. save every result into the database, and record the search itself;
4. print a summary of what changed since the last complete run;
5. email that same summary to the recipients in the driver script.

The first run has nothing to compare against,
so it reports everything it found as "added".
Every run after that reports the difference.

## Set up the mail server

Copy `.env.example` to `.env` and fill it in. There are two sensible shapes.

**An external provider** (Gmail, Office 365, a departmental server).
Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME` and `SMTP_PASSWORD`,
and leave `SMTP_STARTTLS=true`.
For Gmail the password must be an
[app password](https://support.google.com/accounts/answer/185833),
not the account password.
Note that many University Office 365 tenants have basic SMTP authentication
turned off, in which case this route will not work without asking IT to enable it.

**A relay on the server itself** (`postfix`, `msmtp`, or whatever the box already
has). Set `SMTP_HOST=localhost`, `SMTP_PORT=25` and `SMTP_STARTTLS=false`,
and leave the username and password blank:
a relay that only listens on the loopback interface authenticates nothing,
because nothing off the machine can reach it.
This is usually the least trouble on a shared server, and there is no password
to leak.

!!! warning
    `.env` is in `.gitignore`, and it must stay that way.
    If a password does get committed, rotate it rather than trying to rewrite history.

## Run it every day

`cron` is the right tool here: the job is a single command, it runs once a day,
and it does not need to be told anything about when it last ran
(the database knows that). A `systemd` timer would also work and is a bit more
robust — it logs to the journal, and `Persistent=true` catches up a run missed
because the server was down — but it is more to set up and cannot be installed
without root.

`scripts/run-daily.sh` is the wrapper to point cron at.
It exists because cron is not a login shell:
it starts with a nearly empty `PATH`, reads no profile,
and has no useful working directory.
The wrapper puts all of that back, logs each run to `logs/`,
and exits non-zero if the job fails.

Install it with `crontab -e`:

```cron
MAILTO=your.address@example.com
15 7 * * * /full/path/to/CMIP-Output-Tracking/scripts/run-daily.sh
```

That runs at 07:15 every morning. Pick a time that suits you;
there is nothing special about overnight, since ESGF publishes continuously.

`MAILTO` is worth setting. It is cron's own notification,
and it is the only one left if the job dies *before* it can send its own email —
if the machine cannot reach ESGF, say, or if the mail settings are wrong.

### Check that it is working

```sh
# What the last runs did
tail -50 logs/search-and-notifications-$(date +%Y-%m).log

# What cron thinks it is scheduled to do
crontab -l
```

The job also emails you on a morning when nothing has changed.
That is deliberate: without it, "nothing changed" and "the server is on fire"
both look like an empty inbox.
Turn it off by setting `EMAIL_WHEN_NOTHING_CHANGED = False`
in the driver script once the job has been boring for long enough to be trusted.

The logs are not rotated. They are small — a few kilobytes a day — and there is
one file per month, so this is fine for a long while. Delete old ones when it
starts to bother you.

## Change what is searched for

Edit `REQUIREMENT` in `scripts/drive-search-and-notifications.py`.

The requirement's **name** is the key its whole history hangs off:
it is what the database files the search under,
and what the summary is read back out by.
esmporium deliberately refuses to let a search change meaning without changing name,
so if you edit the query you must either rename the requirement
or bump `DB_VERSION_ID` to start a fresh database.
If you do not, the next run will stop with a `RequirementNameInUseError`
rather than quietly comparing apples with oranges.

Adding a facet value is the easy case — each facet is a tuple:

```python
variable=("tas", "pr"),
```

This still changes the requirement's meaning, so it still needs a new name.

## Where the data lives

`dbs/001_db.sqlite` holds every dataset the search has ever returned
and every run it has ever done. It is deliberately **not** committed:
it is local state, it changes every day, and the copy on the server
is the one that matters.

The `001` is our own version, bumped by hand when a search changes enough
that its history is no longer comparable. It is not the schema version —
esmporium migrates the schema itself, on every run.
Bumping it leaves the old database on disk, so nothing is thrown away.
