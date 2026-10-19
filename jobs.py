import json
import subprocess
import sys
import time
import uuid
import os
import psutil
import store


def status():
    job = store.get("job")
    if not job:
        return None
    if job.get("state") == "running":
        try:
            p = psutil.Process(job["pid"])
            alive = abs(p.create_time() - job["create_time"]) < 1
        except psutil.Error:
            alive = False
        if not alive:
            result = store.USER / (job["id"] + ".result.json")
            code = json.loads(result.read_text())["returncode"] if result.exists() else None
            job["state"] = "completed" if code == 0 else "failed"
            job["returncode"] = code
            store.put("job", job)
    return job


def start(action, *, days=90, timerange="", fee=0.0005, detail=False):
    if action not in ("download", "backtest", "lookahead", "recursive"):
        raise ValueError("研究任务类型无效")
    if status() and status().get("state") == "running":
        raise ValueError("已有研究任务在运行，请等待完成")
    job_id = "job-" + uuid.uuid4().hex[:12]
    args = [action, "--days", str(int(days)), "--fee", str(float(fee))]
    if timerange:
        args += ["--timerange", timerange]
    if detail:
        args.append("--detail")
    logpath = store.USER / (job_id + ".log")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with logpath.open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, str(store.ROOT / "jobs.py"), "--worker", job_id, *args],
                                 cwd=store.ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    job = {"id": job_id, "pid": child.pid, "create_time": psutil.Process(child.pid).create_time(),
           "action": action, "args": args, "started_at": time.time(), "state": "running", "log": logpath.name}
    store.put("job", job)
    return job


if __name__ == "__main__":
    job_id = sys.argv[2]
    result = subprocess.run([sys.executable, str(store.ROOT / "launch.py"), *sys.argv[3:]], cwd=store.ROOT)
    (store.USER / (job_id + ".result.json")).write_text(json.dumps({"returncode": result.returncode}))
    raise SystemExit(result.returncode)
