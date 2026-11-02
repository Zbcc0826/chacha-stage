# -*- coding: utf-8 -*-
"""不连外网的引擎自检。确认动作名单、舞台指令和密钥不会出现在公开文件里。"""

from pathlib import Path
import re
import unittest

import engine_model

ROOT = Path(__file__).resolve().parent
SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,})")
SKIP_DIRS = {
    ".git", "models", "browser-profile", "audio", "__pycache__", "node_modules",
}


class EngineTests(unittest.TestCase):
    def test_model_clips(self):
        catalog = engine_model.public_catalog()
        motions = {item["id"] for item in catalog["motions"]}
        faces = {item["id"] for item in catalog["expressions"]}
        self.assertIn("Idle", motions)
        self.assertIn("默认手切举手", motions)
        self.assertIn("脸红", faces)
        self.assertGreaterEqual(len(faces), 20)
        self.assertNotIn("dir", catalog["model"])

    def test_stage_command(self):
        stage = engine_model.clean_stage({
            "mode": "pet",
            "model": {"x": 12, "scale": 9},
            "background": {"color": "#00b140"},
            "widgets": {"hud": {"show": False, "z": 8}},
            "toggles": {"danmaku": True, "nope": True},
            "css": "javascript:alert(1)",
        })
        self.assertEqual(stage["mode"], "pet")
        self.assertEqual(stage["model"]["scale"], 4)
        self.assertEqual(stage["background"]["color"], "#00b140")
        self.assertFalse(stage["widgets"]["hud"]["show"])
        self.assertNotIn("nope", stage["toggles"])
        self.assertNotIn("css", stage)

    def test_public_tree_has_no_secrets(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in ("keys", "bili.local", "dsh.local", "models/", "github-key.txt", ".env"):
            self.assertIn(name, ignore)
        leaked = []
        ignored_names = {"keys", "bili.local", "dsh.local", "github-key.txt"}
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if path.name in ignored_names or rel.suffix == ".local":
                continue
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
                continue
            if path.suffix.lower() in {".png", ".moc3", ".mp3", ".pyc"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if SECRET.search(text):
                leaked.append(str(path.relative_to(ROOT)))
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
