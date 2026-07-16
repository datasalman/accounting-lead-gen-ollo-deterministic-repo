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

## 3. Tools — connect exactly these three

Your web tool is **Firecrawl**, full stop. ollo's **Search** category (agentic search / Drive /
Notion / Calendar) searches *connected sandbox sources*, not the open web — useless for
discovering a company's website — so it's dropped. That leaves a tighter, cleaner set:

| Tool | Why it earns its place |
|---|---|
| **Firecrawl** | The one web tool: **search** for the company's official site (name + town), then **read** the homepage/contact/about to judge "real, trading business?" and pull an email. Discovery *and* the judgment input. |
| **Gmail** | Create the email **draft** (never send). The deterministic ending for the email path. |
| **Artifacts** | Render the ranked shortlist table, and produce the **letter** artifact for the no-email fallback. |

> ⚠️ **Check this in the builder — it makes or breaks the email demo beat.** Does ollo's
> Firecrawl expose a **search/map** action (find URLs from a query), or only **scrape** (read a
> URL you already have)?
> - **Search available** → the agent discovers sites itself; the flow works exactly as written.
> - **Scrape-only** → the agent can't *find* unknown sites, so every lead falls to
>   LETTER/ABSTAIN and the email beat dies. Fix: move discovery into `build_leads.py` (it can
>   call Firecrawl's *search API* in code to pre-attach a candidate URL per lead), leaving the
>   agent to scrape + judge. Arguably more on-brand: deterministic discovery, agent judgment.

### Connectors need *your* accounts/keys (except Google)
- **Firecrawl** — needs **your own Firecrawl API key** (free tier at firecrawl.dev; paste it
  into ollo's Firecrawl connector). This is the only external key to get.
- **Gmail** — Google APIs are covered by ollo; you just **OAuth your Google account**.
- **Artifacts** — native to ollo; nothing to connect.

### Deliberately do NOT connect (and why — gratuitous tools are a design smell)

| Not connected | Why it would be wrong |
|---|---|
| **Search** (agentic / Drive / Notion / Calendar) | Sandbox-only — it searches connected sources, not the open web. The agent's web tool is Firecrawl; a search tool that can't reach the internet is dead weight here. |
| **Code Execution / Shell / Read-Write File** | The agent must have **no code and no Companies House API access** — that's the deterministic half's job, already done in this repo. Handing the agent code re-opens a settled question and lets it be wrong about something a script decided. This is the single most important "no". |
| **Data Analysis / SQL / Calculator** | There's nothing to compute — the leads arrive pre-scored. A SQL tool implies a database the agent shouldn't be touching. |
| **Google Drive / Notion / Google Calendar / Slack** | No document store, wiki, calendar or channel is in this workflow. Each would be an unused connector inviting scope creep. |
| **Retrieval** | No corpus to retrieve over — the "knowledge" is live websites, which Firecrawl handles. |

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
demos. Pre-load `agent_input.md`, have Gmail authorised, and know your three leads cold.

**Your three demo leads (from this run — confirm they still behave in a dry run):**

| Role in demo | Lead | Why it's the right one |
|---|---|---|
| **EMAIL** (happy path) | **1ST CHOICE ROOFERS LTD** (12471414, Luton, SIC *roofing*) | A real trade with a name that searches well — likely a findable site + email. |
| **LETTER** (fallback) | **2 TONE TRUCKING LTD** (11061680, Bedford, SIC *freight transport*) | A real haulier operating from a nursery/yard address — the kind of business that often has no website, forcing the letter branch. |
| **ABSTAIN** (refusal) | **121CREATIVE LIMITED** (15876207, Biggleswade, **VOLUNTARY_STRIKEOFF**, 0 days overdue) | The directors themselves applied to close it. Unless the web shows a thriving business, the agent should decline. |

> Backup abstain, if you want a second: **10502670 LTD** (10502670) — a number-named company
> with no director on file; the agent should find no evidence of a real trading business.
> Live web behaviour can drift — do the dry run in §6 and swap from `qualified_leads.json` if a
> lead no longer fits its role.



1. **(0:00) Frame it (20s).** "A script already filtered 5.6M companies to Bedfordshire firms
   in filing distress and verified them live. This agent does the part that needs judgment —
   read the web, decide, draft, and it never sends."
2. **(0:20) Happy path — EMAIL (45s).** Point at **1ST CHOICE ROOFERS LTD (12471414)**. Watch Firecrawl search →
   find the real site → read the contact page → pull a business email → mark channel EMAIL.
   This is the normal case.
3. **(1:05) The fallback — LETTER (45s).** Point at **2 TONE TRUCKING LTD (11061680)**. It searches, finds no
   usable website/email, and *chooses* to fall back to a physical letter to the registered
   office. **Say out loud:** "That branch — the agent picking letter over email on its own —
   is the thing a script can't do. It's the reason this is an agent."
4. **(1:50) The refusal — ABSTAIN (40s).** Point at **121CREATIVE LIMITED (15876207)**. It finds no evidence
   of a real trading business (or a voluntary-strike-off it can't contradict) and puts it under
   "Not contacting" with a reason — refusing to draft. **Say:** "A correct refusal is worth as
   much as a good draft. Most agents can't say no."
5. **(2:30) The gate + ending (30s).** It stops and asks which numbers to action. Reply with
   two (the email + the letter). It creates a **Gmail draft** (open it — show it's a draft,
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
- [ ] You've done one **full dry run today** — websites change; confirm your three demo leads
      still behave (one finds a site+email, one finds nothing, one is clearly not-a-business).
      If a lead has drifted, swap in another from `qualified_leads.json` and update the script.
- [ ] You can point to the exact line in AGENT_INSTRUCTIONS that produces each behaviour.
- [ ] You can answer "why an agent not a cron job" in one breath (unstructured input +
      variable path + the letter fallback).
- [ ] A tab open on `data/qualified_leads.json` in case they ask to see the raw output.
- [ ] Fallback if Firecrawl/search is flaky live: a screenshot or recording of a clean run.
