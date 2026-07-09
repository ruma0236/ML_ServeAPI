from __future__ import annotations

from evm.core.image_feature_model import (
    FEATURE_NAMES,
    extract_image_features,
    predict_with_model,
    train_centroid_classifier,
)


def test_extract_image_features_reads_png_dimensions(tmp_path):
    image_path = tmp_path / "sample.png"
    png_header = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
    png_header += (32).to_bytes(4, "big") + (16).to_bytes(4, "big")
    image_path.write_bytes(png_header + bytes(range(128)))

    features = extract_image_features(image_path, sample_bytes=128)

    assert features["width"] == 32
    assert features["height"] == 16
    assert features["aspect_ratio"] == 2.0
    assert set(FEATURE_NAMES).issubset(features.keys())


def test_train_centroid_classifier_predicts_from_features():
    normal = {name: 0.0 for name in FEATURE_NAMES}
    anomaly = {name: 1.0 for name in FEATURE_NAMES}
    rows = [
        {"sample_id": "n1", "split": "train", "label": "normal", "features": normal},
        {"sample_id": "n2", "split": "test", "label": "normal", "features": normal},
        {"sample_id": "a1", "split": "train", "label": "anomaly", "features": anomaly},
        {"sample_id": "a2", "split": "test", "label": "anomaly", "features": anomaly},
    ]

    model = train_centroid_classifier(
        rows,
        model_name="vision-baseline",
        dataset_metadata={"dataset_version": "unit-test"},
    )
    prediction = predict_with_model(model, anomaly)

    assert model["model_type"] == "image_feature_centroid"
    assert model["evaluation"]["selected_split"] == "test"
    assert model["metrics"]["accuracy"] == 1.0
    assert prediction["prediction"] == "anomaly"
