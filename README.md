# 📚 Library Management System

**Simple underneath. Exceptional on the surface.**

A complete, production-ready, web-based library system: a beautiful public
catalogue and member portal, and a fast, barcode-first staff area where
everyday work is **SCAN → CONFIRM → DONE**.

![Circulation desk](docs/screenshots/circulation.png)

---

## Contents

1. [Project overview](#project-overview)
2. [Features](#features)
3. [Technology stack](#technology-stack)
4. [Architecture](#architecture)
5. [Database setup](#database-setup)
6. [Environment variables](#environment-variables)
7. [Running locally](#running-locally)
8. [Creating the admin account](#creating-the-admin-account)
9. [Adding books](#adding-books)
10. [Using a barcode scanner](#using-a-barcode-scanner)
11. [Borrowing books](#borrowing-books)
12. [Returning books](#returning-books)
13. [Reservations](#reservations)
14. [Fees](#fees)
15. [Email configuration](#email-configuration)
16. [Production deployment](#production-deployment)
17. [Backup](#backup)
18. [Testing](#testing)
19. [Troubleshooting](#troubleshooting)

---

## Project overview

| Who | What they get |
|---|---|
| **Members** | Search the catalogue, see live availability, reserve from home, see loans with a "4 days left / Due tomorrow / 2 days overdue" countdown, renew with one tap, see fees, get email reminders, carry a digital library card. Works beautifully on phones. |
| **Assistants** | Circulation desk, Quick Scan, returns, reservations, member look-up. |
| **Librarians** | Everything above plus books & copies, members, fees and payments, inventory mode, reports. |
| **Administrators** | Everything plus settings, staff accounts, audit log, backups. |

Public visitors never see who has a book; members only ever see their own data.

| | |
|---|---|
| ![Home](docs/screenshots/home.png) | ![Book page](docs/screenshots/book.png) |
| **Home** — "Find your next story." | **Book page** — availability, Shelf Finder, Book DNA |
| ![Member dashboard](docs/screenshots/member-mobile.png) | ![Library card](docs/screenshots/library-card.png) |
| **Member dashboard** (mobile) | **Digital library card** (scannable Code 128) |
| ![Staff dashboard](docs/screenshots/dashboard.png) | ![Return](docs/screenshots/return.png) |
| **Staff dashboard** — real insights | **Return** — everything calculated |
| ![Quick scan](docs/screenshots/quick-scan-dark.png) | ![Search](docs/screenshots/search.png) |
| **Quick Scan** in dark mode — hold-shelf routing | **Search** — partial, forgiving |

## Features

**Catalogue & discovery** — forgiving search (partial words, accents, ISBN with or
without hyphens, copy barcodes, "did you mean…?"), instant suggestions with
keyboard navigation, filters (availability, author, category, year, language),
availability states that never rely on colour alone (🟢 Available · 🟡 Reserved ·
🔴 Borrowed · ⚪ Unavailable), tasteful generated covers when no image exists,
Shelf Finder ("Fiction → Shelf A → Row 04"), Book DNA, "You might also like".

**Member portal** — dashboard with greeting, current loans, reservations, fees
and library status; *Your Library Shelf*; *Continue your reading*; *Quiet
recommendations*; one-tap renew (blocked automatically if someone is waiting);
reservation queue position and estimated availability; notification centre;
printable digital library card with a scannable barcode; light/dark mode.

**Circulation (barcode-first)** — scan card → scan book → **Enter** to confirm.
Every rule is checked instantly and explained in plain words (account status,
overdue books, unpaid fees, borrowing limit, copy state, holds, reference-only,
duplicate titles). Returns calculate borrowed/due/returned dates, days overdue
and the final fee, then say *where the book goes next* — back to its shelf, or
onto the hold shelf for the next member in the queue.

**Quick Scan** — a full-screen station for USB scanners: library cards load a
member, borrowed copies are returned, available copies are lent after Enter.
Big status panel, sound feedback, `Esc` to reset, no mouse needed.

**Reservations** — strict FIFO queue; available books are held immediately
and appear on the staff *pull list*; returned copies go to the first eligible
member automatically; uncollected holds expire and pass to the next member;
optional expiry for long waits.

**Fees** — configurable late fee per day, grace period and maximum; *estimated*
fee while a book is out and *final* fee on return; replacement fees for lost
books; partial payments, "mark all as paid", waivers with reasons. Online
payments can be plugged in later (see [Fees](#fees)).

**Staff tools** — dashboard ("What is happening in my library?") with real
insights, a *Today* screen, barcode book registration with duplicate
protection and ISBN auto-fill (Open Library), multiple copies per title,
copy-level editing, printable barcode labels, inventory mode, reports with CSV
export, announcements, audit log, settings, staff accounts, backups.

**Built-in safety** — Argon2 password hashing, secure HttpOnly cookies, CSRF
protection, login rate limiting, server-side role checks on every action,
strict Content-Security-Policy (no inline scripts or styles), HSTS, audit log,
first-run setup token, CSV formula-injection protection, uploaded images
re-encoded, sensitive links never stored in email logs.

## Technology stack

| Layer | Choice | Why |
|---|---|---|
| Language / framework | **Python 3.12 · Django 5.2 LTS** | Mature, secure defaults (password hashing, sessions, CSRF, SQL-injection-safe ORM, auto-escaping templates), migrations built in, long-term support until 2028. Less hand-written security code means fewer security bugs. |
| Database | **PostgreSQL 16** (SQLite for development) | Reliable, transactional, row locking for concurrent desks, partial unique indexes, trigram index for fast substring search. |
| UI | Server-rendered templates + ~650 lines of vanilla JavaScript | Pages are fast on cheap phones and old desk PCs; every action works without JavaScript and JS only makes it instant. No build step, nothing to break. |
| App server | Gunicorn + WhiteNoise | Simple, proven, compressed cached static files. |
| HTTPS / proxy | Caddy (Docker) or nginx (bare server) | Caddy gets and renews HTTPS certificates automatically. |
| Barcodes | Own Code 128 encoder (verified against ZXing), native `BarcodeDetector` or bundled ZXing for cameras | No services to depend on. |
| Email | Django email backends — any SMTP provider | Swap providers with environment variables only. |
| Scheduling | `manage.py run_scheduled_tasks` hourly (Docker service or systemd timer) | No extra queue/broker to operate. |

It is a **modular monolith**: one deployable app, clearly separated modules,
no microservices to run.

## Architecture

```text
config/                 Settings (all from environment variables), URLs
apps/
  core/                 Library settings, audit log, permissions, middleware,
                        template helpers, setup wizard, backup, scheduled tasks
  accounts/             Users & roles, sign-in, registration, rate limiting
  members/              Member profiles and member-ID generation
  catalogue/            Books vs physical copies, search, availability,
                        recommendations, covers, ISBN look-up, shelf finder
  barcodes/             Code 128 SVG encoder, ISBN maths, scanner input
  circulation/          Loans, borrowing rules, due dates, reservations queue
  fees/                 Fee engine, fees, payments
  notifications/        Email service, templates, reminders, notification centre
  inventory/            Inventory sessions and scans
  reports/              Report queries, insights, CSV export
  public/               Public website & catalogue (views)
  portal/               Member portal (views)
  staff/                /admin staff area (views, forms, circulation desk)
templates/              HTML & email templates (components/, public/, portal/, staff/, emails/)
static/                 CSS design system, JS, icons, vendored ZXing (Apache-2.0, see static/vendor/ZXING-LICENSE.txt)
deploy/                 Dockerfile helpers, Caddyfile, nginx, systemd units
docs/                   Database schema, screenshots
tests/                  145 automated tests
```

Business rules live in **services** (`apps/*/services.py`, `rules.py`,
`reservations.py`, `engine.py`), never in views or templates. Views are thin;
the same service is used by the circulation desk, Quick Scan, the member
portal and the tests.

See [docs/DATABASE.md](docs/DATABASE.md) for the schema and relationships.

## Database setup

* **Development:** nothing to do — SQLite is used automatically (`data/library.sqlite3`).
* **Production:** PostgreSQL. With Docker Compose it is created for you.
  On a plain server:

  ```bash
  sudo -u postgres psql -c "CREATE USER library WITH PASSWORD 'choose-a-password';"
  sudo -u postgres psql -c "CREATE DATABASE library OWNER library;"
  # .env:  DATABASE_URL=postgres://library:choose-a-password@localhost:5432/library
  python manage.py migrate
  ```

Migrations are the only way the schema changes: run `python manage.py migrate`
after every upgrade (the Docker image does this automatically on start).

## Environment variables

Copy `.env.example` to `.env` and fill it in. Nothing secret is ever stored in
code or in the database.

| Variable | Required | Meaning |
|---|---|---|
| `SECRET_KEY` | ✅ | Long random string. `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DEBUG` | | `False` in production (default). |
| `ALLOWED_HOSTS` | ✅ | Your domain(s), comma separated. |
| `CSRF_TRUSTED_ORIGINS` | ✅ (HTTPS) | e.g. `https://library.example.org` |
| `SITE_URL` | ✅ | Public address used in email links. |
| `DATABASE_URL` | ✅ (prod) | `postgres://user:pass@host:5432/db` |
| `TIME_ZONE` | | Default `Asia/Colombo`. Due dates use the library's local date. |
| `BEHIND_PROXY` | | `True` behind Caddy/nginx (trust `X-Forwarded-Proto`). |
| `SECURE_HSTS_SECONDS` | | Enable HSTS once HTTPS works (e.g. `31536000`). |
| `SETUP_TOKEN` | | If set, the first-run setup page asks for it. Recommended for public servers. |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | ✅ for email | See [Email configuration](#email-configuration). |
| `SERVE_MEDIA` | | `True` lets Django serve cover images (small installs). |
| `MEDIA_ROOT`, `BACKUP_DIR` | | Where covers and backups are stored. |
| `ISBN_LOOKUP_ENABLED` | | Fill book details from Open Library when an ISBN is scanned (default `True`). |
| `POSTGRES_PASSWORD`, `DOMAIN` | Docker only | Database password and the domain Caddy gets a certificate for. |

## Running locally

Requirements: Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then set DEBUG=True and a SECRET_KEY
python manage.py migrate
python manage.py seed_demo --password "demo-library-2026"   # optional demo data
python manage.py runserver
```

Open <http://localhost:8000>. With demo data you can sign in as:

| Account | Email | What to try |
|---|---|---|
| Administrator | `admin@library.local` | Everything, including Settings |
| Librarian | `librarian@library.local` | Books, members, fees, inventory, reports |
| Assistant | `assistant@library.local` | Circulation, Quick Scan, reservations |
| Member | `navoda@example.com` | Member portal (has a book ready for collection) |

The password is whatever you passed to `--password`. The staff area is at
<http://localhost:8000/admin/>. Demo copy barcodes follow the pattern
`2000BBBBCCCC` (e.g. `200000010003` = *The Hobbit*, copy 3) and member cards
are the member IDs (`LIB-2026-00001`). In development, emails are printed in
the terminal.

## Creating the admin account

The first time the site is opened with an empty database it shows **Library
Setup**: library name, admin name/email/password, currency, default loan period
and late fee → **Create library**. Set `SETUP_TOKEN` in production so only
you can complete it.

More staff: **Admin → Staff accounts → Add staff**. They receive an email with
a link to choose their own password. Roles:

* **Assistant** — borrow, return, search, manage reservations, view members.
* **Librarian** — full operational access (books, members, fees, inventory, reports).
* **Administrator** — everything, including settings, staff, audit log and backups.

(Command line alternative: `python manage.py createsuperuser` creates an administrator.)

## Adding books

**Admin → Books → Add book** (or press *Add book* on the Books page).

1. **Scan** the barcode label you stuck in the book — or the ISBN on the back cover.
2. The system checks it:
   * barcode already registered → ⚠️ *This barcode is already registered* (book, copy ID, status, location). Nothing is ever duplicated.
   * ISBN of a title you already own → *Add a copy*.
   * new ISBN → the form opens **pre-filled from Open Library** (title, author, publisher, year, cover).
   * new barcode → the form opens with the barcode filled in.
3. Check the details, set the shelf (e.g. `FIC-A-04`), press **Add book**.
4. Need several copies? Enter *Additional copies* — each gets its own generated
   barcode, and you're offered a sheet of **printable labels**.

A *book* (title, author, ISBN …) and its *copies* (barcode, shelf, condition,
status) are separate: edit a copy's barcode/shelf/condition from the copy page
without touching the book. Marking a copy *Damaged* or *Lost* stops it being
lent; if it was on hold for someone, their reservation moves to another copy.

Give each category a **shelf code** (e.g. Fiction = `FIC`) so the Shelf Finder
can say *Fiction → Shelf A → Row 04*.

## Using a barcode scanner

Any ordinary **USB or Bluetooth barcode scanner** works — they behave like a
keyboard (type the code, then press Enter). No drivers or settings are needed.

* Desk pages keep the scan box focused automatically. Keystrokes typed while
  you are "on the page" go into the scan box, so you never need the mouse.
* After each scan the box is cleared and ready for the next one.
* `Enter` on an empty box confirms a pending loan; `Esc` resets.
* **Camera scanning** — on phones and tablets a 📷 button appears next to scan
  boxes (requires HTTPS). It uses the browser's built-in barcode detector or a
  bundled decoder, so it also works on iPhone.
* Member cards and copy labels use **Code 128** barcodes, which every scanner reads.
  Print labels from a book page (*Labels*) and cards from a member page (*Card*).

## Borrowing books

**Admin → Circulation** (or **Quick scan**):

1. Scan the **member's card** (or type a name, member ID, email or phone).
2. Scan the **book**.
3. The system checks instantly: account active? overdue books? unpaid fees over
   the limit? borrowing limit? copy available? on hold for someone else?
   reference-only? already has this title?
4. ✅ **READY TO BORROW** — shows borrower and due date. Press **Enter** (or
   *Confirm borrow*). Done. Scan the next book, or *Finish* for the next member.

If borrowing is blocked the screen says exactly why ("2 overdue books must be
returned first.", "Unpaid fees of LKR 650.00 exceed the LKR 500.00 limit.").
Due dates use the loan period from Settings, or a per-book override.
Members can renew online; staff can renew from the desk or member page.

## Returning books

**Admin → Circulation → Return** (or just scan the book in **Quick scan**):

Scan the book → 📚 **BOOK RETURNED** with borrowed date, due date, return date,
days borrowed, days overdue and the late fee. A fee is created automatically
(and the member is emailed). The screen then tells you where the book goes:
back to its shelf, or onto the **hold shelf** for the next member in the queue
(who is notified automatically).

## Reservations

* *Available* book → **Reserve this book**: a copy is set aside for the member
  and appears on the staff **pull list** (Today / Reservations). Press *On hold
  shelf* once you've fetched it.
* Unavailable → **Join the reservation queue**: strictly first come, first
  served. The member sees their queue position and an estimate.
* When a copy is returned it is offered to the first member automatically:
  "📚 Your book is waiting for you — please collect it before 12 October."
* Not collected in time (*Settings → Days to collect a reserved book*) → the
  hold expires and passes to the next member.
* Borrowing the book completes the reservation automatically.

## Fees

Configured in **Settings → Fees**: fee per overdue day, grace period, maximum
per loan, default replacement fee, and the unpaid-fee limit that blocks
borrowing.

```text
overdue days    = return date − due date        (due today → 0)
chargeable days = overdue days − grace period
fee             = chargeable days × fee per day  (never above the maximum)
```

Example (LKR 50/day, no grace): returned 4 days late → **LKR 200**. While the
book is still out members see an *estimated* fee that keeps growing; the
*final* fee is set when it's returned. Lost books: *Mark lost* on the member
page closes the loan, charges the replacement cost and notifies the member.

Payments are recorded by staff (amount, date, method, receipt number) —
partial payments are fine. **Online payments later:** a payment gateway's
webhook only needs to call `apps.fees.services.record_payment(fee, amount=…,
method="online", provider="…", reference=<transaction id>)`.

## Email configuration

Emails sent: welcome, account verification, set-password invitations, due
soon (3 days before, configurable), overdue (repeats weekly, configurable),
reservation available, reservation expiring, fee generated, payment
confirmation, lost book, password reset. Members can switch reminder emails
off; members without an email address still get in-app notifications.

Use any SMTP provider in `.env`:

```ini
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.sendgrid.net            # or smtp.gmail.com, email-smtp.<region>.amazonaws.com, smtp.office365.com …
EMAIL_PORT=587
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=your-smtp-password-or-api-key
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=City Library <no-reply@library.example.org>
```

Then **Admin → Settings → Send a test email to me**. Every email is logged
(member page → *Recent emails*); failed sends are retried automatically by the
hourly task. Reminders are sent by the scheduled task — see below.

## Production deployment

### Option A — Docker Compose (recommended)

Needs a server with Docker and a domain name pointing at it.

```bash
git clone <this repository> library && cd library
cp .env.example .env     # set SECRET_KEY, DOMAIN, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS,
                         # SITE_URL, POSTGRES_PASSWORD, SETUP_TOKEN and EMAIL_* values
docker compose up -d --build
```

This starts PostgreSQL, the app (migrations run automatically), an hourly
**scheduler** (reminders, reservation expiry, email retries, nightly backup at
02:00) and **Caddy**, which obtains and renews the HTTPS certificate. Open
`https://your-domain/` and complete **Library Setup**.

Upgrading: `git pull && docker compose up -d --build`.

### Option B — a plain Linux server (systemd + nginx)

```bash
sudo useradd --system --home /opt/library library
sudo git clone <repo> /opt/library && cd /opt/library
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env      # DATABASE_URL, SECRET_KEY, … BEHIND_PROXY=True
sudo .venv/bin/python manage.py collectstatic --noinput
sudo chown -R library:library /opt/library
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now library-web library-tasks.timer library-backup.timer
sudo cp deploy/nginx.conf /etc/nginx/sites-available/library   # edit server_name
sudo ln -s /etc/nginx/sites-available/library /etc/nginx/sites-enabled/
sudo certbot --nginx -d library.example.org
```

Production checklist: `DEBUG=False`, strong `SECRET_KEY`, HTTPS working (then
set `SECURE_HSTS_SECONDS`), `SETUP_TOKEN` set before first start, email tested,
backups copied off the server.

## Backup

* **One click:** Admin → *Backup & export* → **Download backup** (a compressed
  file with all library data) and CSV exports of books, members, loans and fees.
* **Automatic:** the Docker scheduler / `library-backup.timer` runs nightly:
  `python manage.py backup --with-media --keep 30` (writes to `BACKUP_DIR`,
  keeps the newest 30, includes cover images).
* **Restore** (replaces all data — works across SQLite and PostgreSQL):

  ```bash
  python manage.py restore backups/library-20261005-020000.json.gz --replace
  # Docker: docker compose exec web python manage.py restore /app/backups/<file> --replace
  ```
* PostgreSQL-level backups also work:
  `docker compose exec db pg_dump -U library library > library.sql`.

Copy backups to another machine or cloud storage regularly.

## Testing

```bash
python manage.py test tests            # 145 tests, ~6 seconds on SQLite
DATABASE_URL=postgres://… python manage.py test tests   # same suite on PostgreSQL
ruff check . && ruff format --check .  # lint (pip install -r requirements-dev.txt)
```

Covered: fee engine edge cases (due today = 0, overdue tomorrow = daily fee,
grace, cap), every borrowing rule, borrow/return/renew/lost, FIFO reservations
(notification, expiry → next member, suspended members skipped, holds moved
when a copy goes missing), duplicate barcodes, search (partial, ISBN, barcode,
typos, filters), registration & member IDs, login/logout/wrong password/rate
limiting, first-run setup, access control for every staff URL, permission
escalation, privacy, CSRF, XSS escaping, CSV injection, emails (idempotent
reminders, opt-out, failures retried), desk workflows over HTTP, inventory,
every page rendering with demo data, and backup → restore round trip.

## Troubleshooting

| Problem | Fix |
|---|---|
| `SECRET_KEY is not set` | Add `SECRET_KEY=…` to `.env` (or set `DEBUG=True` for local development). |
| "Bad Request (400)" | Add your domain to `ALLOWED_HOSTS`. |
| "This page expired" on every form | Add `https://your-domain` to `CSRF_TRUSTED_ORIGINS`; behind a proxy set `BEHIND_PROXY=True`. |
| Can't stay signed in on `http://` | Cookies are HTTPS-only when `DEBUG=False`. Use HTTPS, or `COOKIE_SECURE=False` for an internal test server only. |
| Everything redirects to `/setup/` | No administrator exists yet — complete Library Setup (or `createsuperuser`). |
| Scanner types into the wrong place | Click once anywhere on the desk page; the scan box takes focus again. Make sure the scanner is set to send **Enter** after each code (the factory default). |
| Camera button doesn't appear | Camera access needs HTTPS (or `localhost`) and a browser with camera support. Typing / USB scanners always work. |
| Emails not arriving | Admin → Settings → *Send a test email*; check the member page's *Recent emails* for the error; check spam folders; verify `EMAIL_*` values. |
| Reminders not sent | Make sure the scheduler is running (`docker compose ps`, or `systemctl list-timers library-tasks.timer`). Run `python manage.py run_scheduled_tasks` manually to see output. |
| Covers missing after deploy | With Docker, Caddy serves `/media` from the shared volume. On nginx, check the `location /media/` alias path. Small installs can set `SERVE_MEDIA=True`. |
| ISBN details not filled | The server needs outbound internet to `openlibrary.org`; otherwise type the details. Disable with `ISBN_LOOKUP_ENABLED=False`. |
