---
name: dev-estimate
description: Estimate how many billable hours a piece of development work will take, using a fresh, unanchored sub-agent and calibration factors, then quote a range, log it, and give the client a heads-up before tracked time passes the quote. Use when a client or teammate asks how long development work will take, what it will cost, or for a ballpark or quote on a ticket, to-do, feature or PR; when deciding whether a request is small enough to just do; or when tracked time on quoted work is nearing the number given. Not for multi-month budgets, fixed-price proposals, or the runtime of a build, deploy or test suite.
---

# Dev estimate

A model asked to estimate a task from a busy session anchors on whatever numbers are in that session. A fresh agent that sees only the ask and the code does better, and its raw number still needs converting, because models consistently estimate several times the hands-on hours an agent-assisted developer actually spends.

## Set it up for yourself

Every number in this skill is one developer's starting point, measured on their work with one model. Before relying on it, settle with the developer and note where the log lives (section 4):
- each client's "just do it" threshold (the size of item they don't want a quote for) and any monthly hours cap
- each client's overhead level (section 3)
- whether billed hours differ from hands-on hours, and by roughly what ratio
- the estimator model, if `claude-opus-5-5` is not available

Then replace the factors with your own once you have the data (section 6).

## 1. Get the raw estimate from a fresh agent

Spawn a new background sub-agent with the Agent tool: a fresh agent, never a fork or anything else that inherits this conversation. Run it on `claude-opus-5-5` with reasoning effort left unset, the setup the factors were measured under. If you can't launch that model, run it on the model you have, note the model in the log, and treat the factors as a rough guide until you refit them. One estimator per item. The session can carry on with other things meanwhile, but not with the work being estimated.

Give it only:
- the ask, in the requester's own words (paste the message, ticket or to-do)
- where the code is (repo paths, PRs, branches) and any scope facts already written down
- this instruction, verbatim: "Do your best to estimate how long this ticket takes to complete, in the developer's billable hours, end to end. Look at whatever you need."
- what to return: `likely` hours (required), `low`/`high` (optional), `steps` as `{label, hours}` that add up to `likely`, what it explored (files, queries, past work it actually looked at), and two or three sentences on the main cost drivers

Leave out every hour, cost or budget figure: prior estimates, hours mentioned in the conversation, budgets, rates, caps, and questions like "does it fit under N hours". Scope facts such as "12 pages" or "3 integrations" stay in. The same goes for any project instruction file the estimator will load (CLAUDE.md, AGENTS.md): keep these factors, rates and caps out of it. If a number leaked into the prompt, discard the result and re-run with a fresh agent rather than rewording.

Ask for "cost drivers", in those words, not "reasoning". If the estimator is stopped by an API safeguard error, retry once with a fresh agent; if it stops again, run the same prompt on `claude-sonnet-5-5`, use the same factors, and label the result as a Sonnet estimate. Don't ask any agent which model it is; record the model you launched.

## 2. Scope first for bigger work

**When the work is plainly 5h or more, or has several separate parts,** write down the parts before estimating and, for each: known or unknown and why, what done means and who decides, what is blocked on someone else, and what could slip to a later phase. Scope facts only, no hours. Skip this for small items.

**When a raw `likely` comes back above about 15-20h, or the work has several parts,** never convert one whole-build number. Split the work into parts, run one estimator per part, convert each part (section 3) and sum. Before those estimators run, ask the developer one round of at most 4 questions for the whole job (or the whole batch), spent on what would move the total most. Useful topics: is any part a proof of concept or a technique they haven't used; does done mean hitting an accuracy or quality bar; what depends on real client data or someone else's system or person; what will the client likely ask for once they see it; what is out. Decide everything else yourself and list it as assumptions the developer can correct. Write the answers into the scope facts the estimators read.

## 3. Convert the raw number

Multiply the estimator's `likely` by the factor for its size:

| Raw `likely` | Planning (average) | Quoting (conservative, ~80% come in under) |
|---|---|---|
| Under 4h | ×0.25 | ×0.4 |
| 4h and up | ×0.12 | ×0.2 |

Unknown parts, and ops, infra, audits and research with unknowns (key rotations, incidents, security reviews, proofs of concept, accuracy targets, work that depends on real client data): plan at ×0.5, any size. There isn't enough data for a quoting factor, so show the developer the ×0.5 figure and let them set the high end; the quote names the unknown as a condition ("revisited after the first accuracy report"). Known parts of the same job keep the normal tiers.

These predict hands-on time. Two things go on top, and they stack:
- **Overhead**, on a phase or project quote only: the triage, estimating, calls and client communication around the work. Leave it off a single small item, which doesn't cause the calls. Levels by how much back-and-forth the client needs: low about 30% of hands-on, medium about 40%, high about 50%. A new client starts at medium. Agency-side commercial work (proposals, quotes) and incidents are not overhead. The level is a starting point, not a formula: show the developer the hands-on range, the range with the level on top, and what you expect to drive overhead on this job, and they set the number.
- **Billed over hands-on**, if the developer bills differently from the time they spend hands-on: multiply by their ratio.

Example: 10h of hands-on build for a high-overhead client = 15h worked.

## 4. Show, quote, log

Show the developer the raw number, the planning figure and the quoting figure before anything is quoted. The developer sets the quote.

A client quote is a range: the planning figure is the low end, the quoting figure the high end, rounded to whole hours (1.0 and 1.7 become "1-2 hours"), and hedged ("assuming no revisions"). Never quote a single figure. Only the range goes to the client; the raw number and the steps stay with the developer. Short quotes are the least reliable: under 4h, the quoting figure held about two times in three.

Log every estimate before work starts, in a log kept outside client repos: date, item, model, raw `likely`, quoted range, cost drivers, scope change (none / grew / shrank, filled in at the end), and `actual`. Never change the estimate columns; a re-estimate is a new row that says what changed in scope.

`actual` is hands-on time on the item only. Calls, client messages and estimating time are tracked separately: they are what the overhead level measures, and counting them in `actual` would add overhead twice.

## 5. Small items and the commitment

If the client has a "just do it" threshold, judge it on the unrounded quoting figure. At or under the threshold, do the work. Over it, send the range and start on the client's yes. If the client has a monthly cap, check the month's billed hours plus the quoting figure against it first, however small the item. Changes that are plainly a few minutes need no estimator.

Trip wire on a just-do item: if hands-on time passes the threshold, stop and draft a range for the developer to send.

On quoted work, the high end of the range is what the client agreed to. When the developer's tracked time on the item nears it and work remains, draft a short note for the developer to send the client: what is done, what is left, and a new number for the remainder. Work past the quote continues once the client says yes. A revised number the client approved becomes the new commitment.

## 6. Calibrate to yourself

Your factors depend on how you work and on the model. After 20-30 logged estimates with actuals filled in:
- compute actual ÷ raw `likely` per item; count the parts of one split job as one data point
- take the median per size tier (under 4h, 4h and up) as your planning factor, and roughly the 80th percentile as your quoting factor; keep two tiers until you have about 60 items
- refit whenever you switch estimator models
- per client, compare the overhead time (calls, messages, estimating) with hands-on time, and move the client to the overhead level that rate sits nearest once you have about four weeks of data

Any single item can land about 2.5× either way; the factors hold on average across many items, so judge them on totals, not on one miss. Most big misses come from scope the estimator never saw, so the scope step matters more than the multiplier.
