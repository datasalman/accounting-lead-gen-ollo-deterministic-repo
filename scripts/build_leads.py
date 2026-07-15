#!/usr/bin/env python3
"""
Deterministic lead pipeline for a Bedfordshire accountancy firm (run ONCE, before the
ollo agent demo). This is the "code half" of the build: everything here is a rule a
script can decide. Judgment (is this a real business? email or letter? abstain?) is left
to the ollo agent — this script never touches a website and never drafts anything.

Pipeline
--------
1. FILTER  the Companies House "Free Company Data Product" bulk CSV (local, ~2.7GB) down to
           Bedfordshire companies that are overdue on filings or proposed for strike-off.
           Streaming stdlib `csv` — the file never loads into memory.
2. VERIFY  each candidate live against the Companies House Public Data API (the monthly
           snapshot is up to a month stale) and DROP any that have already filed or closed.
3. ENRICH  each surviving lead with its first active director + service (correspondence)
           address. Home addresses are never stored.
4. CLASSIFY the filing signal: COMPULSORY_STRIKEOFF / VOLUNTARY_STRIKEOFF / OVERDUE_ACCOUNTS.
5. WRITE   data/qualified_leads.json (full output) + data/agent_input.md (curated demo set).

Provenance
----------
The verify/classify/officer logic (interpret, parse_officers, _humanize_name,
_officer_address, CLOSED_STATUSES, the 0.55s rate spacing) is lifted VERBATIM from the
source micro-SaaS (an internal Flask app; app/ch_api.py, app/verify.py). The CSV scan is re-expressed
in the stdlib (the micro-SaaS uses Polars) so this run-once script depends only on
`requests` — one wheel, no build step, zero surprises on a demo laptop.

Run
---
    python scripts/build_leads.py
Env (.env in repo root):  CH_API_KEY=your_key   [CH_BULK_CSV=path\to\bulk.csv]
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import requests

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CH_API_BASE = "https://api.company-information.service.gov.uk"

# Stay comfortably under 600 requests / 5 min (= 0.5s/request). Each lead = 2 calls.
REQUEST_SPACING_S = 0.55

# Bedfordshire preset — embedded as config (mirrors regions.yaml in the micro-SaaS).
REGION = {
    "label": "Bedfordshire",
    "post_towns": {
        "BEDFORD", "LUTON", "DUNSTABLE", "LEIGHTON BUZZARD", "BIGGLESWADE", "SANDY",
        "FLITWICK", "AMPTHILL", "SHEFFORD", "KEMPSTON", "HOUGHTON REGIS", "CRANFIELD",
        "WOBURN", "POTTON",
    },
    "postcode_districts": {
        "LU1", "LU2", "LU3", "LU4", "LU5", "LU6", "LU7",
        "MK40", "MK41", "MK42", "MK43", "MK44", "MK45",
        "SG15", "SG16", "SG17", "SG18", "SG19",
    },
    "county_aliases": {"BEDFORDSHIRE", "BEDS"},
}

# Clean field name -> candidate CH column headers (headers have drifted over the years and
# some carry a leading space, so we strip and probe candidates). Mirrors ingest.py.
FIELD_CANDIDATES = {
    "name": ["CompanyName"],
    "company_number": ["CompanyNumber"],
    "care_of": ["RegAddress.CareOf"],
    "address_line_1": ["RegAddress.AddressLine1"],
    "address_line_2": ["RegAddress.AddressLine2"],
    "post_town": ["RegAddress.PostTown"],
    "county": ["RegAddress.County"],
    "postcode": ["RegAddress.PostCode"],
    "status": ["CompanyStatus"],
    "category": ["CompanyCategory"],
    "incorporation_date": ["IncorporationDate"],
    "accounts_next_due": ["Accounts.NextDueDate"],
    "account_category": ["Accounts.AccountCategory"],
    "confstmt_next_due": ["ConfStmtNextDueDate", "Returns.NextDueDate"],
    "sic_1": ["SICCode.SicText_1"],
    "sic_2": ["SICCode.SicText_2"],
    "sic_3": ["SICCode.SicText_3"],
    "sic_4": ["SICCode.SicText_4"],
}

# Statuses that mean the company is effectively gone — not a lead. (Verbatim from ch_api.py)
CLOSED_STATUSES = {
    "dissolved", "removed", "converted-closed", "liquidation",
    "receivership", "administration", "insolvency-proceedings",
}


# --------------------------------------------------------------------------------------
# Tiny .env loader (avoids a python-dotenv dependency)
# --------------------------------------------------------------------------------------
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------------------
# Date + address helpers
# --------------------------------------------------------------------------------------
def parse_ddmmyyyy(value: str | None) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_iso(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def postcode_district(postcode: str | None) -> str:
    """'LU1 2AB' -> 'LU1'."""
    pc = (postcode or "").strip().upper()
    return pc.split()[0] if pc else ""


def one_line_address(row: dict) -> str:
    parts = [
        row.get("care_of"), row.get("address_line_1"), row.get("address_line_2"),
        row.get("post_town"), row.get("county"), row.get("postcode"),
    ]
    return ", ".join(p.strip() for p in parts if p and p.strip())


# --------------------------------------------------------------------------------------
# Step 1 — FILTER the bulk CSV (streaming; the 2.7GB file never loads into memory)
# --------------------------------------------------------------------------------------
def resolve_csv(source: Path) -> Path:
    """If given a .zip, extract the first .csv inside it (once)."""
    if source.suffix.lower() != ".zip":
        return source
    with zipfile.ZipFile(source) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"No CSV inside {source}")
        out_dir = source.parent / "extracted"
        out_dir.mkdir(exist_ok=True)
        return Path(zf.extract(names[0], out_dir))


def in_region(post_town: str, county: str, postcode: str) -> bool:
    return (
        post_town.upper() in REGION["post_towns"]
        or postcode_district(postcode) in REGION["postcode_districts"]
        or county.upper() in REGION["county_aliases"]
    )


def filter_bulk_csv(csv_path: Path, today: dt.date) -> list[dict]:
    """Scan the bulk CSV and return in-region, overdue-or-strike-off, not-dissolved rows.

    Same keep-rule as the micro-SaaS ingest:
        region_match AND (accounts_overdue OR confstmt_overdue OR strike_off) AND NOT dissolved
    Each returned dict carries the snapshot flags + days_overdue + urgency_score.
    """
    kept: list[dict] = []
    scanned = 0
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader)]
        pos = {h: i for i, h in enumerate(header)}
        # Resolve each clean field to a column index once.
        idx: dict[str, int | None] = {}
        for clean, cands in FIELD_CANDIDATES.items():
            idx[clean] = next((pos[c] for c in cands if c in pos), None)

        def g(rowvals: list[str], clean: str) -> str:
            i = idx[clean]
            return rowvals[i] if (i is not None and i < len(rowvals)) else ""

        for rowvals in reader:
            scanned += 1
            if scanned % 1_000_000 == 0:
                print(f"  ...scanned {scanned:,} rows, kept {len(kept):,}", flush=True)

            post_town = g(rowvals, "post_town")
            county = g(rowvals, "county")
            postcode = g(rowvals, "postcode")
            # Cheap region gate first — the vast majority of rows fail here and cost nothing.
            if not in_region(post_town, county, postcode):
                continue

            status_lower = g(rowvals, "status").lower()
            if "dissolved" in status_lower:
                continue

            acc_due = parse_ddmmyyyy(g(rowvals, "accounts_next_due"))
            conf_due = parse_ddmmyyyy(g(rowvals, "confstmt_next_due"))
            accounts_overdue = acc_due is not None and acc_due < today
            confstmt_overdue = conf_due is not None and conf_due < today
            strike_off = "strike off" in status_lower

            if not (accounts_overdue or confstmt_overdue or strike_off):
                continue

            account_category = g(rowvals, "account_category").strip().upper()
            never_filed = account_category == "NO ACCOUNTS FILED"

            overdue_dates = [d for d, o in ((acc_due, accounts_overdue), (conf_due, confstmt_overdue)) if o]
            days_overdue = (today - min(overdue_dates)).days if overdue_dates else 0
            urgency = (
                (1000 if strike_off else 0)
                + max(0, min(days_overdue, 730))
                + (300 if accounts_overdue else 0)
                + (100 if confstmt_overdue else 0)
            )
            sic = [g(rowvals, k) for k in ("sic_1", "sic_2", "sic_3", "sic_4") if g(rowvals, k)]

            kept.append({
                "company_number": g(rowvals, "company_number").strip(),
                "name": g(rowvals, "name").strip(),
                "status_snapshot": g(rowvals, "status").strip(),
                "care_of": g(rowvals, "care_of").strip(),
                "address_line_1": g(rowvals, "address_line_1").strip(),
                "address_line_2": g(rowvals, "address_line_2").strip(),
                "post_town": post_town.strip(),
                "county": county.strip(),
                "postcode": postcode.strip(),
                "category": g(rowvals, "category").strip(),
                "sic": sic,
                "accounts_overdue_snap": accounts_overdue,
                "confstmt_overdue_snap": confstmt_overdue,
                "strike_off_snap": strike_off,
                "never_filed": never_filed,
                "days_overdue_snap": days_overdue,
                "urgency_score": urgency,
            })
    print(f"  scanned {scanned:,} rows total; {len(kept):,} in-region candidates.", flush=True)
    kept.sort(key=lambda r: r["urgency_score"], reverse=True)
    return kept


# --------------------------------------------------------------------------------------
# Step 2/3 — VERIFY + ENRICH against the CH API  (logic lifted from ch_api.py)
# --------------------------------------------------------------------------------------
class CHAuthError(RuntimeError):
    pass


class CHRateLimited(RuntimeError):
    pass


def _api_get(session: requests.Session, url: str, key: str, params: dict | None = None):
    r = session.get(url, auth=(key, ""), params=params, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code in (401, 403):
        raise CHAuthError("Companies House API rejected the key (401/403). Check CH_API_KEY.")
    if r.status_code == 429:
        raise CHRateLimited("Rate limited by Companies House API (429).")
    r.raise_for_status()
    return r.json()


def interpret(profile: dict | None) -> dict:
    """Turn a live profile (or None for a 404) into the fields we care about.

    state ∈ {closed, confirmed, resolved}:
      closed    — dissolved/removed/etc: dead lead, drop.
      confirmed — still overdue or proposed strike-off: a live lead, keep.
      resolved  — active and no longer overdue: filed since snapshot, drop.
    (Verbatim behaviour from the source micro-SaaS app/ch_api.py::interpret)
    """
    if profile is None:
        return {"state": "closed", "status": "not-found", "status_detail": None,
                "accounts_overdue": None, "confstmt_overdue": None,
                "accounts_next_due": None, "confstmt_next_due": None}

    status = (profile.get("company_status") or "").lower()
    detail = (profile.get("company_status_detail") or "").lower() or None
    accounts = profile.get("accounts") or {}
    confstmt = profile.get("confirmation_statement") or {}

    accounts_overdue = bool(accounts.get("overdue"))
    confstmt_overdue = bool(confstmt.get("overdue"))
    strike_off = "strike-off" in (detail or "") or "strike off" in (detail or "")

    if status in CLOSED_STATUSES:
        state = "closed"
    elif accounts_overdue or confstmt_overdue or strike_off:
        state = "confirmed"
    else:
        state = "resolved"

    return {
        "state": state,
        "status": profile.get("company_status"),
        "status_detail": profile.get("company_status_detail"),
        "accounts_overdue": accounts_overdue,
        "confstmt_overdue": confstmt_overdue,
        "strike_off": strike_off,
        "accounts_next_due": parse_iso(accounts.get("next_due")),
        "confstmt_next_due": parse_iso(confstmt.get("next_due")),
    }


def humanize_name(name: str | None) -> str:
    """'SURNAME, Forename(s)' -> 'Forename(s) Surname'. (Verbatim from ch_api.py)"""
    if not name:
        return ""
    name = name.strip()
    if "," in name:
        surname, _, forenames = name.partition(",")
        return f"{forenames.strip().title()} {surname.strip().title()}".strip()
    return name.title()


def officer_service_address(address: dict | None) -> str:
    """Flatten an officer's SERVICE (correspondence) address. Never a home address."""
    if not address:
        return ""
    parts = [address.get("premises"), address.get("address_line_1"),
             address.get("address_line_2"), address.get("locality"),
             address.get("region"), address.get("postal_code")]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def first_active_director(payload: dict | None) -> dict | None:
    """From /officers, return the first ACTIVE director (directors first, newest first)."""
    items = (payload or {}).get("items") or []
    officers = []
    for item in items:
        if item.get("resigned_on"):
            continue  # active officers only
        officers.append({
            "name": humanize_name(item.get("name")),
            "officer_role": item.get("officer_role"),
            "appointed_on": item.get("appointed_on") or "",
            "address": officer_service_address(item.get("address")),
        })
    officers.sort(key=lambda o: (
        "director" not in (o["officer_role"] or "").lower(),
        o["appointed_on"] or "",
    ), reverse=False)
    # After sort, directors sort first; among them, earliest appointed first — flip to newest.
    directors = [o for o in officers if "director" in (o["officer_role"] or "").lower()]
    if directors:
        directors.sort(key=lambda o: o["appointed_on"] or "", reverse=True)
        return directors[0]
    return officers[0] if officers else None


def classify_signal(live: dict) -> str:
    """The COMPULSORY/VOLUNTARY split the micro-SaaS did NOT compute — added per the brief.

    Heuristic (documented approximation):
      strike-off AND overdue      -> COMPULSORY_STRIKEOFF  (registrar s1000 for non-filing;
                                     salvageable, hottest)
      strike-off AND not overdue  -> VOLUNTARY_STRIKEOFF   (directors' own DS01; winding down)
      else (overdue, no strike)   -> OVERDUE_ACCOUNTS
    The exact truth (registrar s1000 first gazette vs a voluntary DS01) lives in the
    filing-history gazette codes, not the company profile — that is the named production
    upgrade. See README.
    """
    overdue = live["accounts_overdue"] or live["confstmt_overdue"]
    if live.get("strike_off") and overdue:
        return "COMPULSORY_STRIKEOFF"
    if live.get("strike_off"):
        return "VOLUNTARY_STRIKEOFF"
    return "OVERDUE_ACCOUNTS"


def profile_url(number: str) -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{number}"


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def build_lead(cand: dict, live: dict, director: dict | None, today: dt.date) -> dict:
    # Prefer live overdue dates; fall back to the snapshot's day count.
    live_dates = [d for d, o in (
        (live["accounts_next_due"], live["accounts_overdue"]),
        (live["confstmt_next_due"], live["confstmt_overdue"]),
    ) if o and d]
    days_overdue = (today - min(live_dates)).days if live_dates else cand["days_overdue_snap"]

    sic_text = "; ".join(cand["sic"])
    dormant = any(s.strip().startswith("99999") for s in cand["sic"]) \
        or cand["category"].strip().lower() == "dormant"

    return {
        "company_number": cand["company_number"],
        "name": cand["name"],
        "signal": classify_signal(live),
        "days_overdue": days_overdue,
        "accounts_overdue": bool(live["accounts_overdue"]),
        "confstmt_overdue": bool(live["confstmt_overdue"]),
        "never_filed": cand["never_filed"],
        "dormant": dormant,
        "director": (director or {}).get("name") or "",
        "director_service_address": (director or {}).get("address") or "",
        "registered_address": one_line_address(cand),
        "post_town": cand["post_town"],
        "postcode": cand["postcode"],
        "sic": sic_text,
        "company_type": cand["category"],
        "live_status": live["status"],
        "live_status_detail": live["status_detail"],
        "ch_profile_url": profile_url(cand["company_number"]),
    }


# We STRATIFY verification across the three signals instead of taking pure top-urgency.
# Strike-off scores +1000, so a naive urgency sort returns ONLY compulsory strike-offs and
# the agent demo loses its variety (a "solid lead" and, crucially, the abstain case). This
# mirrors the micro-SaaS, which itself works strike-off / accounts / conf-stmt as separate
# lists. (bucket, keep_target, check_cap) — check_cap bounds API calls per bucket.
BUCKET_PLAN = [
    ("compulsory", 12, 25),   # strike-off AND overdue — the hottest leads
    ("overdue", 8, 20),       # overdue, no strike-off — solid leads
    ("voluntary", 6, 40),     # strike-off, NOT overdue — weak; the abstain case (rarer, so higher cap)
]

SIGNAL_ORDER = {"COMPULSORY_STRIKEOFF": 0, "OVERDUE_ACCOUNTS": 1, "VOLUNTARY_STRIKEOFF": 2}


def snapshot_bucket(cand: dict) -> str:
    """Predict the signal from the snapshot flags, to steer which candidates we verify."""
    overdue = cand["accounts_overdue_snap"] or cand["confstmt_overdue_snap"]
    if cand["strike_off_snap"] and overdue:
        return "compulsory"
    if cand["strike_off_snap"]:
        return "voluntary"
    return "overdue"


def _verify_one(session, cand, key, today, stats):
    """Verify + enrich a single candidate. Returns a lead dict, or None if dropped."""
    number = cand["company_number"]
    profile = _api_get(session, f"{CH_API_BASE}/company/{number}", key)
    live = interpret(profile)
    time.sleep(REQUEST_SPACING_S)
    if live["state"] != "confirmed":
        stats[live["state"]] += 1
        print(f"  drop [{live['state']:>9}] {number}  {cand['name'][:38]}", flush=True)
        return None
    officers = _api_get(session, f"{CH_API_BASE}/company/{number}/officers", key,
                        params={"items_per_page": 50})
    time.sleep(REQUEST_SPACING_S)
    director = first_active_director(officers)
    lead = build_lead(cand, live, director, today)
    stats["confirmed"] += 1
    print(f"  KEEP [{lead['signal']:>19}] {number}  {lead['name'][:34]:<34} "
          f"dir={lead['director'][:22]}", flush=True)
    return lead


def verify_and_enrich(candidates: list[dict], key: str, today: dt.date) -> dict:
    session = requests.Session()
    kept: list[dict] = []
    stats = {"checked": 0, "confirmed": 0, "resolved": 0, "closed": 0, "error": 0}

    buckets: dict[str, list[dict]] = {"compulsory": [], "overdue": [], "voluntary": []}
    for c in candidates:
        if c["company_number"]:
            buckets[snapshot_bucket(c)].append(c)
    print(f"  candidate buckets (snapshot): "
          f"compulsory={len(buckets['compulsory'])}, overdue={len(buckets['overdue'])}, "
          f"voluntary={len(buckets['voluntary'])}", flush=True)

    for bucket, keep_target, check_cap in BUCKET_PLAN:
        kept_here = checked_here = 0
        print(f"  -- bucket '{bucket}' (want {keep_target}, check <={check_cap}) --", flush=True)
        for cand in buckets[bucket]:  # already urgency-sorted from the filter step
            if kept_here >= keep_target or checked_here >= check_cap:
                break
            stats["checked"] += 1
            checked_here += 1
            try:
                lead = _verify_one(session, cand, key, today, stats)
            except CHAuthError:
                raise
            except CHRateLimited:
                print("  rate limited — pausing 60s…", flush=True)
                time.sleep(60)
                continue
            except Exception as exc:  # one bad company shouldn't kill the run
                stats["error"] += 1
                print(f"  error {cand['company_number']}: {type(exc).__name__}: {exc}", flush=True)
                continue
            if lead:
                kept.append(lead)
                kept_here += 1

    kept.sort(key=lambda l: (SIGNAL_ORDER.get(l["signal"], 9), -l["days_overdue"]))
    return {"leads": kept, "stats": stats}


# --------------------------------------------------------------------------------------
# Step 5 — curated agent_input.md
# --------------------------------------------------------------------------------------
def write_agent_input(leads: list[dict], out: Path) -> None:
    """~8 curated demo leads to paste into ollo, chosen for VARIETY so the agent's judgment
    is actually exercised: a few strong strike-offs, a couple of solid overdue leads, at
    least one VOLUNTARY_STRIKEOFF (the abstain case), and at least one lead with no director
    (a natural letter/abstain candidate)."""
    comp = [l for l in leads if l["signal"] == "COMPULSORY_STRIKEOFF"]
    over = [l for l in leads if l["signal"] == "OVERDUE_ACCOUNTS"]
    vol = [l for l in leads if l["signal"] == "VOLUNTARY_STRIKEOFF"]

    picked: list[dict] = comp[:4] + over[:2] + vol[:2]

    # Guarantee at least one no-director lead (letter fallback / abstain fodder) if one exists.
    picked_nums = {l["company_number"] for l in picked}
    if all(l["director"] for l in picked):
        no_dir = next((l for l in leads if not l["director"] and l["company_number"] not in picked_nums), None)
        if no_dir:
            picked.append(no_dir)

    # De-dupe, cap at 8, order by signal then urgency for display.
    seen: set[str] = set()
    uniq: list[dict] = []
    for l in picked:
        if l["company_number"] in seen:
            continue
        seen.add(l["company_number"])
        uniq.append(l)
    picked = sorted(uniq, key=lambda l: (SIGNAL_ORDER.get(l["signal"], 9), -l["days_overdue"]))[:8]

    abstain_note = ""
    if not vol:
        abstain_note = (
            "\n> **Note:** this run turned up no VOLUNTARY_STRIKEOFF. For the abstain beat, "
            "use a lead where the agent finds no evidence of a real trading business "
            "(a shell/holding company or a dead website).\n"
        )

    lines = [
        "# Agent input — curated demo leads",
        "",
        "Paste the table below into the ollo agent to start a run. These are real, "
        "live-verified Bedfordshire companies. **Website presence is discovered by the "
        "agent live** — it is deliberately not in this table.",
        "",
        "| # | Company | Company no. | Director | Signal | Days overdue | Registered address |",
        "|---|---------|-------------|----------|--------|--------------|--------------------|",
    ]
    for i, l in enumerate(picked, start=1):
        director = l["director"] or "—"
        lines.append(
            f"| {i} | {l['name']} | {l['company_number']} | {director} | {l['signal']} | "
            f"{l['days_overdue']} | {l['registered_address']} |"
        )
    lines.append(abstain_note)
    out.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def main() -> int:
    # Windows consoles default to cp1252; force UTF-8 so a stray character never kills a run.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    load_env(REPO_ROOT / ".env")
    key = os.environ.get("CH_API_KEY", "").strip()
    if not key:
        print("ERROR: CH_API_KEY not set. Add it to .env (see .env.example).", file=sys.stderr)
        return 2

    default_csv = REPO_ROOT.parent / "BasicCompanyDataAsOneFile-2026-07-01" / \
        "BasicCompanyDataAsOneFile-2026-07-01.csv"
    csv_path = Path(os.environ.get("CH_BULK_CSV", default_csv))
    if not csv_path.exists():
        print(f"ERROR: bulk CSV not found at {csv_path}\n"
              f"Set CH_BULK_CSV in .env to its location.", file=sys.stderr)
        return 2
    csv_path = resolve_csv(csv_path)

    today = dt.date.today()
    DATA_DIR.mkdir(exist_ok=True)

    print(f"[1/4] Filtering bulk CSV for {REGION['label']} (overdue / strike-off)…", flush=True)
    candidates = filter_bulk_csv(csv_path, today)
    if not candidates:
        print("No candidates found — check the CSV path / region.", file=sys.stderr)
        return 1

    plan = ", ".join(f"{b}:{k}" for b, k, _ in BUCKET_PLAN)
    print(f"\n[2-3/4] Verifying live + enriching, stratified by signal (targets {plan})…",
          flush=True)
    result = verify_and_enrich(candidates, key, today)
    leads, stats = result["leads"], result["stats"]

    print(f"\n[4/4] Writing outputs…", flush=True)
    (DATA_DIR / "qualified_leads.json").write_text(
        json.dumps(leads, indent=2, ensure_ascii=False), encoding="utf-8")
    write_agent_input(leads, DATA_DIR / "agent_input.md")

    # Summary + sample so the run can be eyeballed.
    by_signal: dict[str, int] = {}
    for l in leads:
        by_signal[l["signal"]] = by_signal.get(l["signal"], 0) + 1
    with_dir = sum(1 for l in leads if l["director"])
    print("\n" + "=" * 72)
    print(f"Candidates in region:      {len(candidates):,}")
    print(f"Checked live:              {stats['checked']}")
    print(f"  confirmed (kept):        {stats['confirmed']}")
    print(f"  resolved (already filed):{stats['resolved']}")
    print(f"  closed (dissolved/gone): {stats['closed']}")
    print(f"  errors:                  {stats['error']}")
    print(f"Kept leads with director:  {with_dir}/{len(leads)}")
    print(f"By signal:                 {by_signal}")
    print("=" * 72)
    print("\nSample (first 5):")
    for l in leads[:5]:
        print(f"  {l['signal']:>19} | {l['name'][:32]:<32} | {l['director'][:22]:<22} | "
              f"{l['days_overdue']}d | {l['post_town']}")
    print(f"\nWrote data/qualified_leads.json ({len(leads)} leads) and data/agent_input.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
