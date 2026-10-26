#!/usr/bin/env python3
"""Package the complete app as ordinary YAML; deployment needs no generator."""
from pathlib import Path
import argparse
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
NAME = "portfolio-202024191"
IMAGE = ("registry.access.redhat.com/ubi9/python-312:1-1792838979"
         "@sha256:26533737695d39d003558de435f54f52bd54879d8184a0d7038147d1ff3ea9bd")
LABELS = {"app": NAME, "app.kubernetes.io/name": "sungwook-portfolio",
          "app.kubernetes.io/instance": NAME, "app.kubernetes.io/part-of": "iot-assignment"}


def yaml_dump(value, indent=0):
    """Emit the limited YAML types used here, with JSON-escaped scalar strings."""
    pad = " " * indent
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                lines.append(yaml_dump(item, indent + 2))
            elif isinstance(item, str) and "\n" in item:
                lines.append(f"{pad}{key}: {'|' if item.endswith(chr(10)) else '|-'}")
                lines.extend(" " * (indent + 2) + line if line else "" for line in item.rstrip("\n").split("\n"))
            else:
                lines.append(f"{pad}{key}: {json.dumps(item, ensure_ascii=False)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(yaml_dump(item, indent + 2))
            else:
                lines.append(f"{pad}- {json.dumps(item, ensure_ascii=False)}")
    return "\n".join(lines)


def metadata(suffix=""):
    return {"name": NAME + suffix, "labels": dict(LABELS)}


def manifests():
    source = {"server.py": (ROOT / "app/server.py").read_text(encoding="utf-8")}
    for file in sorted((ROOT / "app/static").iterdir()):
        if file.is_file():
            source[file.name] = file.read_text(encoding="utf-8")
    assert sum(len(v.encode()) for v in source.values()) < 900_000, "ConfigMap content too large"
    settings = {"PORT": "8080", "DATABASE_PATH": "/data/guestbook.sqlite3",
                "config.json": (ROOT / "app/config.json").read_text(encoding="utf-8")}
    verifier = (ROOT / "admin-key.sha256").read_text().strip()
    assert re.fullmatch(r"[a-f0-9]{64}", verifier), "Run scripts/admin_key.py first"
    checksum = hashlib.sha256(json.dumps([source, settings, verifier], sort_keys=True).encode()).hexdigest()
    probe = lambda path, period, failures, timeout: {
        "httpGet": {"path": path, "port": "http"}, "periodSeconds": period,
        "failureThreshold": failures, "timeoutSeconds": timeout,
    }
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata(),
        "spec": {"replicas": 1, "revisionHistoryLimit": 2, "strategy": {"type": "Recreate"},
                 "selector": {"matchLabels": {"app": NAME}},
                 "template": {"metadata": {"labels": dict(LABELS), "annotations": {"portfolio/content-sha256": checksum}},
                 "spec": {"automountServiceAccountToken": False, "terminationGracePeriodSeconds": 20,
                          "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                          "containers": [{"name": "portfolio", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
                              "command": ["python3", "/app/server.py"],
                              "ports": [{"name": "http", "containerPort": 8080}],
                              "env": [
                                  {"name": key, "valueFrom": {"configMapKeyRef": {"name": NAME + "-settings", "key": key}}}
                                  for key in ["PORT", "DATABASE_PATH"]
                              ] + [{"name": "PORTFOLIO_CONFIG", "value": "/config/config.json"},
                                   {"name": "ADMIN_KEY_HASH_FILE", "value": "/run/portfolio-admin/key-sha256"},
                                   {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                                   {"name": "PYTHONUNBUFFERED", "value": "1"}],
                              "resources": {"requests": {"cpu": "50m", "memory": "64Mi", "ephemeral-storage": "16Mi"},
                                            "limits": {"cpu": "250m", "memory": "128Mi", "ephemeral-storage": "64Mi"}},
                              "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                                  "capabilities": {"drop": ["ALL"]}},
                              "startupProbe": probe("/readyz", 2, 30, 4),
                              "livenessProbe": probe("/healthz", 10, 3, 2),
                              "readinessProbe": probe("/readyz", 5, 3, 4),
                              "volumeMounts": [
                                  {"name": "app", "mountPath": "/app", "readOnly": True},
                                  {"name": "settings", "mountPath": "/config", "readOnly": True},
                                  {"name": "admin", "mountPath": "/run/portfolio-admin", "readOnly": True},
                                  {"name": "data", "mountPath": "/data"}, {"name": "tmp", "mountPath": "/tmp"}]}],
                          "volumes": [
                              {"name": "app", "configMap": {"name": NAME + "-app", "defaultMode": 292,
                                  "items": [{"key": key, "path": key if key == "server.py" else "static/" + key} for key in source]}},
                              {"name": "settings", "configMap": {"name": NAME + "-settings", "defaultMode": 292,
                                  "items": [{"key": "config.json", "path": "config.json"}]}},
                              {"name": "admin", "secret": {"secretName": NAME + "-admin", "defaultMode": 288}},
                              {"name": "data", "persistentVolumeClaim": {"claimName": NAME + "-data"}},
                              {"name": "tmp", "emptyDir": {"sizeLimit": "32Mi"}}]}}}
    }
    return {
        "configmap-app.yaml": {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata("-app"), "data": source},
        "configmap-settings.yaml": {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata("-settings"), "data": settings},
        "secret.yaml": {"apiVersion": "v1", "kind": "Secret", "metadata": metadata("-admin"), "type": "Opaque", "stringData": {"key-sha256": verifier}},
        "pvc.yaml": {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": metadata("-data"),
                     "spec": {"accessModes": ["ReadWriteOnce"], "volumeMode": "Filesystem", "resources": {"requests": {"storage": "1Gi"}}}},
        "deployment.yaml": deployment,
        "service.yaml": {"apiVersion": "v1", "kind": "Service", "metadata": metadata(),
                         "spec": {"type": "ClusterIP", "selector": {"app": NAME}, "ports": [{"name": "http", "port": 8080, "targetPort": "http", "protocol": "TCP"}]}},
        "route.yaml": {"apiVersion": "route.openshift.io/v1", "kind": "Route", "metadata": metadata(),
                       "spec": {"to": {"kind": "Service", "name": NAME}, "port": {"targetPort": "http"},
                                "tls": {"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect"}, "wildcardPolicy": "None"}},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify checked-in YAML matches app sources without writing")
    args = parser.parse_args()
    output = ROOT / "k8s"
    output.mkdir(exist_ok=True)
    mismatches = []
    for name, resource in manifests().items():
        content = "# Generated by scripts/build_manifests.py; edit app sources, then regenerate.\n" + yaml_dump(resource) + "\n"
        file = output / name
        if args.check:
            if not file.exists() or file.read_text(encoding="utf-8") != content:
                mismatches.append(name)
        else:
            file.write_text(content, encoding="utf-8")
    if mismatches:
        raise SystemExit("Stale manifest(s): " + ", ".join(mismatches))
    print("Manifest source consistency: PASS" if args.check else "Packaged 7 resources; k8s/ is ready for oc apply.")


if __name__ == "__main__":
    main()
