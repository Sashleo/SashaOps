# SashaOps Runbook

Owner-facing guide.  Every section answers one question: "What does this look
like, what does it mean, and what do I do?"  No step needs an AI agent.

---

## Contents

1. [Everyday operations](#1-everyday-operations)
2. [Upgrades and rollbacks](#2-upgrades-and-rollbacks)
3. [A source changed](#3-a-source-changed)
4. [A pipeline run failed](#4-a-pipeline-run-failed)
5. [The golden re-check failed](#5-the-golden-re-check-failed)
6. [A backup failed](#6-a-backup-failed)
7. [A migration failed](#7-a-migration-failed)
8. [A release needs rolling back](#8-a-release-needs-rolling-back)

---

## 1. Everyday operations

### Start the app
```
docker compose up -d
```

### Stop the app
```
docker compose down
```

### Check the app is running
Open `https://localhost:8000` in the browser.
If it does not load, run `docker logs sashaops` to see the startup output.

### See the health dashboard
Log in and go to **https://localhost:8000/health**.
Green = healthy.  Amber = needs attention soon.  Red = action required now.

### Run the data pipeline manually
```
docker exec sashaops python -m pipeline run --universe kse100
```
Only one run happens at a time.  The health page shows the last run result.

### Trigger a scraper refresh
On the **Dashboard** page, click **Refresh all**.  The Data Health card shows
progress.  Expect 2–10 minutes.

---

## 2. Upgrades and rollbacks

### Upgrade to a new release

A backup is taken automatically when the app starts, before any migration.

```bash
# 1. Pull the new tag
git fetch --tags
git checkout v1.x.y       # replace with the tag you want

# 2. Rebuild and restart
docker compose up --build -d

# 3. Check the health page
open https://localhost:8000/health
```

If anything looks wrong, roll back immediately (see section 8).

### Check that migrations ran
```
docker exec sashaops python3 -c "
from database.connection import init_database, get_db
init_database()
rows = get_db().execute(\"SELECT key, value FROM app_settings WHERE key='schema_version'\").fetchone()
print(rows)
"
```

---

## 3. A source changed

**What it looks like:**  The health page or Data Health card shows a source as
amber or red with "source_changed" in the detail.  This means the scraper
detected that the page it reads has changed its layout.

**What it means:**  The scraper is refusing to store data from a page it does
not recognise.  No wrong data has been stored; the rows are just not being
updated.

**What to do:**
1. Note which scraper is affected (shown on the Data Health card).
2. Open the source URL (also on the card) and check what changed.
3. Report the change in the project issue tracker.
4. Until the scraper is fixed, the last known-good value is still shown
   (with its age in amber).  It will go red once it exceeds the freshness
   limit (usually 3 × the scraper's normal cadence).

---

## 4. A pipeline run failed

**What it looks like:**  Health page shows "Pipeline last run: failed" with a
run ID.

**What to do:**
1. Check the pipeline log for that run ID:
   ```
   docker logs sashaops | grep <run_id>
   ```
2. Or read the run report:
   ```
   cat docs/pipeline/runs/<date>_<run_id>.md
   ```
3. Common failures and their reasons are listed in the quarantine reasons
   table at the bottom of the run report.
4. If the failure is a source change, see section 3.
5. If new documents could not be downloaded (HTTP 429 or 503), re-run
   with `--stages fetch` the next day.

---

## 5. The golden re-check failed

**What it looks like:**  The nightly run is marked failed and the dashboard
says "Golden re-check: N figures changed".

**What it means:**  A figure that was previously verified has a different
value in the new run.  This might mean a parser change introduced a
regression, or a company restated a prior year.

**What to do:**
1. Check `docs/pipeline/runs/<latest>.md` for the "golden diff" section.
2. For each changed figure:
   a. Open the relevant PDF at the cited page.
   b. If the new value is correct (a restatement), approve it in the review queue.
   c. If the old value was correct (a regression), revert the last parser
      change and open an issue.
3. Do not release a new version until the golden re-check passes at 0 wrong.

---

## 6. A backup failed

**What it looks like:**  Health page shows "Last backup: more than 2 days ago"
or "No backup found".

**What it means:**  The daily backup job did not run or could not write the
backup file.

**What to do:**
1. Run a manual backup:
   ```
   docker exec sashaops python3 -c "
   from database.connection import init_database, get_db
   init_database()
   from database.backup import backup_database
   p = backup_database()
   print('Backup:', p)
   "
   ```
2. Check disk space:
   ```
   docker exec sashaops df -h /app/data
   ```
3. If disk is full, prune old backups manually:
   ```
   docker exec sashaops python3 -c "
   from database.backup import _prune_backups
   _prune_backups('/app/data/backups', keep=7)
   "
   ```
4. Investigate what prevented the daily job from running:
   ```
   docker logs sashaops | grep backup
   ```

---

## 7. A migration failed

**What it looks like:**  The app does not start.  `docker logs sashaops` ends
with a migration error.

**What it means:**  A schema migration could not be applied.  The database
was not changed (all migrations copy into a new table, then rename — they
never DROP before copying).

**What to do:**
1. Note the error message from the logs.
2. The pre-migration backup is at `/app/data/backups/` — find the one taken
   just before the failed start (sorted by name, newest is first).
3. Roll back to the previous release (section 8) before attempting to restore.
4. If you need to restore the backup:
   ```
   docker exec -it sashaops python3 -c "
   from database.backup import restore_database
   restore_database('/app/data/backups/<backup_file>')
   "
   ```
5. Restart the container on the previous release tag.
6. Report the migration failure with the full log output.

---

## 8. A release needs rolling back

```bash
# 1. Find the previous release tag
git tag --sort=-version:refname | head -5

# 2. Check out the previous tag
git checkout v1.x.y       # the version before the one you want to undo

# 3. Rebuild and restart
docker compose up --build -d

# 4. Verify health page
open https://localhost:8000/health
```

If the database was migrated forward and the old code can't read it, restore
the pre-migration backup (section 7, step 4) before restarting on the old tag.

---

## Appendix: useful commands

| What | Command |
|---|---|
| Tail live logs | `docker logs -f sashaops` |
| Open a Python shell inside the container | `docker exec -it sashaops python3` |
| List backups | `docker exec sashaops ls -lh /app/data/backups/` |
| Check pipeline status | `docker exec sashaops python -m pipeline status` |
| Run the quality gate | `docker exec sashaops bash scripts/check.sh` |
| Explain one figure's lineage | `docker exec sashaops python -m pipeline explain LUCK FY2026 total_assets` |
