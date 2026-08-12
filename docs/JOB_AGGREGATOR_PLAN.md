# Job Aggregator v1 — Implementation Plan

Concrete build plan for **Phase 1** of [ROADMAP.md](ROADMAP.md). Goal: a compliant job
search inside CVOLVE PRO using **official/free APIs only — no scraping**, **zero LLM calls**,
and a local keyword match score that reuses existing code.

**Design principle carried from the roadmap:** ship a bare-minimum, cost-free v1 that runs
**locally today without any client-provided API keys**, then light up more sources as keys
arrive.

---

## 1. Sources (tiered by whether they need a key)

| Source | Endpoint | Key needed? | Strength | v1 tier |
|---|---|---|---|---|
| **Remotive** | `GET https://remotive.com/api/remote-jobs?search=&limit=` | ❌ none | Remote tech/non-tech | **Tier A (always on)** |
| **Arbeitnow** | `GET https://www.arbeitnow.com/api/job-board-api` | ❌ none | EU + remote | **Tier A (always on)** |
| **Adzuna** | `GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}` | ✅ `app_id` + `app_key` (free tier) | Location/onsite, salary data | Tier B (key-gated) |
| **Jooble** | `POST https://jooble.org/api/{key}` | ✅ free key | Broad aggregator | Tier B (key-gated) |
| **JSearch** | `GET https://jsearch.p.rapidapi.com/search-v2` | ✅ RapidAPI key | Salary/remote data | Tier B (key-gated) |
| **The Muse** | `GET https://www.themuse.com/api/public/jobs` | ❌ none | Curated roles, levels | Tier A (always on) |
| **Findwork** | `GET https://findwork.dev/api/jobs/` | ✅ `FINDWORK_API_KEY` (free token) | Dev jobs (HN/boards), remote flag | Tier B (key-gated) |

**Why tiers matter:** Tier A gives a working aggregator with no credentials — so we can build,
demo, and start "running locally" immediately. Tier B adapters register themselves only when
their keys are present in the environment; absent keys = source silently skipped, never an error.

No scraping anywhere — satisfies the client's explicit constraint.

---

## 2. Architecture placement

New standalone module **`job_aggregator.py`**, framework-agnostic (pure Python + `requests`),
imported by the Streamlit UI. Deliberately **not** coupled to Streamlit or FastAPI so
`api_server.py` can reuse it later (for the Chrome extension / jobsqa frontend) without a rewrite.

```
Streamlit tab (app.py)  ──►  job_aggregator.search_jobs(query, resume_text)
                                   │
                 ┌─────────────────┼──────────────────┐
             Remotive          Arbeitnow          Adzuna/Jooble (if keys)
                 └───────── normalize → dedupe → score → sort ─────────┘
                                   │
                          in-process TTL cache
```

v1 is **Streamlit-only** (the main product surface). API exposure is a later, separate step.

---

## 3. Module design — `job_aggregator.py`

### Data model
```python
@dataclass
class Job:
    title: str
    company: str
    location: str
    remote_type: str      # "Remote (in-country)" | "Remote (worldwide)" |
                          # "Remote — check eligibility" | "Onsite/Hybrid" | "Contract/Project"
    job_type: str         # full-time / contract / etc. (source-provided when available)
    url: str              # source apply/detail link
    source: str           # machine id: "remotive"
    source_name: str      # display: "Remotive"
    posted_date: str      # ISO date when available
    description: str      # HTML-STRIPPED plain text — used for display + match scoring
    salary: str | None = None        # optional; Adzuna provides, others usually don't
    match_score: int | None = None   # 0–100, None if no resume uploaded
```

> `description` is always HTML-stripped on normalize (Remotive returns HTML). Raw HTML must
> never reach the match scorer (pollutes keywords) or the UI (XSS — see §12).

### Source adapters
Each source is an adapter exposing:
- `enabled() -> bool` — Tier A returns `True`; Tier B checks for its env keys.
- `fetch(query) -> list[dict]` — raw payloads, with per-request timeout (~8s) and a
  descriptive `User-Agent`; caps results per source (~25).
- `normalize(raw) -> Job` — maps source fields onto the `Job` model, **strips HTML** from
  the description, applies the work-type mapping below.

**Tier B key states** — an adapter distinguishes three cases so failures are legible:
- **No key in env** → `enabled()` returns `False`, source skipped silently.
- **Key present, request 401/403** → surfaced once as "Adzuna: check API key", not a crash.
- **Key present, request OK** → normal.

### Orchestration
```python
def search_jobs(query: SearchQuery, resume_text: str | None) -> list[Job]:
    # 1. cache lookup by (sorted-source-set, query-hash)
    # 2. fan out to enabled() sources; per-source try/except so one failure ≠ total failure
    # 3. normalize + dedupe (by (title, company) or normalized url)
    # 4. score each job (local keyword overlap) if resume_text present
    # 5. filter by requested work_type; sort by match_score desc, then posted_date
    # 6. cache + return, plus a per-source status map (returned / errored / skipped)
```

### Caching
Module-level dict `{cache_key: (timestamp, raw_jobs)}` with a configurable TTL
(default **30 min**, env-overridable). Survives Streamlit reruns, respects provider rate
limits, speeds the UI. No external cache dependency.

Three rules that fell out of the test matrix (§12):
- **Cache is user-independent.** Store *normalized jobs without `match_score`*. Match scoring
  is applied per-request against the current user's resume **after** cache retrieval — so one
  user's scores never leak into another's results.
- **Cache key = hash of all query fields _plus_ the enabled-source set** (not just the title),
  so changing a filter or connecting a key produces a distinct entry.
- **Stale-while-error.** On a source timeout / 429 / 5xx, serve that source's last good cached
  payload even if the TTL has expired, rather than dropping the source.

---

## 4. Work-type mapping (honest labelling)

Search filter enum → source fields:

| Filter | Meaning | Mapping note |
|---|---|---|
| Remote — in country | Remote but geo/visa-bound | Only when a source states the country restriction |
| Remote — worldwide | Hire-anywhere remote | Only when a source explicitly says worldwide |
| Contract / Project | Non-permanent | From source `job_type` where present |
| Onsite / Hybrid | Location-bound | Default when a physical location is given |

Where a source **can't distinguish** visa-bound vs worldwide remote (common with Remotive),
label it **"Remote — check eligibility"** rather than guessing — matches the roadmap's honesty
requirement.

---

## 5. Match score — zero LLM (reuse existing code)

Add one function to [utils.py](../utils.py), reusing the existing
`extract_keywords_from_text()` + `filter_keywords()`:

```python
def keyword_overlap_score(resume_text: str, jd_text: str) -> int:
    """0–100 local match: % of meaningful JD keywords present in the resume. No API calls."""
```

- Resume text source: `st.session_state.uploaded_resume` → `extract_resume_text()`
  (already used across the CV flow).
- **No resume uploaded** → `match_score = None`; the card shows "Upload a resume for match
  score" instead of a number. Search still works.

### Years of experience — soft signal (decided)
No source API filters by YoE, so v1 keeps the field but **never hides jobs** with it:
- Parse a rough level from each JD (`junior/mid/senior/lead` + "N+ years" regex).
- Nudge sort/score: gently boost cards whose inferred level is near the user's YoE; small
  penalty when the JD clearly demands far more (or is clearly junior). Match score stays the
  dominant signal.
- Show a per-card seniority hint (e.g., "Senior · ~5+ yrs") when parseable; omit when not.
- Purely local string parsing — still **zero LLM calls**.

---

## 6. Credits — 1 credit per search

Reuse the existing path: `deduct_user_credits(email, 1, feature="Job Search")`
([app.py:2258](../app.py#L2258)).

- `credit_usage.feature` is `VARCHAR(50)` → `"Job Search"` fits; **no schema change**.
- Deduct **after** at least one source returns results, so users aren't charged when every
  source errors. Insufficient credits → the existing warning + blocked action.
- A cache hit still counts as one search (simplest, and matches user expectation).

**No new DB table for v1.** `credit_usage` already logs each search for analytics. A
`job_searches` table (saved queries / history) is deferred.

---

## 7. UI — new "Find Jobs" tab

At [app.py:541](../app.py#L541), extend:
```python
tab1, tab2, tab3, tab4 = st.tabs(
    ["🎯 Match Me to Job", "🔎 Find Jobs", "📊 Analytics", "💳 Billing"]
)
with tab2:
    show_job_aggregator_page()
```

`show_job_aggregator_page()`:
- **Search form**: job title, years of experience, location, geography, work-type
  multiselect. Small caption listing which sources are live (Tier A always; Tier B "connect
  a key to enable").
- **Search button** → credit check/deduct → `search_jobs()` → render cards.
- **Job card**: title · company · location · remote-type badge · job type · **match-score
  badge** · posted date · "View / Apply" source link (opens in new tab).
- Shows the per-source status line ("Remotive ✓ · Arbeitnow ✓ · Adzuna – no key").

---

## 8. Config & dependencies

- **`requirements.txt`**: add and pin `requests` (currently only transitive).
- **`.env.example`** (all optional, Tier B only):
  ```
  ADZUNA_APP_ID=
  ADZUNA_APP_KEY=
  JOOBLE_API_KEY=
  JOB_CACHE_TTL_MIN=30
  ```
- Document that Remotive + Arbeitnow need **nothing** to run.

---

## 9. Build steps (in order)

1. ✅ `requirements.txt` += `requests`; `.env.example` += optional keys + TTL.
2. ✅ `job_aggregator.py`: `Job`/`SearchQuery` models, TTL cache, orchestration,
   Remotive + Arbeitnow + Adzuna adapters.
3. ✅ `utils.py` += `keyword_overlap_score()` (reuses existing keyword helpers).
4. ⬜ Wire credit deduction (`feature="Job Search"`).  *(needs running app — deferred)*
5. ⬜ `app.py`: add tab + `show_job_aggregator_page()`.  *(needs running app)*
6. ✅ **Engine verified**: 26 hermetic unit tests (`tests/test_job_aggregator.py`) +
   live end-to-end run — all 3 sources `ok`, scoring/salary/dedupe/sort confirmed.
7. ✅ Adzuna key added & live-verified (moved from Tier B → active). Jooble/JSearch/Findwork
   keys added & live-verified too; engine now runs 7 sources (Remotive, Arbeitnow, The Muse,
   Adzuna, Jooble, JSearch, Findwork).
7a. ✅ **Volume tuning** (2026-08): non-Adzuna sources now fetch more — JSearch `num_pages=3`,
   Jooble `ResultOnPage=100` + All-Countries location fan-out, Findwork follows `next` links,
   Adzuna 2 pages/country, Arbeitnow ANY-distinctive-token match + paging, The Muse 4 pages.
   Per-source `max_variants` caps keep slow fan-outs (Adzuna=1, JSearch=2) responsive; latency
   stayed ≈ baseline while non-Adzuna shown jobs roughly quadrupled. Accuracy filters untouched.
7b. ✅ **Result fast-path**: scored/filtered results are cached keyed on query + resume-content
   hash (never user identity) with the same TTL, so an identical repeat search skips re-fetch,
   re-scoring and the LLM re-ranker — 30s+ cold → ~10ms warm. Explicit `sources=` calls bypass it.
8. ⬜ *(Later, separate)* expose `search_jobs()` via `api_server.py` for the extension.

**Engine (steps 1–3, 6–7) is done and green.** Remaining work (4–5) is the UI + credit
wiring, which needs Postgres + the app running — aligns with the deferred credit-deduction
test.

---

## 10. Testing

- **Unit**: `normalize()` per source against a saved sample payload (fixtures) + edge cases
  (missing fields); `keyword_overlap_score()` with known resume/JD pairs.
- **Resilience**: simulate one source timing out → search still returns the others.
- **Manual**: real search against Remotive/Arbeitnow locally; confirm no LLM call fires and
  exactly 1 credit is deducted per search.

---

## 11. Client-facing notes / open questions

- **Sources "good enough" for v1?** Roadmap flags this as the only client input needed —
  Tier A (Remotive + Arbeitnow) works immediately; Adzuna/Jooble need them to create free
  keys.
- **API cost:** zero LLM; only free-tier job-board APIs. **New deps:** `requests` only.

---

## 12. Test matrix & edge-case handling

The scenarios v1 must survive, and the concrete rule for each. Rows marked **UT** get a unit
test; **M** = manual/integration check.

### Source & network
| # | Scenario | Handling |
|---|---|---|
| 1 | Source 200 + valid payload | normalize → Job (**UT** per source w/ fixture) |
| 2 | Source timeout (>8s) | skip source, others still return (**M**) |
| 3 | Source 429 rate-limited | serve stale cache for that source (stale-while-error) |
| 4 | Source 5xx / malformed JSON | skip source, log, continue |
| 5 | Source returns empty list | not an error → "no results from X" |
| 6 | **All** sources fail | **no credit deducted**; show "couldn't reach job sources, try again" |
| 7 | Tier B key missing | `enabled()=False`, skip silently |
| 8 | Tier B key invalid (401/403) | warn once ("check API key"), don't crash |

### Normalization / data quality
| # | Scenario | Handling |
|---|---|---|
| 9 | HTML in description (Remotive) | **strip to plain text** before score + display (**UT**) |
| 10 | Missing company / date / description | safe defaults; no description → `match_score=None` |
| 11 | Duplicate across sources | **dedupe** (rule below) (**UT**) |
| 12 | Non-English posting (Arbeitnow DE) | accepted as-is; low match score OK; no crash |
| 13 | Very long description | cap to ~8k chars before scoring (perf) |
| 14 | Salary present (Adzuna) | populate `salary`; others `None` |

### Query / filters
| # | Scenario | Handling |
|---|---|---|
| 15 | Empty job title | **require a title** to search (button disabled until filled) |
| 16 | Filters exclude everything | "no jobs match your filters" (≠ "sources returned nothing") |
| 17 | Location set, Tier A remote-only | UI note: location mainly affects Adzuna |
| 18 | Special chars in query | URL-encode all params; length caps |

### Match scoring
| # | Scenario | Handling |
|---|---|---|
| 19 | No resume uploaded | `match_score=None`; card shows "upload resume for score" |
| 20 | Resume extraction empty/garbage | score `0` or `None`; no crash (**UT**) |
| 21 | Determinism | same inputs → same score (**UT**) |

### Caching / credits / security
| # | Scenario | Handling |
|---|---|---|
| 22 | Identical search within TTL | served from cache; still 1 credit (search = search) |
| 23 | Cross-user cache | cache holds **no** match scores; scored per-request (§3) (**UT**) |
| 24 | Double-click search | button guard prevents double deduction |
| 25 | Insufficient credits | blocked before fetch (existing warning) |
| 26 | XSS via job text | render **without** `unsafe_allow_html`; Streamlit escapes (**M**) |

### Dedupe rule (row 11)
1. Primary key = normalized `url` (strip query string / trailing slash).
2. Fallback when URLs differ = `(lower(title), lower(company))`.
3. On collision, keep the copy with the **more complete** record (has salary/date/description);
   tie-break by higher `match_score`. Record which sources it appeared in for display.

### Three distinct empty states (rows 5/6/16)
Never collapse these into one "no results" — they need different user action:
- **Filtered out** → loosen your filters.
- **Sources returned nothing** → try a different title/location.
- **Sources unreachable** → transient, retry (and no credit charged).
