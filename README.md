# The Academic Mirror

A server-rendered **Django** application for Professor Rashidah Funke Olarenwaju’s Inaugural Lecture on **25 November 2026**. It runs a portrait-first, touch-optimised academic-gown kiosk and a private event-management console from one Python application.

There is no React/Vite frontend, FastAPI service, REST API, Node runtime, Docker configuration, or separate database service. Browser camera access uses a small vanilla JavaScript module and submits normal CSRF-protected Django forms.

## Included

### Guest kiosk

- Full-screen portrait experience for 1080 × 1920 standees.
- Consent-first guest sessions backed by Django sessions.
- Real `getUserMedia` camera preview, mirrored capture, countdown, retake, and secure form upload.
- Six replaceable sample academic gowns.
- FASHN and Replicate/IDM-VTON provider adapters.
- Durable database-backed generation jobs with in-process workers and restart recovery.
- Honest failure states: the application never substitutes a fake AI result.
- Signed, expiring portrait downloads and server-generated QR codes.
- Inactivity reset, offline warning, browser-back suppression, and privacy cleanup.

### Event console

Open `/admin/login/`.

- Django session authentication with Argon2 password hashing.
- Guest, capture, portrait, failure, collection, and hourly activity metrics.
- Add, edit, hide, and archive gowns.
- Search, preview, download, and permanently delete portrait records.
- Edit event identity, consent copy, timeout, retention, logo, and welcome background.
- Configure the AI provider and store its credential encrypted at rest.
- Monitor camera heartbeats, SQLite latency, private storage, workers, queue depth, and failures.

## Project layout

```text
academic_mirror/           Django project settings, URLs, WSGI and ASGI
mirror/
  ai/                      FASHN and Replicate provider adapters
  management/commands/     Privacy-retention command
  migrations/              Django schema migrations
  services/                Jobs, retention, sessions and sample assets
  static/                  Compiled local CSS, vanilla JS, fonts and event artwork
  templates/               Server-rendered kiosk and event-console pages
  tests/                   Guest journey, private download and console tests
  forms.py                  Django forms
  models.py                 Application data model
  views_guest.py            Form/redirect guest journey
  views_admin.py            Authenticated event console
scripts/launch-kiosk.ps1   Windows Chrome/Edge kiosk launcher
storage/                   Private captures, garments, portraits and event uploads
manage.py                  Django command entry point
```

## Quick start

Python 3.12 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

macOS/Linux activation and environment copy:

```bash
source .venv/bin/activate
cp .env.example .env
```

Then open:

- Guest kiosk: `http://localhost:8000/kiosk/`
- Event console: `http://localhost:8000/admin/login/`

The first migration seeds the event, six sample gowns, and the administrator configured by `ADMIN_USERNAME` and `ADMIN_PASSWORD`. Set those values **before the first migration**. Later environment changes do not silently replace an existing password; use `python manage.py changepassword <username>`.

SQLite and the local `storage/` directory are the defaults, so no database server or container runtime is needed.

## Configure virtual try-on

Generation is deliberately disabled by default. Set a provider in **Event console → Event settings**, or configure it in `.env` before the initial migration.

For OpenAI, set `OPENAI_API_KEY` in `.env`, restart the server, and choose **OpenAI GPT Image** in Event settings. You can also enter the OpenAI key in the console's encrypted credential field. The default model is `gpt-image-2`. It uses the guest photo, gown, and optional cap as references in a single edit.

Generate a credential-encryption key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### FASHN

```env
AI_PROVIDER=fashn
AI_API_KEY=your_fashn_api_key
FASHN_API_BASE_URL=https://api.fashn.ai/v1
FASHN_MODEL_NAME=tryon-v1.6
```

### Replicate / IDM-VTON

```env
AI_PROVIDER=replicate
AI_API_KEY=r8_your_replicate_token
REPLICATE_MODEL_OWNER=cuuupid
REPLICATE_MODEL_NAME=idm-vton
REPLICATE_MODEL_VERSION=
```

Pin and validate the exact model version before the event. Provider model contracts and input names can change.

## How the server-rendered journey works

The browser follows ordinary Django pages and form posts:

1. `/consent/` creates a private guest session.
2. `/camera/` receives a multipart image form.
3. `/collection/` records the selected gown.
4. `/portrait/generate/` creates a durable job and redirects to an HTML waiting page.
5. The waiting page refreshes until Django renders either the result or a recoverable error.
6. `/portrait/<id>/file/` serves a signed, expiring private image download.

No browser-facing JSON API is used.

## Test and quality checks

```powershell
python manage.py check
python manage.py test
ruff check .
```

The journey tests verify disabled-provider failure, successful provider output, signed private downloads, Django session authentication, and the absence of the previous `/api` route.

## Privacy and maintenance

- Source photographs are removed when a guest session ends unless an active job still needs them.
- Completed portraits follow the retention period set in the event console.
- Retention runs opportunistically in the application and can also be run manually:

  ```powershell
  python manage.py purge_expired
  ```

- Private files are outside Django static hosting and are served only by ownership checks or signed links.
- Uploaded image bytes are decoded and validated with Pillow; file extensions and MIME headers are not trusted alone.
- Provider credentials are write-only in the console and encrypted before storage.

## Docker-free production run

1. Set `ENVIRONMENT=production`, `DEBUG=false`, a long random `SECRET_KEY`, `SETTINGS_ENCRYPTION_KEY`, strong administrator credentials, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and the public HTTPS `PUBLIC_BASE_URL`.
2. Prepare the application:

   ```powershell
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```

3. Run one Waitress process:

   ```powershell
   waitress-serve --listen=0.0.0.0:8000 academic_mirror.wsgi:application
   ```

4. Put IIS, Caddy, Apache, or another host-managed HTTPS reverse proxy in front of port 8000. Camera access requires HTTPS except on `localhost`.

The built-in queue is intended for one application process. Its database records survive restarts, and unfinished work is recovered at startup. For horizontal scaling, replace `mirror/services/jobs.py` with a shared queue such as Celery while keeping the provider interface.

## 55-inch standee

1. Use portrait orientation at 1080 × 1920 and 100%–125% scaling.
2. Pre-authorise camera access for the HTTPS kiosk origin.
3. Disable sleep, lock screen, notifications, and conflicting touch gestures.
4. Launch Chrome:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\launch-kiosk.ps1 -Url "http://localhost:8000/kiosk/"
   ```

5. Before guests arrive, test an end-to-end portrait and scan its QR code from a phone on another network.
