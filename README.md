# Computer Typology

A Windows activity recorder and local website for Jeffr. The recorder starts at login, identifies the foreground application, and saves timestamped periods of activity. Chrome and Edge can report the focused tab through the included extension. An on-demand dashboard shows native app icons and website favicons, daily timelines, time by app, and CSV / JSON exports.

## Current delivery

- Windows recorder, tray controls, and local dashboard are implemented.
- The recorder is running and automatic login startup is configured for `JEFFREYWANG\Jeffr`. Windows recognizes the Startup shortcut, and its target, arguments and working directory have been verified.
- The Chrome / Edge extension is provided; load and pair it once in each browser profile.
- AWS Agent Toolkit is installed for Codex and Cursor using `jeffrey-dev` in `us-east-1`. PostgreSQL deployment status and measured costs are recorded in [the report](docs/COST_AND_PERFORMANCE_REPORT.md).
- The website is local at **http://127.0.0.1:43128**. It is not deployed on the public internet. Cloud sync stores a copy of records; it does not make the dashboard remotely accessible.

## Install and run

Requires Windows 10/11 and Python 3.11 or later. The installer has been restored and successfully rerun after the user allowed it in security software. Startup and Start menu shortcuts are verified. A manual launch shortcut is also available at `%USERPROFILE%\.computer-typology\Computer Typology.lnk`.

Run from this repository in PowerShell while logged in as Jeffr:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

The installer copies the application to `%USERPROFILE%\.computer-typology\app`, creates a separate Python runtime, adds a **Computer Typology** shortcut to the current user's Startup folder and Start menu, and starts it in the background. `ExecutionPolicy Bypass` applies only to this invocation; it does not change the machine policy. No administrator account is needed. Development files can move without breaking the installed recorder. Rerun the installer after editing the source to update the installed copy.

Open **http://127.0.0.1:43128** in a browser. Right-click the tray icon for **Open dashboard**, **Pause tracking / Resume tracking**, and **Quit**. Windows may put the tray icon in the overflow area. Pausing persists across restarts; quitting lasts until the next login or manual launch.

Runtime files stay outside the repository and outside OneDrive:

| File under `%USERPROFILE%\.computer-typology` | Purpose |
| --- | --- |
| `activity.sqlite3` | Local activity history (SQLite WAL) |
| `config.json` | Recorder preferences, device ID, local pairing token |
| `icons\` | Cached application icons |
| `tracker.log` | Rotating operational logs; no collected titles or URLs |
| `cloud-config.json` | Optional database settings; password encrypted with Windows DPAPI |
| `rds-ca.pem` | RDS CA bundle downloaded during AWS setup |

To disable automatic startup and stop the installed recorder, preserving history and cloud resources:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

## Connect Chrome and Edge

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode**, select **Load unpacked**, and choose `%USERPROFILE%\.computer-typology\app\extension`.
3. In the dashboard, open **Connections & settings → Copy pairing token**.
4. Open the extension's options, choose the correct browser, paste the token, and select **Save & connect**.
5. After updating an existing installation, click **Reload** for Computer Typology on the extensions page in each browser. Focus an ordinary web tab, then click **Refresh** on the dashboard. Its title, domain and cached favicon should appear. Missing icons fall back to the browser icon.

Repeat for each Chrome / Edge profile you use. The extension needs `tabs`, `favicon`, local extension storage, alarms, and access only to the local recorder address. It observes tab selection, navigation, browser-window focus, and a 30-second heartbeat. It does not inject content scripts, read page contents, or request every website as a host permission.

Reports are matched against the foreground browser and its native window title to avoid attributing background windows to the active one. Unmatched or stale reports retain only generic browser identity. Identical titles in multiple windows/profiles can still be ambiguous because the Windows handle and browser tab ID are different identifiers; domain attribution is best-effort in that case. Incognito / InPrivate extension access is disabled. Recognizable private window titles are skipped; otherwise private browsing can count as generic browser time, without a tab title or domain. Titles from browser UI dialogs and new-tab pages depend on what the browser exposes.

## How time is recorded

- Foreground app is sampled approximately once per second with Win32 APIs.
- Changing the app, title, or matched browser tab creates a new interval.
- The running interval is checkpointed every 60 seconds and before dashboard reads.
- Five minutes without input pauses recording by default; that grace period counts as active time. The dashboard can change it to 1, 5, 10, or 15 minutes.
- Locked desktops, unavailable foreground processes, and sleep gaps are not filled with fabricated activity. Abrupt shutdown can lose up to 60 seconds since the last checkpoint.
- Only the foreground app counts. Background playback and work in background processes are not measured. This measures app focus, not productivity or attention.
- App process names and website domains can be excluded in settings. Website exclusions fail closed: browser recording pauses whenever the extension cannot identify the current domain.
- Native window titles and paired tab titles are enabled by default and can be disabled. Existing records are not rewritten when settings change.

No screenshots, key contents, clipboard data, page contents, full executable paths, URL paths, URL queries, or fragments are stored. Window and tab titles can still contain personal document names or search text. SQLite is stored under your Windows user profile and is not independently encrypted. PostgreSQL passwords are encrypted for this Windows user; the local pairing token remains in the user's configuration file.

## Data format

**Use relational interval records, SQLite locally and PostgreSQL in AWS.** They support date/time queries, daily aggregation, indexed history, offline operation, and safe retrying of uploads. JSON is useful for interchange and CSV for spreadsheets; neither is the primary database format.

| Field | Meaning |
| --- | --- |
| `id`, `device_id` | UUIDs for retry-safe identity and device separation |
| `username` | Windows account name |
| `app_name`, `process_name`, `icon_key` | Application identity and local icon lookup |
| `window_title` | Optional native application title |
| `browser`, `tab_title`, `domain`, `favicon_key` | Optional matched browser context and content-addressed icon |
| `started_at`, `ended_at` | UTC timestamps; PostgreSQL uses `TIMESTAMPTZ` |
| `revision` | Monotonic checkpoint revision for idempotent uploads |

SQLite stores integer UTC milliseconds and a `synced_revision` acknowledgement. Devices and repeated app/tab contexts are stored once, with events referencing their numeric keys. The compatible `intervals` view supplies ISO timestamps on demand. PostgreSQL mirrors this using UUID keys and `TIMESTAMPTZ`. Durations are derived from start/end rather than maintained as a second source of truth. The dashboard computes each selected local day's exact UTC boundaries, including daylight saving changes, and clips intervals that cross midnight. Exported timestamps retain the original interval bounds; `duration_seconds` is clipped to the selected date.

The sync worker uploads at **23:55 local time**, configurable in settings, and catches up after missed uploads on login/wake. It batches up to 500 pending rows, uses a transaction and UUID upserts, and acknowledges the exact uploaded revision. Updates that arrive during an upload remain queued. Connection failures leave local rows intact and retry with bounded backoff. It is an outbound copy of the local history, not bidirectional synchronization. Website favicons are 32 x 32 PNG blobs, capped at 16 KiB, deduplicated by SHA-256 and uploaded once. Native application icon files remain local; AWS stores their lookup keys. Use **Upload now** for an immediate copy. The dashboard has no automatic refresh timer and can remain closed. Initial migration preserves `activity.pre-v2.sqlite3` as a one-time recovery copy.

## AWS RDS PostgreSQL setup

The prepared [CloudFormation template](infra/aws-postgres.json) creates a dedicated VPC, two subnets, one encrypted **single-AZ `db.t4g.micro` PostgreSQL 18** database with 20 GiB gp3 storage, one-day backups (required by this free account plan), deletion protection, TLS enforcement, and a managed administrator secret. The setup resolves an available PostgreSQL 18 minor version in the selected region and checks the instance class before creation.

This creates **billable AWS resources**, including RDS, storage, backups/logs, and Secrets Manager. Check your account's current pricing and free-tier eligibility. It is a personal-use configuration, not a high-availability deployment. For laptop access, the RDS endpoint is public but its firewall admits only your specified public IPv4 `/32`; it never opens PostgreSQL to the entire internet. Use a private subnet plus VPN instead if you require a non-public endpoint.

1. Sign into an AWS CLI profile with permission to manage the stack's CloudFormation, VPC, RDS, and managed secret resources (including the RDS service-linked role if it does not yet exist). Do not paste access keys into chat or commit them.
2. Run the installed script from PowerShell:

```powershell
$PublicIp = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()
$TypologyPython = "$env:USERPROFILE\.computer-typology\runtime\Scripts\python.exe"
$DeployScript = "$env:USERPROFILE\.computer-typology\app\scripts\deploy_aws.py"
& $TypologyPython $DeployScript --profile jeffrey-dev --region us-east-1 --allow-cidr "$PublicIp/32"
```

The script validates the template, creates `computer-typology`, waits for completion, downloads the official RDS certificate bundle, creates the normalized activity schema and a restricted `typology_writer` login, verifies the connection, then saves the password encrypted with Windows DPAPI. The recorder notices the configuration without restarting. The official AWS `asm-exec` wrapper resolves the managed administrator secret into the bootstrap process at runtime. The administrator secret is never printed or saved in the repository; the writer role has only CONNECT, schema USAGE, and table SELECT / INSERT / UPDATE privileges.

Use `--validate-only` to validate without provisioning. If the stack already exists and completed successfully, use `--connect-existing` to bootstrap/reconnect instead of creating another stack. This rotates the one shared writer-role password; reconfigure other devices if you later reuse this stack. If your public IP changes, update the stack's `AllowedClientCidr` parameter to the new `/32`. Never replace it with `0.0.0.0/0`.

The local tracker does not depend on AWS. The current deployment and verification result is in [the report](docs/COST_AND_PERFORMANCE_REPORT.md). Daily tracking uses only the restricted database login, so it does not need your root AWS session. Restart Codex/Cursor once to load the new AWS MCP server. Future AWS setup sessions can refresh `jeffrey-dev` using `aws login --profile jeffrey-dev`.

## Connect another PostgreSQL server

Use a TLS-enabled PostgreSQL instance and its trusted CA bundle. Initialize the schema with an appropriately privileged role, then use a writer role in normal operation:

```powershell
& "$env:USERPROFILE\.computer-typology\runtime\Scripts\python.exe" `
  "$env:USERPROFILE\.computer-typology\app\scripts\configure_postgres.py" `
  --host your-database-host --database typology --user your-role `
  --ca C:\path\to\trusted-ca.pem --initialize
```

The password is requested interactively, not in command arguments. Omit `--initialize` when the table already exists and the role has no schema CREATE permission. Connections require `sslmode=verify-full`. The native app holds database credentials; the website and browser extension never receive them.

To query total app time for a day in PostgreSQL, clipping intervals at the local midnight boundaries:

```sql
WITH bounds AS (
  SELECT timestamp '2026-09-28 00:00:00' AT TIME ZONE 'America/New_York' AS start_time,
         timestamp '2026-09-29 00:00:00' AT TIME ZONE 'America/New_York' AS end_time
)
SELECT app_name,
       round(sum(extract(epoch FROM
         least(ended_at, end_time) - greatest(started_at, start_time))) / 60, 1) AS minutes
FROM activity_intervals CROSS JOIN bounds
WHERE ended_at > start_time AND started_at < end_time
GROUP BY app_name ORDER BY minutes DESC;
```

## Stop cloud charges

Uninstalling the recorder does **not** remove AWS resources. To retire the database, first disable RDS deletion protection, then delete the CloudFormation stack. The template requests a final snapshot. Retained snapshots, automated backups, Secrets Manager secrets, and CloudWatch log groups may continue incurring charges; review these resources in the AWS console and preserve or delete them deliberately. Remove the local `cloud-config.json` after retiring the connection so sync stops retrying.

## Development and verification

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check web/app.js
node --check extension/background.js
.\.venv\Scripts\python.exe -m tracker
```

Tests cover normalized migration and backups, favicon validation/deduplication/privacy, daily scheduling and missed uploads, upload failures and revision races, interval switches, idle/lock/pause, sleep and crash gaps, midnight clipping, domain sanitization and exclusions, stale/mismatched tabs, title suppression, sync acknowledgement races, API authentication and origins, export escaping, and range validation. The Windows collector and DPAPI password round trip can be smoke-tested on Windows. Measured storage and CPU/memory results are in the report. Reproduce the synthetic storage comparison with `python scripts/benchmark_storage.py`.

The loopback HTTP API validates Host and Origin, requires a pairing token for mutations, avoids external scripts/assets, and serves with a restrictive Content Security Policy. It is intended for one trusted Windows user; it is not an authenticated multi-user web service. Do not bind it to `0.0.0.0` or expose it through a public tunnel. A future remote dashboard should use a separate authenticated API to PostgreSQL.

Primary references: [Microsoft foreground windows](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow), [Windows input idle timing](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo), [Chrome Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs), [Chrome window focus](https://developer.chrome.com/docs/extensions/reference/api/windows), [RDS PostgreSQL TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html), [PostgreSQL certificate verification](https://www.postgresql.org/docs/current/libpq-ssl.html).
