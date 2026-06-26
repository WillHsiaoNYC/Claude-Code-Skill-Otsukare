import os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import install as inst


class TestInstall(unittest.TestCase):
    def test_copies_skill_and_scripts(self):
        src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
        dest = os.path.join(tempfile.mkdtemp(), "otsukare")
        inst.install(src, dest)
        self.assertTrue(os.path.exists(os.path.join(dest, "SKILL.md")))
        self.assertTrue(os.path.exists(
            os.path.join(dest, "scripts", "otsukare_usage.py")))

    def test_reinstall_over_existing_dir_ok(self):
        src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dest = os.path.join(tempfile.mkdtemp(), "otsukare")
        inst.install(src, dest)
        inst.install(src, dest)   # must not raise (dirs_exist_ok)
        self.assertTrue(os.path.exists(os.path.join(dest, "scripts")))


if __name__ == "__main__":
    unittest.main()
