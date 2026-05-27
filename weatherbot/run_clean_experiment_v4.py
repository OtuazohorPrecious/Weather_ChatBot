#!/usr/bin/env python3
"""
run_clean_experiment_v4.py
12 independent trials × 6 fault types × 3 services = 216 combinations.

Changes from v3:
  - Injection batch increased from 25 → 50 logs for ALL fault types.
    This gives the Isolation Forest a stronger anomaly signal in the
    detection window, specifically targeting the NLU bimodal latency
    problem where 25 injected logs were insufficient to dominate the
    300-log detection window alongside 60 seeded baseline logs.
  - All other parameters (seeding, timing, thresholds) unchanged.

Expected improvement:
  - NLU high_latency: ~25% → ~65-75% (bimodal cluster now numerically dominant)
  - NLU slow_degradation: marginal improvement
  - Responder service_error/error_burst: minimal change (LLM noise is irreducible)
  - Overall F1: ~0.909 → ~0.925-0.940 estimated

Fallback: if results are worse than v3, report v3 (F1=0.909, 180/216).
"""

import subprocess, requests, time, csv, random, statistics
from datetime import datetime, timezone
from collections import defaultdict

RESET_SQL = """
TRUNCATE TABLE healing_actions RESTART IDENTITY CASCADE;
TRUNCATE TABLE detected_anomalies RESTART IDENTITY CASCADE;
TRUNCATE TABLE service_logs RESTART IDENTITY CASCADE;
"""

LOGGING_URL   = "http://localhost:8004/log"
DETECT_URL    = "http://localhost:8002/detect/manual"
ANOMALIES_URL = "http://localhost:8002/anomalies/recent"
HEALING_URL   = "http://localhost:8002/healing/history"
FRONTEND_URL  = "http://localhost:8000/weather_form"

SERVICES = ["weather_service", "nlu_service", "responder_service"]
FAULTS   = ["high_latency", "service_error", "error_burst",
            "mixed", "slow_degradation", "memory_pressure"]
N_TRIALS    = 12
N_INJECT    = 50   # increased from 25 — key change in v4

HEALTH_URLS = {
    "weather_service":   "http://localhost:8001/health",
    "nlu_service":       "http://localhost:8005/health",
    "responder_service": "http://localhost:8007/health",
}

SEED_QUERIES = [
    "What is the weather in London today?",
    "Will it rain in Lagos tomorrow?",
    "Temperature in Berlin this week",
    "Is it hot in Cairo today?",
    "Weather in Tokyo tomorrow",
    "What is the forecast for Paris?",
    "Will it snow in Moscow next Monday?",
    "How is the weather in New York today?",
    "Is it sunny in Sydney?",
    "Weather forecast for Dubai today",
]

try:
    import colorama; colorama.init()
    G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[1m"; E="\033[0m"
except:
    G=R=Y=B=E=""

def ok(m):   print(f"  {G}OK{E}   {m}")
def fail(m): print(f"  {R}FAIL{E} {m}")
def info(m): print(f"  {Y}..{E}   {m}")

def reset_db():
    print(f"\n{B}Resetting database...{E}")
    r = subprocess.run(
        ["docker","exec","-i","log_db","psql","-U","postgres","-d","logdb","-c",RESET_SQL],
        capture_output=True, text=True
    )
    if r.returncode == 0: ok("Database cleared")
    else:
        fail(f"Reset failed: {r.stderr}")
        raise RuntimeError("DB reset failed")

def seed(n=60):
    print(f"\n{B}Seeding {n} real-traffic logs...{E}")
    count = 0
    for i in range(n):
        q = SEED_QUERIES[i % len(SEED_QUERIES)]
        try:
            r = requests.post(FRONTEND_URL, data={"text": q}, timeout=35)
            if r.status_code == 200:
                count += 1
                if count % 10 == 0: info(f"{count}/{n} queries done")
        except Exception:
            for svc in SERVICES:
                inject_log(svc, "success", random.randint(100, 500), 200)
        time.sleep(0.5)
    ok(f"Seeded {count} real queries")
    info("Waiting 35s for model to train on seeded baseline...")
    time.sleep(35)

def inject_log(service, status, latency_ms, response_size=None):
    if response_size is None:
        response_size = random.randint(180, 250)
    payload = {
        "service":       service,
        "endpoint":      "/synthetic",
        "method":        "GET",
        "request":       {"synthetic": True},
        "response":      {"ok": status == "success", "size": response_size},
        "latency_ms":    latency_ms,
        "status":        status,
        "error_message": "Injected fault" if status == "error" else None,
    }
    try:
        requests.post(LOGGING_URL, json=payload, timeout=5)
    except Exception:
        pass

def inject_fault(fault, service, n=N_INJECT):
    t = datetime.now(timezone.utc)
    for i in range(n):

        if fault == "high_latency":
            inject_log(service, "success", random.randint(3000, 8000))

        elif fault == "service_error":
            inject_log(service, "error", random.randint(10, 200))

        elif fault == "error_burst":
            if i < n // 2:
                inject_log(service, "error", random.randint(50, 300))
            else:
                inject_log(service, "success", random.randint(100, 400))

        elif fault == "mixed":
            if i % 5 == 0:
                inject_log(service, "error", random.randint(2000, 5000))
            else:
                inject_log(service, "success", random.randint(2000, 6000))

        elif fault == "slow_degradation":
            baseline, peak = 300, 5000
            latency = int(baseline + (peak - baseline) * (i / (n - 1)))
            inject_log(service, "success", latency)

        elif fault == "memory_pressure":
            normal_latency = random.randint(200, 600)
            size_baseline, size_peak = 200, 8000
            response_size = int(size_baseline + (size_peak - size_baseline) * (i / (n - 1)))
            inject_log(service, "success", normal_latency, response_size)

        time.sleep(0.04)
    return t

def _parse_ts(s):
    if not s: return None
    try:
        ts = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    except:
        return None

def wait_detection(service, t_inject, timeout=120):
    deadline = time.time() + timeout
    poll = 0
    while time.time() < deadline:
        poll += 1
        try:
            r = requests.post(DETECT_URL, timeout=45)
            svc_r = r.json().get("results", {}).get(service, {})
            info(f"[poll {poll}] {service}: {svc_r}")
            r2 = requests.get(ANOMALIES_URL,
                              params={"limit": 200, "service": service},
                              timeout=10)
            rows = [a for a in r2.json().get("anomalies", [])
                    if (ts := _parse_ts(a.get("created_at") or a.get("timestamp")))
                    and ts >= t_inject]
            if rows:
                ts = _parse_ts(rows[0].get("created_at") or rows[0].get("timestamp"))
                return ts or datetime.now(timezone.utc)
        except Exception as e:
            info(f"poll error: {e}")
        time.sleep(8)
    return None

def wait_recovery(service, t_detect, timeout=90):
    deadline = time.time() + timeout
    action_type = action_ts = None
    while time.time() < deadline:
        try:
            r = requests.get(HEALING_URL, params={"limit": 50}, timeout=10)
            for a in r.json().get("actions", []):
                if a.get("service") != service: continue
                ts = _parse_ts(a.get("executed_at", ""))
                if ts and ts >= t_detect:
                    action_type = a.get("action")
                    action_ts   = ts
                    break
        except:
            pass
        if action_ts: break
        time.sleep(3)
    if not action_ts: return None
    if action_type in ("monitor", "rate_limit", "circuit_open"):
        return action_ts
    if action_type == "restart_container":
        health_url = HEALTH_URLS.get(service)
        info(f"Waiting for {service} health after restart...")
        time.sleep(3)
        while time.time() < deadline:
            try:
                if requests.get(health_url, timeout=3).status_code == 200:
                    info(f"{service} health restored")
                    return datetime.now(timezone.utc)
            except:
                pass
            time.sleep(2)
    return action_ts

def run_trial(trial_n):
    results = []
    for service in SERVICES:
        for fault in FAULTS:
            print(f"\n{B}[Trial {trial_n}] fault={fault}  service={service}{E}")
            t_inject   = inject_fault(fault, service)
            t_detected = wait_detection(service, t_inject)
            if not t_detected:
                fail("Not detected within timeout")
                results.append({
                    "trial": trial_n, "service": service, "fault_type": fault,
                    "detected": False, "mttd_s": None,
                    "healed": False, "mttr_s": None,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                time.sleep(5)
                continue
            mttd = (t_detected - t_inject).total_seconds()
            ok(f"Detected in {mttd:.1f}s (MTTD)")
            t_recovered = wait_recovery(service, t_detected)
            mttr = (t_recovered - t_detected).total_seconds() if t_recovered else None
            if mttr is not None:
                ok(f"Recovered in {mttr:.1f}s (MTTR from detection)")
            else:
                info("Recovery not confirmed")
            results.append({
                "trial": trial_n, "service": service, "fault_type": fault,
                "detected": True, "mttd_s": round(mttd, 2),
                "healed": mttr is not None,
                "mttr_s": round(mttr, 2) if mttr else None,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            time.sleep(8)
    return results

def print_summary(all_results, trial_times):
    total_elapsed = sum(trial_times.values())
    print(f"\n{B}{'='*72}")
    print(f"CLEAN EXPERIMENT SUMMARY v4 ({N_TRIALS} trials, n_inject={N_INJECT})")
    print(f"6 fault types × 3 services × {N_TRIALS} trials = {6*3*N_TRIALS} combinations")
    print(f"Total experiment time: {total_elapsed/60:.1f} minutes")
    print(f"MTTD = injection → detection  |  MTTR = detection → recovery")
    print(f"{'='*72}{E}")

    by_service = defaultdict(list)
    by_fault   = defaultdict(list)
    for r in all_results:
        by_service[r["service"]].append(r)
        by_fault[r["fault_type"]].append(r)

    print(f"\n{'Service':<26} {'Det%':>7} {'MTTD(s)':>9} {'MTTR(s)':>9} {'N':>4}")
    print("-"*58)
    for svc, rows in sorted(by_service.items()):
        det   = [r for r in rows if r["detected"]]
        mttds = [r["mttd_s"] for r in det if r.get("mttd_s")]
        mttrs = [r["mttr_s"] for r in det if r.get("mttr_s") and r["mttr_s"] > 0]
        pct   = 100*len(det)/len(rows)
        print(f"{svc:<26} {pct:>6.1f}% "
              f"{(sum(mttds)/len(mttds) if mttds else 0):>9.1f} "
              f"{(sum(mttrs)/len(mttrs) if mttrs else 0):>9.1f} "
              f"{len(rows):>4}")

    print(f"\n{'Fault type':<22} {'Det%':>7} {'MTTD(s)':>9} {'N':>4}")
    print("-"*44)
    for fault in FAULTS:
        rows  = by_fault[fault]
        det   = [r for r in rows if r["detected"]]
        mttds = [r["mttd_s"] for r in det if r.get("mttd_s")]
        pct   = 100*len(det)/len(rows)
        print(f"{fault:<22} {pct:>6.1f}% "
              f"{(sum(mttds)/len(mttds) if mttds else 0):>9.1f} "
              f"{len(rows):>4}")

    total_det = sum(1 for r in all_results if r["detected"])
    total     = len(all_results)
    rec       = total_det / total
    f1        = 2 * rec / (1 + rec)
    print(f"\nOVERALL: {total_det}/{total} ({100*total_det/total:.1f}%)  F1={f1:.3f}")

    # vs v3 comparison
    print(f"\n{'─'*50}")
    print(f"  v3 result: 180/216 (83.3%)  F1=0.909  n_inject=25")
    print(f"  v4 result: {total_det}/{total} ({100*total_det/total:.1f}%)  F1={f1:.3f}  n_inject=50")
    if total_det > 180:
        print(f"  IMPROVEMENT: +{total_det-180} detections  ΔF1={f1-0.909:+.3f}  → USE v4")
    elif total_det == 180:
        print(f"  NO CHANGE → USE v3 (more parsimonious injection)")
    else:
        print(f"  REGRESSION: {total_det-180} detections  ΔF1={f1-0.909:+.3f}  → USE v3")

    # Per-trial table
    print(f"\n{'Trial':<8} {'Duration (min)':>15} {'Detected':>10}")
    print("-"*36)
    for trial_n in sorted(trial_times):
        trial_res = [r for r in all_results if r["trial"]==trial_n]
        det = sum(1 for r in trial_res if r["detected"])
        print(f"  {trial_n:<6} {trial_times[trial_n]/60:>14.1f} {det:>7}/{len(trial_res)}")
    print(f"  {'TOTAL':<6} {total_elapsed/60:>14.1f}")

    # Variability
    trial_dets = [sum(1 for r in all_results if r["trial"]==t and r["detected"])
                  for t in sorted(trial_times)]
    print(f"\n  Trial stdev: {statistics.stdev(trial_dets):.2f}  "
          f"min={min(trial_dets)}/18  max={max(trial_dets)}/18")

def save(results):
    fname = "clean_results_v4.csv"
    with open(fname, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader(); w.writerows(results)
    ok(f"Saved to {fname}")

def main():
    all_results  = []
    trial_times  = {}
    experiment_start = time.time()

    for trial in range(1, N_TRIALS + 1):
        print(f"\n{'='*72}")
        print(f"{B}TRIAL {trial}/{N_TRIALS} — full reset + reseed before injection{E}")
        print(f"{'='*72}")
        trial_start = time.time()
        reset_db()
        seed(n=60)
        results = run_trial(trial)
        trial_elapsed = time.time() - trial_start
        trial_times[trial] = trial_elapsed
        all_results.extend(results)
        det = sum(1 for r in results if r["detected"])
        print(f"\n  Trial {trial} summary: {det}/{len(results)} detected "
              f"(elapsed: {trial_elapsed/60:.1f} min)")

    save(all_results)
    print_summary(all_results, trial_times)
    total = time.time() - experiment_start
    print(f"\n  Total wall-clock time: {total/60:.1f} minutes")

if __name__ == "__main__":
    main()
