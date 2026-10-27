from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.test.yml"


@dataclass
class Stack:
    environment: dict[str, str]
    api_url: str
    receiver_url: str

    def compose(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
            cwd=ROOT,
            env=self.environment,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def docker(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", *arguments],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        receiver: bool = False,
    ) -> tuple[int, Any]:
        data = None if payload is None else json.dumps(payload).encode()
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        request = urllib.request.Request(
            (self.receiver_url if receiver else self.api_url) + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            body = error.read()
            return error.code, json.loads(body) if body else None

    def sql(self, statement: str, *, database: str = "harvester") -> str:
        result = self.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-XAt",
            "-F",
            "|",
            "-U",
            "harvester",
            "-d",
            database,
            "-c",
            statement,
        )
        return result.stdout.strip()

    def wait_for(
        self,
        description: str,
        predicate: Callable[[], Any],
        *,
        timeout: float = 30,
        interval: float = 0.25,
    ) -> Any:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except Exception as error:  # Services can be transiently unavailable.
                last_error = error
            time.sleep(interval)
        detail = f"; last error: {last_error}" if last_error else ""
        raise AssertionError(f"Timed out waiting for {description}{detail}")

    def wait_for_job(self, job_id: str, *, timeout: float = 30) -> dict[str, Any]:
        def terminal() -> dict[str, Any] | None:
            status, body = self.request("GET", f"/jobs/{job_id}")
            if status == 200 and body["status"] in {"success", "failure"}:
                return body
            return None

        return self.wait_for(f"job {job_id} to finish", terminal, timeout=timeout)

    def wait_for_api_and_worker(self) -> None:
        def api_ready() -> bool:
            status, body = self.request("GET", "/health/ready")
            return status == 200 and body["status"] == "ready"

        def worker_ready() -> bool:
            result = self.compose(
                "exec",
                "-T",
                "worker",
                "celery",
                "-A",
                "llm_metadata_harvester_service.core.celery_app.celery_app",
                "inspect",
                "ping",
                "--timeout",
                "3",
                check=False,
            )
            return result.returncode == 0 and "pong" in result.stdout

        self.wait_for("API readiness", api_ready)
        self.wait_for("worker broker reconnection", worker_ready)

    def state(self) -> dict[str, Any]:
        status, body = self.request("GET", "/state", receiver=True)
        if status != 200:
            raise AssertionError(f"receiver state returned {status}: {body}")
        return body


def _published_port(
    environment: dict[str, str], service: str, container_port: int
) -> str:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "port",
            service,
            str(container_port),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout.strip().rsplit(":", 1)[1]


@pytest.fixture(scope="session")
def stack(request: pytest.FixtureRequest) -> Generator[Stack]:
    docker = subprocess.run(
        ["docker", "info"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if docker.returncode != 0:
        pytest.skip(f"Docker daemon is unavailable: {docker.stdout.strip()}")

    environment = os.environ.copy()
    environment["COMPOSE_PROJECT_NAME"] = (
        f"harvester-it-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    before_failures = request.session.testsfailed
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--quiet"],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "up",
                "--build",
                "--detach",
                "--wait",
                "--wait-timeout",
                "120",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0:
            logs = subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "logs"],
                cwd=ROOT,
                env=environment,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            pytest.fail(f"Compose startup failed:\n{result.stdout}\n{logs.stdout}")
        yield Stack(
            environment,
            f"http://127.0.0.1:{_published_port(environment, 'api', 8000)}",
            f"http://127.0.0.1:{_published_port(environment, 'receiver', 8080)}",
        )
    finally:
        if request.session.testsfailed > before_failures:
            diagnostics = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE_FILE),
                    "logs",
                    "--no-color",
                    "--tail",
                    "300",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            print("\nIntegration Compose diagnostics:\n" + diagnostics.stdout)
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        )
