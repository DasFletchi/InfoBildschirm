import tempfile
import unittest
from pathlib import Path

from app import ItemStore, normalize_source


class ItemStoreTests(unittest.TestCase):
    def test_add_and_list_active_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            store = ItemStore(db_path)

            store.add_item(
                title="Test Bild",
                item_type="image",
                source="/media/test.jpg",
                duration_seconds=10,
                sort_order=1,
            )
            store.add_item(
                title="Web",
                item_type="web",
                source="https://example.com",
                duration_seconds=20,
                sort_order=0,
            )
            all_items = store.list_items()
            self.assertEqual(len(all_items), 2)

            store.set_active(int(all_items[1]["id"]), False)
            active_items = store.list_active()
            self.assertEqual(len(active_items), 1)
            self.assertEqual(active_items[0]["title"], "Web")


class ValidationTests(unittest.TestCase):
    def test_normalize_source_accepts_local_and_http(self):
        self.assertEqual(normalize_source("/media/a.png"), "/media/a.png")
        self.assertEqual(normalize_source("https://example.com"), "https://example.com")

    def test_normalize_source_rejects_invalid(self):
        with self.assertRaises(ValueError):
            normalize_source("file:///tmp/a")


if __name__ == "__main__":
    unittest.main()
