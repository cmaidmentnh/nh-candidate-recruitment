# NH 2026 Candidate Cross-Reference API

Check your membership list against candidates who filed for the 2026 New Hampshire
ballot. The API returns names and towns only. It does not return email addresses,
phone numbers, street addresses, or social accounts, and there is no endpoint that
does.

Base URL: `https://<host>/api/v1`

## Authentication

Every request needs the API key we issue you, in either header:

```
X-API-Key: ctehr_xxxxxxxxxxxxxxxxxxxx
Authorization: Bearer ctehr_xxxxxxxxxxxxxxxxxxxx
```

Keys are per organisation. Tell us right away if one is exposed and we will revoke it.

## What the data covers

All 1,301 candidates who filed for the 2026 ballot, every party and every office:
State Representative, State Senator, Executive Councillor, Governor, US Senator,
US Representative, county offices, and Delegate to the State Convention.

Filing records are public. Nothing about our internal recruitment work is included.

## POST /api/v1/match

Send your members, get back the ones who filed.

```bash
curl -X POST https://<host>/api/v1/match \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"id": "M-1001", "first_name": "Bill",   "last_name": "Horn",   "town": "Franklin"},
      {"id": "M-1002", "first_name": "Robert", "last_name": "Lovett", "town": "Hollis"},
      {"id": "M-1003", "name": "O'\''Brien, Mary"}
    ],
    "min_confidence": 0.6,
    "matched_only": true
  }'
```

Fields per record: `first_name`, `last_name`, and optionally `town` (or `city`).
A single `name` field works too, as either `"First Last"` or `"Last, First"`.
Your `id` is echoed back so you can join to your own database. We do not store it.

Body options:

| Option | Default | Meaning |
|---|---|---|
| `min_confidence` | `0.6` | Drop matches below this score |
| `matched_only` | `true` | Omit unmatched rows from the response |

Limit is 5,000 records per request.

Response:

```json
{
  "year": 2026,
  "submitted": 3,
  "matched": 3,
  "returned": 3,
  "results": [
    {
      "input_id": "M-1001",
      "matched": true,
      "best_confidence": 0.9,
      "matches": [
        {
          "id": 1482,
          "first_name": "William",
          "last_name": "Horn",
          "town": "Franklin",
          "party": "R",
          "office": "State Representative",
          "district": "Merrimack 4",
          "year": 2026,
          "match_type": "nickname+town",
          "confidence": 0.9
        }
      ]
    }
  ]
}
```

### How confidence works

| `match_type` | Score | Meaning |
|---|---|---|
| `exact` | 0.95 | Surname and first name both match |
| `nickname` | 0.85 | Bill/William, Cinde/Cynthia, and similar |
| `initial` | 0.60 | One side has an initial only, e.g. `J MacDonald` |
| `partial` | 0.55 | One first name is a prefix of the other |
| `surname_only` | 0.30 | No first name supplied |

Supplying a `town` that agrees adds `0.05` and appends `+town` to the type.
A town that disagrees subtracts `0.35`, so a same-name person in a different town
falls below the default threshold instead of being reported as a match.

Generational suffixes are ignored, so `Smith Jr` matches `Smith`. Accents and
punctuation are ignored, so `O'Brien` and `OBrien` are the same.

Treat anything under `0.9` as needing human review. Ten different people named
Smith filed in 2026.

## GET /api/v1/candidates

The full roster, if you would rather match locally.

```bash
curl -H "X-API-Key: $KEY" "https://<host>/api/v1/candidates?party=R&office=State%20Representative"
```

Optional filters: `party` (`R`, `D`, `I`), `office` (substring match), `town`.

```json
{ "year": 2026, "count": 365, "candidates": [ ... ] }
```

## Limits and errors

Rate limits are 60 requests per minute on `/match` and 30 per minute on
`/candidates`.

| Status | Meaning |
|---|---|
| `401` | Key missing or not valid |
| `413` | More than 5,000 records in one request |
| `400` | `records` was not a list |
| `429` | Rate limit hit, retry after a pause |
