---
description: Implement an approved change inside its own update set
argument-hint: <change number, e.g. CHG0030045>
---
Implement approved change $ARGUMENTS.

1. Call `begin_implementation`. If it refuses because the change is not in Implement, tell me and stop.
2. Follow the returned `implementation_plan` step by step, skipping steps marked `[MANUAL]` or `[AFTER MANUAL]`
   (and any Flow Designer step, even if unmarked). For each step you do:
   - read any existing record you are about to modify (`dev_query`, `display_values=false`);
   - write it with `dev_write_config` (pass `sys_id` to update);
   - read it back and confirm the values took.
3. If a step cannot be done as written, or the plan proves wrong, call `cancel_implementation` with the reason
   and stop. Do not substitute a different solution.
4. Call `list_update_set_changes` and check that every write appears in the captured updates.
5. Call `finish_implementation` with a short summary and the test steps from the test plan. If the plan has
   `[MANUAL]` steps, the summary must also list them under "Manual steps for the reviewer", starting with:
   "Select update set <name> as your current update set before building, then run /verify <CHG>."
6. Reply with the update set link, what was captured, the manual steps I need to do (if any), and the tests to
   run. Do not complete the update set.
