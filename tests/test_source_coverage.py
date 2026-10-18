"""Offline release regression: every offered model can reach generation."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from engine import EngineError, MarketingEngine
from public_evidence import build_public_evidence, supported_products


ROOT = Path(__file__).resolve().parents[1]
DIMENSION_CONFLICT_MODELS = {
    "HUAWEI MateBook D 14 2024",
    "HUAWEI MateBook D 16 2024",
    "HUAWEI MateBook D 16 SE 版 2024",
    "HUAWEI MateBook X Pro 酷睿 Ultra 微绒典藏版",
}


class SourceCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="zhi-hua-source-test-")
        cls.environment = patch.dict(os.environ, {"ZHIHUA_RUNTIME_DIR": cls.temporary.name})
        cls.environment.start()
        cls.cache = json.loads((ROOT / "data/ima_verified_cache.json").read_text(encoding="utf-8"))["products"]
        cls.references = json.loads((ROOT / "data/public_evidence.json").read_text(encoding="utf-8"))["products"]
        # Parse the public choices without importing FastAPI or starting a server.
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        choices = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(getattr(target, "id", "") == "PRODUCTS" for target in node.targets))
        cls.names = [item["name"] for item in choices]
        for materials in cls.cache.values():
            for image in materials["images"]:
                source = ROOT / "data/ima_assets" / image["asset_filename"]
                destination = Path(cls.temporary.name) / source.name
                shutil.copyfile(source, destination)
                image["local_path"] = str(destination)

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.temporary.cleanup()

    def test_all_ten_catalog_models_pass_public_evidence_gate(self):
        self.assertEqual(len(self.names), 10)
        self.assertEqual(set(self.names), set(self.cache))
        self.assertEqual(set(self.names), set(supported_products()))
        self.assertEqual(sum(len(entry["sections"]) for entry in self.references), 141)
        for name in self.names:
            with self.subTest(product=name):
                material = copy.deepcopy(self.cache[name])
                for image in material["images"]:
                    asset = Path(image["local_path"])
                    self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), asset.stem)
                result = build_public_evidence(name, material)
                self.assertEqual(result["public_evidence"]["unmatched_reference_count"], 0)
                self.assertEqual(len(result["images"]), 3)
                self.assertEqual(result["public_evidence"]["excluded_image_count"], 0)
                if name in DIMENSION_CONFLICT_MODELS:
                    self.assertNotIn("## 尺寸与重量\n", result["documents"][0]["text"])
                    self.assertTrue(any("尺寸标签存在冲突" in note for note in result["warnings"]))
                    # Preserve the original retrieved material for auditability.
                    self.assertIn("## 尺寸与重量\n", material["documents"][0]["text"])

    def test_all_ten_models_reach_mocked_model_boundary_without_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network is forbidden in offline tests")) as network:
            for name in self.names:
                with self.subTest(product=name):
                    engine = MarketingEngine()
                    engine.url = "https://example.invalid/chat/completions"
                    engine.key = "offline-placeholder"
                    engine.model = "offline-mock"
                    captured = []

                    def intercept(instruction, data, max_tokens=4500):
                        captured.append(data)
                        raise EngineError("offline-model-boundary-reached")

                    engine._chat = intercept
                    result = engine.generate(
                        {"product": name, "audience": "测试受众", "scene": "门店到访", "platform": "门店海报", "brief": ""},
                        copy.deepcopy(self.cache[name]),
                    )
                    self.assertEqual(len(captured), 1, result.get("message"))
                    self.assertEqual(result["message"], "offline-model-boundary-reached")
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
