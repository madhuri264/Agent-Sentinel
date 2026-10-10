"""
Agent Sentinel Python SDK
Simple client for sending agent actions to Agent Sentinel.
"""
import requests
from datetime import datetime
from typing import Optional, Dict, Any
import uuid


class Sentinel:
    def __init__(self, agent_id: str, url: str = "http://127.0.0.1:8000"):
        self.agent_id = agent_id
        self.url = url.rstrip("/")
        self.current_trace_id = None
        self.step_number = 0

    def start_trace(self) -> str:
        """Start a new trace chain. All logs after this share the same trace_id."""
        self.current_trace_id = str(uuid.uuid4())[:8]
        self.step_number = 0
        return self.current_trace_id

    def log(
        self,
        action: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        reasoning: Optional[str] = None,
        status: str = "success",
        duration_ms: Optional[int] = None,
        trace_id: Optional[str] = None,
    ) -> Dict:
        """
        Log an agent action.
        If no trace_id is set, a new one is auto-started.
        """
        if trace_id:
            tid = trace_id
        elif self.current_trace_id:
            tid = self.current_trace_id
        else:
            tid = self.start_trace()

        self.step_number += 1

        payload = {
            "agent_id": self.agent_id,
            "action": action,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "reasoning": reasoning,
            "trace_id": tid,
            "parent_trace_id": None,
            "step_number": self.step_number,
            "duration_ms": duration_ms,
            "status": status,
        }

        try:
            response = requests.post(f"{self.url}/log", json=payload, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"[Sentinel] Failed to log: {e}")
            return {"status": "error", "message": str(e)}

    def kill_self(self) -> bool:
        """Check if this agent has been paused by Agent Sentinel."""
        try:
            r = requests.get(f"{self.url}/agents", timeout=5)
            agents = r.json()
            for a in agents:
                if a["agent_id"] == self.agent_id and a["paused"]:
                    return True
            return False
        except Exception:
            return False

    def __enter__(self):
        """Support context manager: `with Sentinel(...) as s:`"""
        self.start_trace()
        return self

    def __exit__(self, *args):
        pass