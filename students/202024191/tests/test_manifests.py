from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_manifests as build


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.resources = build.manifests()

    def test_checked_in_yaml_matches_source(self):
        subprocess.run([sys.executable, str(ROOT / "scripts/build_manifests.py"), "--check"], check=True, capture_output=True)

    def test_all_references_and_ports_are_consistent(self):
        dep = self.resources["deployment.yaml"]["spec"]
        pod = dep["template"]["spec"]
        c = pod["containers"][0]
        labels = dep["template"]["metadata"]["labels"]
        for k, v in self.resources["service.yaml"]["spec"]["selector"].items():
            self.assertEqual(labels[k], v)
        self.assertEqual(self.resources["service.yaml"]["spec"]["ports"][0]["targetPort"], c["ports"][0]["name"])
        self.assertEqual(self.resources["route.yaml"]["spec"]["to"]["name"], self.resources["service.yaml"]["metadata"]["name"])
        names = {r["metadata"]["name"] for r in self.resources.values()}
        for volume in pod["volumes"]:
            for kind, key in [("configMap", "name"), ("secret", "secretName"), ("persistentVolumeClaim", "claimName")]:
                if kind in volume:
                    self.assertIn(volume[kind][key], names)

    def test_resources_security_and_storage_strategy(self):
        spec = self.resources["deployment.yaml"]["spec"]
        pod = spec["template"]["spec"]
        c = pod["containers"][0]
        self.assertEqual(spec["strategy"]["type"], "Recreate")
        self.assertEqual(spec["replicas"], 1)
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertNotIn("runAsUser", pod["securityContext"])
        self.assertFalse(c["securityContext"]["allowPrivilegeEscalation"])
        for p in ["startupProbe", "readinessProbe", "livenessProbe"]:
            self.assertIn(p, c)
        self.assertIn("requests", c["resources"]); self.assertIn("limits", c["resources"])
        self.assertIn("@sha256:", c["image"])
        self.assertNotIn("storageClassName", self.resources["pvc.yaml"]["spec"])

    def test_no_environment_bound_or_generated_fields(self):
        forbidden = {"namespace", "uid", "resourceVersion", "creationTimestamp", "managedFields", "status", "clusterIP", "host"}
        def walk(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for key, child in value.items():
                    if key not in {"data", "stringData"}: walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        for resource in self.resources.values(): walk(resource)

    def test_packaged_application_is_complete_and_bounded(self):
        data = self.resources["configmap-app.yaml"]["data"]
        for name in ["server.py", "index.html", "styles.css", "app.js", "admin.html", "admin.js", "favicon.svg"]:
            self.assertIn(name, data)
        self.assertLess(len(json.dumps(data).encode()), 900_000)
        self.assertEqual(len(self.resources["secret.yaml"]["stringData"]["key-sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
