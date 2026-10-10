"""
Demo agent that uses the Sentinel SDK.
Simulates an agent that keeps calling web_search with the same arguments
and silently ignores errors.
"""
import time
from sentinel_sdk import Sentinel


def fake_web_search(location: str):
    """A fake tool that always returns an error."""
    return {"error": "city not found", "code": 404}


def run_demo():
    # Connect to local server (use the Render URL if testing live)
    SENTINEL_URL = "http://127.0.0.1:8000"

    with Sentinel(agent_id="demo-agent", url=SENTINEL_URL) as sentinel:
        print(f"Started trace: {sentinel.current_trace_id}")

        # Simulate an agent looping on the same broken tool
        for i in range(4):
            args = {"location": "delhi"}
            
            # Call the (broken) tool
            result = fake_web_search(**args)
            
            # Log it via Sentinel
            sentinel.log(
                action="search",
                tool_name="web_search",
                arguments=args,
                result=result,
                reasoning="User wants delhi weather, I'll search for it.",
                status="success",  # <- AGENT LIES: ignores the 404
                duration_ms=234,
            )
            print(f"  Logged step {i+1}")
            time.sleep(0.5)

        print("Done. Check the dashboard at http://127.0.0.1:8000")


if __name__ == "__main__":
    run_demo()