import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    ItemStore, WeatherCache, MediaCleaner, normalize_source,
    parse_multipart, sanitize_filename, deduplicate_filename,
    VALID_ITEM_TYPES,
)


class ItemStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = ItemStore(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_and_list_active_items(self):
        self.store.add_item(title="Test Bild", item_type="image",
                            source="/media/test.jpg", duration_seconds=10, sort_order=1)
        self.store.add_item(title="Web", item_type="web",
                            source="https://example.com", duration_seconds=20, sort_order=0)
        all_items = self.store.list_items()
        self.assertEqual(len(all_items), 2)

        self.store.set_active(int(all_items[1]["id"]), False)
        active_items = self.store.list_active()
        self.assertEqual(len(active_items), 1)
        self.assertEqual(active_items[0]["title"], "Web")

    def test_add_weather_item(self):
        self.store.add_item(title="Wetter", item_type="weather",
                            source="weather://current", duration_seconds=15, sort_order=0)
        items = self.store.list_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["item_type"], "weather")

    def test_toggle_weather_item(self):
        self.assertFalse(self.store.has_active_weather())
        res1 = self.store.toggle_weather_item()
        self.assertTrue(res1)
        self.assertTrue(self.store.has_active_weather())
        res2 = self.store.toggle_weather_item()
        self.assertFalse(res2)
        self.assertFalse(self.store.has_active_weather())

    def test_edit_item(self):
        self.store.add_item(title="Orig", item_type="image",
                            source="/media/a.jpg", duration_seconds=10, sort_order=0)
        items = self.store.list_items()
        item_id = int(items[0]["id"])
        self.store.edit_item(item_id, title="Updated", duration_seconds=30, sort_order=5)
        items = self.store.list_items()
        self.assertEqual(items[0]["title"], "Updated")
        self.assertEqual(int(items[0]["duration_seconds"]), 30)
        self.assertEqual(int(items[0]["sort_order"]), 5)

    def test_delete_item(self):
        self.store.add_item(title="Del", item_type="video",
                            source="/media/v.mp4", duration_seconds=10, sort_order=0)
        items = self.store.list_items()
        self.store.delete(int(items[0]["id"]))
        self.assertEqual(len(self.store.list_items()), 0)

    def test_media_tracking(self):
        self.store.track_upload("test.jpg")
        tracking = self.store.get_media_tracking()
        self.assertIn("test.jpg", tracking)
        self.assertIsNone(tracking["test.jpg"]["unreferenced_since"])

        self.store.mark_unreferenced("test.jpg")
        tracking = self.store.get_media_tracking()
        self.assertIsNotNone(tracking["test.jpg"]["unreferenced_since"])

        self.store.clear_unreferenced("test.jpg")
        tracking = self.store.get_media_tracking()
        self.assertIsNone(tracking["test.jpg"]["unreferenced_since"])

    def test_get_referenced_media(self):
        self.store.add_item(title="A", item_type="image",
                            source="/media/photo.jpg", duration_seconds=10, sort_order=0)
        self.store.add_item(title="B", item_type="web",
                            source="https://example.com", duration_seconds=10, sort_order=1)
        refs = self.store.get_referenced_media()
        self.assertIn("photo.jpg", refs)
        self.assertNotIn("example.com", refs)

    def test_reset_retention(self):
        self.store.track_upload("keep.jpg")
        self.store.mark_unreferenced("keep.jpg")
        self.store.reset_retention("keep.jpg")
        tracking = self.store.get_media_tracking()
        self.assertIsNone(tracking["keep.jpg"]["unreferenced_since"])


class ValidationTests(unittest.TestCase):
    def test_normalize_source_accepts_local_and_http(self):
        self.assertEqual(normalize_source("/media/a.png"), "/media/a.png")
        self.assertEqual(normalize_source("https://example.com"), "https://example.com")

    def test_normalize_source_accepts_weather(self):
        self.assertEqual(normalize_source("weather://current"), "weather://current")

    def test_normalize_source_rejects_invalid(self):
        with self.assertRaises(ValueError):
            normalize_source("file:///tmp/a")
        with self.assertRaises(ValueError):
            normalize_source("")
        with self.assertRaises(ValueError):
            normalize_source("   ")

    def test_valid_item_types(self):
        self.assertIn("image", VALID_ITEM_TYPES)
        self.assertIn("video", VALID_ITEM_TYPES)
        self.assertIn("web", VALID_ITEM_TYPES)
        self.assertIn("weather", VALID_ITEM_TYPES)


class MultipartTests(unittest.TestCase):
    def test_parse_simple_multipart(self):
        boundary = "----TestBoundary123"
        body = (
            f"------TestBoundary123\r\n"
            f'Content-Disposition: form-data; name="title"\r\n\r\n'
            f"Testbild\r\n"
            f"------TestBoundary123\r\n"
            f'Content-Disposition: form-data; name="file"; filename="photo.jpg"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
            f"FAKE_IMAGE_DATA\r\n"
            f"------TestBoundary123--\r\n"
        ).encode("utf-8")

        content_type = f"multipart/form-data; boundary=----TestBoundary123"
        fields, files = parse_multipart(content_type, body)

        self.assertEqual(fields["title"], "Testbild")
        self.assertIn("file", files)
        self.assertEqual(files["file"][0], "photo.jpg")
        self.assertEqual(files["file"][1], b"FAKE_IMAGE_DATA")

    def test_parse_no_boundary_raises(self):
        with self.assertRaises(ValueError):
            parse_multipart("text/plain", b"data")


class FilenameTests(unittest.TestCase):
    def test_sanitize_removes_path_separators(self):
        self.assertNotIn("/", sanitize_filename("path/to/file.jpg"))
        self.assertNotIn("\\", sanitize_filename("path\\to\\file.jpg"))

    def test_sanitize_removes_dots_at_start(self):
        result = sanitize_filename(".hidden")
        self.assertFalse(result.startswith("."))

    def test_sanitize_empty_becomes_upload(self):
        self.assertEqual(sanitize_filename(""), "upload")
        self.assertEqual(sanitize_filename("..."), "upload")

    def test_sanitize_preserves_extension(self):
        result = sanitize_filename("my photo (1).jpg")
        self.assertTrue(result.endswith(".jpg"))

    def test_deduplicate_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test.jpg").touch()
            result = deduplicate_filename(d, "test.jpg")
            self.assertEqual(result, "test_1.jpg")
            self.assertNotEqual(result, "test.jpg")

    def test_deduplicate_no_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            result = deduplicate_filename(d, "new.jpg")
            self.assertEqual(result, "new.jpg")


class WeatherCacheTests(unittest.TestCase):
    def test_cache_returns_data(self):
        cache = WeatherCache()
        # Mock the _fetch method
        cache._fetch = lambda: {"ok": True, "temperature": 22, "location": "Test"}
        result = cache.get()
        self.assertTrue(result["ok"])
        self.assertEqual(result["temperature"], 22)

    def test_cache_reuses_data(self):
        cache = WeatherCache()
        call_count = 0
        def fake_fetch():
            nonlocal call_count
            call_count += 1
            return {"ok": True, "count": call_count}
        cache._fetch = fake_fetch
        r1 = cache.get()
        r2 = cache.get()
        self.assertEqual(r1["count"], r2["count"])
        self.assertEqual(call_count, 1)  # Only fetched once


if __name__ == "__main__":
    unittest.main()
