import base64
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from captcha_training import (
    build_model,
    decode_image_data_url,
    import_labeled_batch,
    normalize_digits,
    predict_batch,
    read_labels,
    save_unlabeled_batch_sample,
    save_labeled_sample,
    validate_dataset,
)


class ModelArchitectureTest(unittest.TestCase):
    def test_uses_one_shared_classifier_for_all_digit_positions(self):
        model = build_model(sequence_length=4)

        self.assertEqual(model.output_shape, (None, 4, 10))
        self.assertEqual(model.get_layer("foreground_contrast").get_config()["scale"], -1.0)
        self.assertEqual(
            model.get_layer("position_features").output.shape,
            (None, 4, 50, 40, 3),
        )
        self.assertEqual(
            model.get_layer("shared_digit_classifier").output.shape,
            (None, 4, 10),
        )
        self.assertLess(model.count_params(), 70_000)


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

    def test_predicts_batch_into_third_column_and_reports_exact_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch_dir = Path(temporary) / "batch"
            save_unlabeled_batch_sample(batch_dir, b"first", 1)
            save_unlabeled_batch_sample(batch_dir, b"second", 2)
            labels_path = batch_dir / "labels.csv"
            with labels_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["filename", "label"])
                writer.writeheader()
                writer.writerow({"filename": "images/captcha-000001.png", "label": "۱۲۳۴۵"})
                writer.writerow({"filename": "images/captcha-000002.png", "label": "67890"})

            model_bundle = object()
            with (
                patch("captcha_training.load_model", return_value=model_bundle) as load,
                patch(
                    "captcha_training.predict_image",
                    side_effect=[("12345", 0.9), ("67891", 0.8)],
                ) as predict,
            ):
                result = predict_batch(batch_dir, Path(temporary) / "model")

            load.assert_called_once_with(Path(temporary) / "model")
            self.assertEqual(
                [call.args[0] for call in predict.call_args_list],
                [b"first", b"second"],
            )
            with labels_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, ["filename", "label", "prediction"])
                rows = list(reader)
            self.assertEqual([row["prediction"] for row in rows], ["12345", "67891"])
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["matches"], 1)
            self.assertEqual(result["evaluated"], 2)
            self.assertEqual(result["exact_match"], 0.5)

    def test_batch_prediction_failure_preserves_original_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch_dir = Path(temporary) / "batch"
            save_unlabeled_batch_sample(batch_dir, b"first", 1)
            save_unlabeled_batch_sample(batch_dir, b"second", 2)
            labels_path = batch_dir / "labels.csv"
            original = labels_path.read_bytes()

            with (
                patch("captcha_training.load_model", return_value=object()),
                patch(
                    "captcha_training.predict_image",
                    side_effect=[("12345", 0.9), RuntimeError("prediction failed")],
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "prediction failed"):
                    predict_batch(batch_dir, Path(temporary) / "model")

            self.assertEqual(labels_path.read_bytes(), original)
            self.assertFalse(labels_path.with_suffix(".tmp").exists())


if __name__ == "__main__":
    unittest.main()
