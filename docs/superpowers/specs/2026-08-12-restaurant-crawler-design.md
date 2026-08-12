# restaurant-crawler — v1 design

> **Status** Approved design · **not built** · **Updated** 2026-08-12 · **Version** v1

Implements [`crawler-service-spec.md`](../../../../restaurant-rag/docs/crawler-service-spec.md)
from the `restaurant-rag` repo. That document is the contract; this one is how
v1 satisfies it and what v1 deliberately leaves out.

Code follows `restaurant-rag/docs/coding-guidelines.md`.

---

## 1. Scope

An HTTP service that takes a neighborhood name and returns structured restaurant
records plus review text. It stores nothing, embeds nothing, and knows nothing
about the consumer beyond the JSON shape in the contract.

**Done means the local service passes the contract's §7 acceptance criteria**,
verified by `curl` against `localhost`. Deployment to Render/Railway/Fly and the
consumer-side wiring in `restaurant-rag` are separate pieces of work with their
own plans.

### Sources in v1

| Source | Role | Status |
|---|---|---|
| Google Places | discovery, facts, reviews | in v1 |
| Foursquare | tips as review prose, price, categories | in v1 |
| Restaurant website | page text into `raw`, explicit dietary claims | in v1 |
| Reddit | honest opinion prose | **deferred** |

Reddit is deferred because it is name-matched rather than id-matched, needs its
own app registration, and needs filtering the other sources do not. Three sources
satisfy every acceptance criterion, including #5, which needs at least two.

**Discovery requires Google or Foursquare.** The website crawl enriches a place
that has already been found; it cannot produce the list. A deployment with no API
credentials cannot satisfy criterion #2 regardless of implementation.

### Not in v1

Each of these is absent by decision, not oversight:

- **Deep website parsing.** No hours, menu, or price extraction from arbitrary
  restaurant HTML. Those fields come from the APIs.
- **Retries and backoff.** A failed source becomes an `errors` entry and the job
  still succeeds. Partial success already covers this.
- **Per-host rate limiting.** At 3–5 restaurants behind a semaphore of 4 the
  service is already under 1–2 req/s, and each restaurant site is a different
  host. The semaphore is the rate limit.
- **A disk cache of raw upstream payloads.** Worth it at hundreds of restaurants;
  at demo scale it is another layer to debug.
- **A CLI entry point.** `curl` against localhost is the same thing with no code.
- **Job eviction.** Jobs are an in-process dict that dies on restart, which the
  contract explicitly blesses. Stated in the README.
- **Integration tests against live APIs.** They need credentials, cost money, and
  fail for reasons that are not the code's fault. See §6.

## 2. Repo layout

```
restaurant-crawler/
  app/
    main.py         FastAPI: three routes + the bearer check
    config.py       env vars, and which sources have credentials
    jobs.py         in-memory job registry + the discovery -> fan-out pipeline
    normalize.py    the pure §4 rules: hours, slug, dietary, price tier, assembly
    sources/
      google.py       Places text search + details
      foursquare.py   place search + tips
      website.py      crawl4ai against the restaurant's own site
  tests/            pure-logic and route tests, no network
  fixtures/         recorded raw payloads
  Dockerfile  docker-compose.yml  Makefile  requirements.txt  .env.example  README.md
```

The load-bearing seam is `sources/` against `normalize.py`. Everything in
`sources/` performs I/O and returns **untouched** upstream JSON — that JSON is
what lands in the contract's `raw` field. `normalize.py` is pure: dicts in, dicts
out, no network and no clock, so the rules the contract says are where the bugs
live can be tested with no credentials.

`normalize.py` is one file rather than four because it lands around 180 lines,
inside the guideline limit, and it is the single thing to read to understand
every normalization rule at once.

`sources/` stays split because each source is independently optional: a missing
Foursquare key must disable exactly one file's worth of behaviour. There is no
shared `http.py` — each source uses `httpx` directly until three copies of the
same logic exist to extract.

Conventions carried over from the sibling `restaurant-rag-ingest` repo: `pytest`,
a `Makefile` of short commands, and `uvicorn --workers 1`, because job state is
in-process and a second worker would answer polls for jobs it cannot see. Not
carried over: that repo installs Chromium onto `python:3.11-slim`; the contract's
§6 requires a Playwright base image instead.

Source modules port the request details already worked out in
`restaurant-rag-ingest/app/sources/` — notably that Places Text Search uses
`places.`-prefixed field masks while Place Details uses bare ones, and that
Foursquare yields far more tips across its `POPULAR` and `NEWEST` sorts. All
database and consumer-schema knowledge is dropped in the port.

## 3. Job flow

`POST /crawl` does four cheap things and returns: validate the body, look for an
existing `queued` or `running` job for the same neighborhood, create the job,
hand the pipeline to `asyncio.create_task`, respond `202`. No network call
happens before the response, which is how criterion #1 — a jobId in under a
second — is met.

Returning the existing job's id on a duplicate is required, not an optimization:
the consumer calls this on a user click and clicks double-fire.

The pipeline then:

1. **Discovery.** One Google Places text search for
   `restaurants in <neighborhood>, <city>`, capped at `limit`. Sets `found` and
   `total`.
2. **Fan-out.** Each candidate is enriched concurrently behind a semaphore of 4:
   Google details for hours, price and reviews; a Foursquare lookup by name near
   the place's coordinates for tips; a crawl4ai pass over the restaurant's own
   site when Google supplied one. `completed` increments per restaurant finished.
3. **Assemble.** Each restaurant's raw payloads go through `normalize.py` into
   one record.
4. **Finish.** `succeeded` when at least one restaurant survived, `failed` only
   when none did.

`GET /crawl/{jobId}` reads the registry: progress while running, the full payload
when done, `404` for an unknown id.

**The whole-job timeout is a deadline, not a kill switch.** `asyncio.wait_for` at
`JOB_TIMEOUT_SECONDS` (default 300) wraps the fan-out. On expiry the restaurants
that finished are returned as `succeeded`, with the unfinished ones recorded in
`errors`. Returning two of three restaurants beats returning a failure.

## 4. Normalization

The rules are the contract's §4 verbatim; this section records only the decisions
the contract left open.

**Neighborhood codes cannot be derived.** The codes in use are `ev` East Village,
`fl` Flushing, `wb` Williamsburg, `hl` Harlem, `as` Astoria, and the contract's
own example uses `bw` for Bushwick. `wb` and `bw` follow no rule that also
produces `hl`. So `normalize.py` holds a dict of those six, plus a deterministic
fallback of the first two letters of the neighborhood name for anything new,
logged at INFO so a better code can be added by hand. Both the table and the
fallback are stable across re-crawls, which is what criterion #4 grades and what
keeps the consumer's upsert-by-slug from creating duplicates.

**Dietary tags come only from explicit statements.** Google and Foursquare
structured attributes, plus a literal phrase scan over the crawled page markdown
for claims such as "vegan menu", "certified halal", "gluten-free". Never inferred
from cuisine. The evidence goes into `raw` so the consumer can audit it. An
omitted tag reads as unknown, which is correct; a wrong halal or kosher tag can
cause real harm.

**Price tier when no source gives a price is `2`**, recorded in `raw`, never `0`
and never `null`.

**Foursquare matching** takes the top result for a name search near the Google
place's coordinates. When nothing matches, that restaurant simply has no
Foursquare contribution — not an error.

## 5. Errors and partial success

Failure is per-restaurant-per-source and is never fatal. Each enrichment call is
wrapped where it is made, catching the specific errors it can raise —
`httpx.HTTPError`, `asyncio.TimeoutError`, crawl4ai's own failure — never a bare
`except`. A failure appends `{source, slug, message}` to `errors` and leaves that
field empty on the record; the restaurant still ships with whatever worked.

`sourceStatus` is derived at the end rather than tracked along the way: `ok` when
a source worked for every restaurant, `partial` for some, `failed` for none.

**Deviation from the contract, agreed 2026-08-12:** §3 shows `sourceStatus` with
all four sources present. In v1 Reddit is not implemented and a source may have
no credential. Rather than introduce a `"skipped"` value the consumer's zod
schema does not expect, **a source that did not run is absent from the map**. The
consumer reads `sourceStatus` as a dict, so an absent key is unambiguous. Enabled
sources are logged at startup.

**Assembly validates per record.** The output is a pydantic model, so a
restaurant that cannot produce seven `hours` keys or a price tier in 1–4 raises,
and that raise drops *that restaurant* with an `errors` entry rather than killing
the job. This is a deliberate softening of fail-loudly: the alternative is one
malformed Google hours payload ending a live demo. Zero surviving restaurants is
still a `failed` job.

**Config fails loudly at startup.** A missing `CRAWLER_API_KEY` exits the process
with a message naming the variable. Source credentials are optional and their
absence is logged at INFO, since running without one is legitimate.

**Logging** is stdlib, one logger per module, including the line the contract's
§6 requires: one per restaurant per source, carrying jobId, slug, source, outcome
and duration. No secrets, ever.

## 6. Tests

`pytest`, no network, two groups.

**`normalize.py` against committed fixtures**

- hours: all seven keys present, 24-hour values, a closed day as `null`, a
  past-midnight close
- price tier: each source mapping, and the no-price-anywhere default of `2`
- slug: identical output across two runs, the known-code table, the fallback
- dietary: casing and spelling normalization, and an explicit test that a Middle
  Eastern restaurant with no stated halal attribute yields `[]`
- reviews: plain text, no markup, no reviewer names, not merged across reviews

**The routes via FastAPI's `TestClient`, pipeline stubbed**

- `401` without a bearer token on `/crawl`, but `/health` open
- the `202` response shape
- a second `POST` for the same neighborhood returning the same jobId
- `404` for an unknown jobId

That covers acceptance criteria #1, #4 and #6 as tests rather than as claims.
Criteria #2, #3 and #5 need live credentials and are a `make smoke` run by hand:
a Bushwick crawl at `limit: 3`, and the same again with a deliberately broken
Foursquare key.

## 7. Configuration

| Variable | Required | Purpose |
|---|---|---|
| `CRAWLER_API_KEY` | yes | the shared bearer secret |
| `GOOGLE_MAPS_API_KEY` | no | enables Google discovery, facts, reviews |
| `FSQ_SERVICE_KEY` | no | enables Foursquare tips |
| `JOB_TIMEOUT_SECONDS` | no, default 300 | whole-job deadline |
| `CRAWL_CONCURRENCY` | no, default 4 | fan-out semaphore size |
| `LOG_LEVEL` | no, default INFO | |

No hardcoded keys, URLs, ports, or paths anywhere.

## 8. Acceptance

The contract's §7, restated as what will be demonstrated:

1. `POST /crawl {"neighborhood":"Bushwick","limit":3}` returns a jobId in under a
   second.
2. Polling that jobId eventually returns `succeeded` with 3 restaurants.
3. Every record validates: seven `hours` keys, `priceTier` in 1–4, stable slug.
4. Re-running the same crawl produces the same slugs.
5. A deliberately broken Foursquare key still yields `succeeded`, with
   `sourceStatus.foursquare` set to `failed` and the reason in `errors`.
6. No endpoint except `/health` is reachable without the bearer token.

Every implementation step ends in a command to run and an expected output. A step
is done when that output has actually been seen, not when the code looks right.
