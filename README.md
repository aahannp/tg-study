# TG Study

A local, distraction-free study app. It reads lecture content from your Telegram channels (MTProto, via Telethon), sorts it into subject/chapter folders, and gives you a video and PDF study workspace in your browser.

## Windows: step by step

You need Windows 10 or 11 and a Telegram account. About 10 minutes the first time.

**1. Install Python (once)**
1. Go to https://www.python.org/downloads/ and click the yellow **Download Python 3.x** button (3.11 or newer).
2. Run the installer. On the first screen, **tick "Add python.exe to PATH"** at the bottom, then click **Install Now**.

**2. Download the app**
1. On this GitHub page, click the green **Code** button, then **Download ZIP**.
2. In your Downloads folder, right-click `tg-study-main.zip` and choose **Extract All...**, then **Extract**.

**3. Get your Telegram API keys (once)**
1. Go to https://my.telegram.org and log in with your phone number (with country code, e.g. `+91...`). The code arrives in your Telegram app, not by SMS.
2. Click **API development tools**. Fill in any **App title** and **Short name** (e.g. `tgstudy`), pick **Desktop**, and click **Create application**.
3. Keep the page open: you need **App api_id** (a number) and **App api_hash** (a long code). Never share these.

**4. Start the app**
1. Open the extracted `tg-study` folder and double-click **`start.bat`**.
2. If Windows shows "Windows protected your PC", click **More info** and then **Run anyway**. (The script only sets up Python packages and starts the app.)
3. The first start takes a few minutes while it installs things in a black window. Then the TG Study window opens. **Leave the black window open**: closing it closes the app.

**5. Log in and pick channels**
1. Enter your api_id, api_hash and phone number, then the login code Telegram sends to your Telegram app, and your two-step verification password if you have one.
2. Tick the channels you study from, then press **Sync** on each one. Your lectures appear sorted into chapter folders.

**Every day after that:** just double-click `start.bat`. It opens in a few seconds.

**If something goes wrong**
- **"Setup failed" or "Python was not found":** reinstall Python and make sure "Add python.exe to PATH" is ticked, then run `start.bat` again.
- **The window stays blank or doesn't open:** install the Microsoft Edge WebView2 Runtime (search "WebView2 Runtime download" on microsoft.com). Or use your browser instead: open the `tg-study\backend` folder, click the address bar, type `cmd` and press Enter, then run
  ```
  .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
  ```
  and open http://127.0.0.1:8765 in Edge or Chrome. Keep that window open while you study.
- **Updating to a new version:** download and extract the new zip, then copy the `data` folder from your old `tg-study` folder into the new one. That keeps your login, library, progress and notes.
- **Removing it:** delete the `tg-study` folder. To also cut its access to your Telegram account, open Telegram, go to **Settings > Devices** and end the TG Study session.

Your login and library live in `tg-study\data`. Don't send that folder to anyone: it gives full access to your Telegram account.

## Folder structure

```
tg-study/
  backend/
    requirements.txt      # exact pinned versions
    app/
      main.py             # FastAPI app: mounts routers + serves ../frontend at /
      schema.sql          # SQLite schema (the contract)
      db.py               # connect() -> sqlite3 conn, applies schema
      config.py           # read/write data/.env (API_ID, API_HASH, PHONE)
      telegram.py         # single Telethon client, login flow, message -> row mapping
      sync.py             # incremental sync: fetch id > last_message_id, store, organize
      organizer.py        # Phase 2: index parser, sequential parser, planner/worksheet routing
      routes_auth.py      # /api/auth/*
      routes_channels.py  # /api/dialogs, /api/channels/*
      routes_library.py   # folder/item edits (Phase 3)
      routes_media.py     # Range streaming + offline download (Phase 3)
      routes_study.py     # item detail, progress, continue, notes (Phase 4)
    tests/                # pytest: organizer, sync mapping, library, media
  frontend/
    index.html, app.js    # setup, channel picker, library (Tailwind CDN + Alpine, dark)
    player.html, player.js # study workspace: video, PDF split pane, notes
                          # add ?mock=1 to either page to try it without Telegram
  desktop.py              # native window launcher (pywebview)
  start.command, start.bat # double-click launchers: macOS, Windows
  data/                   # gitignored, chmod 700: app.db, tg.session, .env (chmod 600)
```

## Run

Three ways. The server always binds to 127.0.0.1 only. Your Telegram session file gives full access to your account, so never expose this port.

**a) Double-click `start.command`** (macOS). First run creates `backend/.venv` and installs the pinned requirements, then opens the app window. (If macOS blocks it: right-click, Open.)

**a2) Windows: double-click `start.bat`.** Full walkthrough in "Windows: step by step" above. Needs Python 3.11+ from python.org (tick "Add python.exe to PATH"). The window uses Microsoft Edge WebView2, which Windows 10/11 already include. Same first-run setup as above.

**b) Desktop window from the venv** (on Windows: `.venv\Scripts\activate` instead of `. .venv/bin/activate`)

```
cd tg-study/backend
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cd .. && python desktop.py
```

Starts the server on port 8765 (or the next free one) and opens a native window. Closing the window stops the server.

**c) Browser only**

```
cd tg-study/backend
. .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8765
open http://127.0.0.1:8765
```

**First run:** get an `API_ID` and `API_HASH` at https://my.telegram.org (log in, "API development tools"). Enter them with your phone number on the setup screen.

## API contract (Phase 1 + 2)

All JSON. Errors are `{"detail": "..."}` with a 4xx/5xx status.

| Method & path | Body | Response |
|---|---|---|
| `GET /api/auth/status` | – | `{configured, authorized, me: {name, phone} \| null}` |
| `POST /api/auth/start` | `{api_id, api_hash, phone}` | saves .env, sends OTP → `{code_sent: true}` |
| `POST /api/auth/code` | `{code}` | `{authorized: true}` or `{password_required: true}` (2FA) |
| `POST /api/auth/password` | `{password}` | `{authorized: true}` |
| `POST /api/auth/logout` | – | `{ok: true}` (deletes session, keeps .env) |
| `GET /api/dialogs` | – | live joined channels/groups: `[{id, title, username, kind: "channel"\|"group", selected}]` |
| `GET /api/channels` | – | selected channels: `[{id, title, subject, ignore_stickers, last_synced_at, parse_mode, message_count, item_count}]` |
| `PUT /api/channels/{id}` | any of `{selected, subject, ignore_stickers, title, username}` | upserts row → the channel |
| `POST /api/channels/{id}/sync` | – | fetches new messages, re-runs organizer → `{new_messages, mode, folders, items}` |
| `GET /api/channels/{id}/folders` | – | `[{id, name, kind, position, done, total, items: [{message_id, title, lecture_no, kind, file_name, duration, confidence, manual, position_sec, completed, downloaded}]}]` ordered by position. `done`/`total` count completed/all items in the folder (Phase 3 adds these fields). |

## API contract (Phase 3 + 4)

`{ch}` = channel id, `{msg}` = message id. Booleans are JSON booleans.

**Media**

| Method & path | Body | Response |
|---|---|---|
| `GET /api/media/{ch}/{msg}` | – | The file bytes. Honours `Range: bytes=a-b` → `206` + `Content-Range`, `Accept-Ranges: bytes`, `Content-Length`, correct `Content-Type` from mime, `Content-Disposition: inline`. Streams from Telegram chunk-by-chunk, or from the local file when downloaded. Used by `<video src>`, `<img src>` and `<iframe src>` for PDFs. |
| `POST /api/media/{ch}/{msg}/download` | – | Starts an offline download in the background → `{status: "downloading", progress: 0}` |
| `GET /api/media/{ch}/{msg}/download` | – | `{status: "none" \| "downloading" \| "done" \| "error", progress: 0..1}` |
| `DELETE /api/media/{ch}/{msg}/download` | – | Deletes the local file → `{status: "none"}` |

**Library edits** (every item edit sets `manual = 1`, so re-syncs never undo it)

| Method & path | Body | Response |
|---|---|---|
| `POST /api/channels/{ch}/folders` | `{name}` | New folder, kind `custom`, placed last → the folder (`409` if the name exists) |
| `PATCH /api/folders/{id}` | any of `{name, position}` | Rename (keeps `auto_name`, so syncs keep filling it) and/or move to index `position`; siblings renumbered → the folder |
| `DELETE /api/folders/{id}` | – | Its items move to the subject's "General / Unsorted" folder (created if missing) as manual → `{ok: true}` |
| `PATCH /api/items/{ch}/{msg}` | any of `{folder_id, position, title}` | Move to a folder and/or index `position` in it (siblings renumbered), or rename → the item |

**Study** (Phase 4)

| Method & path | Body | Response |
|---|---|---|
| `GET /api/items/{ch}/{msg}` | – | `{channel_id, message_id, title, lecture_no, kind, mime_type, file_name, duration, subject, folder_id, folder_name, position_sec, completed, downloaded, prev, next, folder_pdfs}`. `prev`/`next` = `{message_id, title, kind} \| null`, the neighbouring item in the same folder by position. `folder_pdfs` = `[{message_id, title}]`, the PDFs in the same folder. |
| `PUT /api/progress/{ch}/{msg}` | `{position_sec, duration_sec}` and/or `{completed}` | Upsert. Auto-sets `completed = true` once `position_sec >= 0.9 * duration_sec` (never auto-unsets) → `{position_sec, duration_sec, completed}` |
| `GET /api/continue` | – | Up to 12 items with progress, not completed, `position_sec > 0`, newest first: `[{channel_id, message_id, title, subject, kind, position_sec, duration_sec, updated_at}]` |
| `GET /api/notes/{ch}/{msg}` | – | `[{id, timestamp_sec, body, created_at}]` by timestamp (nulls last) |
| `POST /api/notes/{ch}/{msg}` | `{body, timestamp_sec}` | The new note |
| `PATCH /api/notes/{id}` | `{body}` | The note |
| `DELETE /api/notes/{id}` | – | `{ok: true}` |

**Pages**: `/` is the library (`index.html`). `/player.html?ch=<ch>&msg=<msg>` is the study workspace (video + PDF split pane + notes) and opens images/PDFs too.

## Phases

1. Login, schema, channel picker, incremental sync. **(built)**
2. Index parser + sequential parser + planner/worksheet classifier. **(built)**
3. HTTP Range streaming, offline download, drag-and-drop/move folders. **(built)**
4. Custom player, shortcuts, auto-resume, split-pane PDF + timestamped notes. **(built)**
