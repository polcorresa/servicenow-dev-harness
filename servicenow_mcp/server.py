#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "httpx>=0.27", "python-dotenv>=1.0"]
# ///
"""ServiceNow MCP server for the workflow:

PROD ticket (read-only) -> change proposal in DEV -> human review/revise loop
-> human moves change to Implement -> agent implements inside a dedicated update set.

The approval gate is enforced here, not just in prompts: config writes are refused
unless an implementation was started for a change that is in the Implement state.
"""

import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
STATE_FILE = ROOT / ".harness_state.json"

CHANGE_STATES = {
    "-5": "New", "-4": "Assess", "-3": "Authorize", "-2": "Scheduled",
    "-1": "Implement", "0": "Review", "3": "Closed", "4": "Canceled",
}
REVISABLE_STATES = {"-5", "-4"}  # New, Assess: before approval
IMPLEMENT_STATE = "-1"
REVIEW_STATE = "0"

PROPOSAL_FIELDS = [
    "short_description", "description", "justification", "implementation_plan",
    "risk_impact_analysis", "backout_plan", "test_plan",
]

# Configuration tables the agent may write to during implementation (all update-set tracked).
DEFAULT_WRITE_TABLES = [
    "sys_script", "sys_script_include", "sys_script_client", "sys_ui_script",
    "sys_ui_action", "sys_ui_policy", "sys_ui_policy_action",
    "sys_data_policy2", "sys_data_policy_rule",
    "sys_db_object", "sys_dictionary", "sys_dictionary_override", "sys_documentation", "sys_choice",
    "sys_properties", "sysauto_script",
    "sys_security_acl", "sys_security_acl_role",
    "sysevent_register", "sysevent_script_action", "sysevent_email_action",
    "sys_ws_definition", "sys_ws_operation", "sc_cat_item", "item_option_new", "io_set_item"
]
WRITE_TABLES = set(
    t.strip() for t in os.environ.get("SN_WRITE_TABLES", ",".join(DEFAULT_WRITE_TABLES)).split(",") if t.strip()
)


class Instance:
    def __init__(self, url: str, user: str, password: str, read_only: bool):
        if not (url and user and password):
            raise RuntimeError("Missing ServiceNow credentials. Copy .env.example to .env and fill it in.")
        self.url = url.rstrip("/")
        self.user = user
        self.read_only = read_only
        self.client = httpx.Client(
            base_url=self.url,
            auth=(user, password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=60,
        )

    def request(self, method: str, path: str, **kw) -> Any:
        if self.read_only and method != "GET":
            raise PermissionError("This instance connection is read-only.")
        # No session reuse: each call must pick up the user's current update set preference fresh.
        self.client.cookies.clear()
        r = self.client.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", r.text)
            except ValueError:
                detail = r.text[:500]
            raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {detail}")
        return r.json().get("result") if r.content else None

    def query(self, table: str, query: str = "", fields: str = "", limit: int = 20,
              display: bool = True) -> list[dict]:
        params = {
            "sysparm_query": query,
            "sysparm_limit": max(1, min(limit, 500)),
            "sysparm_display_value": "true" if display else "false",
            "sysparm_exclude_reference_link": "true",
        }
        if fields:
            params["sysparm_fields"] = fields
        return self.request("GET", f"/api/now/table/{table}", params=params)

    def insert(self, table: str, data: dict) -> dict:
        return self.request("POST", f"/api/now/table/{table}",
                            params={"sysparm_display_value": "false"}, json=data)

    def update(self, table: str, sys_id: str, data: dict) -> dict:
        return self.request("PATCH", f"/api/now/table/{table}/{sys_id}",
                            params={"sysparm_display_value": "false"}, json=data)

    def delete(self, table: str, sys_id: str) -> None:
        self.request("DELETE", f"/api/now/table/{table}/{sys_id}")

    def link(self, table: str, sys_id: str) -> str:
        return f"{self.url}/{table}.do?sys_id={sys_id}"


_dev: Instance | None = None
_prod: Instance | None = None


def dev() -> Instance:
    global _dev
    if _dev is None:
        _dev = Instance(os.environ.get("SN_INSTANCE", ""), os.environ.get("SN_USER", ""),
                        os.environ.get("SN_PASSWORD", ""), read_only=False)
    return _dev


def prod() -> Instance:
    global _prod
    if _prod is None:
        _prod = Instance(os.environ.get("PROD_INSTANCE") or os.environ.get("SN_INSTANCE", ""),
                         os.environ.get("PROD_USER") or os.environ.get("SN_USER", ""),
                         os.environ.get("PROD_PASSWORD") or os.environ.get("SN_PASSWORD", ""),
                         read_only=True)
    return _prod


def _compact(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if v not in ("", None, "false", "0") or k in ("state", "active")}


def _journal(inst: Instance, sys_id: str) -> list[dict]:
    rows = inst.query("sys_journal_field", f"element_id={sys_id}^ORDERBYsys_created_on",
                      "element,value,sys_created_by,sys_created_on", limit=200)
    return [{"type": r["element"], "author": r["sys_created_by"], "at": r["sys_created_on"],
             "from_agent": r["sys_created_by"] == inst.user, "text": r["value"]} for r in rows]


def _get_change(number: str) -> dict:
    rows = dev().query("change_request", f"number={number}", limit=1, display=False)
    if not rows:
        raise ValueError(f"Change {number} not found in DEV.")
    return rows[0]


def _agent_user_id() -> str:
    rows = dev().query("sys_user", f"user_name={dev().user}", "sys_id", limit=1, display=False)
    if not rows:
        raise RuntimeError(f"Integration user {dev().user} not found in sys_user.")
    return rows[0]["sys_id"]


def _set_current_update_set(update_set_id: str | None) -> None:
    uid = _agent_user_id()
    prefs = dev().query("sys_user_preference", f"user={uid}^name=sys_update_set", "sys_id", limit=5, display=False)
    if update_set_id is None:
        for p in prefs:  # no preference -> falls back to Default
            dev().delete("sys_user_preference", p["sys_id"])
        return
    if prefs:
        dev().update("sys_user_preference", prefs[0]["sys_id"], {"value": update_set_id})
    else:
        dev().insert("sys_user_preference", {"user": uid, "name": "sys_update_set", "value": update_set_id})


def _current_update_set() -> str | None:
    rows = dev().query("sys_user_preference", f"user={_agent_user_id()}^name=sys_update_set", "value",
                       limit=1, display=False)
    return rows[0]["value"] if rows else None


def _load_state() -> dict | None:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None


def _save_state(state: dict | None) -> None:
    if state is None:
        STATE_FILE.unlink(missing_ok=True)
    else:
        STATE_FILE.write_text(json.dumps(state, indent=2))


def _require_active() -> dict:
    state = _load_state()
    if not state:
        raise PermissionError("No active implementation. Call begin_implementation for an approved change first.")
    return state


def _captured(update_set_id: str) -> list[dict]:
    return dev().query("sys_update_xml", f"update_set={update_set_id}^ORDERBYsys_created_on",
                       "name,type,target_name,action,sys_created_on", limit=500)


mcp = FastMCP("servicenow")


# ---------- PROD (read-only) ----------

@mcp.tool()
def prod_get_ticket(number: str) -> dict:
    """Read a ticket (incident, problem, request item, ...) from PROD by number, including its comments and work notes."""
    hits = prod().query("task", f"number={number}", "sys_id,sys_class_name", limit=1, display=False)
    if not hits:
        raise ValueError(f"Ticket {number} not found in PROD.")
    table, sys_id = hits[0]["sys_class_name"], hits[0]["sys_id"]
    rec = prod().query(table, f"sys_id={sys_id}", limit=1)[0]
    return {"table": table, "record": _compact(rec), "journal": _journal(prod(), sys_id),
            "link": prod().link(table, sys_id)}


@mcp.tool()
def prod_query(table: str, query: str = "", fields: str = "", limit: int = 20) -> list[dict]:
    """Read-only query against PROD using an encoded query (e.g. 'active=true^collection=incident'). fields is comma-separated."""
    return prod().query(table, query, fields, limit)


# ---------- DEV (read) ----------

@mcp.tool()
def dev_query(table: str, query: str = "", fields: str = "", limit: int = 20, display_values: bool = True) -> list[dict]:
    """Query any DEV table with an encoded query. Use this to inspect existing configuration before proposing or writing.
    Set display_values=false to get raw values (sys_ids for references, raw choice values)."""
    return dev().query(table, query, fields, limit, display=display_values)


@mcp.tool()
def dev_describe_table(table: str) -> dict:
    """List a DEV table's fields (including inherited ones) with type, label and reference target."""
    chain, current = [], table
    while current:
        rows = dev().query("sys_db_object", f"name={current}", "name,super_class", limit=1, display=False)
        if not rows:
            break
        chain.append(current)
        parent = rows[0]["super_class"]
        current = None
        if parent:
            p = dev().query("sys_db_object", f"sys_id={parent}", "name", limit=1, display=False)
            current = p[0]["name"] if p else None
    if not chain:
        raise ValueError(f"Table {table} not found.")
    fields = dev().query("sys_dictionary", f"nameIN{','.join(chain)}^elementISNOTEMPTY^ORDERBYelement",
                         "name,element,column_label,internal_type,reference,mandatory,max_length", limit=500)
    return {"table": table, "hierarchy": chain, "fields": fields}


# ---------- DEV change proposal ----------

@mcp.tool()
def inspect_flow(name: str, change_number: str | None = None) -> list[dict]:
    """Read a Flow Designer flow/subflow in DEV by name (or sys_id): status, trigger, ordered steps, catalog items
    using it, and which update sets captured it. Flows are built by the human; use this to verify them against
    the plan. With change_number, also reports whether the flow is in that change's update set."""
    flows = dev().query("sys_hub_flow", f"name={name}^ORsys_id={name}", limit=5)
    if not flows:
        like = dev().query("sys_hub_flow", f"nameLIKE{name}", "name,status", limit=10)
        raise ValueError(f"No flow named '{name}'. Similar: {[f['name'] for f in like] or 'none'}")
    change_set = None
    if change_number:
        sets = dev().query("sys_update_set", f"nameSTARTSWITH{change_number} - ", "sys_id", limit=1, display=False)
        change_set = sets[0]["sys_id"] if sets else None
    out = []
    for f in flows:
        fid = f["sys_id"]
        triggers = dev().query("sys_hub_trigger_instance_v2", f"flow={fid}",
                               "trigger_type,trigger_definition,table,condition", limit=5)
        steps = [{"order": a["order"], "kind": "action", "type": a["action_type"], "comment": a["comment"]}
                 for a in dev().query("sys_hub_action_instance_v2", f"flow={fid}", "order,action_type,comment", 100)]
        steps += [{"order": l["order"], "kind": "logic", "type": l["logic_definition"], "comment": l["comment"]}
                  for l in dev().query("sys_hub_flow_logic_instance_v2", f"flow={fid}", "order,logic_definition,comment", 100)]
        steps.sort(key=lambda s: int(s["order"] or 0))
        captured = dev().query("sys_update_xml", f"name={f['sys_update_name']}^ORDERBYDESCsys_updated_on",
                               "update_set,sys_updated_on", limit=10, display=False)
        set_names = {c["update_set"]: dev().query("sys_update_set", f"sys_id={c['update_set']}", "name,state", 1)
                     for c in captured}
        out.append({
            "name": f["name"], "sys_id": fid, "type": f["type"], "status": f["status"], "active": f["active"],
            "scope": f["sys_scope"], "run_as": f["run_as"], "triggers": triggers, "steps": steps,
            "catalog_items": dev().query("sc_cat_item", f"flow_designer_flow={fid}", "name,sys_id", 10),
            "captured_in": [s[0] for s in set_names.values() if s],
            **({"in_change_update_set": change_set in set_names} if change_number else {}),
            "link": f"{dev().url}/$flow-designer.do#/flow-designer/{fid}",
        })
    return out


@mcp.tool()
def create_change_proposal(prod_ticket: str, short_description: str, description: str, justification: str,
                           implementation_plan: str, risk_impact_analysis: str, backout_plan: str,
                           test_plan: str) -> dict:
    """Create a Normal change in DEV holding the solution proposal for a PROD ticket.
    implementation_plan must list every record to create/modify (table, name, exact field values/script)."""
    existing = dev().query("change_request", f"correlation_id={prod_ticket}^active=true", "number", limit=1)
    if existing:
        raise ValueError(f"{existing[0]['number']} is already open for {prod_ticket}. Revise it instead.")
    rec = dev().insert("change_request", {
        "type": "normal",
        "correlation_id": prod_ticket,
        "correlation_display": "Claude harness",
        "short_description": short_description,
        "description": description,
        "justification": justification,
        "implementation_plan": implementation_plan,
        "risk_impact_analysis": risk_impact_analysis,
        "backout_plan": backout_plan,
        "test_plan": test_plan,
        "work_notes": f"[Revision 1] Proposal created from PROD ticket {prod_ticket}.",
    })
    return {"number": rec["number"], "sys_id": rec["sys_id"], "link": dev().link("change_request", rec["sys_id"])}


@mcp.tool()
def get_change(number: str) -> dict:
    """Read a DEV change: state, proposal fields and the full comment/work-note history (from_agent marks the agent's own entries)."""
    raw = _get_change(number)
    revisions = [j for j in _journal(dev(), raw["sys_id"]) if j["from_agent"] and j["text"].startswith("[Revision")]
    return {
        "number": number,
        "sys_id": raw["sys_id"],
        "state": CHANGE_STATES.get(raw["state"], raw["state"]),
        "approval": raw.get("approval"),
        "prod_ticket": raw.get("correlation_id"),
        "proposal": {f: raw.get(f, "") for f in PROPOSAL_FIELDS},
        "revision": len(revisions),
        "journal": _journal(dev(), raw["sys_id"]),
        "link": dev().link("change_request", raw["sys_id"]),
    }


@mcp.tool()
def revise_change_proposal(number: str, revision_summary: str, short_description: str | None = None,
                           description: str | None = None, justification: str | None = None,
                           implementation_plan: str | None = None, risk_impact_analysis: str | None = None,
                           backout_plan: str | None = None, test_plan: str | None = None) -> dict:
    """Update the proposal on a change that has not been approved yet (New or Assess).
    revision_summary goes into a work note and should answer each piece of reviewer feedback."""
    raw = _get_change(number)
    if raw["state"] not in REVISABLE_STATES:
        raise PermissionError(f"{number} is in state {CHANGE_STATES.get(raw['state'])}; proposals can only be revised in New or Assess.")
    n = get_change(number)["revision"] + 1
    updates = {k: v for k, v in {
        "short_description": short_description, "description": description, "justification": justification,
        "implementation_plan": implementation_plan, "risk_impact_analysis": risk_impact_analysis,
        "backout_plan": backout_plan, "test_plan": test_plan,
    }.items() if v is not None}
    updates["work_notes"] = f"[Revision {n}] {revision_summary}"
    dev().update("change_request", raw["sys_id"], updates)
    return {"number": number, "revision": n, "updated_fields": [k for k in updates if k != "work_notes"]}


@mcp.tool()
def add_change_work_note(number: str, note: str) -> dict:
    """Add a work note to a DEV change (questions for the reviewer, progress, blockers)."""
    raw = _get_change(number)
    dev().update("change_request", raw["sys_id"], {"work_notes": note})
    return {"number": number, "ok": True}


# ---------- DEV implementation (gated) ----------

@mcp.tool()
def begin_implementation(number: str) -> dict:
    """Start implementing an approved change, or start a fix on one already implemented.
    - Change in Implement: creates (or reuses) an update set named after the change.
    - Change in Review: fix mode. Reuses the change's update set, only while it is still In progress;
      once the human marks it Complete the work is locked and corrections need a new change.
    Makes the update set the agent user's current update set."""
    active = _load_state()
    if active and active["change_number"] != number:
        raise PermissionError(f"Implementation of {active['change_number']} is still active. Finish or cancel it first.")
    raw = _get_change(number)
    if raw["state"] not in (IMPLEMENT_STATE, REVIEW_STATE):
        raise PermissionError(
            f"{number} is in state {CHANGE_STATES.get(raw['state'], raw['state'])}. "
            "It must be approved and moved to Implement in ServiceNow before the agent may implement it.")
    fix = raw["state"] == REVIEW_STATE
    sets = dev().query("sys_update_set", f"nameSTARTSWITH{number} - ^ORDERBYDESCsys_created_on",
                       "sys_id,name,state", limit=1, display=False)
    if fix:
        if not sets:
            raise PermissionError(f"{number} is in Review but has no update set; nothing to fix.")
        if sets[0]["state"] != "in progress":
            raise PermissionError(
                f"Update set {sets[0]['name']} is {sets[0]['state']}, so {number} is locked. "
                "Corrections now need a new change.")
    # The agent user has one current update set, shared with any other harness using the same account.
    current = _current_update_set()
    if current and not (sets and current == sets[0]["sys_id"]):
        held = dev().query("sys_update_set", f"sys_id={current}", "name", 1)
        raise PermissionError(f"{dev().user}'s current update set is '{held[0]['name'] if held else current}': "
                              "another implementation is open (possibly in another harness). Finish or cancel it first.")
    if sets and sets[0]["state"] == "in progress":
        us_id, name = sets[0]["sys_id"], sets[0]["name"]
    else:
        name = f"{number} - {raw['short_description']}"[:80]
        us_id = dev().insert("sys_update_set", {
            "name": name, "state": "in progress", "application": "global",
            "description": f"Implements {number}: {dev().link('change_request', raw['sys_id'])}",
        })["sys_id"]
    _set_current_update_set(us_id)
    state = {"change_number": number, "change_sys_id": raw["sys_id"], "update_set_sys_id": us_id,
             "update_set_name": name, "mode": "fix" if fix else "implement",
             "written": active["written"] if active else []}
    _save_state(state)
    verb = "Fix started" if fix else "Implementation started"
    dev().update("change_request", raw["sys_id"],
                 {"work_notes": f"{verb} in update set {name}: {dev().link('sys_update_set', us_id)}"})
    return {"mode": state["mode"], "update_set": name, "update_set_sys_id": us_id,
            "link": dev().link("sys_update_set", us_id),
            "implementation_plan": raw.get("implementation_plan", ""),
            "captured_so_far": _captured(us_id) if fix else []}


@mcp.tool()
def dev_write_config(table: str, fields: dict[str, Any], sys_id: str | None = None) -> dict:
    """Create (no sys_id) or update (with sys_id) a configuration record in DEV during an active implementation.
    Only allowlisted config tables are writable; the write is captured in the change's update set."""
    state = _require_active()
    if table not in WRITE_TABLES:
        raise PermissionError(f"{table} is not in the write allowlist: {sorted(WRITE_TABLES)}")
    if _current_update_set() != state["update_set_sys_id"]:
        raise RuntimeError("Agent user's current update set no longer matches the implementation. Call begin_implementation again.")
    rec = dev().update(table, sys_id, fields) if sys_id else dev().insert(table, fields)
    entry = {"table": table, "sys_id": rec["sys_id"], "action": "update" if sys_id else "insert",
             "name": rec.get("name") or rec.get("sys_name") or rec.get("element", "")}
    state["written"].append(entry)
    _save_state(state)
    return {**entry, "link": dev().link(table, rec["sys_id"])}


@mcp.tool()
def dev_delete_config(table: str, sys_id: str, reason: str) -> dict:
    """Delete a configuration record during an active implementation or fix. Only allowed for records the agent
    itself created since this change's update set was opened (e.g. a wrong business rule it added).
    To undo changes to pre-existing records, update them back with dev_write_config instead."""
    state = _require_active()
    if table not in WRITE_TABLES:
        raise PermissionError(f"{table} is not in the write allowlist: {sorted(WRITE_TABLES)}")
    if _current_update_set() != state["update_set_sys_id"]:
        raise RuntimeError("Agent user's current update set no longer matches the implementation. Call begin_implementation again.")
    recs = dev().query(table, f"sys_id={sys_id}", "sys_created_by,sys_created_on", limit=1, display=False)
    if not recs:
        raise ValueError(f"{table} {sys_id} not found.")
    us = dev().query("sys_update_set", f"sys_id={state['update_set_sys_id']}", "sys_created_on", limit=1, display=False)[0]
    if recs[0]["sys_created_by"] != dev().user or recs[0]["sys_created_on"] < us["sys_created_on"]:
        raise PermissionError(
            "Only records the agent created for this change can be deleted. "
            "Restore a pre-existing record with dev_write_config, or deactivate it (active=false).")
    dev().delete(table, sys_id)
    entry = {"table": table, "sys_id": sys_id, "action": "delete", "reason": reason}
    state["written"].append(entry)
    _save_state(state)
    return entry


@mcp.tool()
def list_update_set_changes() -> dict:
    """Show what the active implementation's update set has captured so far (sys_update_xml)."""
    state = _require_active()
    return {"update_set": state["update_set_name"], "captured": _captured(state["update_set_sys_id"]),
            "written_by_agent": state["written"]}


@mcp.tool()
def finish_implementation(summary: str) -> dict:
    """Close out the active implementation: verify the update set captured changes, post a work note with the
    summary and captured records, move the change to Review, and reset the agent's current update set.
    The update set is left In progress for human review."""
    state = _require_active()
    captured = _captured(state["update_set_sys_id"])
    if not captured:
        raise RuntimeError("The update set captured nothing. Do not finish; check list_update_set_changes and the writes.")
    lines = "\n".join(f"- {c['type']}: {c['target_name']} ({c['action']})" for c in captured)
    fix = state.get("mode") == "fix"
    header = "[Fix] Correction applied" if fix else "Implementation complete"
    note = (f"{header} in update set {state['update_set_name']}: "
            f"{dev().link('sys_update_set', state['update_set_sys_id'])}\n\n{summary}\n\nCaptured updates:\n{lines}")
    dev().update("change_request", state["change_sys_id"], {"work_notes": note})
    moved = True
    if not fix:  # a fix happens in Review already
        try:
            dev().update("change_request", state["change_sys_id"], {"state": REVIEW_STATE})
            moved = _get_change(state["change_number"])["state"] == REVIEW_STATE
        except RuntimeError:
            moved = False
    _set_current_update_set(None)
    _save_state(None)
    return {"change": state["change_number"], "moved_to_review": moved, "captured_count": len(captured),
            "update_set_link": dev().link("sys_update_set", state["update_set_sys_id"])}


@mcp.tool()
def cancel_implementation(reason: str) -> dict:
    """Stop the active implementation without moving the change (e.g. the approved plan turned out to be wrong).
    Resets the agent's current update set; anything already written stays in the update set for review."""
    state = _require_active()
    dev().update("change_request", state["change_sys_id"], {"work_notes": f"Implementation stopped by agent: {reason}"})
    _set_current_update_set(None)
    _save_state(None)
    return {"change": state["change_number"], "written_before_stop": state["written"]}


if __name__ == "__main__":
    mcp.run()
