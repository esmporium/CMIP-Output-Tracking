# CMIP Output Tracking

Tracking the availability of CMIP data retrieved from ESGF.

This repository runs one job: it searches ESGF for the datasets we are tracking,
records what it found, and emails whoever is on the list with what has changed
since the last time it ran.

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

- `scripts/drive-search-and-notifications.py`, which holds **all** the
  configuration (which search, which database, who gets emailed) and is the only
  entry point;
- the `.env` file at the root of the repository, which holds the **secrets** (how
  to reach the mail server) and is never committed.

### Run it once, by hand

```sh
make run-search-and-notifications
```

That will:

1. create and migrate `dbs/001_db.sqlite` if it is not there yet;
2. search ESGF for the requirement in the driver script;
3. save every result into the database, and record the search itself;
4. print a summary of what changed since the last complete run;
5. email that same summary to the recipients in the driver script.

The first run has nothing to compare against, so it reports everything it found
as "added". Every run after that reports the difference.

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

Install it with `crontab -e`:

```cron
MAILTO=your.address@example.com
15 7 * * * /full/path/to/CMIP-Output-Tracking/scripts/run-daily.sh
```

That runs at 07:15 every morning. Pick a time that suits you; there is nothing
special about overnight, since ESGF publishes continuously.

`MAILTO` is worth setting. It is cron's own notification, and it is the only one
left if the job dies *before* it can send its own email — if the machine cannot
reach ESGF, say, or if the mail settings are wrong.

### Check that it is working

```sh
# What the last runs did
tail -50 logs/search-and-notifications-$(date +%Y-%m).log

# What cron thinks it is scheduled to do
crontab -l
```

The job also emails you on a morning when nothing has changed. That is
deliberate: without it, "nothing changed" and "the server is on fire" both look
like an empty inbox. Turn it off by setting `EMAIL_WHEN_NOTHING_CHANGED = False`
in the driver script once the job has been boring for long enough to be trusted.

The logs are not rotated. They are small — a few kilobytes a day — and there is
one file per month, so this is fine for a long while. Delete old ones when it
starts to bother you.

### Change what is searched for

Edit `REQUIREMENT` in `scripts/drive-search-and-notifications.py`.

The requirement's **name** is the key its whole history hangs off: it is what the
database files the search under, and what the summary is read back out by.
esmporium deliberately refuses to let a search change meaning without changing
name, so if you edit the query you must either rename the requirement or bump
`DB_VERSION_ID` to start a fresh database. If you do not, the next run will stop
with a `RequirementNameInUseError` rather than quietly comparing apples with
oranges.

Adding a facet value is the easy case — each facet is a tuple:

```python
variable=("tas", "pr"),
```

This still changes the requirement's meaning, so it still needs a new name.

### Change what the email looks like

This is the part of the job we expect to keep changing, and it is why
`src/local/summary_formatting.py` lives in this repository rather than in
esmporium.

The division of labour is: **esmporium knows the database, we know the layout.**
`summarise_search` reads a window of runs and hands back a `SearchSummary`;
everything in `summary_formatting.py` is a pure function of that object. So the
email can be rewritten as often as you like without touching the library, without
a release and without a changelog.

The workflow is:

1. edit `src/local/summary_formatting.py`;
2. run `make test`. The eight files under
   `tests/regression/test_summary_rendering/` are checked-in copies of the email,
   so the failure *is* the before-and-after diff. Read it;
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
functions we import, and every field of a `SearchSummary` the renderer reads --
and fails by name the moment one of them moves. Without it, the first symptom of a
rename upstream is a cron job that sends no email, which looks exactly like a
morning on which nothing changed.

When a name there does legitimately change upstream, fix the renderer and that
file in the same commit. Loosening the tripwire to make a bump pass is worse than
not having one, because it still looks like one.

### Where the data lives

`dbs/001_db.sqlite` holds every dataset the search has ever returned and every run
it has ever done. It is deliberately **not** committed: it is local state, it
changes every day, and the copy on the server is the one that matters.

The `001` is our own version, bumped by hand when a search changes enough that its
history is no longer comparable. It is not the schema version — esmporium migrates
the schema itself, on every run.
Bumping it leaves the old database on disk, so nothing is thrown away.

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
scripts/drive-search-and-notifications.py  the job, and all of its configuration
scripts/run-daily.sh                       cron wrapper around the above
src/local/                                 the local package, shared helpers
src/local/notifications.py                 the mechanics of sending an email
src/local/summary_formatting.py            how the summary is laid out for reading
tests/conftest.py                          builders for summaries, so tests need no database
tests/regression/                          checked-in copies of the email itself
tests/unit/                                the invariants, and the esmporium contract
dbs/                                       SQLite databases (not committed)
logs/                                      output from the cron job (not committed)
```

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
