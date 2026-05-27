#!/usr/bin/env python3
"""
Self-Healing System Evaluation Script
- Generates normal user queries.
- Injects controlled faults (container stop, network latency) at specified times.
- Records ground truth.
- Queries anomaly detection service for detected anomalies.
- Computes precision, recall, F1-score, detection latency, and healing effectiveness.
"""

import argparse
import json
import subprocess
import time
import requests
import datetime
from typing import List, Dict, Tuple
import sys

# --- Configuration ---
BASE_URL = "http://localhost:8000"
ANOMALY_SERVICE_URL = "http://localhost:8002"
LOGGING_SERVICE_URL = "http://localhost:8004"
TEST_DURATION = 300  # seconds
NORMAL_QUERY_INTERVAL = 2  # seconds between normal queries

# Services and their container names (as in docker-compose)
SERVICES = {
    "frontend": "frontend",
    "nlu_service": "weatherbot-nlu_service-1",
    "weather_service": "weather_service",
    "responder_service": "weatherbot-responder_service-1",
    "logging_service": "weatherbot-logging_service-1",
    "anomaly_detection_service": "weatherbot-anomaly_detection_service-1"
}

# Fault types
FAULT_TYPES = {
    "stop": "container_stop",
    "latency": "network_latency"
}

class GroundTruth:
    """Record of a fault injection event."""
    def __init__(self, fault_type: str, service: str, start_time: float, end_time: float, details: str = ""):
        self.fault_type = fault_type
        self.service = service
        self.start_time = start_time
        self.end_time = end_time
        self.details = details

    def to_dict(self):
        return {
            "fault_type": self.fault_type,
            "service": self.service,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "details": self.details
        }

def inject_fault(service: str, fault_type: str, duration: int = 60, delay_ms: int = 500):
    """Inject a fault into a service container."""
    container = SERVICES.get(service)
    if not container:
        raise ValueError(f"Unknown service: {service}")

    if fault_type == "stop":
        print(f"🛑 Stopping container {container}")
        subprocess.run(["docker", "stop", container], check=True)
        time.sleep(duration)
        print(f"▶️ Starting container {container}")
        subprocess.run(["docker", "start", container], check=True)
    elif fault_type == "latency":
        print(f"⏱️  Adding {delay_ms}ms latency to {container}")
        subprocess.run(["docker", "exec", container, "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", f"{delay_ms}ms"], check=True)
        time.sleep(duration)
        print(f"✅ Removing latency from {container}")
        subprocess.run(["docker", "exec", container, "tc", "qdisc", "del", "dev", "eth0", "root"], check=True)
    else:
        raise ValueError(f"Unknown fault type: {fault_type}")

def query_normal(text: str = "Weather in London") -> Tuple[int, float]:
    """Send a normal user query and return status code and response time."""
    start = time.time()
    try:
        resp = requests.post(f"{BASE_URL}/weather_form", data={"text": text}, timeout=10)
        latency = time.time() - start
        return resp.status_code, latency
    except Exception as e:
        print(f"Query failed: {e}")
        return 500, time.time() - start

def fetch_anomalies(since: float) -> List[Dict]:
    """Fetch anomalies detected after a given timestamp (seconds since epoch)."""
    # Convert to ISO format for API
    since_iso = datetime.datetime.fromtimestamp(since).isoformat()
    try:
        resp = requests.get(f"{ANOMALY_SERVICE_URL}/anomalies/recent?limit=1000")
        if resp.status_code == 200:
            data = resp.json()
            anomalies = data.get("anomalies", [])
            # Filter by timestamp (the API returns all recent, but we can filter client-side)
            filtered = [a for a in anomalies if a['timestamp'] >= since_iso]
            return filtered
        else:
            print(f"Failed to fetch anomalies: {resp.status_code}")
            return []
    except Exception as e:
        print(f"Error fetching anomalies: {e}")
        return []

def match_anomalies_to_ground_truth(anomalies: List[Dict], ground_truths: List[GroundTruth], window: float = 10.0) -> Dict:
    """
    Match detected anomalies to ground truth faults.
    Anomaly matches a fault if:
        - anomaly.service == fault.service
        - anomaly.timestamp is within [fault.start - window, fault.end + window]
    Returns counts: TP, FP, FN, and list of detections with latencies.
    """
    tp = 0
    fp = 0
    used_truths = set()
    detections = []  # (anomaly_time, fault_start, latency)

    for a in anomalies:
        matched = False
        a_time = datetime.datetime.fromisoformat(a['timestamp']).timestamp()
        for i, gt in enumerate(ground_truths):
            if i in used_truths:
                continue
            if a['service'] == gt.service and gt.start_time - window <= a_time <= gt.end_time + window:
                tp += 1
                used_truths.add(i)
                matched = True
                latency = a_time - gt.start_time
                detections.append((a_time, gt.start_time, latency))
                break
        if not matched:
            fp += 1

    fn = len(ground_truths) - len(used_truths)
    return {"tp": tp, "fp": fp, "fn": fn, "detections": detections}

def print_metrics(tp, fp, fn, total_queries, total_anomalies):
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    print("\n=== Evaluation Results ===")
    print(f"Total normal queries: {total_queries}")
    print(f"Total injected faults: {total_anomalies}")
    print(f"True Positives: {tp}")
    print(f"False Positives: {fp}")
    print(f"False Negatives: {fn}")
    print(f"Precision: {precision:.3f}")
    print(f"Recall: {recall:.3f}")
    print(f"F1-score: {f1:.3f}")

def main():
    parser = argparse.ArgumentParser(description="Evaluate self-healing system")
    parser.add_argument("--faults", type=str, default="stop,latency", help="Comma-separated fault types to inject")
    parser.add_argument("--duration", type=int, default=300, help="Total test duration (seconds)")
    parser.add_argument("--interval", type=float, default=2.0, help="Interval between normal queries (seconds)")
    args = parser.parse_args()

    fault_types = [f.strip() for f in args.faults.split(",")]
    total_time = args.duration
    query_interval = args.interval

    print("Starting self-healing evaluation...")
    print(f"Test duration: {total_time}s, query interval: {query_interval}s")
    print(f"Faults to inject: {fault_types}")

    ground_truths = []
    start_time = time.time()
    end_time = start_time + total_time

    # Schedule faults at specific times (e.g., every 60 seconds, rotate through services)
    fault_schedule = []
    t = start_time + 30  # first fault after 30 seconds
    services_list = list(SERVICES.keys())
    # Exclude logging and anomaly from faults to keep them healthy
    target_services = ["frontend", "nlu_service", "weather_service", "responder_service"]
    i = 0
    while t < end_time - 30:  # ensure last fault has time to recover
        service = target_services[i % len(target_services)]
        fault_type = fault_types[i % len(fault_types)]
        duration = 40  # seconds each fault lasts
        fault_schedule.append((t, service, fault_type, duration))
        t += 70  # next fault after 70 seconds
        i += 1

    print("Fault schedule:")
    for ft, svc, typ, dur in fault_schedule:
        print(f"  {datetime.datetime.fromtimestamp(ft)}: {typ} on {svc} for {dur}s")

    # Start normal query loop
    query_count = 0
    fault_index = 0
    current_fault = None  # (service, fault_type, start_time, end_time)

    try:
        while time.time() < end_time:
            now = time.time()

            # Check if it's time to start a new fault
            if fault_index < len(fault_schedule) and now >= fault_schedule[fault_index][0]:
                ft, svc, typ, dur = fault_schedule[fault_index]
                print(f"\n[{datetime.datetime.fromtimestamp(now)}] Injecting {typ} on {svc} for {dur}s")
                # Start fault in background (non-blocking)
                # We'll run the fault in a subprocess to not block the query loop
                subprocess.Popen([
                    sys.executable, __file__,
                    "--inject-fault", svc, typ, str(dur)
                ])
                # Record ground truth
                gt = GroundTruth(
                    fault_type=typ,
                    service=svc,
                    start_time=now,
                    end_time=now + dur,
                    details=f"duration={dur}"
                )
                ground_truths.append(gt)
                fault_index += 1

            # Send a normal query
            status, lat = query_normal()
            query_count += 1
            print(f"[{datetime.datetime.fromtimestamp(now)}] Query {query_count}: status={status}, latency={lat:.2f}s")

            time.sleep(query_interval)

    except KeyboardInterrupt:
        print("\nTest interrupted by user.")
    finally:
        # Allow time for last fault to finish and anomalies to be recorded
        print("Waiting 30 seconds for final anomaly processing...")
        time.sleep(30)

        # Fetch anomalies
        anomalies = fetch_anomalies(start_time)
        print(f"Retrieved {len(anomalies)} anomalies from service.")

        # Match
        results = match_anomalies_to_ground_truth(anomalies, ground_truths)
        tp, fp, fn = results["tp"], results["fp"], results["fn"]
        detections = results["detections"]

        # Compute overall metrics
        total_anomalies = len(ground_truths)
        print_metrics(tp, fp, fn, query_count, total_anomalies)

        if detections:
            avg_detection_latency = sum(d[2] for d in detections) / len(detections)
            print(f"Average detection latency: {avg_detection_latency:.2f}s")

        # Save ground truth and results to file for later reference
        with open("evaluation_results.json", "w") as f:
            json.dump({
                "ground_truths": [gt.to_dict() for gt in ground_truths],
                "anomalies": anomalies,
                "metrics": {"tp": tp, "fp": fp, "fn": fn, "total_queries": query_count}
            }, f, indent=2)
        print("Results saved to evaluation_results.json")

if __name__ == "__main__":
    # This allows the script to be called recursively for fault injection
    import sys
    if "--inject-fault" in sys.argv:
        idx = sys.argv.index("--inject-fault")
        service = sys.argv[idx+1]
        fault_type = sys.argv[idx+2]
        duration = int(sys.argv[idx+3])
        inject_fault(service, fault_type, duration)
    else:
        main()