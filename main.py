# main.py - FastAPI orchestrator app

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
import shutil
import os
import tempfile
import uuid
import asyncio

from nlp_extractor import extract
from repo_analyzer import RepoAnalyzer
from decision_engine import DecisionEngine
from terraform_provisioning import TerraformProvisioner
from code_modifier import modify_code_for_deployment

app = FastAPI()
jobs = {}

async def stream_logs(job_id: str):
    last_line = 0
    while True:
        await asyncio.sleep(1)
        if job_id not in jobs:
            return
        logs = jobs[job_id]["logs"]
        new_lines = logs[last_line:]
        last_line = len(logs)
        for line in new_lines:
            yield line + "\n"
        if jobs[job_id]["done"]:
            break

@app.post("/deploy")
async def deploy(
        background_tasks: BackgroundTasks,
        message: str = Form(...),
        repo_url: str = Form(None),
        repo_zip: UploadFile = File(None)):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"logs": [], "done": False, "result": None}

    if repo_zip is not None:
        tmp_dir = tempfile.mkdtemp()
        repo_path = os.path.join(tmp_dir, repo_zip.filename)
        with open(repo_path, "wb") as f:
            shutil.copyfileobj(repo_zip.file, f)
    elif repo_url is not None:
        repo_path = repo_url
    else:
        return {"error": "Either repo_url or repo_zip must be provided"}

    background_tasks.add_task(run_pipeline, message, repo_path, job_id)
    return {"job_id": job_id}

@app.get("/logs/{job_id}")
def get_logs(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"logs": jobs[job_id]["logs"], "done": jobs[job_id]["done"], "result": jobs[job_id]["result"]}

@app.get("/stream_logs/{job_id}")
async def stream_logs_endpoint(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(stream_logs(job_id), media_type="text/plain")

def log(job_id: str, message: str):
    if job_id in jobs:
        jobs[job_id]["logs"].append(message)
        print(f"[{job_id}] {message}")

def run_pipeline(message, repo_path, job_id):
    try:
        log(job_id, "Starting deployment pipeline...")
        provider, framework, database = extract(message)
        log(job_id, f"Parsed intent: {provider=}, {framework=}, {database=}")

        analyzer = RepoAnalyzer(repo_path, specified_framework=framework)
        facts = analyzer.analyze()
        log(job_id, f"Repository analyzed: {facts['framework']=}, {facts['entry_point']=}")

        nlp_data = {"provider": provider, "framework": framework, "preference": None}
        engine = DecisionEngine(nlp_data, facts)
        plan = engine.make_plan()
        log(job_id, f"Deployment plan: {plan}")

        prov = TerraformProvisioner(plan, facts)
        outputs = prov.apply()
        log(job_id, f"Provisioned resources: {outputs}")

        env_vars = {
            "DB_HOST": outputs.get("db_endpoint", "DB_HOST"),
            "REDIS_HOST": outputs.get("cache_endpoint", "REDIS_HOST"),
            "APP_HOST": outputs.get("public_ip", "APP_HOST"),
            "PORT": str(outputs.get("port", 8080))
        }

        modify_code_for_deployment(facts["repository_path"], facts["modification_map"], env_vars)
        log(job_id, "Code modified successfully for deployment.")

        jobs[job_id]["result"] = outputs
        jobs[job_id]["done"] = True
        log(job_id, "Deployment pipeline completed.")
    except Exception as e:
        log(job_id, f"Error during deployment: {e}")
        jobs[job_id]["done"] = True
        jobs[job_id]["result"] = None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
