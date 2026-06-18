import subprocess
import time
import sys
from pathlib import Path

import requests

from app.runtime.runtime_models import RuntimeResult


class BackendRunner:

    def run(self, backend_path: str) -> RuntimeResult:

        backend_dir = Path(backend_path)

        if not backend_dir.exists():

            raise Exception(
                f"Project path does not exist: {backend_path}"
            )

        start_time = time.time()

        print(
            f"Running runtime validation in: {backend_dir}"
        )

        print(
            f"Using Python: {sys.executable}"
        )

        requirements_file = (
            backend_dir / "app" / "requirements.txt"
        )

        if requirements_file.exists():

            print(
                f"Installing requirements from: {requirements_file}"
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(requirements_file)
                ],
                cwd=backend_dir,
                capture_output=True,
                text=True
            )

            print(
                "Requirements installation complete"
            )

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8001"
            ],
            cwd=backend_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        time.sleep(5)

        if process.poll() is None:

            try:

                response = requests.get(
                    "http://127.0.0.1:8001/docs",
                    timeout=5
                )

                healthy = (
                    response.status_code == 200
                )

                print(
                    f"Health Check Status: {response.status_code}"
                )

            except Exception as e:

                print(
                    f"Health Check Failed: {e}"
                )

                healthy = False

            process.terminate()

            stdout, stderr = process.communicate(
                timeout=5
            )

            if not healthy:

                return RuntimeResult(
                    success=False,
                    exit_code=1,
                    stdout=stdout,
                    stderr=(
                        stderr
                        + "\nHealth Check Failed"
                    ),
                    startup_time=time.time() - start_time
                )

            if "Traceback" in stderr:

                return RuntimeResult(
                    success=False,
                    exit_code=1,
                    stdout=stdout,
                    stderr=stderr,
                    startup_time=time.time() - start_time
                )

            return RuntimeResult(
                success=True,
                exit_code=0,
                stdout=stdout,
                stderr=stderr,
                startup_time=time.time() - start_time
            )

        stdout, stderr = process.communicate()

        return RuntimeResult(
            success=False,
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            startup_time=time.time() - start_time
        )