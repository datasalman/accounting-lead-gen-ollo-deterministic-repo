# Accounting lead-gen — deterministic half (+ ollo agent setup)

A take-home for the ollo AI Forward Deployed Engineer bootcamp. The job: turn Bedfordshire
companies that are **behind on their Companies House filings** into **drafted, human-approved
outreach** for a local accountancy firm.

This repository is the **deterministic half** — the part a script should own — plus everything
needed to wire up the **agent half** in ollo. The two halves are separated on purpose, and
that boundary is the whole design.

---

## The governing principle: a hard deterministic / judgment boundary

> Give the agent only the work that needs judgment. Everything a rule can decide is code.

| | **CODE** (this repo, runs before the demo) | **AGENT** (ollo) |
|---|---|---|
| Bulk-filter 5.6M companies to a region | ✅ | |
| Verify a company is still live (CH API) | ✅ | |
| Classify the filing signal | ✅ | |
| Look up the director + service address | ✅ | |
| Is this a *real, trading* business? | | ✅ reads the website |
| Email, or letter, or don't bother? | | ✅ variable path |
| Draft the outreach | | ✅ |
| Refuse a bad lead | | ✅ abstain |

The agent gets **no Companies House API access and no code execution.** If a step is
deterministic, it does not belong in the agent — putting it there just adds a place for a
language model to be wrong about something a `<` comparison already settled. Knowing what
*not* to give the agent is the point.

---

## Architecture

```
 Companies House "Free Company Data Product"  (local bulk CSV, ~2.7 GB, ~5.6M companies)
        │
        │  scripts/build_leads.py   ── DETERMINISTIC, run once ──
        │
        ├─ 1. FILTER    region (Bedfordshire) + overdue/strike-off + not dissolved   (stdlib csv, streaming)
        ├─ 2. VERIFY    live vs Companies House API  → drop already-filed / dissolved
        ├─ 3. ENRICH    first active director + service (correspondence) address
        ├─ 4. CLASSIFY  COMPULSORY_STRIKEOFF / VOLUNTARY_STRIKEOFF / OVERDUE_ACCOUNTS
        └─ 5. WRITE     data/qualified_leads.json   +   data/agent_input.md
                                    │
                                    │  paste agent_input.md into ─────────────┐
                                    ▼                                          │
        ollo AGENT  ── JUDGMENT ──                                            │
        find website → read it (Firecrawl) → real business? → email/letter/abstain
                    → HUMAN GATE (pick leads) → Gmail draft  /  letter artifact
                                    (never sends)
```

---

## What the pipeline actually filters (the ICP, as implemented)

A company is a **candidate** iff **all** of:

1. **In region** — post town ∈ the 14 Bedfordshire towns, **or** postcode district ∈
   `LU1–7 / MK40–45 / SG15–19`, **or** county ∈ `{BEDFORDSHIRE, BEDS}` (county is a
   deliberately weak fallback — the CH county field is unreliable).
2. **Has a filing problem** — accounts overdue (`accounts_next_due < today`) **or**
   confirmation statement overdue **or** status contains "strike off".
3. **Not dissolved.**

It then live-verifies each candidate and **keeps only those still overdue / proposed for
strike-off today** (the monthly snapshot can be up to a month stale, so a chunk have already
filed or been struck off — those are dropped).

> This is a **"companies in filing trouble"** play, not a "newly incorporated" play.
> Incorporation date is captured but never filtered on. The buying signal is distress:
> a company about to be struck off is a motivated buyer for exactly the service an
> accountant sells.

### The signal classification (and its one honest limitation)

| Signal | Rule | Meaning |
|---|---|---|
| `COMPULSORY_STRIKEOFF` | strike-off **and** overdue | Registrar is striking them off for non-filing. Salvageable, motivated — the hottest lead. |
| `VOLUNTARY_STRIKEOFF` | strike-off **and not** overdue | Directors applied to close it themselves (DS01). Usually wants it gone — weak; the agent abstains unless the web contradicts it. |
| `OVERDUE_ACCOUNTS` | overdue, no strike-off | Behind but not yet at strike-off. A solid lead. |

**Limitation, stated plainly (interview-ready):** compulsory-vs-voluntary is a *heuristic*.
The register's `company_status` shows both as "proposed to be struck off"; the true
distinction (a registrar **s1000** first gazette notice vs a voluntary **DS01** application)
lives in the **filing-history gazette codes**, which this build does not read. The
overdue-implies-compulsory approximation is right the large majority of the time and wrong
at the edges. **Named next upgrade:** read `GET /company/{n}/filing-history` and key off the
gazette description instead of inferring from the overdue flag.

---

## Why this is an agent, not a cron job

An agent is justified only where a script cannot decide. This task clears all three bars —
but only in its second half:

1. **Unstructured input needing judgment.** "Is this a real, trading business worth
   contacting?" is answered by *reading a website* — messy, unschematised text. No filter
   expresses it.
2. **Variable path.** Email on the site → use it. No email → try the about/contact page.
   No website at all → fall back to a **physical letter** to the registered office. The agent
   *choosing* that fallback per-lead is the thing a script can't do.
3. **Deterministic ending.** Every lead terminates in one of three defined states: a Gmail
   draft, a queued letter, or a logged abstention. The judgment is bounded.

The first half of the task fails bar 1 and 2 — filtering, verifying and classifying are pure
rules — which is exactly why they're **code in this repo**, run before the agent ever starts.

---

## The autonomy decision: draft-only, behind a human gate

The agent **drafts and queues; it never sends.** After processing all leads it presents a
ranked shortlist + a "not contacting" list and waits for a human to pick numbers.

- **Why gated:** UK cold B2B outreach carries GDPR/PECR exposure. A human approving each send
  is a cheap, defensible control, and "I can explain why I gated it" is worth more in this
  interview than autonomy would be. The deterministic half already did the expensive
  narrowing, so the human is only ever rubber-stamping ~a handful of drafts, not doing work.
- **Strongest counter-argument (so I'm not caught out):** the gate caps throughput and a
  confident model arguably doesn't need it for a first *draft* (a draft sends nothing). A
  fair critique. My answer: the gate isn't there because drafting is risky — it's there
  because *sending* is, and keeping a human between "drafted" and "sent" is the lowest-cost
  place to sit the control. If volume grew, I'd move to **confidence-tiered** autonomy:
  auto-queue High-confidence drafts, gate Medium, and always gate Low / any first-contact —
  but I would not remove the gate on send.

---

## Compliance notes (built in, not bolted on)

- **Public data only.** Everything stored comes from the public register.
- **Service addresses only.** Director *correspondence* addresses are stored; residential
  addresses are never fetched or written.
- **The agent never sends.** Drafts and letters only, human-approved. Every email carries an
  opt-out line (PECR).

---

## Reuse provenance

The verify/classify/officer logic is **lifted from the source micro-SaaS** (an internal Flask
app that does this at scale for the client):

- `interpret()` (live closed/confirmed/resolved classification) and `CLOSED_STATUSES` — verbatim.
- `parse_officers` / name-flip / service-address flattening — verbatim.
- The region preset and the region+overdue+not-dissolved filter *semantics* — reused; the
  scan is re-expressed in the **stdlib** here (the micro-SaaS uses Polars) so this run-once
  script needs only `requests` — one wheel, no build step, nothing to break on a demo laptop.

Parity with the micro-SaaS's own test suite is checked (same fixture keeps `{1,2,4,7}`,
drops out-of-region/dissolved/healthy).

---

## Results (this run)

<!-- RESULTS -->
Run against the July 2026 bulk file (`BasicCompanyDataAsOneFile-2026-07-01`, 5,734,780 companies):

| | |
|---|---|
| Scanned | 5,734,780 companies |
| **In-region candidates** (Bedfordshire, overdue/strike-off, not dissolved) | **10,417** |
| Live-checked (stratified demo slice) | 39 |
| → still live & qualified (**kept**) | **21** |
| → dropped: **already dissolved/struck-off since the snapshot** | **18** |
| → dropped: already filed | 0 |
| Kept leads with a named director | 19 / 21 |
| By signal | 12 `COMPULSORY_STRIKEOFF` · 3 `OVERDUE_ACCOUNTS` · 6 `VOLUNTARY_STRIKEOFF` |

Two things worth saying out loud in the interview:
- **The live check earns its keep:** 18 of 39 candidates were dropped because they'd been
  struck off or dissolved in the ~2 weeks since the monthly snapshot. Skipping verification
  would mean drafting to dead companies ~half the time. This is *why* verification is code,
  not a nicety.
- The "21 kept from 39 checked" is a **deliberately capped demo slice** (the pipeline stops at
  a per-signal quota so the agent gets a varied set). The full addressable pool is 10,417
  candidates → thousands of live leads; this repo just produces enough to demo well.
- **The distress ↔ contactability tension** (a genuinely useful finding): the businesses
  distressed enough to still be overdue are the *least* likely to have a polished web presence.
  Curating the 8-lead demo set meant re-verifying candidates live — and several promising
  trading businesses (a driving-safety firm, a High-St tobacconist, a garage) had **caught up
  on their filings since the June export and were no longer leads.** That "resolved since
  snapshot" drop is the same reason the live check exists — and it's why the demo leads are
  re-verified on the day, not trusted from a month-old file.

Curated demo set: **[data/agent_input.md](data/agent_input.md)** (8 leads, each role
pre-verified live). Full output: **[data/qualified_leads.json](data/qualified_leads.json)**
(24 leads — the 21 from the run plus 3 established trading businesses re-verified live so the
demo reliably exercises the email path).

---

## Run it yourself

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # then paste your CH_API_KEY into .env
python scripts/build_leads.py
```

Outputs land in `data/`. The bulk CSV is **not** in this repo (it's ~2.7 GB and not ours to
host); point `CH_BULK_CSV` at your local copy or drop it beside the repo.

## Then: build the agent

See **[OLLO_SETUP.md](OLLO_SETUP.md)** — model choice, the exact 4 tools to connect (and the
ones to deliberately skip), Gmail authorisation, how to start a run, the 3-minute demo script,
and a rehearsal checklist. The agent's system prompt is **[AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md)**.
