---
description: Read a PROD ticket and create a solution proposal as a change in DEV
argument-hint: <PROD ticket number, e.g. INC0010023>
---
Propose a solution for PROD ticket $ARGUMENTS.

1. Read it with `prod_get_ticket`. Understand the reported behaviour, the affected table/records and any notes.
2. Investigate DEV (read-only): find the configuration involved (business rules, client scripts, UI policies,
   script includes, ACLs, dictionary, choices). Use `dev_describe_table` for real field names. Note the evidence.
3. Decide the root cause. If the ticket is not a configuration problem (user error, a one-off data fix, missing
   information), tell me and stop without creating a change.
4. Draft the proposal following CLAUDE.md, with an `implementation_plan` precise enough to implement mechanically.
5. Create it with `create_change_proposal`.
6. Reply with: the change number and link, a 3–5 line summary of the root cause and fix, and any open questions
   I should answer in the change. Do not implement anything.
