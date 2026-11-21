# dev-estimate: why each rule exists

For editors of this skill, not for the agent following it. Source: the agency's ticket tracker (the `record_estimates` tool description, the weekly calibration pass and the estimating section of the owner's agent guidance), rewritten 2026-11-21 directly from those sources so a developer without the tracker can use it. A drift check on the tracker side compares the instruction sentence, the estimator model, the factor table and the overhead levels with this skill.

- **Fresh agent, never a fork; no hour/cost/budget figures in the prompt or in project instruction files.** An anchored estimator came back at 10.5h where a fresh one said 17h on the same ticket. The tracker redacts hours and money from what the estimator reads; outside it, leaving them out of the prompt and out of CLAUDE.md (which estimators load) is the equivalent. A leaked number means re-run fresh, because rewording keeps the anchor.
- **The verbatim instruction sentence.** It is the prompt the factors were fitted on. Changing it changes what the factors mean.
- **`claude-opus-5-5`, effort unset, one estimator per item.** "Factors belong to one model"; no other models are added and no newer Opus without recalibrating. A developer who can't launch it runs what they have and names it; those rows are kept out of the fit. Sonnet is only the fallback after a safeguard stop.
- **"Cost drivers", not "reasoning"; never ask a model its name.** The provider's reasoning-extraction safeguard stopped estimator runs, likely triggered by the word. Models misreport their own name.
- **Scope at 5h+; split and sum above 15-20h raw; one question round of at most 4.** A whole-build row (58h raw, 7-12h converted) under-predicted a build that passed 16h unfinished; its part rows summed to 113.5h raw, 14-23h converted. Nearly every miss came from work the estimate never saw, hence the question round. One round only, so big estimates don't become interviews.
- **Factor table.** Fitted 2026-11-13 on 33 tickets from one developer across four clients. Actual = hands-on attention minutes. Conservative column held on 26 of 33 overall, but only 7 of 11 under 4h. Log-space r=0.54; single items about 2.5× either way, sums about 1.00×. A fitted sliding scale predicted no better than two tiers; revisit at about 60 items.
- **×0.5 for unknown work, developer sets the high end.** 5 tickets, median .49, held on 3 of 5: too few for a conservative factor.
- **Overhead on phase/project quotes only, 30/40/50% by client, new clients medium, show both ranges.** The owner's guidance: "leave it out on a single small ticket, which doesn't cause the calls... The level is a starting point, not a formula." A one-day version of this skill applied overhead to every quote; that contradicted the source and was reverted.
- **`actual` is hands-on only.** Calls, comms and estimating feed the overhead rate; counting them in `actual` would push the factors up and add overhead twice.
- **Range, whole hours, hedged, never a single figure; the developer sets the quote.** Owner's rule since 2026-10-30.
- **Just-do threshold judged on the unrounded quoting figure, plus a trip wire.** From the owner's lane rules for an hourly client with a monthly cap. The trip wire exists because a just-do item can grow silently.
- **The quoted high is the commitment; the agent drafts the heads-up, the developer sends it.** Written after a contractor estimated "about 2-3 hours" for three client items, logged 5:10 without a heads-up, and the agency absorbed an hour. The agent never messages the client itself.
- **Out clauses** (an approved revision becomes the new number; plainly-minutes changes need no estimator) keep the rules from turning every task into a quote request.

Rejected on purpose:
- 1h/1.6h floors on the 4h+ tier, suggested by a cold review to remove the step at 4h. Not in the source; the developer sets the final number from the figures shown.
- a two-ended ×0.5/×0.8 range for unknown work. No data behind a second number.

Do not add back:
- client names, client-specific thresholds, ticket prefixes or per-client billed ratios (repo is public)
- tracker-specific mechanics (locked rows, model/human kinds, provenance, stage, redaction flags, work segments)
