from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any
import sqlite3
import json
import uuid
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()
DB_NAME = "sentinel.db"

# ---------- DB SETUP ----------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT,
        action TEXT,
        tool_name TEXT,
        arguments TEXT,
        result TEXT,
        reasoning TEXT,
        trace_id TEXT,
        parent_trace_id TEXT,
        step_number INTEGER,
        duration_ms INTEGER,
        timestamp TEXT,
        status TEXT DEFAULT 'success')""")
    c.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT,
        alert_type TEXT,
        message TEXT,
        root_cause TEXT,
        trace_id TEXT,
        timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        paused INTEGER DEFAULT 0)""")
    conn.commit()
    conn.close()

# ---------- MODELS ----------
class AgentAction(BaseModel):
    agent_id: str
    action: str
    tool_name: str
    arguments: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    reasoning: Optional[str] = None
    trace_id: Optional[str] = None
    parent_trace_id: Optional[str] = None
    step_number: Optional[int] = 1
    duration_ms: Optional[int] = None
    status: str = "success"

# ---------- HELPERS ----------
def create_alert(agent_id, alert_type, message, root_cause, trace_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""INSERT INTO alerts 
        (agent_id, alert_type, message, root_cause, trace_id, timestamp) 
        VALUES (?, ?, ?, ?, ?, ?)""",
        (agent_id, alert_type, message, root_cause, trace_id, str(datetime.now())))
    conn.commit()
    conn.close()

# ---------- DETECTORS ----------
def detect_repeated_arguments(agent_id, tool_name, arguments, trace_id):
    """The REAL loop detector - same tool + same args = agent is stuck."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""SELECT arguments FROM actions 
        WHERE agent_id = ? AND tool_name = ? 
        ORDER BY id DESC LIMIT 4""", (agent_id, tool_name))
    recent = [row[0] for row in c.fetchall()]
    conn.close()
    
    current_args = json.dumps(arguments, sort_keys=True) if arguments else "{}"
    matches = sum(1 for r in recent if r == current_args)
    
    if matches >= 2:  # current + 2 previous = 3 total
        create_alert(
            agent_id, 
            "LOOP_REPEATED_ARGS",
            f"Agent called {tool_name} with identical args {matches + 1} times",
            f"Tool '{tool_name}' received the same arguments repeatedly. Likely cause: the tool is returning an error/empty result that the agent doesn't handle, causing it to retry with unchanged input.",
            trace_id
        )

def detect_silent_failure(agent_id, tool_name, result, status, trace_id):
    """Detects when a tool failed but the agent marked it as success."""
    if status == "success" and result:
        result_str = json.dumps(result).lower()
        error_keywords = ["error", "failed", "not found", "timeout", "denied", "404", "500"]
        if any(kw in result_str for kw in error_keywords):
            create_alert(
                agent_id,
                "SILENT_FAILURE",
                f"Tool {tool_name} returned an error but agent marked status as success",
                f"The result from '{tool_name}' contains error indicators ({result_str[:100]}), but the agent reported success. The agent may be ignoring failures.",
                trace_id
            )

def detect_failure(agent_id, tool_name, status, trace_id):
    if status == "failed":
        create_alert(
            agent_id,
            "EXPLICIT_FAILURE",
            f"Agent's {tool_name} call failed",
            f"The tool '{tool_name}' returned a failure status. Check the tool implementation or the arguments passed.",
            trace_id
        )

# ---------- MOUNT STATIC ----------
app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------- INIT DB ----------
init_db()

# ---------- ENDPOINTS ----------
@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.post("/log")
def log_action(action: AgentAction):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO agents (agent_id) VALUES (?)", (action.agent_id,))
    c.execute("SELECT paused FROM agents WHERE agent_id = ?", (action.agent_id,))
    paused = c.fetchone()[0]
    
    if paused == 1:
        conn.close()
        return {"status": "rejected", "reason": "agent is paused"}
    
    # Generate trace_id if not provided
    trace_id = action.trace_id or str(uuid.uuid4())[:8]
    
    c.execute("""INSERT INTO actions 
        (agent_id, action, tool_name, arguments, result, reasoning, 
         trace_id, parent_trace_id, step_number, duration_ms, timestamp, status) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (action.agent_id, action.action, action.tool_name,
         json.dumps(action.arguments) if action.arguments else None,
         json.dumps(action.result) if action.result else None,
         action.reasoning, trace_id, action.parent_trace_id,
         action.step_number, action.duration_ms,
         str(datetime.now()), action.status))
    conn.commit()
    conn.close()
    
    # Run detectors
    detect_repeated_arguments(action.agent_id, action.tool_name, action.arguments, trace_id)
    detect_silent_failure(action.agent_id, action.tool_name, action.result, action.status, trace_id)
    detect_failure(action.agent_id, action.tool_name, action.status, trace_id)
    
    return {"status": "logged", "trace_id": trace_id}

@app.get("/logs")
def get_logs():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""SELECT id, agent_id, action, tool_name, arguments, result, 
        reasoning, trace_id, step_number, duration_ms, timestamp, status 
        FROM actions ORDER BY id DESC LIMIT 100""")
    rows = c.fetchall()
    conn.close()
    return [{
        "id": r[0], "agent_id": r[1], "action": r[2], "tool_name": r[3],
        "arguments": json.loads(r[4]) if r[4] else None,
        "result": json.loads(r[5]) if r[5] else None,
        "reasoning": r[6], "trace_id": r[7], "step_number": r[8],
        "duration_ms": r[9], "timestamp": r[10], "status": r[11]
    } for r in rows]

@app.get("/alerts")
def get_alerts():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""SELECT id, agent_id, alert_type, message, root_cause, trace_id, timestamp 
        FROM alerts ORDER BY id DESC LIMIT 50""")
    rows = c.fetchall()
    conn.close()
    return [{
        "id": r[0], "agent_id": r[1], "alert_type": r[2], "message": r[3],
        "root_cause": r[4], "trace_id": r[5], "timestamp": r[6]
    } for r in rows]

@app.get("/trace/{trace_id}")
def get_trace(trace_id: str):
    """Get the full chain of actions for a given trace."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""SELECT id, agent_id, action, tool_name, arguments, result, 
        reasoning, step_number, duration_ms, timestamp, status 
        FROM actions WHERE trace_id = ? ORDER BY step_number ASC""", (trace_id,))
    rows = c.fetchall()
    conn.close()
    return [{
        "id": r[0], "agent_id": r[1], "action": r[2], "tool_name": r[3],
        "arguments": json.loads(r[4]) if r[4] else None,
        "result": json.loads(r[5]) if r[5] else None,
        "reasoning": r[6], "step_number": r[7], "duration_ms": r[8],
        "timestamp": r[9], "status": r[10]
    } for r in rows]

@app.get("/agents")
def get_agents():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT agent_id, paused FROM agents")
    rows = c.fetchall()
    conn.close()
    return [{"agent_id": r[0], "paused": bool(r[1])} for r in rows]

@app.post("/kill/{agent_id}")
def kill_agent(agent_id: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE agents SET paused = 1 WHERE agent_id = ?", (agent_id,))
    conn.commit()
    conn.close()
    return {"status": "killed", "agent_id": agent_id}

@app.post("/revive/{agent_id}")
def revive_agent(agent_id: str):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE agents SET paused = 0 WHERE agent_id = ?", (agent_id,))
    conn.commit()
    conn.close()
    return {"status": "revived", "agent_id": agent_id}

if __name__ == "__main__":
    import uvicorn, os
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))