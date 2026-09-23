# Cloud cost monitor

Ross renders After Effects on Windows GPU VMs he rents in the Google Cloud project
**gcloudgpu**. One costs about $0.76 to $3.21 an hour, and one left on overnight is real money.
This page says what is running and what it is costing, and emails a warning when a VM has been
on too long.

| | |
|---|---|
| Page | `/cost-monitor/`, in the Private menu (the faint padlock, bottom left) as **Cloud Costs** |
| Code | `cost_monitor.py`, `templates/cost/`, `migrations/038_cost_monitor.sql`, `tests/test_cost_monitor.py` |
| Off until | `GCP_COST_PROJECT` is set. Without it nothing is called, nothing is sent, and the page says "Not set up" |
| Talks to | the Compute Engine API (read only), and BigQuery if the billing export is set up |
| Writes to Google | nothing, unless the Stop button is turned on (see below) |

## What it shows

- **A banner.** Red when anything is running: each VM, how long it has been up, its estimated
  cost an hour and so far. Green when nothing is. Amber when Google could not be read, with the
  reason (a missing role, an expired token).
- **Four numbers.** VMs running now; estimated burn rate an hour; estimated spend today (since
  midnight in New Hampshire); actual spend this month from Google's bill, or "not set up".
- **Every VM in the project.** Name, zone, Windows or Linux, machine type with vCPUs and RAM,
  GPU, running since, hours in the current run, est. $/hr, est. cost of this run. A stopped VM
  shows its last run, dimmed. On a phone each VM is a card.
- **Actual spend** (when set up): this month by service and by day, cost less credits.
- **Checks and warnings:** when the page last read Google, when the background check last ran
  and whether it failed, who gets the warnings and at what thresholds, the last ten warnings sent,
  and which credential it is using (by name, never the value).
- **How the estimates are made:** each VM's rate, part by part.

### Estimates and actuals are never mixed

Every figure marked **est.** is worked out here: machine (vCPUs + RAM) + GPUs + the Windows
licence ($0.046 per vCPU-hour), at Google's list prices. The prices were read from Google's own
price list (the Cloud Billing Catalog API) on 2026-09-23; each rate in `cost_monitor.py` has its
SKU id beside it. Worked through:

| Machine | Machine | GPU | Windows | Est. $/hr |
|---|---|---|---|---|
| n2-standard-8, Windows | 8 vCPU x 0.031611 + 32 GB x 0.004237 = 0.3885 | - | 8 x 0.046 = 0.368 | **0.7565** |
| g2-standard-32, Windows | 32 x 0.024988 + 128 GB x 0.002927 = 1.1743 | 1 L4 = 0.5600 | 32 x 0.046 = 1.472 | **3.2064** |

- Priced for us-east1, us-central1 and us-west1 (one price tier). Families: n2, n1, n2d, e2, c3,
  g2. GPUs: L4, T4, P4, P100, V100. Anything else shows **rate unknown**, never $0, and every
  total says how many VMs it leaves out. So do custom and shared-core machine types.
- **Not counted:** disks, snapshots, IP addresses and network traffic (these bill even while a
  VM is stopped); sustained-use and committed-use discounts; credits; tax. A Spot VM is shown at
  the on-demand price and flagged; it really costs less.
- **Actual** spend comes only from Google's billing export (below). It is the real bill, but it
  runs hours behind, which is why the estimates exist.

To change or add a rate without a deploy, set `GCP_COST_RATES_JSON`. It is laid over the
built-in table section by section:

```
GCP_COST_RATES_JSON={"families": {"c4": {"vcpu": 0.0, "gb": 0.0}}, "gpus": {"nvidia-a100-80gb": 0.0}}
```

Sections: `families` ($ per vCPU-hour and per GB-hour), `gpus` ($ per GPU-hour), `windows_per_vcpu`,
`regions` (list), and `machine_types` (a whole type at a fixed $/hour, Linux; for G2 that
published price already includes its L4, so it is not added twice).

## Who can see it

- The super admin, and every email in `GCP_COST_ADMINS` (default `berryrm0@gmail.com`).
- Anyone granted **Cost Monitor** on Manage Access (feature slug `cost_monitor`), like the
  other private features.
- The **Stop VM** button: only the super admin and `GCP_COST_ADMINS`, and only when
  `GCP_COST_ALLOW_STOP=1`.

## Every setting

All of these go in the server's `.env` (`/opt/nh-candidate-recruitment/.env`). **This repo is
public: never put a key, token or credential file in it.** The app reads them at runtime.

| Variable | Default | What it does |
|---|---|---|
| `GCP_COST_PROJECT` | *(unset: feature off)* | The Google Cloud project to watch: `gcloudgpu` |
| `GCP_COST_CREDENTIALS_JSON` | | The service account's key: the JSON itself, or base64 of it (easier to quote). Used first on the server |
| `GOOGLE_APPLICATION_CREDENTIALS` | | Instead of the above: the path to the key file (or a Workload Identity Federation config file) |
| `GCP_ACCESS_TOKEN` | | **Developer laptops only, never the server.** A token from `gcloud auth print-access-token`; it lasts an hour. Wins over everything else |
| `GCP_BILLING_BQ_TABLE` | *(unset: "not set up")* | The billing export table, `project.dataset.table` |
| `GCP_COST_ALERT_HOURS` | `3` | Warn when a VM has been on this many hours in one run. A list such as `3,8,24` warns again at each mark |
| `GCP_COST_ALERT_USD` | *(unset: off)* | Warn once a day when today's estimated spend passes this many dollars |
| `GCP_COST_ALERT_TO` | the `GCP_COST_ADMINS` list | Who gets the warning emails, comma separated |
| `GCP_COST_ADMINS` | `berryrm0@gmail.com` | Who sees the page without a grant, and who may use Stop |
| `GCP_COST_ALLOW_STOP` | *(off)* | `1` shows the Stop VM button (needs the extra role below) |
| `GCP_COST_AUTO_CHECK` | `1` | `0` turns off the in-app ten-minute check, for a host that uses cron instead |
| `GCP_COST_RATES_JSON` | | Rate overrides, above |

Already on the server and shared with the rest of the app: `CRON_SECRET` (for the cron URL),
`APP_URL` (the link in the email), and the SES settings `send_email` uses (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `SES_SENDER_EMAIL`, `SES_SENDER_NAME`).

Where the credential is looked for, first hit wins: `GCP_ACCESS_TOKEN`, then
`GCP_COST_CREDENTIALS_JSON`, then the file in `GOOGLE_APPLICATION_CREDENTIALS`, then the file
`gcloud auth application-default login` writes. No Google library is needed for a key: it is
signed with `cryptography`, already in `requirements.txt`.

## One-time Google Cloud setup (Ross or Chris)

Nothing in this section has been done. The app never creates or changes anything in Google Cloud.

### 1. A read-only service account

```
gcloud iam service-accounts create cost-monitor --project=gcloudgpu \
    --display-name="NH tracker cost monitor (read-only)"

gcloud projects add-iam-policy-binding gcloudgpu \
    --member="serviceAccount:cost-monitor@gcloudgpu.iam.gserviceaccount.com" \
    --role="roles/compute.viewer"
```

`roles/compute.viewer` lists VMs and machine types and cannot change anything.

### 2. DECISION for Ross and Chris: how the server proves it is that account

The server is not on Google Cloud, so it needs a credential. **New Google Cloud organizations
block service-account key creation by default** (organization policy
`iam.disableServiceAccountKeyCreation`; on some newer organizations the managed form
`iam.managed.disableServiceAccountKeyCreation`). `gcloud iam service-accounts keys create`
fails until that is dealt with. Two ways:

**A. A key, with a policy exception for this one project.** Quickest. Someone with Organization
Policy Administrator (`roles/orgpolicy.policyAdmin`, granted at the organization) turns the
policy off for `gcloudgpu` only:

```
gcloud resource-manager org-policies disable-enforce iam.disableServiceAccountKeyCreation --project=gcloudgpu
gcloud iam service-accounts keys create cost-monitor-key.json \
    --iam-account=cost-monitor@gcloudgpu.iam.gserviceaccount.com
base64 -w0 cost-monitor-key.json     # on a Mac: base64 -i cost-monitor-key.json
                                     # paste the output into .env as GCP_COST_CREDENTIALS_JSON
```

If the organization enforces the managed form instead, the exception is a small policy file,
applied with `gcloud org-policies set-policy policy.yaml`:

```
name: projects/gcloudgpu/policies/iam.managed.disableServiceAccountKeyCreation
spec:
  rules:
  - enforce: false
```

Then delete `cost-monitor-key.json` from the laptop (and turn the policy back on if no other key
is wanted). The key can only read VM lists (and billing, if step 4 is done), and only in
`gcloudgpu`. It does not expire on its own: rotate it once a year, and delete it in the console
(IAM, Service accounts, cost-monitor, Keys) if the server is ever retired.

**B. Workload Identity Federation.** No long-lived key at all, but the server needs an identity
provider Google can trust (an OIDC or AWS identity it already has). A plain VPS has none built
in, so this is more setup. If chosen: add `google-auth` to `requirements.txt` (the app says so
when it is missing) and put the federation config file (not a secret) in
`GOOGLE_APPLICATION_CREDENTIALS` or `GCP_COST_CREDENTIALS_JSON`.

A read-only key on one project (A) is the smaller job and a small risk; B removes the key
altogether. Either works with this code.

### 3. Turn it on

In `/opt/nh-candidate-recruitment/.env`:

```
GCP_COST_PROJECT=gcloudgpu
GCP_COST_CREDENTIALS_JSON=<base64 of the key, from step 2>
```

Restart the app. The tables create themselves on start (`migrations/038_cost_monitor.sql` is
all `IF NOT EXISTS`). Open **Cloud Costs**: it should list `render-builder`. If it cannot read
Google it says why, in the amber banner.

### 4. Optional: actual spend (the billing export to BigQuery)

Without this the page shows estimates only and says "not set up" for actual spend. To set it up
(needs Billing Account Administrator on the billing account behind `gcloudgpu`):

1. Make a dataset for it: `bq --location=US mk --dataset gcloudgpu:billing_export`
2. Google Cloud console, **Billing**, the billing account, **Billing export**, **BigQuery export**.
   Under **Standard usage cost**, **Edit settings**: project `gcloudgpu`, dataset
   `billing_export`, **Save**. Google's guide:
   https://cloud.google.com/billing/docs/how-to/export-data-bigquery-setup
3. After a few hours a table appears in the dataset, named
   `gcp_billing_export_v1_<billing account id, with underscores>`. Set
   `GCP_BILLING_BQ_TABLE=gcloudgpu.billing_export.gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX`
4. Let the service account read it and run queries:

   ```
   gcloud projects add-iam-policy-binding gcloudgpu \
       --member="serviceAccount:cost-monitor@gcloudgpu.iam.gserviceaccount.com" --role="roles/bigquery.jobUser"
   gcloud projects add-iam-policy-binding gcloudgpu \
       --member="serviceAccount:cost-monitor@gcloudgpu.iam.gserviceaccount.com" --role="roles/bigquery.dataViewer"
   ```

   (`roles/bigquery.dataViewer` can be granted on the `billing_export` dataset alone instead, in
   the console under the dataset's **Sharing, Permissions**.)

The query reads only `gcloudgpu`'s rows for the current invoice month, is capped at 2 GB scanned
(about a cent; past that it fails rather than bills), and its result is kept for 30 minutes.

### 5. Optional: the Stop VM button

Off by default. To turn it on, give the service account one more permission, stopping VMs and
nothing else, through a custom role:

```
gcloud iam roles create costMonitorStop --project=gcloudgpu \
    --title="Cost monitor: stop VMs" --permissions=compute.instances.stop --stage=GA
gcloud projects add-iam-policy-binding gcloudgpu \
    --member="serviceAccount:cost-monitor@gcloudgpu.iam.gserviceaccount.com" \
    --role="projects/gcloudgpu/roles/costMonitorStop"
```

Then set `GCP_COST_ALLOW_STOP=1`. The button appears only to the super admin and
`GCP_COST_ADMINS`, only on a running VM, and leads to a confirm page that re-reads the VM; the
stop itself is a POST with the app's CSRF token and must match the VM's id, so a deleted and
re-created VM with the same name is never stopped by mistake. It is the same as Stop in the
console: the disk is kept and the VM can be started again.

## The check and the warnings

- **The in-app timer.** Each gunicorn worker runs a thread that wakes every ten minutes; a
  Postgres advisory lock means only one does the check. Nothing to install. `GCP_COST_AUTO_CHECK=0`
  turns it off.
- **Or cron.** Either call the URL with the bearer secret:

  ```
  */10 * * * *  curl -s -H "Authorization: Bearer $CRON_SECRET" https://YOUR-HOST/cost-monitor/cron/check
  ```

  or run `cd /opt/nh-candidate-recruitment && flask --app app cost-monitor-check`.
- **The email** goes through the app's existing AWS SES sender (`send_email`), to
  `GCP_COST_ALERT_TO`. No new service.
- **Once only.** One email per VM run per threshold: a VM that restarts starts a new run. With
  `GCP_COST_ALERT_HOURS=3,8,24` a VM left on all night gets three emails, not one every ten
  minutes; a VM first seen already past two marks gets one email. The dollar line warns once per
  day. Each warning is recorded before it is sent, so two workers never both send it; if the
  email fails the record is removed and the next check tries again. If the database is down, no
  warning is sent at all, because without it the same one would repeat.
- **Today's estimate** counts every run that overlapped today, including a VM that ran this
  morning and was then stopped or deleted (the check remembers each run it sees in
  `cost_monitor_runs`). A deleted VM's run is counted to the last time a check saw it, so it can
  be one check (ten minutes) short.

## Tests

```
python -m unittest discover -s tests -v
```

No test calls Google: every answer is made up. The warning tests need Postgres, the same way the
Meta port was tested: set `COST_MONITOR_TEST_DATABASE_URL` to a throwaway database, or
`pip install pgserver` and they start an embedded Postgres 16. Without either they are skipped.

## Adding another cost source later

Everything after the Google section of `cost_monitor.py` works on plain dicts carrying a
`source` field, and the tables are keyed by source. A second provider needs a function that
returns the same dict shape as `_normalize()` and a place in the page; the check, the warnings
and today's estimate work unchanged.
