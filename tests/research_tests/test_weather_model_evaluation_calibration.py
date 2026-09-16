import numpy as np

from weather_model_evaluation import (
    fit_simplex_logit_calibrator,
    predict_simplex_logit_calibrator,
    simplex_log_ratio_features,
)


def test_simplex_log_ratio_features_include_context_interactions() -> None:
    q = np.array([[0.2, 0.3, 0.5], [0.5, 0.4, 0.1]])
    context = np.array([[0.0], [1.0]])
    features = simplex_log_ratio_features(q, context=context)
    assert features.shape == (2, 5)
    assert np.allclose(features[0, 3:], 0.0)
    assert np.allclose(features[1, 3:], features[1, :2])


def test_multiclass_calibrator_returns_valid_simplex() -> None:
    rng = np.random.default_rng(7)
    raw = rng.dirichlet([2, 2, 2], size=90)
    labels = np.tile(np.arange(3), 30)
    context = np.tile([0.0, 1.0], 45)[:, None]
    calibrator = fit_simplex_logit_calibrator(
        raw,
        labels,
        context=context,
        sample_weight=np.ones(len(raw)),
    )
    predicted = predict_simplex_logit_calibrator(
        calibrator, raw[:7], context=context[:7]
    )
    assert predicted.shape == (7, 3)
    assert np.all(predicted > 0)
    assert np.allclose(predicted.sum(axis=1), 1.0)
