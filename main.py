from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import sqlite3
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
        agent_id TEXT, action TEXT, tool_name TEXT,
        timestamp TEXT, status TEXT DEFAULT 'success')""")
    c.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT, alert_type TEXT,
        message TEXT, timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY, paused INTEGER DEFAULT 0)""")
    conn.commit()
    conn.close()

# ---------- MODELS ----------
class AgentAction(BaseModel):
    agent_id: str
    action: str
    tool_name: str
    status: str = "success"

# ---------- DETECTORS ----------
def create_alert(agent_id, alert_type, message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO alerts (agent_id, alert_type, message, timestamp) VALUES (?, ?, ?, ?)",
              (agent_id, alert_type, message, str(datetime.now())))
    conn.commit()
    conn.close()

def detect_loop(agent_id, tool_name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT tool_name FROM actions WHERE agent_id = ? ORDER BY id DESC LIMIT 5", (agent_id,))
    recent = [row[0] for row in c.fetchall()]
    conn.close()
    if recent.count(tool_name) >= 3:
        create_alert(agent_id, "LOOP", f"Agent called {tool_name} {recent.count(tool_name)}x in last 5 actions")

def detect_failure(agent_id, tool_name, status):
    if status == "failed":
        create_alert(agent_id, "SILENT_FAILURE", f"Agent's {tool_name} call failed")

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
    c.execute("INSERT INTO actions (agent_id, action, tool_name, timestamp, status) VALUES (?, ?, ?, ?, ?)",
              (action.agent_id, action.action, action.tool_name, str(datetime.now()), action.status))
    conn.commit()
    conn.close()
    detect_loop(action.agent_id, action.tool_name)
    detect_failure(action.agent_id, action.tool_name, action.status)
    return {"status": "logged"}

@app.get("/logs")
def get_logs():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, agent_id, action, tool_name, timestamp, status FROM actions ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "agent_id": r[1], "action": r[2], "tool_name": r[3], "timestamp": r[4], "status": r[5]} for r in rows]

@app.get("/alerts")
def get_alerts():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, agent_id, alert_type, message, timestamp FROM alerts ORDER BY id DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "agent_id": r[1], "alert_type": r[2], "message": r[3], "timestamp": r[4]} for r in rows]

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