import base64
import csv
import tempfile
import unittest
from pathlib import Path

from captcha_training import (
    decode_image_data_url,
    import_labeled_batch,
    normalize_digits,
    read_labels,
    save_unlabeled_batch_sample,
    save_labeled_sample,
    validate_dataset,
)


class DigitNormalizationTest(unittest.TestCase):
    def test_normalizes_persian_arabic_and_ascii_digits(self):
        self.assertEqual(normalize_digits("۰١2۳"), "0123")

    def test_rejects_non_digits(self):
        with self.assertRaises(ValueError):
            normalize_digits("۱۲A۴")


class DataUrlTest(unittest.TestCase):
    def test_decodes_image_data_url(self):
        payload = base64.b64encode(b"image-bytes").decode("ascii")
        self.assertEqual(
            decode_image_data_url(f"data:image/png;base64,{payload}"),
            b"image-bytes",
        )

    def test_rejects_non_image_data_url(self):
        with self.assertRaises(ValueError):
            decode_image_data_url("https://example.test/captcha.png")


class DatasetTest(unittest.TestCase):
    def test_saves_image_and_normalized_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            dataset_dir = Path(temporary)
            image_path = save_labeled_sample(
                dataset_dir, b"png", "۱۲۳۴۵", expected_length=5
            )

            self.assertEqual(image_path.read_bytes(), b"png")
            self.assertEqual(read_labels(dataset_dir), [(f"images/{image_path.name}", "12345")])
            samples, length = validate_dataset(dataset_dir, expected_length=5)
            self.assertEqual(samples, [(image_path, "12345")])
            self.assertEqual(length, 5)

    def test_rejects_wrong_label_length_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            dataset_dir = Path(temporary)
            with self.assertRaises(ValueError):
                save_labeled_sample(dataset_dir, b"png", "۱۲۳", expected_length=5)
            self.assertEqual(read_labels(dataset_dir), [])

    def test_imports_completed_excel_editable_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_dir = root / "batch"
            dataset_dir = root / "dataset"
            save_unlabeled_batch_sample(batch_dir, b"first", 1)
            save_unlabeled_batch_sample(batch_dir, b"second", 2)
            labels_path = batch_dir / "labels.csv"
            with labels_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["filename", "label"])
                writer.writeheader()
                writer.writerow({"filename": "images/captcha-000001.png", "label": "۱۲۳۴۵"})
                writer.writerow({"filename": "images/captcha-000002.png", "label": "67890"})

            self.assertEqual(import_labeled_batch(batch_dir, dataset_dir, 5), 2)
            rows = read_labels(dataset_dir)
            self.assertEqual([label for _, label in rows], ["12345", "67890"])

    def test_rejects_incomplete_batch_before_changing_dataset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_dir = root / "batch"
            dataset_dir = root / "dataset"
            save_unlabeled_batch_sample(batch_dir, b"image", 1)

            with self.assertRaisesRegex(ValueError, "ردیف 2"):
                import_labeled_batch(batch_dir, dataset_dir, 5)
            self.assertEqual(read_labels(dataset_dir), [])


if __name__ == "__main__":
    unittest.main()
