# ServiceNow developer harness

Claude Code + an MCP server that takes a PROD ticket to an implemented update set, with you approving in between.

```
PROD ticket ──/propose──▶ Change (New) in DEV ◀──┐
                              │ you: work notes / edit fields
                              ├──/revise────────────┘
                              │ you: approve, move to Implement
                              └──/implement──▶ Update set "CHG… - …" ──▶ Change in Review ◀──┐
                                                                            │ you: describe the bug │
                                                                            ├──/fix──────────────────┘
                                                                            │ you: mark update set Complete
                                                                            ▼ (locked)
```

The gate is enforced in code: config writes fail unless the change is in **Implement**, and they go
only to an allowlist of config tables. PROD is a read-only connection.

## Setup (PDI)

1. **Create an integration user** in your PDI (*User Administration → Users → New*):
   User ID `claude.agent`, set a password, tick *Web service access only*, and give it the `admin` role
   (needed to write config and update sets; acceptable on a PDI only).
   Use a dedicated user: the harness switches *this user's* current update set.
2. **Configure credentials**: copy `.env.example` to `.env` and fill in `SN_INSTANCE`, `SN_USER`, `SN_PASSWORD`.
   Leave the `PROD_*` values empty to let the PDI play PROD too.
3. **Open this folder in Claude Code** and approve the `servicenow` MCP server when prompted.
   Requires [`uv`](https://docs.astral.sh/uv/).

## Use

1. Create an incident in the PDI that describes a config problem, e.g.
   *"When the caller is a VIP, the incident should automatically get priority 1 – Critical, but it doesn't."*
2. `/propose INC00100xx`: the agent investigates and creates a change with the full proposal.
3. Review the change. Leave feedback as a work note or edit the plan fields, then `/revise CHG00300xx`.
   Repeat as needed.
4. Approve: *Request Approval*, approve the generated approval(s) (as admin from the *Approvers* related list),
   then click *Implement* once it's Scheduled. Exact buttons vary slightly by release.
5. `/implement CHG00300xx`: the agent builds the plan inside update set `CHG00300xx - …`, verifies capture,
   posts a summary to the change and moves it to Review.
   If the plan has `[MANUAL]` steps (flows), build them in Flow Designer with update set `CHG00300xx - …`
   selected as your current update set, then run `/verify CHG00300xx`. The agent checks them and finishes any
   steps that depend on them, such as linking the flow to a catalog item.
6. Test. If something is wrong, describe it in a work note on the change and run `/fix CHG00300xx`: the agent
   corrects it in the same update set. Repeat until it works.
7. Mark the update set Complete yourself. That locks the change: no more agent writes.

## Files

- `servicenow_mcp/server.py`: the MCP server (tools + gate)
- `CLAUDE.md`: the rules and ServiceNow conventions the agent follows
- `.claude/commands/`: `/propose`, `/revise`, `/implement`, `/verify`, `/fix`
- `.harness_state.json`: the active implementation (created at runtime; delete it only if you reset by hand)
