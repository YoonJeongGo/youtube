from pathlib import Path
import tempfile
import unittest

from PIL import Image

from asset_tools import build_review_views, normalize_asset
from episode_core import sha256_file


class AssetToolsTests(unittest.TestCase):
    def test_proportional_save_preserves_original_and_requires_visual_review(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "original.png"
            Image.new("RGB", (200, 100), "red").save(source)
            before = sha256_file(source)
            destination = base / "episode" / "a_1.png"
            record = normalize_asset(source, destination, prompt="실제 생성 프롬프트")
            self.assertEqual(before, sha256_file(source))
            self.assertEqual([1024, 512], record["contained_size"])
            self.assertEqual("letterbox", record["transform"])
            self.assertEqual("UNREVIEWED", record["review_status"])
            self.assertTrue((destination.parent / record["preserved_original"]).is_file())
            with Image.open(destination) as image:
                self.assertEqual((1024, 1536), image.size)
            with self.assertRaises(FileExistsError):
                normalize_asset(source, destination, prompt="두 번째 프롬프트")

    def test_replacements_advance_version_and_keep_prior_image(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source.png"
            destination = base / "episode" / "a.png"
            Image.new("RGB", (64, 64), "red").save(source)
            first = normalize_asset(source, destination, prompt="첫 생성")
            Image.new("RGB", (64, 64), "blue").save(source)
            with self.assertRaises(ValueError):
                normalize_asset(source, destination, prompt="잘못된 버전", replace=True)
            second = normalize_asset(source, destination, prompt="손 자세 수정", replace=True, generation_version=2)
            self.assertEqual(first["sha256"], second["replaced_sha256"])
            self.assertTrue(list((destination.parent / "revisions").glob("a_replaced_*.png")))

    def test_native_detail_views_never_claim_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.png"
            Image.new("RGB", (128, 192), "green").save(source)
            result = build_review_views(source, {"관찰한 오른손": [16, 40, 50, 90]})
            self.assertEqual("UNREVIEWED", result["status"])
            with Image.open(result["views"][1]["file"]) as crop:
                self.assertEqual((34, 50), crop.size)
            self.assertTrue(Path(result["contact_sheet"]).is_file())
            with self.assertRaises(ValueError):
                build_review_views(source, {"바깥": [-1, 0, 10, 10]})


if __name__ == "__main__":
    unittest.main()
