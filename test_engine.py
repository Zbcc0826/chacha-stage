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
        for name in ("keys", "bili.local", "dsh.local", "models/", "models.local.json", "github-key.txt", ".env"):
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

    def test_live_key_is_saved_locally_and_not_echoed(self):
        import json
        import tempfile
        import danmaku
        folder = Path(tempfile.mkdtemp())
        old = danmaku.BILI_FILE
        danmaku.BILI_FILE = folder / "bili.local"
        sample = "room-secret-value"
        try:
            danmaku.BILI_FILE.write_text("idcode=old-code\nsse=https://example.test/danmu\n", encoding="utf-8")
            danmaku.save_live_key(sample)
            text = danmaku.BILI_FILE.read_text(encoding="utf-8")
            self.assertIn("key=" + sample, text)
            self.assertNotIn("idcode=", text.lower())
            self.assertIn("sse=https://example.test/danmu", text)
            url = danmaku.stream_url(danmaku.load_bili())
            self.assertIn("IDCode=" + sample, url)
            status = danmaku.feed.status()
            self.assertNotIn(sample, json.dumps(status))
            self.assertTrue(status["configured"])
            self.assertNotIn("key", status)
            quiet = danmaku._quiet_error(RuntimeError("boom " + sample), sample)
            self.assertNotIn(sample, quiet)
            danmaku.save_live_key("")
            cleared = danmaku.BILI_FILE.read_text(encoding="utf-8")
            self.assertNotIn(sample, cleared)
            self.assertNotIn("idcode=", cleared.lower())
            self.assertFalse(danmaku.feed.status()["configured"])
        finally:
            danmaku.BILI_FILE = old
            danmaku._WAKE.set()

    def test_user_key_file_keeps_other_lines(self):
        import tempfile
        import dsh_link
        folder = Path(tempfile.mkdtemp())
        env = folder / ".env"
        env.write_text("OTHER=1\nDEEPSEEK_API_KEY=old\nDEEPSEEK_API_KEY=again\n", encoding="utf-8")
        sample = "sk-" + ("b" * 24)
        dsh_link._write_env_key(env, sample)
        text = env.read_text(encoding="utf-8")
        self.assertIn("OTHER=1", text)
        self.assertEqual(text.count("DEEPSEEK_API_KEY="), 1)
        self.assertTrue(text.strip().endswith(sample))
        with self.assertRaises(RuntimeError) as caught:
            dsh_link._clean_key("nope")
        self.assertNotIn("nope", str(caught.exception))
        self.assertNotIn("key", dsh_link.status())

    def test_freedom_model_allow(self):
        blocked = engine_model.clean_stage({"modelId": "other-model", "mode": "live"}, {"deepseek-chan"})
        self.assertNotIn("modelId", blocked)
        allowed = engine_model.clean_stage({"modelId": "deepseek-chan"}, {"deepseek-chan"})
        self.assertEqual(allowed["modelId"], "deepseek-chan")

    def test_save_user_model_respects_freedom(self):
        import tempfile
        folder = Path(tempfile.mkdtemp())
        model = folder / "Demo.model3.json"
        model.write_text(
            '{"Version":3,"FileReferences":{"Moc":"Demo.moc3"}}',
            encoding="utf-8",
        )
        old = engine_model.LIBRARY
        engine_model.LIBRARY = folder / "library.json"
        try:
            saved = engine_model.save_model(model, "演示", False)
            self.assertFalse(saved["freedom"])
            blocked = engine_model.clean_stage({"modelId": saved["id"]}, engine_model.freedom_ids())
            self.assertNotIn("modelId", blocked or {})
            engine_model.set_freedom(saved["id"], True)
            self.assertIn(saved["id"], engine_model.freedom_ids())
            opened = engine_model.clean_stage({"modelId": saved["id"]}, engine_model.freedom_ids())
            self.assertEqual(opened["modelId"], saved["id"])
            engine_model.remove_model(saved["id"])
            self.assertNotIn(saved["id"], {item["id"] for item in engine_model.load_library()})
        finally:
            engine_model.LIBRARY = old


class ShareTests(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        server._LOOK_REV = 0
        server._LOOK = None
        server._MIC_REV = 0
        server._MIC = {"want": False, "hold": [], "owner": "", "ownerAt": 0.0}

    def test_same_look_keeps_its_number(self):
        body = {
            "modelId": "deepseek-chan",
            "zoom": 9,
            "x": 40,
            "y": -30,
            "toggles": {"physics": True, "breath": False, "sway": False, "blink": False, "mouse": False, "ahoge": False},
            "pose": {"ParamAngleZ": 3},
        }
        first = self.server.post_share({"look": body})
        rev = first["look"]["rev"]
        self.assertEqual(first["look"]["zoom"], 4)
        again = self.server.post_share({"look": body})
        self.assertEqual(again["look"]["rev"], rev)
        self.assertEqual(again["look"]["x"], 40)
        seen = self.server.read_share(rev, 0)
        self.assertIsNone(seen["look"])

    def test_pet_keeps_the_mic_until_it_goes_quiet(self):
        import time
        first = self.server.post_share({"mic": {"want": True, "hold": ["F1"], "owner": "pet"}})
        self.assertEqual(first["mic"]["owner"], "pet")
        stolen = self.server.post_share({"mic": {"owner": "web"}})
        self.assertEqual(stolen["mic"]["owner"], "pet")
        self.assertEqual(stolen["mic"]["rev"], first["mic"]["rev"])
        self.server._MIC["ownerAt"] = time.time() - 5
        heard = self.server.read_share(0, first["mic"]["rev"])
        self.assertEqual(heard["mic"]["owner"], "web")
        self.assertEqual(heard["mic"]["hold"], ["F1"])
        self.assertTrue(heard["mic"]["want"])

    def test_pet_heartbeat_does_not_renumber(self):
        first = self.server.post_share({"mic": {"owner": "pet", "want": False, "hold": ["F1"]}})
        again = self.server.post_share({"mic": {"owner": "pet"}})
        self.assertEqual(again["mic"]["rev"], first["mic"]["rev"])


class TalkHookTests(unittest.TestCase):
    def setUp(self):
        import pet_window as pet
        self.pet = pet
        self.old_notify = pet._notify_talk
        self.old_vk = pet._vk_from_hook
        self.old_thread = pet._PAGE_THREAD
        pet._TALK_KEYS = []
        pet._TALK_DOWN = False
        pet._TALK_HELD.clear()
        pet._TALK_CAPTURE = False
        pet._PAGE_QUEUE.clear()

    def tearDown(self):
        self.pet._notify_talk = self.old_notify
        self.pet._vk_from_hook = self.old_vk
        self.pet._PAGE_THREAD = self.old_thread
        self.pet._PAGE_QUEUE.clear()
        self.pet._TALK_KEYS = []
        self.pet._TALK_DOWN = False
        self.pet._TALK_HELD.clear()

    def test_hold_notifies_press_once_then_release(self):
        notes = []
        self.pet._notify_talk = lambda window, held: notes.append(bool(held))
        self.pet._TALK_KEYS = ["F1"]
        self.pet._vk_from_hook = lambda lparam: 0x70
        self.pet._on_talk_key(None, 0, 0x0100, 1)
        self.pet._on_talk_key(None, 0, 0x0100, 1)
        self.pet._on_talk_key(None, 0, 0x0101, 1)
        self.assertEqual(notes, [True, False])

    def test_quick_tap_keeps_only_the_latest_notice(self):
        class Alive:
            def is_alive(self):
                return True

        self.pet._PAGE_THREAD = Alive()
        self.pet._notify_talk(object(), True)
        self.pet._notify_talk(object(), False)
        self.assertEqual(len(self.pet._PAGE_QUEUE), 1)
        self.assertIn("false", self.pet._PAGE_QUEUE[0][1])


class CueTests(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self.old_seq = server._ACT_SEQ
        self.old_body = server._ACT_BODY
        self.old_acts = list(server._ACTS)
        server._ACT_SEQ = 0
        server._ACT_BODY = None
        server._ACTS = []

    def tearDown(self):
        self.server._ACT_SEQ = self.old_seq
        self.server._ACT_BODY = self.old_body
        self.server._ACTS = self.old_acts

    def test_unread_lines_stay_in_order(self):
        first = self.server.publish_act({"say": "第一句"})
        second = self.server.publish_act({"say": "第二句"})
        got = self.server.read_act(0)
        self.assertEqual([item["say"] for item in got["batch"]], ["第一句", "第二句"])
        self.assertEqual(got["actId"], second["actId"])
        idle = self.server.read_act(second["actId"])
        self.assertTrue(idle.get("idle"))
        self.assertEqual(first["actId"], 1)


if __name__ == "__main__":
    unittest.main()
