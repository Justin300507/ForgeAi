import requests
import re

# After this many consecutive timeouts we abort remaining smoke tests.
# A hanging SQLAlchemy mapper blocks ALL endpoints, so there's no point
# waiting 3s × (N-4) more endpoints when we already know the server is stuck.
_ABORT_AFTER_CONSECUTIVE_TIMEOUTS = 4


def _build_synthetic_body(architecture, path):

    resource = path.split("?")[0].strip("/").split("/")[0].rstrip("s")
    body = {}

    for table in architecture.get("database_schema", []):
        if table.get("table_name", "").lower().rstrip("s") != resource:
            continue
        for col in table.get("columns", []):
            if col.get("is_primary_key"):
                continue
            name = col.get("name")
            col_type = (col.get("type") or "").upper()
            if "INT" in col_type:
                body[name] = 1
            elif "BOOL" in col_type:
                body[name] = True
            elif "DATE" in col_type or "TIME" in col_type:
                body[name] = "2024-01-01T00:00:00"
            else:
                body[name] = "test"

    return body


def run_endpoint_smoke_tests(architecture, base_url="http://127.0.0.1:8001", timeout=3, seed_timeout=30):

    results = []
    consecutive_timeouts = 0
    server_hanging = False

    endpoints = architecture.get("api_endpoints", [])

    for endpoint in endpoints:

        method = endpoint.get("method", "GET").upper()
        path = endpoint.get("path", "")

        # Early abort: server is clearly hanging — fill remaining without waiting
        if server_hanging:
            results.append({
                "method": method,
                "path": path,
                "status": None,
                "issue": f"Request timed out after {timeout}s — handler may be hanging or extremely slow"
            })
            continue

        url_path = re.sub(r"\{[^}]+\}", "1", path)
        url = base_url + url_path

        # /seed and /seed-data are bulk-insert helpers — skip from smoke tests entirely
        # They use bcrypt (slow), insert many rows, and are not core endpoints.
        is_seed = path.rstrip("/") in ("/seed", "/seed-data")
        if is_seed:
            continue

        try:
            eff_timeout = timeout

            if method == "GET":
                response = requests.get(url, timeout=eff_timeout)

            elif method == "DELETE":
                response = requests.delete(url, timeout=eff_timeout)

            elif method in ("POST", "PUT"):

                body = _build_synthetic_body(architecture, path)

                if method == "POST":
                    response = requests.post(url, json=body, timeout=eff_timeout)
                else:
                    response = requests.put(url, json=body, timeout=eff_timeout)

            else:
                continue

            consecutive_timeouts = 0  # reset on any response
            status = response.status_code

            if status >= 500:

                results.append({
                    "method": method,
                    "path": path,
                    "status": status,
                    "issue": f"Endpoint returned {status} — handler likely crashed",
                    "body_preview": response.text[:300]
                })

        except requests.exceptions.Timeout:

            consecutive_timeouts += 1
            results.append({
                "method": method,
                "path": path,
                "status": None,
                "issue": f"Request timed out after {eff_timeout}s — handler may be hanging or extremely slow"
            })

            if consecutive_timeouts >= _ABORT_AFTER_CONSECUTIVE_TIMEOUTS:
                print(f"  [smoke] {consecutive_timeouts} consecutive timeouts — server is hanging, aborting remaining tests")
                server_hanging = True

        except requests.exceptions.RequestException as e:

            consecutive_timeouts = 0
            results.append({
                "method": method,
                "path": path,
                "status": None,
                "issue": f"Request failed: {e}"
            })

    return results