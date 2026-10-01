---
description: Correct a mistake in an implemented change that is in Review
argument-hint: <change number, e.g. CHG0030045>
---
Fix the implementation of change $ARGUMENTS.

1. Read it with `get_change`. The problem report is every journal entry with `from_agent: false` posted after the
   agent's latest "Implementation complete" or "[Fix]" note. If there is none, ask me what is wrong and stop.
2. Call `begin_implementation`. In Review it starts fix mode on the change's existing update set. If it refuses
   because the update set is Complete, tell me a new change is needed and stop.
3. Diagnose against `captured_so_far` and the live records (`dev_query`, `display_values=false`). Find the
   actual cause before changing anything.
   If the problem is inside a flow, you cannot edit it: describe the exact change for me to make in Flow Designer
   (in a work note and in your reply), then run `/verify` after I've made it.
4. The fix must stay within the approved plan's intent: correct mistakes, typos, wrong conditions, missing steps.
   If it needs a different approach than what was approved, call `cancel_implementation` explaining why, and
   tell me to send the change back for a revised proposal.
5. Apply the correction:
   - update records with `dev_write_config`;
   - remove records you created by mistake with `dev_delete_config`;
   - restore pre-existing records you changed wrongly by writing their previous values back.
   Read each record back afterwards.
6. Call `finish_implementation` with a summary: what was wrong, why, what you changed, and the test steps to re-run.
7. Reply with the same summary and the update set link.
