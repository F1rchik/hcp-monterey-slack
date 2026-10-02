# HCP → Slack, FUSE Monterey

Three Slack notifications from the Housecall Pro account **FUSE HVAC & APPLIANCE
REPAIR MONTEREY INC** (requested by Assiya, 2026-09-24):

| What | Channel | When |
|---|---|---|
| Every approved estimate | `#fuse-monterey-parts` | within ~1 hour of approval |
| Every new lead, labelled by source (Angi / Thumbtack / Yelp / GLS / eLocal / HCP) | `#fuse_monterey_leads` | within ~1 hour |
| Yesterday's jobs, one message per technician: Estimate / Photos / Paid | `#chat-monterey` | daily ~07:15 PT |

Runs on GitHub Actions, like `Operators` and `asbestos`. There is no server and no database.

```
HCP public API (read-only) ──► src/main.py live   ──► #fuse-monterey-parts, #fuse_monterey_leads
                           └─► src/main.py daily  ──► #chat-monterey
                                        ▲
                         GitHub Actions cron (live hourly, daily at 07:15 PT)
```

**No repeat posts.** Every bot message carries Slack message metadata with the HCP
id: the estimate option id, the lead id, or the date plus employee id. Before
posting, each run reads the channel history back and skips anything already there.
So a late or overlapping GitHub run can't post twice, and nothing else has to be
stored.

## Rules

**Approved estimate.** An estimate *option* whose `approval_status` contains
"approv". That covers both the customer approving through the link and the tech
marking it "pro approved". The approval time is the option's
`approval_status_updated_at`. Each run looks back `live.lookback_hours` (6h). One
message goes out per approved option, with an **Estimate link** button.
Description: the estimate's job type, otherwise the option name if it isn't the
default "Option #1", otherwise the first labor line item. Monterey leaves job type
empty, so in practice it's the labor line (e.g. "Washer repair").

**New lead.** Items from `GET /leads`. The source comes from HCP's `lead_source`
through the `lead_sources` table in `config.yaml`; anything unmatched is posted as
"New Lead from HCP". Service is the lead's job type, otherwise its first line item.
Phone is shown only when there is one.

**Daily report.** Job segments whose scheduled start falls on yesterday (Pacific),
with canceled jobs dropped. A job with two techs appears under both. Jobs with
nobody assigned go into a final "Unassigned" message. For each job:

Checked against Natalie's manual reports for Sep 21–23: 19 of 26 jobs identical.
The rest were mostly updated in HCP after she wrote the report.

| First line | When |
|---|---|
| `Repair done: ✅ / ❌` | the job has repair or install work on it (materials, or labor that isn't the diagnostic / estimate / service-call fee). ✅ once the job is completed; a return visit not finished yet is ❌ |
| `Estimate: ✅ Approved / Declined` | otherwise, an estimate for this customer was written on the visit day or later and the customer answered |
| `Estimate: ✅` | same, no answer yet |
| `Diagnostic report: ✅ / ❌` | otherwise, a diagnostic-only visit; ✅ once completed |
| `Estimate: ❌` | none of the above: nothing was sent to the customer |

| Line | ✅ when |
|---|---|
| `Photos` | the job has at least one image attachment |
| `Paid` | outstanding balance is 0, including $0 free-estimate visits. `✅ check` when a payment is recorded as a check |

"Written on this visit": the API has no link from a job to an estimate created
*from* it. So the match is the same customer plus an estimate created on the visit
day or later, excluding the estimate the job was copied from (`original_estimate_id`).

## Setup

### 1. Housecall Pro API key
HCP → **App Store → API** → generate a key. This needs the **MAX plan**. Someone with
admin access to the Monterey HCP account has to do this.

### 2. Slack app
Reuse the existing bot or create one (api.slack.com/apps). **Bot Token Scopes:**

| Scope | Why |
|---|---|
| `chat:write` | post |
| `groups:history` | read back its own posts in the private channels (deduplication) |
| `channels:history` | same, in case a channel becomes public |
| `users:read` | `scripts/suggest_slack_ids.py` |

Install the app, then run `/invite @<bot>` in all three channels. They're private,
so until the bot is a member Slack answers `channel_not_found`.

### 3. `config.yaml`
- `channels:` the three channel IDs (channel name → About → bottom).
- `technicians:` HCP employee → Slack member ID, for the `@Raymond` mentions.
  Generate a draft with `scripts/suggest_slack_ids.py` and check it. Anyone
  missing still gets their report, with their name in bold.
- `links:` check that the estimate/job URL templates open the right page in HCP.

### 4. GitHub
Private repo. **Secrets:** `HCP_API_KEY`, `SLACK_BOT_TOKEN`. **Variable:**
`HCP_ENABLED=true` turns the schedules on. Until it's set, only manual runs work.

## Local run and rollout

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env          # fill in the keys, keep DRY_RUN=1
.\.venv\Scripts\python.exe -m pytest -q

.\.venv\Scripts\python.exe scripts\inspect_hcp.py         # real payloads → data/
.\.venv\Scripts\python.exe scripts\suggest_slack_ids.py   # technicians: block
.\.venv\Scripts\python.exe src\main.py live               # print what would post
.\.venv\Scripts\python.exe src\main.py daily --date 2026-09-17
```

1. **`inspect_hcp.py` first.** The rules were written from the API spec, not from
   live data. Check the summary it prints: the real `approval_status` strings,
   whether leads have `created_at`, what `lead_source` looks like for
   Yelp/Thumbtack/Google/eLocal leads, and that jobs come back with photo
   attachments.
2. **Dry run against a known day.** Run `daily --date <day>` and compare it with
   what the manager sees in HCP for that day.
3. **Test channel.** Set `SLACK_TEST_CHANNEL=<id>` and `DRY_RUN=0`, and everything
   posts there. Note that deduplication then remembers the *test* channel: moving
   to the real channels re-posts whatever is still inside the lookback window,
   which is expected and harmless.
4. **Turn it on.** Set `HCP_ENABLED=true`.

## Things to know

- **Leads without `created_at`.** The spec's Lead object doesn't list it. If the
  live API doesn't return it either, the code falls back to lead numbers: it posts
  anything numbered above the last lead it posted. On the very first run it posts
  only the newest lead as a baseline.
- **Old estimates.** Estimates are scanned by creation date,
  `live.estimate_max_age_days` (60) back. An approval of an older estimate is missed.
- **Actions budget.** Hourly live runs ≈ 500 billed minutes/month on a private repo.
- **Why polling, not HCP webhooks.** HCP (MAX plan) can push events to one URL, and
  that would make posts instant. It would also need a public endpoint running 24/7
  and a second codebase, and events are lost if that endpoint is down. The daily
  report needs a schedule anyway. With 1 hour of delay acceptable, polling is the
  simpler system. It also recovers by itself: a missed run is caught up by the next.
- **API key scope.** The key belongs to the Fuse *parent* account; `hcp_company_id`
  in `config.yaml` selects Monterey (sent as `X-Company-Id`).
- Message formats live in `src/messages.py`, rules in `src/rules.py`.
