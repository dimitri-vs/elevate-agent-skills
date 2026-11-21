# dev-estimate: why each rule exists

For editors of this skill, not for the agent following it. Source method: the agency's ticket tracker (`record_estimates` tool and its weekly calibration pass), lifted out so a developer without the tracker can use it.

- **Fresh agent, never a fork; no numbers in the prompt.** An anchored estimator came back at 10.5h where a fresh one said 17h on the same ticket. The tracker redacts hours and money from the ticket text the estimator reads; outside it, leaving numbers out of the prompt is the equivalent.
- **The verbatim instruction sentence.** It is the prompt the factors were fitted on. Changing it changes what the factors mean.
- **"Cost drivers", not "reasoning".** Anthropic's reasoning-extraction safeguard stopped estimator runs, likely triggered by the word.
- **Don't ask the model its name.** Models misreport it.
- **Fixed estimator model (`claude-opus-5-5`, effort unset), one per item.** The tracker's method allows only that condition: "factors belong to one model", no other models added, no move to a newer Opus without a recalibration. Codex rows ran ~0.08-0.10x and were dropped as an estimator.
- **Scope first at 5h+ / multi-part, estimate parts and sum.** A whole-build row under-predicted a large build badly (7-12h estimated, passed 16h unfinished). Nearly every miss traced to scope the estimate never saw.
- **Factor table.** Fitted 2026-11-13 on 33 tickets from one developer across four clients, `claude-opus-5-5`, plain prompt, effort unset. Actual = raw attention minutes (billed runs 1.0-1.16× raw). Conservative column held on 26 of 33 overall, but only 7 of 11 under 4h, so short quotes are the weakest. Log-space r=0.54; single items ~2.5× either way, sums ~1.00×. ×0.5 for unknowns/ops rests on 5 tickets.
- **Range = planning to quoting.** Matches how quotes are given ("about 1-2 hours, assuming no revisions").
- **Log, never edit.** The tracker locks estimate rows; without that, calibration data gets rewritten after the fact.
- **The quoted high is the commitment; tell the client before passing it.** Written after a contractor estimated "about 2-3 hours" for three client to-do items, logged 5:10 without a heads-up, and the agency absorbed an hour. The rule is phrased as the client's agreement, not as a penalty.
- **Out clauses** (approved revision becomes the new number; small items under the client's threshold need no estimate) keep the overrun rule from turning every task into a quote request.

- **Added after a cold review (2026-11-21):** threshold exemption moved to the top (it was being reached only after spawning an estimator for trivial asks); "every hour, cost or budget figure" instead of "every number" (scope counts were being stripped); floors on the 4h+ row (raw 3.9h quoted higher than raw 4h); per-part conversion when scoping; explicit rounding direction; the agent drafts the overrun note for the developer rather than messaging the client itself; an `actual` column so calibration has data; log kept outside client repos; only the converted range reaches the client.
- **Rejected from that review:** a two-ended ×0.5/×0.8 range for unknown work. No data behind a second number; ×0.5 rests on 5 tickets already.

Do not add back:
- client names, client-specific thresholds or ticket prefixes (repo is public)
- tracker-specific mechanics (locked rows, model/human columns, redaction flags)
- a per-client "handholding" uplift; it was fitted on named clients
