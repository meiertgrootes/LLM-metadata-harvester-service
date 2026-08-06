import os
import subprocess

from watchfiles import run_process


def start() -> None:
    subprocess.run([
        "celery",
        "-A",
        "llm_metadata_harvester_service.core.celery_app.celery_app",
        "worker",
        "-l",
        "info"
    ])

if __name__ == "__main__":
    src_dir = os.environ.get("PYTHONPATH", "/app/src")
    run_process(src_dir, target=start)
