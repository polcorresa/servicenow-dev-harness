---
description: Re-evaluate a change proposal against reviewer feedback and update it
argument-hint: <change number, e.g. CHG0030045>
---
Revise the proposal on change $ARGUMENTS.

1. Read it with `get_change`. The feedback is every journal entry with `from_agent: false` posted after the
   agent's latest `[Revision N]` note. The current field values are authoritative: the reviewer may have edited
   the proposal fields directly, so treat any edits as intended.
2. If there is no new feedback and no field edits, tell me and stop.
3. Re-evaluate. Re-check DEV configuration where the feedback calls the analysis into question. If you disagree
   with a point, say so with evidence instead of silently complying.
4. Update with `revise_change_proposal`. The `revision_summary` must answer each feedback point in order.
   If something is ambiguous, ask about it in the summary rather than guessing.
5. Reply with what changed and anything still waiting on me.
