# Computer Typology: AWS cost, storage and performance

Prepared 28 September 2026 for one Windows computer, Chrome and Edge, in AWS US East (N. Virginia).

## Recommendation and current design

Write activity locally to **SQLite**, then upload to **RDS PostgreSQL at 23:55 local time**. This works offline, avoids a continuous database connection, and keeps the dashboard responsive. A missed upload catches up after the computer wakes or reconnects. Retries retain local records and use UUID/revision checks to avoid duplicate history. The website refreshes only when opened or when you click Refresh or change the selected day.

Keep relational intervals as the primary format: app/tab context, start, end and a stable identifier. SQLite stores UTC milliseconds; PostgreSQL stores `TIMESTAMPTZ`. JSON and CSV remain available as exports. Durations are calculated from interval boundaries, including local midnight and daylight-saving changes.

**Cloud setup is complete:** CloudFormation stack `computer-typology` is `CREATE_COMPLETE`; database `computer-typology-db` is running PostgreSQL 18.6. The first 80 intervals and 22 contexts uploaded, and all 80 acknowledged intervals matched local IDs, timestamps and revisions. Tracking has resumed. **Automatic login startup is now configured:** after the user allowed the installer, it was restored and rerun successfully. Windows lists Computer Typology as a startup program for `JEFFREYWANG\Jeffr`.

## How long $100 lasts

AWS's account-plan API reported **FREE / ACTIVE, $100.00 remaining**, with a **free-plan expiration of 28 March 2027**. That expiration is the account plan's date, not a promise that an individual credit grant expires on the same day. The legacy 750 free RDS hours offer is not assumed for this new account. See [AWS's current RDS Free Tier](https://aws.amazon.com/rds/free/).

The following estimate uses the live AWS Price List API on 28 September 2026, a 730-hour month, one continuously running single-AZ `db.t4g.micro`, PostgreSQL 18.6 and 20 GB of gp3 storage:

| Component | Rate and calculation | Monthly estimate |
|---|---|---:|
| Database compute | 730 hours × $0.016/hour | $11.68 |
| gp3 storage | 20 GB × $0.115/GB-month | $2.30 |
| One public IPv4 address | 730 hours × $0.005/hour | $3.65 |
| One managed administrator secret | $0.40/secret-month | $0.40 |
| **Baseline** | | **$18.03** |

Sources: [RDS PostgreSQL pricing](https://aws.amazon.com/rds/postgresql/pricing/), [AWS IPv4 pricing](https://aws.amazon.com/vpc/pricing/), and [Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/). The two RDS unit rates were retrieved through `aws pricing get-products` for PostgreSQL, Single-AZ, db.t4g.micro and gp3 in us-east-1. Secrets Manager API calls cost $0.05 per 10,000; the recorder uses its locally encrypted writer password, so it does not retrieve the administrator secret for each upload.

| Total monthly consumption | Approximate $100 runway |
|---|---:|
| $18.03 baseline | **169 days**, around 15 March 2027 |
| $20 planning allowance | **152 days** |
| $25 including additional AWS usage | **122 days** |

These are estimates, not a billing guarantee. They exclude tax, excess backup storage, log/transfer charges, burst CPU-credit charges and unrelated AWS resources. Charges and credit balances can update with a delay. Seven-day CloudWatch log retention bounds log accumulation. No NAT gateway, load balancer, hosted website or paid AI model is needed for recording. Local tracking can continue after cloud credits or the free account plan end.

**Daily uploading does not by itself make an always-running RDS instance cheaper.** The main bill is for provisioned infrastructure, not the number of rows or uploads. Shrinking records below the allocated 20 GB does not reduce that storage charge.

## Storage savings without losing the timeline

Implemented changes:

- Consecutive samples of the same activity extend one interval, rather than adding a row every second.
- Devices and repeated app/tab details are stored once and referenced by each interval.
- Browser titles are not duplicated as both a native window title and a tab title.
- Local timestamps use integer milliseconds instead of storing both ISO strings and numeric epochs.
- Website icons are **32 × 32 PNG**, at most **16 KiB each**, saved as binary data. SHA-256 deduplication reuses identical icons across sites and browsers. Base64 is used only in the local extension request, not in the database.
- Icons upload once; interval updates upload only their pending revision. No screenshots or page contents are collected.

The included `scripts/benchmark_storage.py` created 10,000 synthetic 30-second intervals with realistic-length browser titles, then migrated the same rows. Results after closing/checkpointing the database:

| Scenario | Previous format | New format | Reduction |
|---|---:|---:|---:|
| 100 repeated tab contexts | 4,128,768 bytes | 1,376,256 bytes | **66.7%** |
| Every interval has a distinct title | 4,419,584 bytes | 3,518,464 bytes | **20.4%** |

At an assumed **500 intervals/day**, those samples extrapolate to roughly **25–64 MB/year locally**, plus icons, SQLite journal space, logs and migration backup. This is an illustration, not a prediction of your usage or PostgreSQL's on-disk size. Constantly changing titles, long titles and very frequent switching increase storage. One thousand maximum-size favicons add at most **15.625 MiB of image payload**, plus database indexes and row overhead; typical icons can be much smaller.

Your existing small history contained 52 intervals and 15 distinct contexts when tested. Its database grew from 32,768 to 53,248 bytes because the new tables have fixed overhead; the savings emerge as history grows. Migration preserved all interval IDs, timestamps and acknowledgement revisions and passed SQLite's integrity check. The one-time `activity.pre-v2.sqlite3` backup remains in your user data folder for recovery. No old history was pruned.

Native Windows application icons remain in the local `icons` folder; their lookup keys are included in PostgreSQL. Website favicons themselves are copied to PostgreSQL. A future dashboard on another computer would also need the native icon files or a standard app-icon catalog.

For a much longer archive, losslessly compressed monthly JSONL or Parquet files can reduce archival space further. That would add an archive/query workflow; it is unnecessary at the measured size and is not enabled. Automatic deletion or coarse time aggregation would discard detail, so neither is enabled.

## Computer load and accuracy

A second 30.028-second measurement of the installed recorder, after the first cloud upload and with tracking active, observed:

| Measurement | Result |
|---|---:|
| CPU time consumed | 0.03125 CPU-seconds |
| Average CPU, relative to one logical core | 0.104% |
| Average CPU across this 20-thread computer | **0.0052%** |
| Combined process working set | **59.75 MiB** |
| Combined private memory | **35.04 MiB** |

This is a short foreground-sampling measurement, not a maximum-load guarantee. It includes the Python launcher and recorder, but not the browser's extension process, toolkit setup, initial migration or cloud upload. Those operations have separate temporary overhead.

Foreground sampling remains approximately once per second. Boundaries therefore have about one polling interval of uncertainty, and focus changes shorter than that can be missed. The extension sends focus/navigation events and a 30-second heartbeat; window-title matching prevents ordinary background tabs from being attributed to the foreground app. Identical titles across browser profiles can still be ambiguous. This measures foreground use, not productivity.

The current interval checkpoints every **60 seconds**, and immediately on activity transitions, orderly exit and dashboard reads. For eight uninterrupted hours, periodic checkpoints fall from 5,760 to 480, a **91.7% reduction**; transitions and manual refreshes add writes. The timing resolution stays the same, but an abrupt process/power failure can lose up to the most recent minute of an unchanged interval. Lock/idle/sleep gaps are excluded. Five minutes without input is the current default idle threshold; that grace period counts as active use.

The dashboard has no polling timer. The extension caches up to 64 converted favicons in memory and uses the browser's own favicon cache. It does not call an external icon service. An already-seen image bypasses repeated decoding in the recorder. Toolkit and deployment utilities are not part of the daily recording loop.

## Lower-cost alternatives

1. **Schedule database start/stop around uploads.** At an illustrative one billed running hour per day, the baseline would be about **$6.84/month**, including storage, the managed secret and the public IPv4 address even while stopped. Real start/stop times and minimum billing can increase this. It requires a reliable AWS scheduler, maintenance handling and missed-upload coordination; it is not enabled in this deployment. AWS automatically restarts an instance after seven consecutive stopped days. See [RDS stopping and billing behavior](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html).
2. **SQLite plus compressed S3 archives.** This avoids the standing RDS compute bill and suits backup-only usage, but S3 is object storage rather than a PostgreSQL database. It would change your requested cloud database design and is not enabled. See [S3 pricing](https://aws.amazon.com/s3/pricing/).
3. **Stay local until cloud querying is needed.** The website and recorder work without cloud services. Stopping the recorder does not stop RDS charges; retiring cloud resources is a separate action.

The first option could make the dollars last longer, but it does not extend the current free account plan's March 2027 end date. Retain the local database regardless of the cloud option.

## Setup and operational notes

- AWS Agent Toolkit is installed for Codex and Cursor, following [AWS's setup instructions](https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/setup-instructions/setup.md). Profile: `jeffrey-dev`; region: `us-east-1`. The skills catalog and an actual MCP initialization/tool-list handshake succeeded with eight tools. Intermittent HTTPS resets occurred during setup; the secret resolver retries transport failures. Restart the apps to load the new MCP server configuration.
- `jeffrey-dev` currently represents your AWS root session. The recorder uses a separate restricted PostgreSQL login and does not need that root session to stay signed in. A non-root development profile is preferable for subsequent infrastructure management.
- The database uses encrypted storage, certificate-verified TLS, deletion protection and one-day automatic backups. AWS rejected seven-day retention under the current free plan; the failed empty stack was rolled back before retrying. Backup retention does not truncate activity rows.
- Access is restricted to the current public IPv4 address `/32`. If your ISP/VPN changes that address, cloud uploads can fail until the CloudFormation `AllowedClientCidr` parameter is updated. Local tracking continues. Never broaden it to the entire internet.
- Reload **Computer Typology** in both `chrome://extensions` and `edge://extensions` to load version 1.1.0 and its favicon permission. Existing pairing tokens remain valid. Historical intervals will not gain icons retroactively. Missing browser-cache icons use the app icon. See [Chrome's favicon API](https://developer.chrome.com/docs/extensions/how-to/ui/favicons).
- The dashboard remains local at **http://127.0.0.1:43128**. Cloud storage does not expose your history as a public website. You can close the page and keep recording.
- Passwords are encrypted for the Windows user with DPAPI. The administrator secret is resolved only inside the official AWS `asm-exec` bootstrap wrapper. The website and extension receive no AWS/database credentials.

## Validation

All **24 Python tests and six extension tests** pass, covering migration, scheduling, privacy, icons and revision races. CloudFormation template validation, cfn-lint and native cfn-guard checks pass.

The live `typology_writer` connection verified TLS. PostgreSQL confirmed no DELETE privilege on activity events and no CREATE privilege on the public schema. Repeating an existing interval upsert twice did not duplicate rows. A PNG binary round trip passed in a transaction that was rolled back; no test icon was retained.

The on-demand dashboard and daily-time controls were inspected in the in-app browser. Chrome/Edge favicon capture still needs the existing extensions reloaded. These browsers were unavailable to this session's browser-control tool, so extraction was tested with browser API mocks; real-browser capture after reloading is not yet verified.

The initial login shortcut check failed with Windows Access Denied, and the installer disappeared. After the user allowed the installer, installation succeeded. Both Startup and Start menu shortcuts were verified against the installed runtime and app directory. Windows' startup inventory recognizes the Jeffr login entry, and the recorder reports Tracking with Daily upload ready. No explicit disabled entry was found in StartupApproved. A full sign-out/reboot was not performed during verification. No security protection was disabled by the installer.
