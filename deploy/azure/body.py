"""Emit an ARM request body as JSON on stdout: `python3 body.py env|seed|app`.

This lives outside deploy.sh because the app body carries four API keys, and
hand-quoting a JSON document in bash is how a key ends up either mangled or in a
process listing. json.dumps escapes correctly by construction, and reading the
values from the environment keeps them off every command line.
"""

from __future__ import annotations

import json
import os
import sys

# name of the Container Apps secret -> environment variable holding its value.
# The container sees the env var name; the value only ever lives in the secret.
SECRETS = {
    "database-url": "DATABASE_URL",
    "azure-openai-key": "AZURE_OPENAI_API_KEY",
    "groq-key": "GROQ_API_KEY",
    "langchain-key": "LANGCHAIN_API_KEY",
}

# Non-secret settings. DAILY_TOKEN_BUDGET is deliberately not here: production
# takes budget.py's 150,000 default and .env raises it for development days.
PLAIN = {
    "LLM_PROVIDER": ("LLM_PROVIDER", "azure"),
    "AZURE_OPENAI_ENDPOINT": ("AZURE_OPENAI_ENDPOINT", None),
    "AZURE_OPENAI_DEPLOYMENT": ("AZURE_OPENAI_DEPLOYMENT", None),
    "AZURE_OPENAI_API_VERSION": ("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    "LANGCHAIN_TRACING_V2": ("LANGCHAIN_TRACING_V2", "true"),
    "LANGCHAIN_PROJECT": ("LANGCHAIN_PROJECT", "myinvois-compliance-copilot"),
}


def env_id() -> str:
    """Built here, not in the shell. Git Bash rewrites any argument that looks
    like a POSIX path, so an ARM id passed through bash arrives as
    C:/Program Files/Git/subscriptions/... and ARM rejects it."""
    return (f"/subscriptions/{os.environ['SUB']}/resourceGroups/{os.environ['RG']}"
            f"/providers/Microsoft.App/managedEnvironments/{os.environ['ENV_NAME']}")


def env_body() -> dict:
    return {
        "location": os.environ["LOC"],
        "properties": {
            "appLogsConfiguration": {
                "destination": "log-analytics",
                "logAnalyticsConfiguration": {
                    "customerId": os.environ["WS_ID"],
                    "sharedKey": os.environ["WS_KEY"],
                },
            }
        },
    }


def seed_body() -> dict:
    """Minimal app on a public image, only so it has an identity to authorise."""
    return {
        "location": os.environ["LOC"],
        "identity": {"type": "SystemAssigned"},
        "properties": {
            "managedEnvironmentId": env_id(),
            "configuration": {"ingress": {"external": True, "targetPort": 8000}},
            "template": {
                "containers": [{"name": "api", "image": os.environ["SEED_IMAGE"]}],
                "scale": {"minReplicas": 0, "maxReplicas": 1},
            },
        },
    }


def app_body() -> dict:
    plain = {k: os.environ[v] if d is None else os.environ.get(v, d)
             for k, (v, d) in PLAIN.items()}
    return {
        "location": os.environ["LOC"],
        "identity": {"type": "SystemAssigned"},
        "properties": {
            "managedEnvironmentId": env_id(),
            "configuration": {
                "ingress": {"external": True, "targetPort": 8000, "transport": "auto"},
                # identity "system" = pull with the app's own managed identity,
                # so no registry username or password is stored anywhere.
                "registries": [{"server": os.environ["ACR_SERVER"], "identity": "system"}],
                "secrets": [{"name": n, "value": os.environ[v]} for n, v in SECRETS.items()],
                "activeRevisionsMode": "Single",
            },
            "template": {
                "containers": [{
                    "name": "api",
                    "image": os.environ["IMAGE"],
                    "resources": {"cpu": 1.0, "memory": "2Gi"},
                    "env": [{"name": v, "secretRef": n} for n, v in SECRETS.items()]
                    + [{"name": k, "value": v} for k, v in plain.items()],
                }],
                "scale": {"minReplicas": 0, "maxReplicas": 1},
            },
        },
    }


if __name__ == "__main__":
    print(json.dumps({"env": env_body, "seed": seed_body, "app": app_body}[sys.argv[1]]()))
