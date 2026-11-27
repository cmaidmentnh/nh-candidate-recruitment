# Party app partner API

The NH Republicans party app (nh-republicans-app, nhgop.1772strategies.com) shows this site's recruitment board
to the state chair, HQ, region chairs and county chairs. It calls the endpoints below, server to server, with one
key. Code: `party_api.py`. Tests: `tests/test_party_api.py`.

Base URL: `https://nhcandidaterecruitment.com/api/v1/party`

## Authentication

`Authorization: Bearer <key>` (or `X-API-Key: <key>`). Only the key's SHA-256 is kept here, in
`PARTY_API_KEY_SHA256` in `.env` (several hashes may be listed, comma separated, while a key is rotated). With it
unset every call answers 503. To issue a key:

```bash
python3 -c "import secrets,hashlib; k='nhcr_party_'+secrets.token_urlsafe(32); print(k); print(hashlib.sha256(k.encode()).hexdigest())"
```

The key goes in the party app's `.env` (`NHCR_PARTY_API_KEY`); the hash goes here. Neither goes in git.

The party app also sends `X-Party-Actor: <the app user's email>` on every call. Rate limits are per actor: 300
reads a minute, 60 writes a minute and 1,500 writes a day.

## Who sees what

The party app decides who may use the tool and for which area: the state chair, HQ and statewide officers get the
whole state; a region chair the region's counties; a county chair or county officer that county; a town chair or
town officer that town (a city ward officer, that ward). For anyone without statewide scope it sends the area on
every call: `county` and/or `town` in the query string (`county=Rockingham,Strafford`, `town=Bedford,Manchester Ward
3`), or `"area": {"county": [...], "town": [...]}` in a write's JSON body (a top-level `"county"` works too; a
top-level `"town"` in a write is the recruit's own town, not the area).

A town covers every House district it votes in, base and floterial, from the `districts` table's town and ward rows
(never geocoded); a ward covers that ward's districts. Town names are compared without case, apostrophes or
periods, with `&` as "and" and the table's abbreviations spelled out ("Low & Burbank's Gt."). County and town
together mean the districts of either. This API enforces the area too:

- reads return only districts in the area, and candidates whose district this cycle (else their latest district)
  is in it;
- a candidate outside it is 403 `out_of_scope`;
- a write to a candidate, or into or out of a district, outside it is refused with 403 and writes nothing.

An unknown county or town is 400, never the whole state.

Only Republicans (`candidates.party = 'R'`) are served. Never served: private features (secret primaries, speaker
votes), the scout list, whip check-ins, Meta and StackAdapt, spend plans, finance and billing, portal accounts,
passwords, 2FA, voter records (only a yes or no "matched to the voter file") and portal, email, impersonation,
walkbook and consult activity.

## The cycle

Everything is for `CYCLE` (cycle.py, `CYCLE_YEAR` in `.env`; 2028 now). Writes only ever touch this cycle's
`candidate_election_status` row.

## Reads

| Endpoint | Returns |
|---|---|
| `GET /board?county=&town=` | `{cycle, scope, summary, statuses, districts}`. `districts`: every House district with `code, county, group, number, seats, towns, pvi, rating, confirmed, open_seats, active, primary, empty, candidates[{id, name, town, status, incumbent, is_running, filed}], sitting_no_record[{id, name}]`. `group` is the dashboard's heading (Manchester, Nashua and Concord districts are grouped by city). |
| `GET /filter?category=&county=&town=` | One dashboard card. `confirmed, empty_seats, empty_districts, districts_with_vacancies, potentials, primaries, incumbents_running, incumbents_not_running` return `districts`; `incumbents_undecided` and `unmatched_voters` return `people[{id, name, town, district, county, status, year}]`. |
| `GET /districts?county=&town=` | House districts for pickers: `code, county, number, seats, pvi, rating, towns` (from the districts table). |
| `GET /candidates/<id>?county=&town=` | `name, district, county, status, is_running, in_cycle, incumbent` (sitting member), `incumbent_flag`, `voter_matched, filed, town, contact{emails, phones[{number, type, field}], email1, email2, phone1, phone2, address, city, zip, signal, twitter_x, facebook, instagram, website_url}, statuses[], comments[], activity[], history[{at, by, changed, first}]`. |
| `GET /activity?county=&town=&limit=&before=` | Recent recruitment activity, newest first: `items[{id, action, description, candidate_id, name, district, county, status, by, at}]`, `next_before` to page back. |
| `GET /search?q=&county=&town=` | Republicans by name, any year: `results[{id, name, town, district, county, status, in_cycle, last_year, last_status}]`. |
| `GET /confirmed?cycle=` | Confirmed Republicans for the cycle, the rows the party app's Candidates directory is built from (campaign email and phone only). |
| `GET /ping` | `{ok, cycle}` |

`summary` holds the dashboard cards: `confirmed, empty_seats, empty_districts, districts_with_vacancies,
potentials, primaries, incumbents_running, incumbents_not_running, incumbents_undecided, filed, unmatched_voters,
total_seats, total_districts, seats_filled`. For the whole state these are `get_data_and_dashboard()`'s own totals,
the numbers on the dashboard at `/`. For a county they are recomputed over that county's districts with the same
rules (the tests check the recomputation equals the dashboard for the whole state); `filed` and `unmatched_voters`
are counted by district.

## Writes

Every write body carries `actor: {name, email}`, the app user. It is recorded here as this site records its own
writes: `activity_log.user_email` is `"<Name> (NH Republicans app) <email>"`, and `comments.added_by`,
`candidate_election_status.added_by` and `candidates.modified_by` are `"<Name> (NH Republicans app)"` (so the update
trigger's `candidate_history` rows name the person too). The party app also keeps its own audit log.

| Endpoint | Body | Does |
|---|---|---|
| `POST /recruits` | `name` (or `first_name`, `last_name`), `district`, `status` (default New Recruit), `town`, `phone`, `email`, `note` | As `/add_candidate_inline`: an existing Republican with the same first and last name gets this cycle's status (and any blank town, phone or email filled in); anyone else becomes a new candidate. Inserts `candidates` and `candidate_election_status`; the note becomes a comment. 409 `already_in_cycle` if they are already on the board. |
| `POST /candidates/<id>/status` | `status`: Confirmed, Considering, Potential, Declined, New Recruit | As `/api/update_status`; `is_running` = status is not Declined. |
| `POST /candidates/<id>/comments` | `text` | As `/api/quick_comment`. |
| `POST /candidates/<id>/contact` | `fields: {email1, email2, phone1, phone2, address, city, zip, signal, twitter_x, facebook, instagram}` | Sets the given fields; an empty string clears one. |
| `POST /candidates/<id>/incumbent` | | Flips `candidates.incumbent`, as `/api/toggle_incumbent`. The dashboard's incumbent counts come from the `incumbents` table (the last general's winners), not this flag. |
| `POST /candidates/<id>/district` | `district` | Moves this cycle's record, as `/api/update_candidate_district`, and logs it (`district_updated`). |

| `POST /suggestions` | `name`, `town` (a city needs its ward: "Concord Ward 4"), `why`, `office`, `phone`, `email` | "Suggest a candidate" from any app user (`actor` = who suggested it). The person goes on this cycle's board as a **New Recruit** in their town's base district, with a comment "Suggested in the NH Republicans app by <Name> <email>. Office: ... Why: ...", and activity `candidate_suggested`. Someone already on the board keeps their status and district; the suggestion is added as a comment. 400 `ward_required` for a city without a ward. 30 a day per app user. |

Errors are `{"error": code, "detail": text}` with 400, 401, 403, 404, 409 or 503.

## The Coordinator

The Coordinator (CTEHR-2028) syncs its recruiting tracker with this database (`src/lib/recruit/sync.ts` there). It
compares `candidate_election_status` and `candidates.phone1` and `email1` with what it saw at its last run, so a
status, district or contact change made through this API reaches The Coordinator like a change made on this site,
and a recruit added here is added there. This API never reads The Coordinator, so nothing loops. Contact edits
write `email1` (not the legacy `email` column) so the sync and the candidate website triggers see them.
