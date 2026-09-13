# Meta ads and the Ad monitor

Ported from the Goffstown CRM. Two pages, one private feature.

| Page | URL | What it reads | Numbers |
|---|---|---|---|
| **Meta ads** | `/meta/` | Our own ad accounts, through the Marketing API | Exact: spend, impressions, reach, clicks, per day and per campaign, plus every ad with its picture and words |
| **Ad monitor** | `/ad-monitor/` | Anyone's political ads, through the public Ad Library | Brackets ("$100 - $199") for the ad's whole life. Every figure shown is an estimate inside Meta's bracket |

Code: `meta_ads.py`, `ad_monitor.py`, `templates/meta/`, `migrations/031_meta_ads.sql`,
`migrations/032_meta_settings.sql`.

## Who can see it

Every user with the **admin** role, plus the super admin. Staff and whip logins cannot open
either page. There is nothing to grant: it is the role, the same as the other admin pages.
Both pages sit under **Ads** in the top bar. The settings page (where a token is pasted) is
narrower still: the super admin and `META_SETTINGS_EDITORS`.

`/meta/settings` is narrower: the super admin plus the emails in `META_SETTINGS_EDITORS`
(default `berryrm0@gmail.com`). Those editors can also open both pages without a grant.

## The short version: no server access needed

Push to `main` and the site redeploys. On start the app creates its own tables and starts
its own hourly sync. Then the person with the token opens **`/meta/settings`** and pastes it.
That is the whole setup. The rest of this page is for hosts that want to do it by hand.

**Where the token comes from, in order** (first hit wins):

| Shared key (Meta ads page) | Ad Library token (Ad monitor page) |
|---|---|
| 1. `META_ADS_TOKEN` in the environment | 1. `META_AD_LIBRARY_TOKEN` in the environment |
| 2. `META_API_KEY` (the Goffstown name) | 2. Ad Library token pasted on `/meta/settings` |
| 3. `META_ADS_TOKEN` in the CRM's `.env` next door (`META_TOKEN_ENV_FILE`) | 3. whatever the shared key resolves to |
| 4. shared key pasted on `/meta/settings` | |

Environment first, on purpose: the value in the server's `.env` is Chris's permanent
system-user token and must never be copied anywhere. A pasted token is a stand-in for when
nobody who holds that token can reach the box. Pasted tokens are AES-256-GCM encrypted under
`ENCRYPTION_KEY`, or under a key derived from `SECRET_KEY` when that is not set, and are never
shown again. The settings page shows who a token belongs to, when it expires, and which ad
accounts it can see, so a dying token is visible weeks ahead.

## Set up by hand (optional)

1. Run the migrations, or do nothing: the app runs `migrations/031` and `032` itself on
   every start (everything in them is `IF NOT EXISTS`).

   ```bash
   psql "$DATABASE_URL" -f migrations/031_meta_ads.sql
   ```

2. Add to `.env`. **Never put a Meta token value in this repo - it is public.**

   ```
   # The server key. A system-user access token (starts with EAA) with ads_read, serving
   # every ad account it can reach. Read at RUNTIME, in this order:
   #   1. META_ADS_TOKEN in this process's environment
   #   2. META_API_KEY  (the Goffstown name; an alias)
   #   3. META_ADS_TOKEN inside the CRM's .env next door, /opt/nh-civic-crm/.env
   # On the CTEHR server the token is already in (3), so this app needs NO copy of it.
   # Point somewhere else with META_TOKEN_ENV_FILE=/path/to/.env
   # META_ADS_TOKEN=EAA...

   # Optional. The Ad Library needs a token from a PERSON who finished ID confirmation
   # at facebook.com/ID; Meta may refuse a system-user token there. Falls back to the
   # server key when unset. Do not use a personal token that dies before election day.
   # META_AD_LIBRARY_TOKEN=EAA...

   # Only needed to paste a token for one account that the server key cannot see.
   # 64 hex characters:  openssl rand -hex 32
   ENCRYPTION_KEY=

   # Lets cron hit the two sync URLs without a login.
   CRON_SECRET=some-long-random-string

   # Optional. Ad Library search floor, so the 2024 cycle stays out. Default 2026-01-01.
   META_AD_CYCLE_START=2026-01-01
   ```

3. Restart the app (`requirements.txt` gained `cryptography` and `tzdata`). The Meta ads
   page says which variable it is reading the key from, and why if it cannot.

4. Open `/meta/`, click **Add account → Find my ad accounts**, pick one, **Test and save**.
   Open `/ad-monitor/`, click **Watch a page**, type the name, **Find their Facebook Page**.

## Keep it fresh

Both pages have **Sync** buttons. The hourly pull runs **inside the web process**: a thread
in each gunicorn worker wakes every ten minutes, takes a Postgres advisory lock so only one
worker does the work, and syncs whatever has not been attempted in the last 55 minutes.
Nothing to install. Set `META_AUTO_SYNC=0` to turn it off on a host that prefers cron, and
then add two lines to the crontab. Either call the URLs:

```
0  * * * *  curl -s -H "Authorization: Bearer $CRON_SECRET" https://YOUR-HOST/meta/cron/sync       >> /var/log/meta-sync.log 2>&1
30 * * * *  curl -s -H "Authorization: Bearer $CRON_SECRET" https://YOUR-HOST/ad-monitor/cron/sync >> /var/log/meta-sync.log 2>&1
```

or run the CLI commands from the repo directory (needs `.env` there):

```
0  * * * *  cd /opt/nh-candidate-recruitment && flask --app app meta-sync        >> /var/log/meta-sync.log 2>&1
30 * * * *  cd /opt/nh-candidate-recruitment && flask --app app ad-monitor-sync >> /var/log/meta-sync.log 2>&1
```

The Ad Library is read one page at a time with a gap between calls, on purpose: Meta
throttles the archive and answers a throttle with the same "(#10) does not have permission"
sentence it uses for a real refusal. Transient errors are retried; a dead token is not.

## Reading the Ad monitor honestly

- Only ads the advertiser **declared political** are in the archive. A page with nothing
  there may simply never have done the political-ads authorisation.
- Meta's bottom band is "under $100" with no floor. Averaging bands overstates any page that
  runs many small boosts, so each page's cost per thousand impressions is worked out from its
  own larger ads and its small ads are priced at that rate, then pinned inside Meta's band.
- The only exact numbers Meta publishes for someone else's page are on the **Disclaimers**
  panel of their Ad Library page (all-time, and last 7 days). Type those in with **Edit**;
  they are kept as a dated series and shown next to the estimate so it can be checked.
- Each ad's spend is for its whole run. The chart comes from one reading a day taken here,
  so it only goes back to the day the watch started.
