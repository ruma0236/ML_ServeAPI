from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api import efficientnet_serving


def test_sha256_file_uses_artifact_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"real-model-artifact")

    assert efficientnet_serving.sha256_file(artifact) == hashlib.sha256(
        b"real-model-artifact"
    ).hexdigest()


def test_readiness_fails_closed_when_model_is_not_loaded(monkeypatch) -> None:
    monkeypatch.setattr(efficientnet_serving, "MODEL_RUNTIME", None)
    monkeypatch.setattr(efficientnet_serving, "MODEL_LOAD_ERROR", "gpu_not_available")
    monkeypatch.setattr(
        efficientnet_serving,
        "refresh_model",
        lambda: (_ for _ in ()).throw(AssertionError("readiness must not reload the model")),
    )

    response = efficientnet_serving.ready()

    assert response.status_code == 503
    assert b'"status":"blocked"' in response.body
    assert b'"gpu_not_available"' in response.body


def test_prediction_uses_the_checkpoint_anomaly_threshold() -> None:
    scores = {"anomaly": 0.61, "normal": 0.39}

    assert efficientnet_serving.prediction_for_scores(scores, 0.7) == ("normal", 0.39)
    assert efficientnet_serving.prediction_for_scores(scores, 0.6) == ("anomaly", 0.61)
