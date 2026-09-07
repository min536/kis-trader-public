from unittest import mock

import numpy as np

from app.tools import ml_baseline_core
from app.tools import run_ml_baseline_experiment


_ML_BASELINE_CORE_NAMES = (
    "FoldData",
    "EncodedDataset",
    "PureLogisticRegression",
    "_bool_int",
    "_value_as_numeric",
    "_build_fold_ranges",
    "_filter_rows_for_label",
    "_encode_rows",
    "_split_requested_features",
    "_precision_recall_curve_area",
    "_roc_auc_score",
    "_classification_metrics",
    "_choose_threshold",
    "_summarize_importances",
    "_aggregate_feature_summary",
    "_mean_metric",
    "_std_metric",
    "_build_model",
    "_fit_and_score_fold",
    "_aggregate_results",
)


def test_ml_baseline_core_facade_bindings_are_preserved():
    for name in _ML_BASELINE_CORE_NAMES:
        assert getattr(run_ml_baseline_experiment, name) is getattr(ml_baseline_core, name)


def test_bool_numeric_fold_and_filter_helpers_direct_outputs():
    rows = [
        {
            "ts": "2026-04-01T09:00:00+09:00",
            "symbol": "B",
            "label_positive_eod_net_cost": True,
            "score_deep": 10,
            "selection_bucket": "core",
        },
        {
            "ts": "2026-04-01T09:01:00+09:00",
            "symbol": "A",
            "label_positive_eod_net_cost": False,
            "score_deep": 5,
            "selection_bucket": "rotating",
        },
        {
            "ts": "2026-04-02T09:01:00+09:00",
            "symbol": "C",
            "label_positive_eod_net_cost": None,
            "score_deep": 7,
            "selection_bucket": "core",
        },
    ]

    assert ml_baseline_core._bool_int("true") == 1
    assert ml_baseline_core._bool_int("no") == 0
    assert ml_baseline_core._bool_int("bad") is None
    assert ml_baseline_core._value_as_numeric("false") == 0.0
    assert ml_baseline_core._value_as_numeric("3.5") == 3.5
    assert ml_baseline_core._build_fold_ranges(
        ["d1", "d2", "d3", "d4", "d5"],
        train_days=2,
        validation_days=1,
        test_days=1,
        step_days=1,
    ) == [
        {"train": ["d1", "d2"], "validation": ["d3"], "test": ["d4"]},
        {"train": ["d2", "d3"], "validation": ["d4"], "test": ["d5"]},
    ]
    assert ml_baseline_core._filter_rows_for_label(
        rows,
        "label_positive_eod_net_cost",
    ) == rows[:2]
    assert ml_baseline_core._split_requested_features(
        "score_deep,selection_bucket,missing"
    ) == (["score_deep", "missing"], ["selection_bucket"])


def test_encode_rows_full_dataset_fields():
    train = [
        {
            "score_deep": 1.0,
            "passed_count_deep": 2,
            "selection_bucket": "core",
            "label_positive_eod_net_cost": False,
        },
        {
            "score_deep": 3.0,
            "passed_count_deep": 4,
            "selection_bucket": "rotating",
            "label_positive_eod_net_cost": True,
        },
    ]
    validation = [
        {
            "score_deep": 2.0,
            "passed_count_deep": 3,
            "selection_bucket": "core",
            "label_positive_eod_net_cost": True,
        }
    ]
    test = [
        {
            "score_deep": 5.0,
            "passed_count_deep": None,
            "selection_bucket": "other",
            "label_positive_eod_net_cost": False,
        }
    ]

    encoded = ml_baseline_core._encode_rows(
        train,
        validation,
        test,
        label="label_positive_eod_net_cost",
        requested_features="score_deep,passed_count_deep,selection_bucket,missing",
    )

    assert encoded.feature_names == [
        "score_deep",
        "passed_count_deep",
        "selection_bucket=core",
        "selection_bucket=rotating",
    ]
    assert encoded.used_numeric_features == ["score_deep", "passed_count_deep"]
    assert encoded.used_categorical_features == ["selection_bucket"]
    assert encoded.dropped_features == ["missing"]
    assert encoded.x_train.tolist() == [[0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    assert encoded.y_train.tolist() == [0.0, 1.0]
    assert encoded.x_validation.tolist() == [[0.0, 0.0, 1.0, 0.0]]
    assert encoded.y_validation.tolist() == [1.0]
    assert encoded.x_test.tolist() == [[0.0, 0.0, 0.0, 0.0]]
    assert encoded.y_test.tolist() == [0.0]


def test_classification_metric_helpers_full_outputs():
    y_true = np.array([1.0, 0.0, 1.0, 0.0])
    scores = np.array([0.9, 0.8, 0.4, 0.1])

    assert ml_baseline_core._precision_recall_curve_area(y_true, scores) == 0.8333333333333333
    assert ml_baseline_core._roc_auc_score(y_true, scores) == 0.75
    assert ml_baseline_core._classification_metrics(
        y_true,
        scores,
        threshold=0.5,
    ) == {
        "rows": 4,
        "positives": 2,
        "positive_rate": 0.5,
        "predicted_positive_rate": 0.5,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
        "roc_auc": 0.75,
        "pr_auc": 0.8333,
        "tp": 1,
        "fp": 1,
        "tn": 1,
        "fn": 1,
    }
    threshold, metrics = ml_baseline_core._choose_threshold(y_true, scores)
    assert float(threshold) == 0.4
    assert metrics == {
        "rows": 4,
        "positives": 2,
        "positive_rate": 0.5,
        "predicted_positive_rate": 0.75,
        "precision": 0.6667,
        "recall": 1.0,
        "f1": 0.8,
        "roc_auc": 0.75,
        "pr_auc": 0.8333,
        "tp": 2,
        "fp": 1,
        "tn": 1,
        "fn": 0,
    }


def test_importance_and_aggregate_helpers_full_outputs():
    folds = [
        {
            "precision": 0.5,
            "recall": 1.0,
            "f1": 0.6667,
            "predicted_positive_rate": 0.5,
            "positive_rate": 0.5,
            "pr_auc": 0.8333,
        },
        {
            "precision": 1.0,
            "recall": 0.5,
            "f1": 0.6667,
            "predicted_positive_rate": 0.25,
            "positive_rate": 0.5,
            "pr_auc": 0.75,
        },
    ]

    assert ml_baseline_core._summarize_importances(
        ["a", "b", "c"],
        np.array([0.2, -0.5, 0.1]),
        include_sign=True,
        top_n=2,
    ) == [
        {"feature": "b", "importance": 0.5, "signed_weight": -0.5},
        {"feature": "a", "importance": 0.2, "signed_weight": 0.2},
    ]
    assert ml_baseline_core._aggregate_feature_summary(
        [
            [
                {"feature": "a", "importance": 0.2, "signed_weight": 0.2},
                {"feature": "b", "importance": 0.4, "signed_weight": -0.4},
            ],
            [{"feature": "a", "importance": 0.6, "signed_weight": 0.6}],
        ]
    ) == [
        {
            "feature": "a",
            "mean_importance": 0.4,
            "mean_signed_weight": 0.4,
            "fold_count": 2,
        },
        {
            "feature": "b",
            "mean_importance": 0.4,
            "mean_signed_weight": -0.4,
            "fold_count": 1,
        },
    ]
    assert ml_baseline_core._mean_metric(folds, "precision") == 0.75
    assert ml_baseline_core._std_metric(folds, "precision") == 0.25
    assert ml_baseline_core._aggregate_results(
        [
            {
                "fold": "f1",
                "skipped": False,
                "test_metrics": {
                    "rows": 4,
                    "positives": 2,
                    "predicted_positive_rate": 0.5,
                    "positive_rate": 0.5,
                    "precision": 0.5,
                    "recall": 1.0,
                    "f1": 0.6667,
                    "roc_auc": 0.75,
                    "pr_auc": 0.8333,
                    "tp": 2,
                    "fp": 2,
                    "tn": 0,
                    "fn": 0,
                },
                "top_features": [
                    {"feature": "a", "importance": 0.2, "signed_weight": 0.2}
                ],
            },
            {"fold": "f2", "skipped": True, "skip_reason": "no data"},
        ],
        label="label_positive_eod_net_cost",
    ) == {
        "evaluated_folds": 1,
        "skipped_folds": 1,
        "total_test_rows": 4,
        "total_test_positives": 2,
        "class_balance": 0.5,
        "macro_average": {
            "precision": 0.5,
            "recall": 1.0,
            "f1": 0.6667,
            "roc_auc": 0.75,
            "pr_auc": 0.8333,
            "positive_prediction_rate": 0.5,
            "class_balance": 0.5,
        },
        "stability": {
            "precision_std": 0.0,
            "recall_std": 0.0,
            "f1_std": 0.0,
            "pr_auc_std": 0.0,
        },
        "micro_average": {
            "precision": 0.5,
            "recall": 1.0,
            "f1": 0.6667,
            "positive_prediction_rate": 0.5,
        },
        "top_features": [
            {
                "feature": "a",
                "mean_importance": 0.2,
                "mean_signed_weight": 0.2,
                "fold_count": 1,
            }
        ],
        "skipped_fold_reasons": [{"fold": "f2", "reason": "no data"}],
        "research_read": "too little positive coverage",
        "label_definition": (
            "True when ret_eod_bps - effective_cost_bps_used is strictly greater "
            "than the configured margin_bps."
        ),
    }


def test_build_model_fallback_and_pure_logistic_directionality():
    with mock.patch.object(ml_baseline_core, "LogisticRegression", None):
        model, backend, backend_name = ml_baseline_core._build_model("logistic")

    assert isinstance(model, ml_baseline_core.PureLogisticRegression)
    assert backend == "numpy"
    assert backend_name == "PureLogisticRegression"

    model = ml_baseline_core.PureLogisticRegression(
        learning_rate=0.3,
        max_iter=300,
        l2=0.0,
    )
    model.fit(
        np.array([[-2.0], [-1.0], [1.0], [2.0]]),
        np.array([0.0, 0.0, 1.0, 1.0]),
    )
    probabilities = model.predict_proba(np.array([[-2.0], [2.0]]))[:, 1]

    assert probabilities[0] < 0.01
    assert probabilities[1] > 0.99
