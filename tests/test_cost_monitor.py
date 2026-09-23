"""Tests for cost_monitor.py. Nothing here talks to Google: every answer is made up below.

Run from the repo root:

    python -m unittest discover -s tests -v

The alert tests need a real Postgres, the same way the Meta port was tested: set
COST_MONITOR_TEST_DATABASE_URL to a throwaway database, or `pip install pgserver` and they start
an embedded Postgres 16 of their own. Without either they are skipped; everything else runs
wherever requirements.txt is installed.
"""
import base64
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flask_login import UserMixin, current_user  # noqa: E402

import cost_monitor as cm  # noqa: E402
import private_features  # noqa: E402

PROJECT = 'gcloudgpu'
NOW = datetime(2026, 9, 23, 18, 0, tzinfo=timezone.utc)   # 2:00 PM in New Hampshire
PDT = timezone(timedelta(hours=-7))                        # Google writes its timestamps in Pacific
DEV_TOKEN = 'dev-token-for-tests'

SHAPES = {
    'n2-standard-8': {'guestCpus': 8, 'memoryMb': 32768},
    'n1-standard-8': {'guestCpus': 8, 'memoryMb': 30720},
    'g2-standard-32': {'guestCpus': 32, 'memoryMb': 131072,
                       'accelerators': [{'guestAcceleratorType': 'nvidia-l4', 'guestAcceleratorCount': 1}]},
    'c4-standard-8': {'guestCpus': 8, 'memoryMb': 30720},
    'e2-medium': {'guestCpus': 2, 'memoryMb': 4096, 'isSharedCpu': True},
}
WINDOWS = 'https://www.googleapis.com/compute/v1/projects/windows-cloud/global/licenses/windows-server-2022-dc'
DEBIAN = 'https://www.googleapis.com/compute/v1/projects/debian-cloud/global/licenses/debian-12-bookworm'


def gts(dt):
    """A timestamp the way Compute Engine writes one: 2026-09-23T07:47:54.658-07:00."""
    return dt.astimezone(PDT).isoformat(timespec='milliseconds')


def vm(name, iid, machine_type='n2-standard-8', zone='us-east1-c', status='RUNNING', started=None,
       stopped=None, windows=True, gpus=None, spot=False):
    base = f'https://www.googleapis.com/compute/v1/projects/{PROJECT}/zones/{zone}'
    started = started or NOW - timedelta(hours=1)
    inst = {
        'kind': 'compute#instance', 'id': iid, 'name': name, 'status': status,
        'zone': base, 'machineType': f'{base}/machineTypes/{machine_type}',
        'creationTimestamp': gts(started - timedelta(days=1)), 'lastStartTimestamp': gts(started),
        'scheduling': {'provisioningModel': 'SPOT' if spot else 'STANDARD', 'preemptible': spot},
        'disks': [{'boot': True, 'licenses': [WINDOWS if windows else DEBIAN]}],
    }
    if stopped:
        inst['lastStopTimestamp'] = gts(stopped)
    if gpus:
        inst['guestAccelerators'] = [{'acceleratorType': f'{base}/acceleratorTypes/{k}', 'acceleratorCount': n}
                                     for k, n in gpus.items()]
    return inst


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else ''

    def json(self):
        if self._body is None:
            raise ValueError('no body')
        return self._body


class FakeGoogle:
    """Answers the handful of URLs the module calls, and writes down every call."""

    def __init__(self):
        self.instances = []
        self.shapes = dict(SHAPES)
        self.pages = 1
        self.unreachable = []
        self.fail = {}        # URL fragment -> (status, body)
        self.bq = []          # queued BigQuery answers
        self.calls = []
        self.stopped = []
        self.tokens_issued = 0

    def __call__(self, method, url, **kw):
        self.calls.append(dict(kw, method=method, url=url))
        for part, (status, body) in self.fail.items():
            if part in url:
                return Resp(status, body)
        if url == 'https://oauth2.googleapis.com/token':
            self.tokens_issued += 1
            return Resp(200, {'access_token': f'issued-{self.tokens_issued}', 'expires_in': 3599})
        if url.endswith(f'/projects/{PROJECT}/aggregated/instances'):
            return self._aggregated((kw.get('params') or {}).get('pageToken'))
        m = re.search(r'/machineTypes/([^/?]+)$', url)
        if m:
            shape = self.shapes.get(m.group(1))
            return Resp(200, dict(shape, name=m.group(1))) if shape else Resp(404, {'error': {'message': 'not found'}})
        m = re.search(r'/zones/([^/]+)/instances/([^/]+)/stop$', url)
        if m and method == 'POST':
            self.stopped.append((m.group(1), m.group(2)))
            return Resp(200, {'kind': 'compute#operation', 'status': 'RUNNING'})
        if '/queries' in url and self.bq:
            return Resp(200, self.bq.pop(0))
        return Resp(404, {'error': {'message': 'not faked: ' + url}})

    def _aggregated(self, token):
        page = int((token or 'p0')[1:])
        mine = self.instances[page::self.pages]
        body = {'kind': 'compute#instanceAggregatedList', 'items': {
            'zones/us-east1-c': {'instances': mine} if mine else {'warning': {'code': 'NO_RESULTS_ON_PAGE'}},
            'zones/europe-west1-b': {'warning': {'code': 'NO_RESULTS_ON_PAGE'}}}}
        if page + 1 < self.pages:
            body['nextPageToken'] = f'p{page + 1}'
        if self.unreachable:
            body['unreachables'] = self.unreachable
        return Resp(200, body)

    def urls(self, fragment):
        return [c for c in self.calls if fragment in c['url']]


class Base(unittest.TestCase):
    ENV = {'GCP_COST_PROJECT': PROJECT, 'GCP_ACCESS_TOKEN': DEV_TOKEN}

    def setUp(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('GCP_') and k not in ('GOOGLE_APPLICATION_CREDENTIALS', 'CRON_SECRET', 'APP_URL')}
        env.update(self.ENV)
        for p in (mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(cm, '_well_known_adc', return_value=None),
                  mock.patch.object(cm, 'get_db_connection', None),
                  mock.patch.object(cm, 'release_db_connection', None),
                  mock.patch.object(cm, 'send_email', None)):
            p.start()
            self.addCleanup(p.stop)
        cm.clear_caches()
        self.addCleanup(cm.clear_caches)
        self.google = FakeGoogle()
        p = mock.patch.object(cm, '_http', self.google)
        p.start()
        self.addCleanup(p.stop)


# =============================================================================
# RATES
# =============================================================================

class RatesTest(Base):
    N2 = {'vcpus': 8, 'memory_gb': 32.0, 'gpus': {}}
    G2 = {'vcpus': 32, 'memory_gb': 128.0, 'gpus': {'nvidia-l4': 1}}
    N1 = {'vcpus': 8, 'memory_gb': 30.0, 'gpus': {}}

    def test_n2_standard_8_windows_is_about_76_cents(self):
        e = cm.estimate('n2-standard-8', 'us-east1', self.N2, windows=True)
        self.assertEqual(e['rate'], 0.7565)
        self.assertEqual([p for _, p in e['parts']], [0.3885, 0.368])

    def test_g2_standard_32_windows_is_about_3_21(self):
        e = cm.estimate('g2-standard-32', 'us-east1', self.G2, windows=True)
        self.assertEqual(e['rate'], 3.2064)
        self.assertEqual([p for _, p in e['parts']], [1.1743, 0.56, 1.472])

    def test_an_l4_listed_on_the_vm_as_well_is_counted_once(self):
        e = cm.estimate('g2-standard-32', 'us-central1', self.G2, {'nvidia-l4': 1}, windows=True)
        self.assertEqual(e['rate'], 3.2064)

    def test_a_t4_attached_to_an_n1_is_added(self):
        e = cm.estimate('n1-standard-8', 'us-east1', self.N1, {'nvidia-tesla-t4': 1})
        self.assertEqual(e['rate'], round(8 * 0.031611 + 30 * 0.004237 + 0.35, 4))

    def test_unknown_family_is_rate_unknown_never_zero(self):
        e = cm.estimate('c4-standard-8', 'us-east1', {'vcpus': 8, 'memory_gb': 30.0}, windows=True)
        self.assertIsNone(e['rate'])
        self.assertIn('c4', e['unknown'])
        self.assertEqual(cm.usd(e['rate']), 'rate unknown')

    def test_a_region_off_the_price_list_is_unknown(self):
        self.assertIsNone(cm.estimate('n2-standard-8', 'europe-west1', self.N2)['rate'])

    def test_custom_and_shared_core_types_are_unknown(self):
        self.assertIsNone(cm.estimate('n2-custom-8-32768', 'us-east1', self.N2)['rate'])
        self.assertIsNone(cm.estimate('e2-medium', 'us-east1', {'vcpus': 2, 'memory_gb': 4.0, 'shared_cpu': True})['rate'])

    def test_one_unpriced_gpu_makes_the_whole_rate_unknown(self):
        e = cm.estimate('n1-standard-8', 'us-east1', self.N1, {'nvidia-h100-80gb': 1})
        self.assertIsNone(e['rate'])
        self.assertIn('nvidia-h100-80gb', e['unknown'])

    def test_env_json_adds_a_family_and_keeps_the_rest(self):
        os.environ['GCP_COST_RATES_JSON'] = json.dumps({'families': {'c4': {'vcpu': 0.03, 'gb': 0.004}}})
        self.assertEqual(cm.estimate('c4-standard-8', 'us-east1', {'vcpus': 8, 'memory_gb': 30.0})['rate'], 0.36)
        self.assertEqual(cm.estimate('n2-standard-8', 'us-east1', self.N2, windows=True)['rate'], 0.7565)
        self.assertIsNone(cm.rates_problem())

    def test_a_fixed_machine_price_is_not_charged_its_own_gpu_twice(self):
        os.environ['GCP_COST_RATES_JSON'] = json.dumps({'machine_types': {'g2-standard-32': 1.7344}})
        e = cm.estimate('g2-standard-32', 'us-east1', self.G2, {'nvidia-l4': 1}, windows=True)
        self.assertEqual(e['rate'], round(1.7344 + 32 * 0.046, 4))

    def test_bad_override_json_falls_back_and_says_so(self):
        os.environ['GCP_COST_RATES_JSON'] = '{not json'
        self.assertEqual(cm.rates(), cm.RATES)
        self.assertIn('not valid JSON', cm.rates_problem())

    def test_money_labels(self):
        self.assertEqual(cm.usd(1234.5), '$1,234.50')
        self.assertEqual(cm.usd(-2.5), '-$2.50')
        self.assertEqual(cm.usd(None), 'rate unknown')


# =============================================================================
# THE VM LIST
# =============================================================================

class SnapshotTest(Base):

    def test_off_without_a_project_and_calls_nothing(self):
        del os.environ['GCP_COST_PROJECT']
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertFalse(snap['enabled'])
        self.assertEqual(cm.run_check(now=NOW)['enabled'], False)
        self.assertEqual(cm.actual_spend(now=NOW), {'configured': False})
        self.assertEqual(self.google.calls, [])

    def test_a_running_windows_vm(self):
        self.google.instances = [vm('render-builder', '101', started=NOW - timedelta(hours=3, minutes=30))]
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertTrue(snap['ok'], snap['error'])
        r = snap['running'][0]
        self.assertEqual((r['name'], r['zone'], r['region'], r['machine_type']),
                         ('render-builder', 'us-east1-c', 'us-east1', 'n2-standard-8'))
        self.assertTrue(r['windows'])
        self.assertEqual(r['os_label'], 'Windows Server 2022')
        self.assertAlmostEqual(r['hours'], 3.5)
        self.assertEqual(r['rate'], 0.7565)
        self.assertEqual(r['run_cost'], round(0.7565 * 3.5, 2))
        self.assertEqual(snap['burn_rate'], 0.7565)
        self.assertEqual(cm.when_label(r['started_at'], NOW), '10:30 AM')
        auth = self.google.urls('/aggregated/instances')[0]['headers']['Authorization']
        self.assertEqual(auth, 'Bearer ' + DEV_TOKEN)

    def test_a_stopped_vm_shows_its_last_run_and_is_not_running(self):
        start, stop = NOW - timedelta(hours=6), NOW - timedelta(hours=4)
        self.google.instances = [vm('old', '102', status='TERMINATED', started=start, stopped=stop, windows=False)]
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        r = snap['resources'][0]
        self.assertEqual(snap['running'], [])
        self.assertFalse(r['on'])
        self.assertEqual(r['os_label'], 'Linux')
        self.assertAlmostEqual(r['hours'], 2.0)
        self.assertEqual(r['run_cost'], round(0.3885 * 2, 2))
        self.assertEqual(r['stopped_at'], stop.replace(microsecond=0))

    def test_a_g2_shows_its_l4_and_spot_is_flagged(self):
        self.google.instances = [vm('gpu', '103', machine_type='g2-standard-32', spot=True)]
        r = cm.gcp_snapshot(fresh=True, now=NOW)['running'][0]
        self.assertEqual(r['gpu_label'], '1x L4')
        self.assertTrue(r['spot'])
        self.assertEqual(r['rate'], 3.2064)

    def test_a_starting_vm_is_in_the_banner_but_not_billed_yet(self):
        self.google.instances = [vm('boot', '104', status='STAGING')]
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertEqual(len(snap['running']), 1)
        self.assertIsNone(snap['running'][0]['hours'])
        self.assertEqual(snap['burn_rate'], 0.0)

    def test_unknown_machine_type_is_left_out_of_totals_and_counted(self):
        self.google.instances = [vm('a', '105'), vm('b', '106', machine_type='c4-standard-8')]
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertEqual(snap['burn_rate'], 0.7565)
        self.assertEqual(snap['burn_unknown'], 1)
        unknown = [r for r in snap['resources'] if r['name'] == 'b'][0]
        self.assertIsNone(unknown['rate'])
        self.assertIsNone(unknown['run_cost'])

    def test_every_page_is_read_and_machine_sizes_are_asked_once(self):
        self.google.pages = 3
        self.google.instances = [vm(f'vm-{i}', str(200 + i)) for i in range(5)]
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertEqual(len(snap['resources']), 5)
        self.assertEqual(len(self.google.urls('/aggregated/instances')), 3)
        self.assertEqual(len(self.google.urls('/machineTypes/')), 1)

    def test_a_zone_google_could_not_read_is_named(self):
        self.google.unreachable = ['zones/us-east1-d']
        self.assertEqual(cm.gcp_snapshot(fresh=True, now=NOW)['unreachable'], ['zones/us-east1-d'])

    def test_permission_denied_is_explained(self):
        self.google.fail['/aggregated/instances'] = (403, {'error': {'message': "Required 'compute.instances.list' permission"}})
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertFalse(snap['ok'])
        self.assertIn('Permission denied (403)', snap['error'])
        self.assertIn('docs/cost-monitor.md', snap['error'])

    def test_an_expired_dev_token_says_to_print_a_new_one(self):
        self.google.fail['/aggregated/instances'] = (401, {'error': {'message': 'Invalid Credentials'}})
        self.assertIn('lasts an hour', cm.gcp_snapshot(fresh=True, now=NOW)['error'])

    def test_the_list_is_kept_for_a_minute(self):
        self.google.instances = [vm('a', '107')]
        cm.gcp_snapshot(now=NOW)
        cm.gcp_snapshot(now=NOW)
        self.assertEqual(len(self.google.urls('/aggregated/instances')), 1)
        cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertEqual(len(self.google.urls('/aggregated/instances')), 2)

    def test_google_timestamps(self):
        want = datetime(2026, 9, 23, 14, 47, 54, 658000, tzinfo=timezone.utc)
        self.assertEqual(cm._parse_ts('2026-09-23T07:47:54.658-07:00'), want)
        self.assertEqual(cm._parse_ts('2026-09-23T14:47:54.658123456Z'), want.replace(microsecond=658123))
        self.assertEqual(cm._parse_ts('2026-09-23T14:47:54Z'), want.replace(microsecond=0))
        self.assertIsNone(cm._parse_ts('yesterday'))
        self.assertIsNone(cm._parse_ts(None))

    def test_today_is_the_new_hampshire_day(self):
        # 1:00 AM UTC on the 24th is still the 23rd in New Hampshire.
        late = datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(cm._day_start(late), datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc))
        self.google.instances = [vm('late', '108', started=late - timedelta(hours=2))]
        snap = cm.gcp_snapshot(fresh=True, now=late)
        today = cm.estimate_today(snap['resources'], late)
        self.assertEqual(str(today['date']), '2026-09-23')
        self.assertEqual(today['total'], round(0.7565 * 2, 2))

    def test_no_database_means_no_warning(self):
        self.google.instances = [vm('a', '109', started=NOW - timedelta(hours=5))]
        sent = []
        cm.send_email = lambda *a, **k: sent.append(a) or True
        cm.get_db_connection = mock.Mock(side_effect=RuntimeError('database is down'))
        out = cm.run_check(now=NOW)
        self.assertFalse(out['ok'])
        self.assertIn('No warning was sent', out['error'])
        self.assertEqual(sent, [])


# =============================================================================
# CREDENTIALS
# =============================================================================

_KEY = None


def _test_key():
    """A throwaway RSA key made fresh for this run. Nothing real is in this file."""
    global _KEY
    if _KEY is None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        _KEY = (key, pem)
    return _KEY


def _b64d(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


class CredentialsTest(Base):
    ENV = {'GCP_COST_PROJECT': PROJECT}

    def service_account(self):
        return {'type': 'service_account', 'project_id': PROJECT, 'private_key_id': 'test-key-id',
                'private_key': _test_key()[1], 'client_email': f'cost-monitor@{PROJECT}.iam.gserviceaccount.com',
                'token_uri': 'https://oauth2.googleapis.com/token'}

    def test_no_credentials_is_a_plain_error(self):
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertFalse(snap['ok'])
        self.assertTrue(snap['error'].startswith('No Google credentials'))
        self.assertIsNone(cm.credentials_source())

    def test_a_service_account_key_is_signed_used_and_kept(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        os.environ['GCP_COST_CREDENTIALS_JSON'] = json.dumps(self.service_account())
        self.assertTrue(cm.gcp_snapshot(fresh=True, now=NOW)['ok'])
        grant = self.google.urls('oauth2.googleapis.com/token')[0]['data']
        self.assertEqual(grant['grant_type'], 'urn:ietf:params:oauth:grant-type:jwt-bearer')
        head, claims, sig = grant['assertion'].split('.')
        _test_key()[0].public_key().verify(_b64d(sig), f'{head}.{claims}'.encode(), padding.PKCS1v15(), hashes.SHA256())
        claims = json.loads(_b64d(claims))
        self.assertEqual(claims['iss'], f'cost-monitor@{PROJECT}.iam.gserviceaccount.com')
        self.assertEqual(claims['aud'], 'https://oauth2.googleapis.com/token')
        self.assertEqual(claims['exp'] - claims['iat'], 3600)
        self.assertEqual(json.loads(_b64d(head))['kid'], 'test-key-id')
        self.assertEqual(self.google.urls('/aggregated/')[0]['headers']['Authorization'], 'Bearer issued-1')
        cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertEqual(self.google.tokens_issued, 1)   # the hour-long token is reused
        self.assertEqual(cm.credentials_source(), 'GCP_COST_CREDENTIALS_JSON')

    def test_the_key_may_be_base64(self):
        raw = json.dumps(self.service_account()).encode()
        os.environ['GCP_COST_CREDENTIALS_JSON'] = base64.b64encode(raw).decode()
        self.assertTrue(cm.gcp_snapshot(fresh=True, now=NOW)['ok'])

    def test_google_application_credentials_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'key.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.service_account(), f)
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = path
            self.assertTrue(cm.gcp_snapshot(fresh=True, now=NOW)['ok'])
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.join(ROOT, 'no-such-file.json')
        cm.clear_caches()
        self.assertIn('does not exist', cm.gcp_snapshot(fresh=True, now=NOW)['error'])

    def test_gcloud_application_default_user_login(self):
        os.environ['GCP_COST_CREDENTIALS_JSON'] = json.dumps({
            'type': 'authorized_user', 'client_id': 'cid', 'client_secret': 'not-a-secret', 'refresh_token': 'rt'})
        self.assertTrue(cm.gcp_snapshot(fresh=True, now=NOW)['ok'])
        grant = self.google.urls('oauth2.googleapis.com/token')[0]['data']
        self.assertEqual((grant['grant_type'], grant['refresh_token']), ('refresh_token', 'rt'))

    def test_federation_needs_google_auth_and_says_so(self):
        os.environ['GCP_COST_CREDENTIALS_JSON'] = json.dumps({'type': 'external_account', 'audience': 'x'})
        with mock.patch.dict(sys.modules, {'google': None, 'google.auth': None}):
            snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertIn('google-auth', snap['error'])

    def test_a_refused_key_says_why_without_echoing_it(self):
        os.environ['GCP_COST_CREDENTIALS_JSON'] = json.dumps(self.service_account())
        self.google.fail['oauth2.googleapis.com'] = (400, {'error': 'invalid_grant', 'error_description': 'Invalid JWT Signature.'})
        snap = cm.gcp_snapshot(fresh=True, now=NOW)
        self.assertIn('Invalid JWT Signature', snap['error'])
        self.assertNotIn('PRIVATE KEY', snap['error'])

    def test_garbage_in_the_variable(self):
        os.environ['GCP_COST_CREDENTIALS_JSON'] = 'this is not a key'
        self.assertIn('neither JSON nor base64', cm.gcp_snapshot(fresh=True, now=NOW)['error'])


# =============================================================================
# ACTUAL SPEND
# =============================================================================

TABLE = 'gcloudgpu.billing_export.gcp_billing_export_v1_0000AA_BBBBBB_CCCCCC'


def bq_answer(rows, complete=True):
    return {'kind': 'bigquery#queryResponse', 'jobComplete': complete,
            'jobReference': {'projectId': PROJECT, 'jobId': 'job_1', 'location': 'US'},
            'schema': {'fields': [{'name': 'service', 'type': 'STRING'}, {'name': 'day', 'type': 'DATE'},
                                  {'name': 'cost', 'type': 'FLOAT'}, {'name': 'credits', 'type': 'FLOAT'}]},
            'rows': [{'f': [{'v': v} for v in row]} for row in rows] if complete else []}


class ActualSpendTest(Base):
    ENV = dict(Base.ENV, GCP_BILLING_BQ_TABLE=TABLE)

    def test_not_set_up_without_a_table(self):
        del os.environ['GCP_BILLING_BQ_TABLE']
        self.assertEqual(cm.actual_spend(now=NOW), {'configured': False})

    def test_month_to_date_by_service_and_day(self):
        self.google.bq.append(bq_answer([
            ['Compute Engine', '2026-09-22', '10.5', '-1.0'],
            ['Compute Engine', '2026-09-23', '4.25', '0'],
            ['Cloud Storage', '2026-09-23', '0.1', '0']]))
        a = cm.actual_spend(fresh=True, now=NOW)
        self.assertTrue(a['ok'], a.get('error'))
        self.assertEqual((a['cost'], a['credits'], a['net'], a['month']), (14.85, -1.0, 13.85, '2026-09'))
        self.assertEqual([s['label'] for s in a['by_service']], ['Compute Engine', 'Cloud Storage'])
        self.assertEqual([d['label'] for d in a['by_day']], ['2026-09-23', '2026-09-22'])
        call = self.google.urls('/queries')[0]
        self.assertTrue(call['url'].endswith(f'/projects/{PROJECT}/queries'))
        body = call['json']
        self.assertIn(f'`{TABLE}`', body['query'])
        self.assertEqual(body['maximumBytesBilled'], str(cm.BQ_MAX_BYTES))
        params = {p['name']: p['parameterValue']['value'] for p in body['queryParameters']}
        self.assertEqual(params, {'month': '202609', 'project': PROJECT})

    def test_a_slow_query_is_waited_for(self):
        self.google.bq += [bq_answer([], complete=False), bq_answer([['Compute Engine', '2026-09-23', '2', '0']])]
        self.assertEqual(cm.actual_spend(fresh=True, now=NOW)['net'], 2.0)
        self.assertEqual(self.google.urls('/queries/job_1')[0]['params']['location'], 'US')

    def test_a_table_name_that_is_not_one_is_refused_before_any_call(self):
        os.environ['GCP_BILLING_BQ_TABLE'] = 'gcloudgpu.billing.x` UNION ALL SELECT 1 --'
        a = cm.actual_spend(fresh=True, now=NOW)
        self.assertFalse(a['ok'])
        self.assertEqual(self.google.calls, [])

    def test_a_bigquery_refusal_is_shown(self):
        self.google.fail['/queries'] = (403, {'error': {'message': 'Access Denied: Table gcloudgpu:billing_export'}})
        a = cm.actual_spend(fresh=True, now=NOW)
        self.assertFalse(a['ok'])
        self.assertIn('Access Denied', a['error'])


# =============================================================================
# THE PAGE, THE STOP BUTTON, THE CRON URL
# =============================================================================

STUB_BASE = """<!doctype html><html><head><meta name="csrf-token" content="{{ csrf_token() }}">
{% block head %}{% endblock %}</head><body>
{% for c, m in get_flashed_messages(with_categories=true) %}<div class="flash {{ c }}">{{ m }}</div>{% endfor %}
{% block content %}{% endblock %}</body></html>"""


class User(UserMixin):
    def __init__(self, uid, email):
        self.id = f'u_{uid}'
        self.user_id = uid
        self.email = email
        self.role = 'admin'
        self.is_candidate = False


USERS = {'u_1': User(1, 'berryrm0@gmail.com'),    # a cost admin by default
         'u_2': User(2, 'granted@example.org'),   # holds the 'cost_monitor' grant
         'u_3': User(3, 'nobody@example.org'),    # neither
         'u_9': User(9, 'chris@example.org')}     # the super admin


def make_app():
    """The blueprint on a bare Flask app with the real templates, CSRF on, and a stub base.html:
    the real one needs the whole application's endpoints."""
    from flask import Flask
    from flask_login import LoginManager
    from flask_wtf.csrf import CSRFProtect
    from jinja2 import ChoiceLoader, DictLoader
    app = Flask('cost_monitor_test', template_folder=os.path.join(ROOT, 'templates'))
    app.config.update(SECRET_KEY='test-only', TESTING=True)
    csrf = CSRFProtect(app)
    LoginManager(app).user_loader(USERS.get)
    app.add_url_rule('/login', 'login', lambda: 'login page')
    app.add_url_rule('/', 'index', lambda: 'home')
    app.register_blueprint(cm.cost_bp)
    csrf.exempt(app.view_functions['cost.cron_check'])
    app.jinja_env.loader = ChoiceLoader([DictLoader({'base.html': STUB_BASE}), app.jinja_env.loader])
    return app


class RoutesTest(Base):

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(private_features, 'is_super_admin',
                                    lambda: current_user.is_authenticated and current_user.email == 'chris@example.org'),
                  mock.patch.object(private_features, 'has_feature_access',
                                    lambda slug: slug == 'cost_monitor' and current_user.is_authenticated
                                    and current_user.email in ('granted@example.org', 'chris@example.org'))):
            p.start()
            self.addCleanup(p.stop)
        now = datetime.now(timezone.utc)   # the page reads the clock itself
        self.google.instances = [vm('render-builder', '101', started=now - timedelta(hours=2)),
                                 vm('mystery', '102', machine_type='c4-standard-8', started=now - timedelta(hours=1)),
                                 vm('old', '103', status='TERMINATED', started=now - timedelta(days=2),
                                    stopped=now - timedelta(days=2) + timedelta(hours=3))]
        self.client = make_app().test_client()

    def login(self, uid):
        with self.client.session_transaction() as s:
            s['_user_id'] = uid
            s['_fresh'] = True

    def csrf(self, html):
        return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

    def get_page(self, uid='u_1'):
        self.login(uid)
        r = self.client.get('/cost-monitor/')
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_log_in_first(self):
        r = self.client.get('/cost-monitor/')
        self.assertEqual((r.status_code, r.headers['Location']), (302, '/login'))

    def test_no_grant_no_page(self):
        self.login('u_3')
        r = self.client.get('/cost-monitor/')
        self.assertEqual((r.status_code, r.headers['Location']), (302, '/'))

    def test_the_grant_and_the_super_admin_both_open_it(self):
        self.assertIn('render-builder', self.get_page('u_2'))
        self.assertIn('render-builder', self.get_page('u_9'))

    def test_the_page(self):
        html = self.get_page()
        self.assertIn('2 VMs running now', html)
        self.assertIn('est. $0.76/h', html)
        self.assertIn('Est. burn rate', html)
        self.assertIn('rate unknown', html)       # the c4, never $0
        self.assertIn('no rate for c4 machines', html)
        self.assertIn('Windows Server 2022', html)
        self.assertIn('data-label="Est. this run"', html)   # phone cards
        self.assertIn('stopped', html)
        self.assertIn('not set up', html)         # actual spend without the export
        self.assertIn(cm.DOCS_URL, html)
        self.assertNotIn('Stop VM', html)         # off unless GCP_COST_ALLOW_STOP=1
        self.assertNotIn(DEV_TOKEN, html)

    def test_nothing_running(self):
        self.google.instances = [self.google.instances[2]]
        self.assertIn('Nothing is running', self.get_page())

    def test_not_set_up(self):
        del os.environ['GCP_COST_PROJECT']
        html = self.get_page()
        self.assertIn('Not set up', html)
        self.assertIn(cm.DOCS_URL, html)
        self.assertEqual(self.google.calls, [])

    def test_a_google_failure_is_a_banner_not_a_crash(self):
        self.google.fail['/aggregated/instances'] = (403, {'error': {'message': 'denied'}})
        html = self.get_page()
        self.assertIn('Could not read Google Cloud', html)
        self.assertIn('Permission denied (403)', html)

    def test_stop_is_refused_while_the_flag_is_off(self):
        token = self.csrf(self.get_page())
        self.assertEqual(self.client.get('/cost-monitor/stop?zone=us-east1-c&name=render-builder').status_code, 403)
        r = self.client.post('/cost-monitor/stop', data={'csrf_token': token, 'zone': 'us-east1-c',
                                                         'name': 'render-builder', 'id': '101', 'confirm': 'stop'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.google.stopped, [])

    def test_stop_with_the_flag_on_goes_through_a_confirm_page(self):
        os.environ['GCP_COST_ALLOW_STOP'] = '1'
        html = self.get_page()
        self.assertIn('/cost-monitor/stop?zone=us-east1-c&amp;name=render-builder', html)
        confirm = self.client.get('/cost-monitor/stop?zone=us-east1-c&name=render-builder')
        self.assertEqual(confirm.status_code, 200)
        page = confirm.get_data(as_text=True)
        self.assertIn('Yes, stop render-builder', page)
        self.assertEqual(self.google.stopped, [])   # the confirm page changes nothing
        form = {'zone': 'us-east1-c', 'name': 'render-builder', 'id': '101', 'confirm': 'stop'}
        self.assertEqual(self.client.post('/cost-monitor/stop', data=form).status_code, 400)   # no CSRF token
        self.assertEqual(self.google.stopped, [])
        r = self.client.post('/cost-monitor/stop', data=dict(form, csrf_token=self.csrf(page)))
        self.assertEqual((r.status_code, r.headers['Location']), (302, '/cost-monitor/'))
        self.assertEqual(self.google.stopped, [('us-east1-c', 'render-builder')])

    def test_stop_is_for_cost_admins_only(self):
        os.environ['GCP_COST_ALLOW_STOP'] = '1'
        html = self.get_page('u_2')
        self.assertNotIn('Stop VM', html)
        r = self.client.post('/cost-monitor/stop', data={'csrf_token': self.csrf(html), 'zone': 'us-east1-c',
                                                         'name': 'render-builder', 'id': '101', 'confirm': 'stop'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.google.stopped, [])

    def test_stop_checks_it_is_the_same_vm_and_still_running(self):
        os.environ['GCP_COST_ALLOW_STOP'] = '1'
        token = self.csrf(self.get_page())
        for form in ({'zone': 'us-east1-c', 'name': 'render-builder', 'id': '999', 'confirm': 'stop'},
                     {'zone': 'us-east1-c', 'name': 'old', 'id': '103', 'confirm': 'stop'},
                     {'zone': 'us-east1-c', 'name': 'render-builder', 'id': '101', 'confirm': 'yes'}):
            r = self.client.post('/cost-monitor/stop', data=dict(form, csrf_token=token))
            self.assertIn(r.status_code, (302, 400))
        self.assertEqual(self.google.stopped, [])
        self.assertEqual(self.client.get('/cost-monitor/stop?zone=us-east1-c&name=../../x').status_code, 400)

    def test_cron_needs_the_secret(self):
        os.environ['CRON_SECRET'] = 'cron-secret-for-tests'
        self.assertEqual(self.client.get('/cost-monitor/cron/check').status_code, 401)
        r = self.client.post('/cost-monitor/cron/check', headers={'Authorization': 'Bearer cron-secret-for-tests'})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body['enabled'])
        self.assertEqual(len(body['running']), 2)
        self.assertIn('Database', body['error'])   # no database in this app, so nothing is sent


# =============================================================================
# THE WARNINGS (need Postgres)
# =============================================================================

class Outbox(list):
    def __init__(self):
        super().__init__()
        self.working = True

    def __call__(self, to_email, subject, html_body, text_body=None, source=None):
        if not self.working:
            return False
        self.append((to_email, subject, html_body, text_body))
        return True


class AlertTest(Base):
    ENV = dict(Base.ENV, GCP_COST_ALERT_TO='ross@example.org')
    url = None
    server = None

    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get('COST_MONITOR_TEST_DATABASE_URL')
        if not cls.url:
            try:
                import pgserver
            except ImportError:
                raise unittest.SkipTest('needs COST_MONITOR_TEST_DATABASE_URL or `pip install pgserver`')
            cls.server = pgserver.get_server(tempfile.mkdtemp(prefix='cost-monitor-pg-'), cleanup_mode='delete')
            cls.url = cls.server.get_uri()

    @classmethod
    def tearDownClass(cls):
        if cls.server is not None:
            cls.server.cleanup()

    def setUp(self):
        super().setUp()
        import psycopg2
        self.psycopg2 = psycopg2
        cm.get_db_connection = lambda: psycopg2.connect(self.url)
        cm.release_db_connection = lambda conn: conn.close()
        with cm._cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS cost_monitor_runs, cost_monitor_alerts, cost_monitor_state')
        cm.ensure_tables()
        self.outbox = Outbox()
        cm.send_email = self.outbox

    def count(self, table):
        with cm._cursor() as cur:
            cur.execute(f'SELECT count(*) FROM {table}')
            return cur.fetchone()[0]

    def test_one_warning_per_run(self):
        self.google.instances = [vm('render-builder', '101', started=NOW - timedelta(hours=3, minutes=10))]
        first = cm.run_check(now=NOW)
        self.assertTrue(first['ok'], first)
        self.assertEqual(len(self.outbox), 1)
        to, subject, html, text = self.outbox[0]
        self.assertEqual(to, 'ross@example.org')
        self.assertIn('render-builder', subject)
        self.assertIn('past 3 h', text)
        self.assertIn('est. $0.76/h', text)
        self.assertIn('/cost-monitor/', text)
        again = cm.run_check(now=NOW + timedelta(minutes=10))
        self.assertEqual((len(self.outbox), again['sent']), (1, []))

    def test_each_threshold_once(self):
        os.environ['GCP_COST_ALERT_HOURS'] = '3,8'
        started = NOW - timedelta(hours=3, minutes=5)
        self.google.instances = [vm('render-builder', '101', started=started)]
        cm.run_check(now=NOW)
        cm.run_check(now=started + timedelta(hours=8, minutes=1))
        cm.run_check(now=started + timedelta(hours=8, minutes=20))
        self.assertEqual(len(self.outbox), 2)
        self.assertIn('past 8 h', self.outbox[1][3])

    def test_first_seen_past_two_marks_is_one_email(self):
        os.environ['GCP_COST_ALERT_HOURS'] = '3,8'
        self.google.instances = [vm('overnight', '102', machine_type='g2-standard-32', started=NOW - timedelta(hours=9))]
        cm.run_check(now=NOW)
        self.assertEqual(len(self.outbox), 1)
        self.assertIn('past 8 h', self.outbox[0][3])
        self.assertEqual(self.count('cost_monitor_alerts'), 2)
        cm.run_check(now=NOW + timedelta(minutes=10))
        self.assertEqual(len(self.outbox), 1)

    def test_a_restart_is_a_new_run(self):
        self.google.instances = [vm('render-builder', '101', started=NOW - timedelta(hours=4))]
        cm.run_check(now=NOW)
        restarted = NOW + timedelta(hours=1)
        self.google.instances = [vm('render-builder', '101', started=restarted)]
        cm.run_check(now=restarted + timedelta(hours=1))
        self.assertEqual(len(self.outbox), 1)
        cm.run_check(now=restarted + timedelta(hours=3, minutes=1))
        self.assertEqual(len(self.outbox), 2)

    def test_the_daily_dollar_line_once_a_day(self):
        os.environ['GCP_COST_ALERT_HOURS'] = '100'
        os.environ['GCP_COST_ALERT_USD'] = '5'
        self.google.instances = [vm('big', '103', machine_type='g2-standard-32', started=NOW - timedelta(hours=2))]
        cm.run_check(now=NOW)   # 2 h at $3.2064
        self.assertEqual(len(self.outbox), 1)
        self.assertIn('$6.41', self.outbox[0][3])
        self.assertIn('est. $6.41 today', self.outbox[0][1])
        cm.run_check(now=NOW + timedelta(minutes=30))
        self.assertEqual(len(self.outbox), 1)
        cm.run_check(now=NOW + timedelta(days=1))
        self.assertEqual(len(self.outbox), 2)

    def test_a_failed_email_is_tried_again(self):
        self.google.instances = [vm('render-builder', '101', started=NOW - timedelta(hours=4))]
        self.outbox.working = False
        out = cm.run_check(now=NOW)
        self.assertTrue(out['ok'])
        self.assertIn('could not be sent', out['alert_error'])
        self.assertEqual(self.count('cost_monitor_alerts'), 0)
        state, _ = cm.check_history()
        self.assertIn('could not be sent', state['last_alert_error'])
        self.outbox.working = True
        cm.run_check(now=NOW + timedelta(minutes=10))
        self.assertEqual(len(self.outbox), 1)
        state, alerts = cm.check_history()
        self.assertIsNone(state['last_alert_error'])
        self.assertEqual(alerts[0]['resource_name'], 'render-builder')

    def test_today_counts_runs_that_have_ended(self):
        a_start = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)   # 8:00 AM ET, deleted by 10
        self.google.instances = [vm('a', '201', started=a_start)]
        cm.run_check(now=a_start + timedelta(hours=2))
        c_start = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)   # 10:00 AM ET, stopped at 11
        self.google.instances = [
            vm('b', '202', started=datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc)),   # on since 1 PM ET
            vm('c', '203', status='TERMINATED', started=c_start, stopped=c_start + timedelta(hours=1)),
            vm('yesterday', '204', status='TERMINATED', started=NOW - timedelta(days=1, hours=3),
               stopped=NOW - timedelta(days=1))]
        out = cm.run_check(now=NOW)
        self.assertAlmostEqual(out['est_today'], 0.7565 * 4, delta=0.011)   # 2 h of a, 1 of b, 1 of c

    def test_the_timer_checks_once_per_window(self):
        self.assertTrue(cm.auto_check_once()['due'])
        self.assertFalse(cm.auto_check_once()['due'])

    def test_the_timer_waits_for_the_lock(self):
        other = self.psycopg2.connect(self.url)
        try:
            other.cursor().execute('SELECT pg_advisory_lock(%s)', (cm.AUTO_CHECK_LOCK,))
            self.assertEqual(cm.auto_check_once(), {'lock': False})
        finally:
            other.close()

    def test_the_page_shows_what_was_sent(self):
        self.google.instances = [vm('render-builder', '101', started=NOW - timedelta(hours=4))]
        cm.run_check(now=NOW)
        with mock.patch.object(private_features, 'is_super_admin', lambda: False):
            client = make_app().test_client()
            with client.session_transaction() as s:
                s['_user_id'] = 'u_1'
            html = client.get('/cost-monitor/').get_data(as_text=True)
        self.assertIn('The background check last ran', html)
        self.assertIn('render-builder has been running', html)


if __name__ == '__main__':
    unittest.main()
