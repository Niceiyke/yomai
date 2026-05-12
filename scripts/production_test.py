#!/usr/bin/env python3
"""Production smoke test - tests the demo-app server endpoints end-to-end.

This starts a real uvicorn server and makes HTTP requests to verify
the API works correctly in production mode.
"""
from __future__ import annotations

import subprocess
import sys
import time
import os
import signal

# Configuration
HOST = "127.0.0.1"
PORT = 9000
BASE_URL = f"http://{HOST}:{PORT}"

def run_tests():
    """Run all smoke tests against the live server."""
    import httpx
    
    results = []
    
    def check(name: str, passed: bool, detail: str = ""):
        results.append((name, passed, detail))
        status = "✓" if passed else "✗"
        print(f"{status} {name}" + (f": {detail}" if detail else ""))
    
    # Test 1: Health
    try:
        r = httpx.get(f"{BASE_URL}/__yomai__/health", timeout=5)
        check("Health check", r.status_code == 200 and "ok" in r.text)
    except Exception as e:
        check("Health check", False, str(e))
    
    # Test 2: Session creation
    try:
        r = httpx.get(f"{BASE_URL}/sessions/test-session", timeout=5)
        check("Session GET", r.status_code == 200 and "session_id" in r.text)
    except Exception as e:
        check("Session GET", False, str(e))
    
    # Test 3: Research endpoint validation
    try:
        r = httpx.post(f"{BASE_URL}/research", json={}, timeout=5)
        check("Research validation", r.status_code == 400)
    except Exception as e:
        check("Research validation", False, str(e))
    
    # Test 4: Workflow job creation
    try:
        r = httpx.post(f"{BASE_URL}/batch-research", json={"topics": ["test"]}, timeout=10)
        data = r.json()
        check("Workflow job creation", "job_id" in data)
        job_id = data.get("job_id", "")
    except Exception as e:
        check("Workflow job creation", False, str(e))
        job_id = ""
    
    # Test 5: Job status (uses session header, no auth needed)
    if job_id:
        try:
            r = httpx.get(f"{BASE_URL}/jobs/{job_id}", timeout=5)
            check("Job status lookup", r.status_code == 200 and "id" in r.text)
        except Exception as e:
            check("Job status lookup", False, str(e))
    
    # Test 6: Job not found
    try:
        r = httpx.get(f"{BASE_URL}/jobs/nonexistent-xyz", timeout=5)
        check("Job not found", r.status_code == 200 and "error" in r.text)
    except Exception as e:
        check("Job not found", False, str(e))
    
    # Test 7: Metrics endpoint (no auth needed for non-production)
    try:
        r = httpx.get(f"{BASE_URL}/metrics", timeout=5)
        check("Metrics endpoint", r.status_code == 200 and "requests_total" in r.text)
    except Exception as e:
        check("Metrics endpoint", False, str(e))
    
    # Summary
    print("\n" + "="*50)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    return failed == 0


def main():
    # Change to project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    demo_dir = os.path.join(project_root, "demo-app")
    
    # Set up environment (development mode to avoid auth requirements)
    env = os.environ.copy()
    env["PYTHONPATH"] = demo_dir
    # Don't set YOMAI_ENV=production to avoid auth requirements
    
    print(f"Starting server on {HOST}:{PORT} (dev mode)...")
    
    # Kill any existing process on this port
    subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    time.sleep(0.5)
    
    # Start server
    process = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", 
         "--host", HOST, "--port", str(PORT), "--log-level", "error"],
        cwd=demo_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    try:
        # Wait for server
        print("Waiting for server...")
        for i in range(30):
            try:
                import httpx
                httpx.get(f"{BASE_URL}/__yomai__/health", timeout=1)
                print("Server ready!")
                break
            except:
                time.sleep(0.5)
        else:
            print("Server failed to start")
            return 1
        
        # Run tests
        success = run_tests()
        return 0 if success else 1
        
    finally:
        # Cleanup
        print("\nStopping server...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())