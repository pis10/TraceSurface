from __future__ import annotations

import pickle
import tempfile
import unittest

from tracesurface.collection.artifacts.static_analysis import analyze_js_artifact
from tracesurface.sources import store_source


class StaticAnalysisTests(unittest.TestCase):
    def test_js_artifact_analysis_returns_serializable_facts(self) -> None:
        source = """
        const routes = [{ path: '/users', component: Users }];
        const __vite__mapDeps = ['assets/chunk-a.js'];
        import('./lazy.js');
        """
        with tempfile.TemporaryDirectory() as directory:
            ref = store_source(directory, "js", "https://example.test/app.js", source)
            result = analyze_js_artifact(
                ref,
                "https://example.test/app.js",
                "https://example.test/",
            )

        self.assertIn("/users", result.router_routes)
        self.assertIn("https://example.test/assets/chunk-a.js", result.chunk_urls)
        self.assertIn("https://example.test/lazy.js", result.chunk_urls)
        self.assertIsNotNone(result.source_scan)
        self.assertTrue(pickle.dumps(result))
