"""Page host checks; these do not validate a business backend or model."""
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from esg_app.frontend_demo import create_app


class FrontendTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def test_page_and_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("环境披露分析工作台", response.text)
        for asset in ["/static/css/style.css", "/static/js/app.js", "/static/js/demo-data.js"]:
            self.assertEqual(self.client.get(asset).status_code, 200)
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])

    def test_missing_sample_is_explicit(self):
        with TemporaryDirectory() as directory:
            client = TestClient(create_app(Path(directory) / "missing.pdf"), base_url="http://localhost")
            self.assertEqual(client.get("/demo/report-status").json(), {"available": False})
            response = client.get("/demo/report")
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(directory, response.text)

    def test_report_is_fixed_and_read_only(self):
        with TemporaryDirectory() as directory:
            sample = Path(directory) / "sample.pdf"
            sample.write_bytes(b"%PDF-1.4\nfixture")
            client = TestClient(create_app(sample), base_url="http://localhost")
            self.assertEqual(client.get("/demo/report-status").json(), {"available": True})
            self.assertEqual(client.get("/demo/report?path=/etc/passwd").content, sample.read_bytes())
            self.assertEqual(client.post("/demo/report").status_code, 405)

    def test_external_host_rejected_and_no_fake_business_api(self):
        self.assertEqual(self.client.get("/", headers={"host": "untrusted.example"}).status_code, 400)
        self.assertEqual(self.client.post("/api/tasks").status_code, 404)
        self.assertEqual(self.client.get("/health").json()["mode"], "frontend-demo")
