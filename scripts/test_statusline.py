import json, os, tempfile, unittest
import statusline as sl


class TestStatusline(unittest.TestCase):
    def test_write_mirror_roundtrips(self):
        d = tempfile.mkdtemp()
        dest = os.path.join(d, "sub", "mirror.json")
        sl.write_mirror('{"a":1}', dest)
        self.assertTrue(os.path.exists(dest))
        with open(dest) as f:
            self.assertEqual(json.load(f), {"a": 1})

    def test_format_status_reads_model_and_five_hour(self):
        raw = json.dumps({"model": {"display_name": "Opus"},
                          "rate_limits": {"five_hour": {"used_percentage": 42}}})
        self.assertEqual(sl.format_status(raw), "Opus · 5h 42%")

    def test_format_status_tolerates_garbage(self):
        self.assertEqual(sl.format_status("not json"), "Claude")

    def test_format_status_tolerates_nonobject_json(self):
        # valid JSON that isn't an object (no .get) must still degrade to "Claude"
        self.assertEqual(sl.format_status("42"), "Claude")


if __name__ == "__main__":
    unittest.main()
