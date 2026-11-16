# 05 · Summaries

For the top communities, 20 by default, and the top alerts, 50 by default, a language model writes why they
could be risky. It reads one checked evidence object and nothing else.

```mermaid
flowchart LR
  ev["Evidence object<br/>JSON, checked against a schema, hashed"]
  cache{"Seen this hash<br/>with this model?"}
  api["Anthropic API<br/>ANTHROPIC_API_KEY set"]
  agent["A connected agent<br/>describe_alert"]
  tmpl["Template<br/>built from the same evidence"]
  check{"Checks<br/>numbers · members · verdict words"}
  store["Stored with the hash, the model and the source<br/>indexed for search"]
  ev --> cache
  cache -->|yes| store
  cache -->|"no, key set"| api --> check
  cache -->|"no, agent connected"| agent --> check
  cache -->|"no, neither"| tmpl --> store
  check -->|pass| store
  check -->|fail| tmpl
```

## The evidence object

One per community and one per alert. Checked against a JSON schema and hashed with SHA-256 before anything reads
it. It holds:

- the member count, the score and its parts (for a community, also how it formed: `formed_by_identifier`)
- the identifiers members share, and how many share each one (at most `LAB_EVIDENCE_MAX_IDENTIFIERS`)
- money in, money out, time-respecting cycles found (each payment after the one before it; `signals.payment_cycles`, the
  same definition the community score uses), and the time span
- the risk factors that fired, with counts and evidence ids (at most `LAB_EVIDENCE_MAX_IDS` ids each)
- the top members by score, by key only (`LAB_EVIDENCE_TOP_MEMBERS` of them)
- how the community compares to the rest of the slice

**Evidence carries keys and totals. Never names.**

## The summary

120 words or less, in three parts: what the evidence shows, why that pattern could mean risk, and the innocent
explanation to rule out, such as a family sharing a laptop or a payroll account paying many people.

- **It describes patterns, not people.**
- **Every number in the summary must appear in the evidence.**
- **Every account it names must be a member.**
- **Verdict words fail the check. "Fraudster" and "guilty" are out. "Could indicate" is in.**
- **Any failed check means the community gets the template, built from the same evidence.**

## Who writes it

| If | Then |
|---|---|
| `ANTHROPIC_API_KEY` is set | The lab calls the Anthropic API directly. Claude Sonnet 5.5 by default; `LAB_MODEL` in `.env` changes it |
| An agent is connected | The agent writes through the lab's `describe_alert` tool. The same checks apply |
| Neither | Every community and alert gets the template |

- **Evidence leaves the container only when the key is set.**
- Each summary is stored with its evidence hash, the model and the source: API, agent or template.
- The cache key is the evidence hash plus the model, where the hash leaves out the run id and the object's key. The same
  evidence in a new run makes no new call.
- About a thousand tokens of evidence in and 200 at most out, per summary. Cost per community waits for an
  Anthropic key; [98](98-measured.md) says so.

`score` stages the alert and community rows once, after their summaries are written; `summarise` stages nothing.
The template reads a group of `LAB_SUMMARY_SMALL_GROUP` members or fewer as a household, and a bigger one as a club or a
market.

## Where summaries go

Into Relate's full-text index, beside the account summaries from the first pass ([02](02-the-slice.md)). Each
community also gets an embedding, the average of its members'. "Communities like this one" on Results is one
hybrid search over both.

## Where it lives

`lab/summaries/`: the schema, the hasher, the three writers, the checks, the cache.
