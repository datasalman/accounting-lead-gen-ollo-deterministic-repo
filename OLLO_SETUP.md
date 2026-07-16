# ollo agent setup

Everything needed to build the agent in ollo and run the demo. The agent's system prompt is
in **[AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md)** — paste it verbatim (after replacing
`{{FIRM_NAME}}`).

> Platform caveat: ollo's builder UI, exact model list, and tool names are things I can't
> verify from outside. Where this says "should be called X", check the actual label in the
> builder and adjust. Flagged inline as ⚠️.

---

## Agent identity

- **Name:** `Filing-Distress Outreach Drafter` (or shorter: `Lead Drafter`).
- **About (one line):** "Qualifies Companies-House-flagged Bedfordshire companies by reading
  their websites, then drafts human-approved email or letter outreach — never sends."

## Create it

Browse Agents → **Create Agent** → **Create Internal Agent**. Then three things: model,
instructions, tools.

---

## 1. Model

**Pick the strongest general-reasoning model ollo offers** (Claude Sonnet/Opus- or GPT-4-class).
⚠️ exact names vary in the builder.

**Why (the defence):**
- The agent's *entire* job is judgment on unstructured input — "is this a real trading
  business?", "is this the same entity?", "should I abstain?". That is precisely where a
  weaker model fails: it hallucinates a business from a directory listing, or drafts to a
  company that's obviously dead. Judgment quality is the product.
- **Latency and cost barely matter here** and I can say why: the deterministic half already
  cut 5.6M companies to ~25, and a demo runs ~8. At that volume the difference between a
  fast-cheap model and a strong one is a few pennies and a few seconds — nowhere near worth
  trading away judgment.
- **Counter I'd raise myself:** at 10k leads/month cost flips and you'd want a cheaper model
  for the bulk search/scrape and a strong one only for the abstain decision. ollo gives one
  model per agent, so you can't tier within an agent — you'd split into two agents (a cheap
  "enrich" agent feeding a strong "decide/draft" agent). For the demo, one strong model is
  the right call; I'd note the two-agent split as the scale answer.

---

## 2. Instructions

Paste **AGENT_INSTRUCTIONS.md** verbatim into the instructions box (replace `{{FIRM_NAME}}`
with the firm's real name first). The structure is deliberate and you should be able to name
each part: **Role → what it receives → what the signals mean → confirm-real-business →
choose-channel → abstain → human gate → execute → draft rules → output format.** The rules
are written to *constrain* (reject aggregator sites, reject no-reply emails, never draft for
an abstained lead, always include a PECR opt-out) rather than to vibe.

---

## 3. Tools — the exact individual tools to enable

ollo exposes each connector's sub-tools individually. Enable **only** these ~6 toggles across
3 connectors:

**Firecrawl** (your only web tool — `firecrawl_search` confirms discovery works):
- ✅ `firecrawl_search` — find the company's official site (query name + town).
- ✅ `firecrawl_scrape` — read the homepage / contact / about page.
- ✅ `firecrawl_map` *(optional but handy)* — list a site's pages to locate its /contact.
- ❌ everything else: `firecrawl_agent`/`_agent_status` (a nested autonomous agent — never let
  your agent spawn another; you'd lose control of the judgment), `firecrawl_crawl`/
  `_check_crawl_status` (crawls whole sites — slow, costly, unnecessary), `firecrawl_interact`,
  `firecrawl_monitor_*` (change-monitoring, not this job), `firecrawl_research_*` (academic
  papers — irrelevant), `firecrawl_extract`/`_parse`/`_feedback` (redundant with scrape + the
  model doing the reading).

**Gmail:**
- ✅ the **create-draft / compose** action only — **do not enable send.** (Google API is
  ollo-covered; you just OAuth your account.)

**Artifacts** (for the letter fallback):
- ✅ `Save Artifact` — save the letter.
- ✅ `Convert HTML to PDF` — turn the letter into a print-ready PDF to post.
- ✅ `Download Artifact` *(optional)* — retrieve the letter PDF to actually send it.
- ❌ `Upload Artifact`, `Load Artifact`, `List Artifacts`, `Delete Artifact`, `Download Content`
  — artifact *management*, not needed to produce outputs.

> Firecrawl needs **your own API key** (free tier at firecrawl.dev) — already connected.

### Why whole categories stay off (the design-smell answer)

| Off | Why it would be wrong |
|---|---|
| **Search** (agentic / Drive / Notion / Calendar) | Sandbox-only — searches connected sources, not the open web. Firecrawl is the web tool; a search that can't reach the internet is dead weight here. |
| **Code Execution / Shell / Read-Write File** | The agent must have **no code and no Companies House API access** — that's the deterministic half's job, already done in this repo. Handing the agent code re-opens a settled question and lets it be wrong about something a script decided. The single most important "no". |
| **Data Analysis / SQL / Calculator** | Nothing to compute — leads arrive pre-scored. A SQL tool implies a database the agent shouldn't be touching. |
| **Retrieval** | No corpus to retrieve over — the "knowledge" is live websites, which Firecrawl handles. |
| **Google Drive / Notion / Google Calendar / Slack** | No document store, wiki, calendar or channel in this workflow. Each is an unused connector inviting scope creep. |

### Gmail authorisation ⚠️
Connecting Gmail will trigger a Google OAuth consent for the firm's account. Grant the
**compose/drafts** scope; you do **not** need send scope (and not granting it is a nice
belt-and-braces guarantee the agent can't send). ⚠️ Confirm ollo's exact scope prompt in the
builder. Do the auth **before** the demo, not during it.

---

## 4. Start a run

**Primary method — paste:** open **[data/agent_input.md](data/agent_input.md)**, copy the
table, paste it into the agent chat with: *"Here are today's qualified leads. Work them per
your instructions."*

**Optional fetch-from-URL variant:** since this repo is public, `data/agent_input.md` has a
raw URL. You can instead tell the agent: *"Fetch the leads from `<raw github URL of
data/agent_input.md>` and work them."* — it'll use Firecrawl to load the table. Handy if you
don't want a wall of pasted text on screen. (No code execution needed — Firecrawl is already
connected.) ⚠️ Confirm Firecrawl will fetch a raw githubusercontent URL in your ollo instance.

---

## 5. Three-minute demo script

Goal: show the **variable path** and a **well-handled refusal** — the two things almost nobody
demos. Pre-load `agent_input.md`, have Gmail authorised, and know these leads cold.

**The curated demo set (all 8 pre-verified live — the agent still discovers all of this itself; this table is only so *you* know what to expect):**

| # | Lead | Signal | Expected outcome (pre-checked) |
|---|---|---|---|
| 1 | **SHEDSWAREHOUSE LIMITED** (07829634, Bedford) | COMPULSORY | **EMAIL** — live e-commerce site shedswarehouse.com → email `help@ilikestores.com`. |
| 2 | **EATALIA BEDFORD LIMITED** (09788366, Bedford) | COMPULSORY | **EMAIL** — trading Italian restaurant, eataliabedford.co.uk, 72 High St → email `Eatalia1@mail.com`. |
| 3 | **2 TONE TRUCKING LTD** (11061680, Bedford) | COMPULSORY | **LETTER** — small haulier, no website → letter to the registered office. |
| 4 | **ABS AUTO & TYRES LIMITED** (10156797, Biggleswade) | OVERDUE | **LETTER** — real garage, directory listings + phone only, no email → letter. |
| 5 | **24-7 CARS BEDFORD LTD** (06645947, Bedford) | COMPULSORY | **LETTER (nuance)** — real taxi firm *with* a site (247carsbedford.co.uk) but phone-only, no email → still a letter. |
| 6 | **1ST CHOICE ROOFERS LTD** (12471414, Luton) | COMPULSORY | **ABSTAIN** — the web shows a *different* roofer ("First Choice Roofing & Building Solutions") → not the same entity. |
| 7 | **A WORLD OF OLD LIMITED** (06471175) | VOLUNTARY | **EMAIL via override** — voluntary strike-off, *but* aworldofold.co.uk shows a thriving antiques shop → agent may override the abstain and email `Info@aworldofold.co.uk`. |
| 8 | **A PIZZA THIS LTD** (13522210, Cranfield) | VOLUNTARY | **ABSTAIN** — voluntary strike-off, no web evidence of trading → winding down. |

> These outcomes were verified live on 16 Jul 2026. Web presence can drift — do the dry run in
> §6. **Do not tell the agent any of this** (no emails, no roles) — the whole point is that it
> discovers them. Swap from `qualified_leads.json` (24 leads) if any lead has moved.

1. **(0:00) Frame it (20s).** "A script already filtered 5.7M companies to Bedfordshire firms
   in filing distress and verified them live. This agent does the part that needs judgment —
   read the web, decide, draft, and it never sends."
2. **(0:20) Happy path — EMAIL (45s).** Point at **SHEDSWAREHOUSE (07829634)** (and, if time,
   **EATALIA (09788366)**). Watch Firecrawl search → find the real site → read the contact page
   → pull a business email → mark channel EMAIL. Two clean emails from a compulsory-strike-off
   e-commerce firm and a trading restaurant.
3. **(1:05) The fallback — LETTER (45s).** Point at **2 TONE TRUCKING (11061680)**. It searches,
   finds no usable website/email, and *chooses* to fall back to a physical letter to the
   registered office. **Say out loud:** "That branch — the agent picking letter over email on
   its own — is the thing a script can't do. It's the reason this is an agent." (Bonus: **24-7
   CARS (06645947)** *has* a website but no email → still a letter. Judgment, not keyword-matching.)
4. **(1:50) The refusal — ABSTAIN (40s).** Point at **1ST CHOICE ROOFERS (12471414)**. It finds
   a roofing company online — but a *different* one (different name, address, people) — and
   refuses to treat it as the lead: "not the same entity." **Say:** "A correct refusal is worth
   as much as a good draft. Most agents can't say no." (Bonus: **A PIZZA THIS** — voluntary,
   winding down, no web evidence → also abstain.)
5. **(2:20) The judgment flex — OVERRIDE (20s, optional).** Point at **A WORLD OF OLD
   (06471175)**. Signal says VOLUNTARY_STRIKEOFF (weak), but the agent finds a thriving antiques
   shop and *overrides* — treating it as a lead and drafting. "It doesn't blindly follow the
   signal; it reads the evidence and can argue with it."
6. **(2:40) The gate + ending (20s).** It stops and asks which numbers to action. Reply with a
   couple (an email + the letter). It creates a **Gmail draft** (open it — show it's a draft,
   unsent, with the PECR opt-out line) and the **letter artifact**. "Nothing was sent. A human
   is always between drafted and sent."

**Then, unprompted, say where you'd improve it** (they ask this): see README → *autonomy* and
*limitations*. Lead with the strike-off heuristic (gazette codes) and the false-positive
dependence on website-matching.

---

## 6. Rehearsal checklist

- [ ] `{{FIRM_NAME}}` replaced everywhere in the pasted instructions.
- [ ] Gmail authorised on the firm's account; **send scope withheld**, drafts scope granted.
- [ ] `agent_input.md` open in a tab, ready to paste (or raw URL ready).
- [ ] You've done one **full dry run today** — websites change; confirm the 8 demo leads still
      behave (2 find a site+email, 2–3 find no email → letter, 2 are abstains, 1 is an override).
      If a lead has drifted, swap in another from `qualified_leads.json` and update the table.
- [ ] You can point to the exact line in AGENT_INSTRUCTIONS that produces each behaviour.
- [ ] You can answer "why an agent not a cron job" in one breath (unstructured input +
      variable path + the letter fallback).
- [ ] A tab open on `data/qualified_leads.json` in case they ask to see the raw output.
- [ ] Fallback if Firecrawl/search is flaky live: a screenshot or recording of a clean run.
