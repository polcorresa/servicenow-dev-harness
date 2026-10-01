---
description: Verify the manual steps (flows) of a change and finish its dependent steps
argument-hint: <change number, e.g. CHG0030045>
---
Verify the manual steps of change $ARGUMENTS.

1. Read it with `get_change`. List the steps the human had to build: those marked `[MANUAL]`, plus any
   Flow Designer step in older plans without markers.
2. For each flow/subflow, call `inspect_flow` with the name from the plan and `change_number`. Compare with the plan:
   - it exists, the type is right, the status is Published and active (unless the plan says otherwise);
   - the trigger type/table match;
   - the step outline matches the plan's sequence of actions and logic (types and order);
   - `in_change_update_set` is true.
   Step inputs (field values, conditions) aren't readable; list them as things for me to double-check, don't claim
   they're verified.
3. If anything is missing or wrong, post a work note with `add_change_work_note` titled "[Verify] Manual steps
   need attention", listing each problem and exactly what to change. If a flow isn't in the change's update set,
   tell me to select that update set, open the flow, make a trivial edit and save/publish again so it is captured.
   Then reply with the same list and stop.
4. If everything checks out and the plan has `[AFTER MANUAL]` steps: call `begin_implementation` (fix mode),
   do those steps with `dev_write_config`, read each record back, then `finish_implementation` with a summary.
   If there are none, post a work note "[Verify] Manual steps verified" with what you checked.
5. Reply with what was verified, what I still need to check by hand, and the tests to run.
