---
name: dev-estimate
description: Estimate how many billable hours a piece of development work will take, using a fresh, unanchored sub-agent and calibration factors, then quote a range, log it, and give the client a heads-up before tracked time passes the quote. Use when a client or teammate asks how long development work will take, what it will cost, or for a ballpark or quote on a ticket, to-do, feature or PR; when a request may be over the client's "just do it" threshold; or when tracked time on quoted work is nearing the number given. Not for multi-month budgets, fixed-price proposals, or the runtime of a build, deploy or test suite.
---

# Dev estimate

A model asked to estimate a task from a busy session anchors on whatever numbers are in that session. A fresh agent that sees only the ask and the code does better, and its raw number still needs converting, because models consistently estimate several times the hours an agent-assisted developer actually spends.

Small items under the client's own "just do it" threshold need no estimate unless someone asks for one. If you don't know the client's threshold, ask the developer once.

## 1. Get the raw estimate from a fresh agent

Spawn a new background sub-agent with the Agent tool: a fresh agent, never a fork or anything else that inherits this conversation. Run it on `claude-opus-5-5` with reasoning effort left unset, the setup the factors in section 2 were measured under; on any other model or effort the factors don't apply until you refit them (section 5). One estimator per item (per part, when you scope first). The session can carry on with other things meanwhile, but not with the work being estimated.

Give it only:
- the ask, in the requester's own words (paste the message, ticket or to-do)
- where the code is (repo paths, PRs, branches) and any scope facts you already know
- this instruction, verbatim: "Do your best to estimate how long this ticket takes to complete, in the developer's billable hours, end to end. Look at whatever you need."
- what to return: `likely` hours (required), `low`/`high` (optional), `steps` as `{label, hours}` that add up to `likely`, what it looked at (files, queries, past work), and two or three sentences on the cost drivers

Leave out every hour, cost or budget figure: prior estimates, hours mentioned in the conversation, budgets, rates, caps, and questions like "does it fit under N hours". Scope facts such as "12 pages" or "3 integrations" stay in. Whether the number fits a budget is decided afterwards, by you.

Ask for "cost drivers", not "reasoning". Don't ask the agent which model it is; record the sub-agent's model from the configuration it ran with.

**Scope first when the work is plainly 5h or more of real effort, or has several separate parts.** Before estimating, write down the parts and, for each: known or unknown and why, what done means and who decides, what is blocked on someone else, and what could slip to a later phase. Scope facts only, no hours. Then estimate each part with its own fresh agent, convert each part at its own tier (section 2), and sum. A single whole-build estimate under-predicts big work badly.

## 2. Convert the raw number

Multiply the agent's `likely` by the factor for its size:

| Raw `likely` | Planning (average) | Quoting (conservative, ~80% come in under) |
|---|---|---|
| Under 4h | ×0.25 | ×0.4 |
| 4h and up | ×0.12, but never below 1h | ×0.2, but never below 1.6h |

- Work where the approach itself is unknown (research, ops, debugging an unknown cause): ×0.5 for both ends instead. The evidence for this factor is thin.
- Then add the client's overhead to both ends, for the calls, messages and coordination around the work: low 30% (needs little back-and-forth), medium 40%, high 50% (needs a lot). A new client starts at medium. Leave it off incident work.
- Quote a range: low = the planning figure rounded down to the half hour, high = the quoting figure rounded up to the half hour ("1-2 hours, assuming no revisions").

Only the converted range goes to the client. The raw number and the steps stay with you.

These factors are a starting point, not your numbers. They were fitted on one developer's work with one model; see section 5.

## 3. Log it before work starts

The first time, ask the developer where the estimate log lives; keep it outside client repos. Append one row per estimate: date, item, model, raw `likely`, quoted range, cost drivers, and an empty `actual` column. Fill in `actual` when the item closes. Never change the estimate columns; a re-estimate is a new row that says what changed in scope.

## 4. The quoted range is the commitment

The high end of the range you gave the client is what they agreed to. When the time the developer has tracked on the item nears that number and work remains, draft a short note for the developer to send the client: what is done, what is left, and a new number for the remainder. Work past the quote continues once the client says yes.

If the client already approved a revised number, that number is the new commitment.

## 5. Calibrate to yourself

Your factors depend on how you work and on the model. After 20-30 logged estimates with actuals filled in:
- compute actual ÷ raw `likely` per item
- take the median per size tier (under 4h, 4h and up) as your planning factor, and roughly the 80th percentile as your quoting factor
- refit whenever you switch models
- per client, compare the time spent around the work (calls, messages, coordination) with the time on the items themselves, and move the client to the overhead level that rate sits nearest once you have about four weeks of data

Any single item can land about 2.5× either way; the factors hold on average across many items. Most big misses come from scope the estimator never saw, so the scope step matters more than the multiplier.
