"""Tests for party_api.py, the NH Republicans party app's partner API.

Run from the repo root:

    python -m unittest tests.test_party_api -v

The pure helpers always run. The endpoint tests import app.py against a throwaway Postgres database: set
PARTY_API_TEST_DATABASE_URL to a server URL whose user may create databases (default: the local server,
postgresql:///postgres), or they are skipped. Each run creates its own database and drops it afterwards.
"""
import hashlib
import importlib
import os
import sys
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import party_api as pa  # noqa: E402

KEY = 'party-test-key-' + uuid.uuid4().hex
ACTOR = {'name': 'Pat Chair', 'email': 'pat.chair@example.com'}

SCHEMA = r"""
CREATE TABLE candidates (
  candidate_id serial PRIMARY KEY, first_name varchar(50), last_name varchar(50), incumbent boolean,
  address varchar(100), city varchar(50), email1 varchar(255), email2 varchar(255), signal varchar(50),
  phone1 varchar(20), phone2 varchar(20), zip varchar(10), twitter_x varchar(255), facebook varchar(255),
  instagram varchar(255), other text, created_by varchar(50), modified_by varchar(50), created_at timestamp,
  modified_at timestamp, party varchar(20), email varchar(255), photo_url text, voter_id varchar(50),
  website_url varchar(500), facebook_url varchar(500), twitter_url varchar(500), instagram_url varchar(500),
  signal_uuid varchar(40), signal_registered boolean, phone1_type varchar(20), phone2_type varchar(20));
CREATE TABLE candidate_election_status (
  status_id serial PRIMARY KEY, candidate_id integer, election_year integer NOT NULL,
  is_running boolean DEFAULT false, added_by varchar(50), added_at timestamp DEFAULT CURRENT_TIMESTAMP,
  district_code varchar(50), status varchar(50));
CREATE TABLE districts (
  counter serial PRIMARY KEY, county_name text, district_code integer, ward integer, town text,
  seat_count integer, full_district_code text, pvi numeric, pvi_rating text);
CREATE TABLE filings (
  filing_id serial PRIMARY KEY, election_year integer NOT NULL, office varchar(100) NOT NULL DEFAULT 'State Representative',
  district_code varchar(50), first_name varchar(100) NOT NULL, last_name varchar(100) NOT NULL, party char(1) NOT NULL,
  town varchar(100), candidate_id integer, filed_at date, created_at timestamp DEFAULT now(),
  result varchar(10) NOT NULL DEFAULT 'pending');
CREATE TABLE district_relation (district_code varchar(50) NOT NULL, kind varchar(10) NOT NULL, spans smallint NOT NULL DEFAULT 0);
CREATE TABLE incumbents (
  election_year integer NOT NULL, district_code varchar(50) NOT NULL, name varchar(200) NOT NULL,
  party char(1) NOT NULL, status varchar(10) NOT NULL, certified boolean NOT NULL DEFAULT false,
  candidate_id integer, source varchar(20) NOT NULL DEFAULT 'results', synced_at timestamp NOT NULL DEFAULT now());
CREATE TABLE comments (
  comment_id serial PRIMARY KEY, candidate_id integer, comment_text text NOT NULL, added_by varchar(50),
  added_at timestamp DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE activity_log (
  log_id serial PRIMARY KEY, action_type varchar(50) NOT NULL, description text NOT NULL, candidate_id integer,
  user_email varchar(255), created_at timestamp DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE candidate_history (
  history_id serial PRIMARY KEY, candidate_id integer, first_name varchar(50), last_name varchar(50),
  incumbent boolean, address varchar(100), city varchar(50), email1 varchar(255), email2 varchar(255),
  signal varchar(50), phone1 varchar(20), phone2 varchar(20), zip varchar(10), twitter_x varchar(255),
  facebook varchar(255), instagram varchar(255), other text, change_comment text, changed_by varchar(50),
  changed_at timestamp DEFAULT CURRENT_TIMESTAMP);
CREATE FUNCTION log_candidate_changes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO candidate_history (candidate_id, first_name, last_name, incumbent, address, city, email1, email2,
      signal, phone1, phone2, zip, twitter_x, facebook, instagram, other, change_comment, changed_by, changed_at)
  VALUES (NEW.candidate_id, NEW.first_name, NEW.last_name, NEW.incumbent, NEW.address, NEW.city, NEW.email1,
      NEW.email2, NEW.signal, NEW.phone1, NEW.phone2, NEW.zip, NEW.twitter_x, NEW.facebook, NEW.instagram,
      NEW.other, 'Updated candidate record', NEW.modified_by, CURRENT_TIMESTAMP);
  RETURN NEW;
END $$;
CREATE TRIGGER candidates_update_trigger AFTER UPDATE ON candidates FOR EACH ROW EXECUTE FUNCTION log_candidate_changes();
"""

# Belknap 1 (2 seats), Belknap 2 (1), Hillsborough 15 (Manchester Ward 8, so the dashboard groups it under
# MANCHESTER), Rockingham 1 (1).
DISTRICTS = [
    ('Belknap', 1, 0, 'Alton', 2, 'Belknap 1', 5.1, 'SAFE GOP'),
    ('Belknap', 1, 0, 'Gilford', 2, 'Belknap 1', 5.1, 'SAFE GOP'),
    ('Belknap', 2, 0, 'Laconia', 1, 'Belknap 2', -1.0, 'TOSS UP'),
    ('Hillsborough', 15, 8, 'Manchester', 2, 'Hillsborough 15', 3.5, 'LIKELY GOP'),
    ('Rockingham', 1, 0, 'Atkinson', 1, 'Rockingham 1', 9.0, 'SAFE GOP'),
    ('Hillsborough', 16, 9, 'Manchester', 2, 'Hillsborough 16', 1.0, 'LEAN GOP'),
    ('Belknap', 9, 0, 'Alton', 1, 'Belknap 9', 2.0, 'LEAN GOP'),      # a floterial over Alton and Laconia
    ('Belknap', 9, 0, 'Laconia', 1, 'Belknap 9', 2.0, 'LEAN GOP'),
]
RELATIONS = [('Belknap 1', 'base'), ('Belknap 2', 'base'), ('Hillsborough 15', 'base'), ('Rockingham 1', 'base'),
             ('Hillsborough 16', 'base'), ('Belknap 9', 'floterial')]


def _seed(cur, cycle, prior):
    for code, kind in RELATIONS:
        cur.execute("INSERT INTO district_relation (district_code, kind) VALUES (%s, %s)", (code, kind))
    for row in DISTRICTS:
        cur.execute("""INSERT INTO districts (county_name, district_code, ward, town, seat_count, full_district_code,
                       pvi, pvi_rating) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", row)
    people = [  # first, last, party, city, voter_id, district, status
        ('Ann', 'Alpha', 'R', 'Alton', 'V1', 'Belknap 1', 'Confirmed'),
        ('Bob', 'Bravo', 'R', 'Gilford', None, 'Belknap 1', 'Considering'),
        ('Cal', 'Charlie', 'R', 'Laconia', 'V3', 'Belknap 2', 'Confirmed'),
        ('Dee', 'Delta', 'R', 'Laconia', 'V4', 'Belknap 2', 'Confirmed'),        # Belknap 2 primary
        ('Eve', 'Echo', 'R', 'Manchester', None, 'Hillsborough 15', 'Declined'),  # sitting member, out
        ('Fay', 'Fox', 'R', 'Manchester', 'V6', 'Hillsborough 15', 'Potential'),  # sitting member, undecided
        ('Dan', 'Democrat', 'D', 'Atkinson', None, 'Rockingham 1', 'Confirmed'),  # never served
    ]
    ids = {}
    for first, last, party, city, voter, district, status in people:
        cur.execute("""INSERT INTO candidates (first_name, last_name, party, city, voter_id, phone1, email1, address)
                       VALUES (%s,%s,%s,%s,%s,'6035550100',%s,'1 Main St') RETURNING candidate_id""",
                    (first, last, party, city, voter, f'{first.lower()}@example.com'))
        ids[last] = cur.fetchone()[0]
        cur.execute("""INSERT INTO candidate_election_status (candidate_id, election_year, status, is_running,
                       district_code) VALUES (%s,%s,%s,%s,%s)""",
                    (ids[last], cycle, status, status != 'Declined', district))
    cur.execute("INSERT INTO candidates (first_name, last_name, party, city) VALUES ('Gus','Golf','R','Atkinson') "
                "RETURNING candidate_id")
    ids['Golf'] = cur.fetchone()[0]   # sitting member in Rockingham 1 with no record this cycle
    cur.execute("""INSERT INTO candidate_election_status (candidate_id, election_year, status, is_running,
                   district_code) VALUES (%s,%s,'Ran',true,'Rockingham 1')""", (ids['Golf'], prior))
    for last, district in (('Echo', 'Hillsborough 15'), ('Fox', 'Hillsborough 15'), ('Golf', 'Rockingham 1')):
        cur.execute("""INSERT INTO incumbents (election_year, district_code, name, party, status, candidate_id)
                       VALUES (%s,%s,%s,'R','won',%s)""", (prior, district, f'X {last}', ids[last]))
    cur.execute("""INSERT INTO filings (election_year, district_code, first_name, last_name, party, candidate_id)
                   VALUES (%s,'Belknap 1','Ann','Alpha','R',%s)""", (cycle, ids['Alpha']))
    cur.execute("INSERT INTO comments (candidate_id, comment_text, added_by) VALUES (%s,'Met at the fair','staff')",
                (ids['Bravo'],))
    cur.execute("INSERT INTO activity_log (action_type, description, candidate_id, user_email) VALUES "
                "('email_sent','Portal email',%s,'staff'), ('status_updated','Changed Bob Bravo status',%s,'staff')",
                (ids['Bravo'], ids['Bravo']))
    return ids


class PureHelpers(unittest.TestCase):
    def test_county_of_code(self):
        self.assertEqual(pa.county_of_code('Hillsborough 15'), 'Hillsborough')
        self.assertEqual(pa.county_of_code('coos 3'), 'Coos')
        self.assertIsNone(pa.county_of_code('State Senator District 7'))
        self.assertIsNone(pa.county_of_code(None))

    def test_parse_counties(self):
        self.assertIsNone(pa.parse_counties(None))
        self.assertIsNone(pa.parse_counties(''))
        self.assertEqual(pa.parse_counties('rockingham, Strafford'), {'Rockingham', 'Strafford'})
        self.assertEqual(pa.parse_counties(['Belknap']), {'Belknap'})
        with self.assertRaises(pa.ApiError):
            pa.parse_counties('Atlantis')
        with self.assertRaises(pa.ApiError):
            pa.parse_counties(' , ')

    def test_actor_labels_fit_the_columns(self):
        short, long_ = pa.actor_labels({'name': 'A' * 80, 'email': 'a@example.com'})
        self.assertLessEqual(len(short), 50)
        self.assertTrue(short.endswith(' (NH Republicans app)'))
        self.assertTrue(long_.endswith('<a@example.com>'))
        for bad in (None, {}, {'name': 'X'}, {'name': '', 'email': 'a@b.co'}, {'name': 'X', 'email': 'nope'}):
            with self.assertRaises(pa.ApiError):
                pa.actor_labels(bad)

    def test_town_names_match_across_sources(self):
        self.assertEqual(pa.norm_town("Low & Burbank's Gt."), pa.norm_town('Low and Burbanks Grant'))
        self.assertEqual(pa.norm_town("Wentworth's Loc."), pa.norm_town('Wentworths Location'))
        self.assertEqual(pa.norm_town("Thompson & Mes's Pur."), pa.norm_town('Thompson and Meserves Purchase'))
        self.assertEqual(pa.norm_town('Keene '), 'keene')
        self.assertEqual(pa.parse_towns('Manchester Ward 3, Bedford'), {('manchester', 3), ('bedford', None)})

    def test_district_codes_for_towns_and_wards(self):
        rows = [(c, t, w or None) for _county, _n, w, t, _s, c, _p, _r in DISTRICTS]
        self.assertEqual(pa.district_codes(rows, None, pa.parse_towns('Alton')), {'Belknap 1', 'Belknap 9'})
        self.assertEqual(pa.district_codes(rows, None, pa.parse_towns('Manchester Ward 9')), {'Hillsborough 16'})
        self.assertEqual(pa.district_codes(rows, None, pa.parse_towns('manchester')),
                         {'Hillsborough 15', 'Hillsborough 16'})
        self.assertEqual(pa.district_codes(rows, {'Rockingham'}, pa.parse_towns('Laconia')),
                         {'Rockingham 1', 'Belknap 2', 'Belknap 9'})
        with self.assertRaises(pa.ApiError):
            pa.district_codes(rows, None, pa.parse_towns('Atlantis'))

    def test_split_name(self):
        self.assertEqual(pa.split_name({'name': 'Mary Ann  Smith'}), ('Mary Ann', 'Smith'))
        self.assertEqual(pa.split_name({'first_name': 'Jo', 'last_name': 'Lee'}), ('Jo', 'Lee'))
        with self.assertRaises(pa.ApiError):
            pa.split_name({'name': 'Cher'})


class PartyApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import psycopg2
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
            admin_url = os.environ.get('PARTY_API_TEST_DATABASE_URL', 'postgresql:///postgres')
            cls.admin = psycopg2.connect(admin_url, connect_timeout=3)
        except Exception as e:
            raise unittest.SkipTest(f'needs a Postgres server for PARTY_API_TEST_DATABASE_URL ({e})')
        cls.admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cls.dbname = 'nhcr_party_api_test_' + uuid.uuid4().hex[:8]
        cls.admin.cursor().execute(f'CREATE DATABASE {cls.dbname}')
        try:
            cls._build(admin_url)
        except Exception:
            cls.admin.cursor().execute(f'DROP DATABASE IF EXISTS {cls.dbname} WITH (FORCE)')
            raise

    @classmethod
    def _build(cls, admin_url):
        import psycopg2
        base = admin_url.rsplit('/', 1)[0]
        cls.url = f'{base}/{cls.dbname}'
        conn = psycopg2.connect(cls.url)
        cur = conn.cursor()
        cur.execute(SCHEMA)
        from cycle import CYCLE, PRIOR
        cls.cycle, cls.prior = CYCLE, PRIOR
        cls.ids = _seed(cur, CYCLE, PRIOR)
        conn.commit()
        conn.close()

        os.environ.update({'DATABASE_URL': cls.url, 'SECRET_KEY': 'test', 'META_AUTO_SYNC': '0',
                           'STACKADAPT_AUTO_SYNC': '0', 'GCP_COST_AUTO_CHECK': '0',
                           'PARTY_API_KEY_SHA256': hashlib.sha256(KEY.encode()).hexdigest()})
        os.environ.pop('GCP_COST_PROJECT', None)
        cls.appmod = importlib.import_module('app')
        cls.appmod.app.config['TESTING'] = True
        cls.appmod.app.config['RATELIMIT_ENABLED'] = False
        cls.client = cls.appmod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            pool = cls.appmod.db_pool
            if pool:
                pool.closeall()
        except Exception:
            pass
        cls.admin.cursor().execute(f'DROP DATABASE IF EXISTS {cls.dbname} WITH (FORCE)')
        cls.admin.close()

    def get(self, path, key=KEY, **params):
        return self.client.get('/api/v1/party' + path, query_string=params,
                               headers={'Authorization': f'Bearer {key}'})

    def post(self, path, body, key=KEY):
        return self.client.post('/api/v1/party' + path, json=body, headers={'Authorization': f'Bearer {key}'})

    def sql(self, q, params=()):
        import psycopg2
        conn = psycopg2.connect(self.url)
        try:
            cur = conn.cursor()
            cur.execute(q, params)
            rows = cur.fetchall() if cur.description else None
            conn.commit()
            return rows
        finally:
            conn.close()

    # ---- auth

    def test_key_required(self):
        self.assertEqual(self.get('/ping', key='').status_code, 401)
        self.assertEqual(self.get('/ping', key='wrong').status_code, 401)
        self.assertEqual(self.get('/ping').get_json(), {'ok': True, 'cycle': self.cycle})

    def test_not_configured_is_503(self):
        saved = os.environ.pop('PARTY_API_KEY_SHA256')
        try:
            self.assertEqual(self.get('/ping').status_code, 503)
        finally:
            os.environ['PARTY_API_KEY_SHA256'] = saved

    # ---- board

    def test_board_matches_the_dashboard(self):
        with self.appmod.app.test_request_context():
            groups, dashboard, _ = self.appmod.get_data_and_dashboard()
        board = self.get('/board').get_json()
        s = board['summary']
        for k in pa.DISTRICT_CARDS + ('incumbents_undecided', 'filed', 'unmatched_voters'):
            self.assertEqual(s[k], dashboard[k]['total'], k)
        # The recomputation used for a county equals the dashboard's own rules over the whole state.
        again = pa.summarize(board['districts'])
        for k in pa.DISTRICT_CARDS + ('incumbents_undecided',):
            self.assertEqual(again[k], dashboard[k]['total'], k)
        self.assertEqual(s['confirmed'], 3)
        self.assertEqual(s['primaries'], 1)
        self.assertEqual(s['incumbents_undecided'], 2)      # Fox (Potential) + Golf (no record)
        self.assertEqual(s['incumbents_not_running'], 1)    # Echo
        self.assertEqual(s['filed'], 1)
        self.assertEqual(s['unmatched_voters'], 2)          # Bravo and Democrat (the count is any party; Echo declined)
        names = {c['name'] for d in board['districts'] for c in d['candidates']}
        self.assertNotIn('Dan Democrat', names)
        h15 = next(d for d in board['districts'] if d['code'] == 'Hillsborough 15')
        self.assertEqual((h15['county'], h15['group']), ('Hillsborough', 'MANCHESTER'))
        self.assertEqual(h15['towns'], ['Manchester Ward 8'])
        r1 = next(d for d in board['districts'] if d['code'] == 'Rockingham 1')
        self.assertEqual([x['name'] for x in r1['sitting_no_record']], ['Gus Golf'])
        self.assertTrue(r1['empty'])
        b1 = next(d for d in board['districts'] if d['code'] == 'Belknap 1')
        self.assertEqual({c['town'] for c in b1['candidates']}, {'Alton', 'Gilford'})
        self.assertNotIn('phone', str(board))

    def test_board_by_county(self):
        b = self.get('/board', county='Belknap').get_json()
        self.assertEqual({d['county'] for d in b['districts']}, {'Belknap'})
        self.assertEqual(b['scope'], {'counties': ['Belknap'], 'statewide': False, 'towns': None, 'districts': 3})
        self.assertEqual(b['summary']['confirmed'], 3)
        self.assertEqual(b['summary']['total_seats'], 4)
        self.assertEqual(b['summary']['incumbents_undecided'], 0)
        self.assertEqual(b['summary']['filed'], 1)
        h = self.get('/board', county='Hillsborough').get_json()
        self.assertEqual(h['summary']['incumbents_undecided'], 1)
        self.assertEqual(h['summary']['unmatched_voters'], 0)
        self.assertEqual(self.get('/board', county='Atlantis').status_code, 400)

    def test_filter(self):
        f = self.get('/filter', category='primaries').get_json()
        self.assertEqual([d['code'] for d in f['districts']], ['Belknap 2'])
        f = self.get('/filter', category='incumbents_undecided').get_json()
        self.assertEqual({p['name'] for p in f['people']}, {'Fay Fox', 'X Golf'})
        f = self.get('/filter', category='incumbents_undecided', county='Rockingham').get_json()
        self.assertEqual([p['name'] for p in f['people']], ['X Golf'])
        self.assertEqual(f['total'], 1)
        self.assertEqual(self.get('/filter', category='secret_primaries').status_code, 400)

    # ---- detail

    def test_detail_and_scope(self):
        bob = self.ids['Bravo']
        d = self.get(f'/candidates/{bob}').get_json()
        self.assertEqual(d['status'], 'Considering')
        self.assertEqual(d['contact']['emails'], ['bob@example.com'])
        self.assertEqual(d['contact']['address'], '1 Main St')
        self.assertFalse(d['voter_matched'])
        self.assertEqual([c['text'] for c in d['comments']], ['Met at the fair'])
        self.assertEqual([a['action'] for a in d['activity']], ['status_updated'])   # email_sent never leaves
        self.assertEqual(self.get(f'/candidates/{bob}', county='Belknap').status_code, 200)
        self.assertEqual(self.get(f'/candidates/{bob}', county='Rockingham').status_code, 403)
        self.assertEqual(self.get(f"/candidates/{self.ids['Democrat']}").status_code, 404)

    # ---- writes

    def test_add_recruit_and_work_it(self):
        body = {'actor': ACTOR, 'name': 'Zed Zulu', 'town': 'Alton', 'district': 'Belknap 1', 'status': 'Potential',
                'phone': '603-555-0199', 'email': 'zed@example.com', 'note': 'Strong on taxes', 'county': 'Belknap'}
        r = self.post('/recruits', body)
        self.assertEqual(r.status_code, 201, r.get_json())
        cid = r.get_json()['candidate_id']
        self.addCleanup(self._remove, cid)
        self.assertTrue(r.get_json()['created'])
        row = self.sql("""SELECT c.first_name, c.last_name, c.party, c.city, c.phone1, c.email1, c.created_by,
                                 s.status, s.is_running, s.district_code, s.added_by, s.election_year
                          FROM candidates c JOIN candidate_election_status s USING (candidate_id)
                          WHERE candidate_id = %s""", (cid,))[0]
        self.assertEqual(row, ('Zed', 'Zulu', 'R', 'Alton', '603-555-0199', 'zed@example.com',
                               'Pat Chair (NH Republicans app)', 'Potential', True, 'Belknap 1',
                               'Pat Chair (NH Republicans app)', self.cycle))
        self.assertEqual(self.sql('SELECT comment_text, added_by FROM comments WHERE candidate_id=%s', (cid,)),
                         [('Strong on taxes', 'Pat Chair (NH Republicans app)')])
        # Same name again: already on the board.
        self.assertEqual(self.post('/recruits', body).status_code, 409)
        # Outside the call's county: refused, nothing written.
        r = self.post('/recruits', {**body, 'name': 'Out Side', 'district': 'Rockingham 1'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.sql("SELECT count(*) FROM candidates WHERE last_name='Side'"), [(0,)])
        self.assertEqual(self.post('/recruits', {**body, 'name': 'No Actor', 'actor': None}).status_code, 400)
        self.assertEqual(self.post('/recruits', {**body, 'name': 'Bad Dist', 'district': 'Belknap 99', 'county': None}).status_code, 400)
        self.assertEqual(self.post('/recruits', {**body, 'name': 'Bad Dist', 'district': 'Belknap 99'}).status_code, 403)

        # Status, comment, contact, incumbent, district.
        r = self.post(f'/candidates/{cid}/status', {'actor': ACTOR, 'status': 'Confirmed', 'county': 'Belknap'})
        self.assertEqual(r.get_json()['previous'], 'Potential')
        self.assertEqual(self.post(f'/candidates/{cid}/status', {'actor': ACTOR, 'status': 'Maybe'}).status_code, 400)
        self.assertEqual(self.post(f'/candidates/{cid}/status',
                                   {'actor': ACTOR, 'status': 'Declined', 'county': 'Strafford'}).status_code, 403)
        self.assertEqual(self.sql('SELECT status, is_running FROM candidate_election_status WHERE candidate_id=%s',
                                  (cid,)), [('Confirmed', True)])
        self.assertEqual(self.post(f'/candidates/{cid}/comments', {'actor': ACTOR, 'text': 'Said yes'}).status_code, 201)
        r = self.post(f'/candidates/{cid}/contact', {'actor': ACTOR, 'fields': {'phone2': '6035550111', 'zip': ''}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.post(f'/candidates/{cid}/contact',
                                   {'actor': ACTOR, 'fields': {'password_hash': 'x'}}).status_code, 400)
        hist = self.sql('SELECT phone2, changed_by FROM candidate_history WHERE candidate_id=%s', (cid,))
        self.assertEqual(hist, [('6035550111', 'Pat Chair (NH Republicans app)')])
        self.assertTrue(self.post(f'/candidates/{cid}/incumbent', {'actor': ACTOR}).get_json()['incumbent'])
        r = self.post(f'/candidates/{cid}/district', {'actor': ACTOR, 'district': 'Belknap 2', 'county': 'Belknap'})
        self.assertEqual(r.get_json()['previous'], 'Belknap 1')
        self.assertEqual(self.post(f'/candidates/{cid}/district', {'actor': ACTOR, 'district': 'Rockingham 1',
                                                                   'county': 'Belknap'}).status_code, 403)
        log = self.sql('SELECT action_type, user_email FROM activity_log WHERE candidate_id=%s ORDER BY log_id', (cid,))
        self.assertEqual([a for a, _ in log], ['candidate_added', 'status_updated', 'comment_added',
                                               'contact_updated', 'incumbent_toggled', 'district_updated'])
        self.assertEqual({u for _, u in log}, {'Pat Chair (NH Republicans app) <pat.chair@example.com>'})

        d = self.get(f'/candidates/{cid}').get_json()
        self.assertEqual((d['status'], d['district']), ('Confirmed', 'Belknap 2'))
        self.assertTrue(d['history'] and d['history'][0]['by'] == 'Pat Chair (NH Republicans app)')

        feed = self.get('/activity', county='Belknap').get_json()['items']
        self.assertIn('district_updated', [i['action'] for i in feed])
        self.assertNotIn('email_sent', [i['action'] for i in self.get('/activity').get_json()['items']])
        self.assertEqual(self.get('/activity', county='Rockingham').get_json()['items'], [])

        s = self.get('/search', q='zed zu').get_json()['results']
        self.assertEqual([x['id'] for x in s], [cid])
        self.assertEqual(self.get('/search', q='zulu', county='Rockingham').get_json()['results'], [])


    def _remove(self, cid):
        for table in ('candidate_election_status', 'comments', 'activity_log', 'candidate_history', 'candidates'):
            self.sql(f'DELETE FROM {table} WHERE candidate_id=%s', (cid,))

    def test_existing_candidate_gets_this_cycle(self):
        golf = self.ids['Golf']
        r = self.post('/recruits', {'actor': ACTOR, 'first_name': 'gus', 'last_name': 'GOLF',
                                    'district': 'Rockingham 1', 'status': 'Considering', 'phone': '6035550123'})
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json(), {'ok': True, 'candidate_id': golf, 'created': False, 'district': 'Rockingham 1',
                                        'status': 'Considering', 'cycle': self.cycle})
        self.assertEqual(self.sql('SELECT phone1 FROM candidates WHERE candidate_id=%s', (golf,)), [('6035550123',)])
        self.sql('DELETE FROM candidate_election_status WHERE candidate_id=%s AND election_year=%s', (golf, self.cycle))
        self.sql("UPDATE candidates SET phone1=NULL WHERE candidate_id=%s", (golf,))
        self.sql('DELETE FROM activity_log WHERE candidate_id=%s', (golf,))

    def test_town_scope(self):
        b = self.get('/board', town='Alton').get_json()
        self.assertEqual({d['code'] for d in b['districts']}, {'Belknap 1', 'Belknap 9'})
        self.assertEqual(b['scope']['towns'], ['Alton'])
        self.assertEqual(b['summary']['total_seats'], 3)
        self.assertEqual(self.get('/board', town='Atlantis').status_code, 400)
        self.assertEqual(self.get(f"/candidates/{self.ids['Bravo']}", town='Alton').status_code, 200)
        self.assertEqual(self.get(f"/candidates/{self.ids['Charlie']}", town='Alton').status_code, 403)
        self.assertEqual(self.get(f"/candidates/{self.ids['Charlie']}", town='Laconia').status_code, 200)
        w = self.get('/board', town='Manchester Ward 8').get_json()
        self.assertEqual([d['code'] for d in w['districts']], ['Hillsborough 15'])
        # Writes: inside the town's districts (a floterial counts), never outside.
        r = self.post('/recruits', {'actor': ACTOR, 'name': 'Flo Terial', 'district': 'Belknap 9', 'town': 'Alton', 'area': {'town': ['Alton']}})
        self.assertEqual(r.status_code, 201, r.get_json())
        self.addCleanup(self._remove, r.get_json()['candidate_id'])
        r = self.post('/recruits', {'actor': ACTOR, 'name': 'Not Here', 'district': 'Belknap 2', 'area': {'town': ['Alton']}})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post(f"/candidates/{self.ids['Charlie']}/status",
                                   {'actor': ACTOR, 'status': 'Declined', 'area': {'town': ['Alton']}}).status_code, 403)
        self.assertEqual(self.post(f"/candidates/{self.ids['Bravo']}/comments",
                                   {'actor': ACTOR, 'text': 'ok', 'area': {'town': 'Gilford'}}).status_code, 201)
        self.assertEqual(self.post(f"/candidates/{self.ids['Bravo']}/district",
                                   {'actor': ACTOR, 'district': 'Belknap 2', 'area': {'town': 'Alton', 'county': ['Rockingham']}}).status_code, 403)
        self.assertEqual(self.get('/activity', town='Atkinson').get_json()['items'], [])
        self.sql("DELETE FROM comments WHERE candidate_id=%s AND comment_text='ok'", (self.ids['Bravo'],))
        self.sql("DELETE FROM activity_log WHERE candidate_id=%s AND action_type='comment_added'", (self.ids['Bravo'],))

    def test_suggestions_land_on_the_board(self):
        r = self.post('/suggestions', {'actor': ACTOR, 'name': 'Sue Gested', 'town': 'Gilford', 'office': 'State Rep',
                                       'phone': '6035550177', 'why': 'Runs the PTO'})
        self.assertEqual(r.status_code, 201, r.get_json())
        out = r.get_json()
        self.addCleanup(self._remove, out['candidate_id'])
        self.assertEqual((out['district'], out['status'], out['already_on_board']), ('Belknap 1', 'New Recruit', False))
        row = self.sql("""SELECT s.status, s.district_code, s.added_by, c.city, c.phone1 FROM candidate_election_status s
                          JOIN candidates c USING (candidate_id) WHERE candidate_id=%s""", (out['candidate_id'],))
        self.assertEqual(row, [('New Recruit', 'Belknap 1', 'Pat Chair (NH Republicans app)', 'Gilford', '6035550177')])
        note = self.sql('SELECT comment_text FROM comments WHERE candidate_id=%s', (out['candidate_id'],))[0][0]
        self.assertIn('Suggested in the NH Republicans app by Pat Chair <pat.chair@example.com>', note)
        self.assertIn('Runs the PTO', note)
        b = self.get('/board', town='Gilford').get_json()
        self.assertIn('Sue Gested', [c['name'] for d in b['districts'] for c in d['candidates']])
        # Someone already on the board keeps their status; the suggestion is a comment.
        r = self.post('/suggestions', {'actor': ACTOR, 'name': 'Bob Bravo', 'town': 'Gilford', 'why': 'Ask him again'})
        self.assertEqual(r.get_json()['already_on_board'], True)
        self.assertEqual(r.get_json()['status'], 'Considering')
        self.sql("DELETE FROM comments WHERE candidate_id=%s AND comment_text LIKE 'Suggested%%'", (self.ids['Bravo'],))
        self.sql("DELETE FROM activity_log WHERE candidate_id=%s AND action_type='candidate_suggested'", (self.ids['Bravo'],))
        # A city needs its ward; an unknown town is refused.
        r = self.post('/suggestions', {'actor': ACTOR, 'name': 'Ward Less', 'town': 'Manchester', 'why': 'x'})
        self.assertEqual((r.status_code, r.get_json()['error']), (400, 'ward_required'))
        r = self.post('/suggestions', {'actor': ACTOR, 'name': 'Ward Ful', 'town': 'Manchester Ward 9', 'why': 'x'})
        self.assertEqual(r.get_json()['district'], 'Hillsborough 16')
        self.addCleanup(self._remove, r.get_json()['candidate_id'])
        self.assertEqual(self.post('/suggestions', {'actor': ACTOR, 'name': 'No Where', 'town': 'Atlantis',
                                                    'why': 'x'}).status_code, 400)
        self.assertEqual(self.post('/suggestions', {'name': 'No Actor', 'town': 'Gilford', 'why': 'x'}).status_code, 400)

    def test_confirmed_directory_rows(self):
        rows = self.get('/confirmed').get_json()['candidates']
        self.assertEqual({r['last_name'] for r in rows}, {'Alpha', 'Charlie', 'Delta'})
        self.assertNotIn('address', rows[0])


if __name__ == '__main__':
    unittest.main()
