"""Pure ML baseline modelling core."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

import numpy as np

from app.tools.ml_research_common import (
    LABEL_DEFINITIONS,
    ML_SAFE_CATEGORICAL_FEATURE_COLS,
    ML_SAFE_NUMERIC_FEATURE_COLS,
    normalize_null,
    parse_bool,
    parse_float,
)

try:  # pragma: no cover - optional dependency
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
except Exception:  # pragma: no cover - local env dependent
    RandomForestClassifier = None
    LogisticRegression = None


DEFAULT_THRESHOLD_GRID: tuple[float, ...] = tuple(round(v, 2) for v in np.linspace(0.1, 0.9, 17))


@dataclass
class FoldData:
    fold_name: str
    train_rows: list[dict[str, Any]]
    validation_rows: list[dict[str, Any]]
    test_rows: list[dict[str, Any]]
    train_dates: list[str]
    validation_dates: list[str]
    test_dates: list[str]


@dataclass
class EncodedDataset:
    x_train: np.ndarray
    y_train: np.ndarray
    x_validation: np.ndarray
    y_validation: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray
    feature_names: list[str]
    used_numeric_features: list[str]
    used_categorical_features: list[str]
    dropped_features: list[str]


class PureLogisticRegression:
    """Small numpy-only logistic regression fallback with class balancing."""

    def __init__(
        self,
        *,
        learning_rate: float = 0.2,
        max_iter: int = 500,
        l2: float = 0.01,
    ) -> None:
        self.learning_rate = learning_rate
        self.max_iter = max_iter
        self.l2 = l2
        self.coef_: np.ndarray | None = None
        self.intercept_: float = 0.0

    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        clipped = np.clip(z, -35.0, 35.0)
        return 1.0 / (1.0 + np.exp(-clipped))

    def fit(self, x: np.ndarray, y: np.ndarray) -> "PureLogisticRegression":
        n_rows, n_features = x.shape
        positives = float(np.sum(y))
        negatives = float(n_rows - positives)
        if positives <= 0 or negatives <= 0:
            raise ValueError("PureLogisticRegression requires both positive and negative classes.")

        self.coef_ = np.zeros(n_features, dtype=float)
        positive_rate = np.clip(positives / max(n_rows, 1), 1e-4, 1 - 1e-4)
        self.intercept_ = float(math.log(positive_rate / (1.0 - positive_rate)))

        sample_weights = np.where(
            y > 0.5,
            n_rows / (2.0 * positives),
            n_rows / (2.0 * negatives),
        )

        for _ in range(self.max_iter):
            logits = x @ self.coef_ + self.intercept_
            probs = self._sigmoid(logits)
            errors = (probs - y) * sample_weights
            grad_coef = (x.T @ errors) / n_rows + self.l2 * self.coef_
            grad_intercept = float(np.mean(errors))
            self.coef_ -= self.learning_rate * grad_coef
            self.intercept_ -= self.learning_rate * grad_intercept
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if self.coef_ is None:
            raise RuntimeError("Model has not been fit yet.")
        logits = x @ self.coef_ + self.intercept_
        probs = self._sigmoid(logits)
        return np.column_stack([1.0 - probs, probs])


def _bool_int(value: Any) -> int | None:
    parsed = parse_bool(value)
    if parsed is None:
        return None
    return 1 if parsed else 0


def _value_as_numeric(value: Any) -> float | None:
    bool_value = _bool_int(value)
    if bool_value is not None:
        return float(bool_value)
    return parse_float(value)


def _build_fold_ranges(
    unique_dates: list[str],
    *,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
) -> list[dict[str, list[str]]]:
    span = train_days + validation_days + test_days
    if len(unique_dates) < span:
        return []
    ranges: list[dict[str, list[str]]] = []
    start_index = 0
    while start_index + span <= len(unique_dates):
        train = unique_dates[start_index : start_index + train_days]
        validation = unique_dates[start_index + train_days : start_index + train_days + validation_days]
        test = unique_dates[start_index + train_days + validation_days : start_index + span]
        ranges.append({"train": train, "validation": validation, "test": test})
        start_index += step_days
    return ranges


def _filter_rows_for_label(rows: Iterable[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if normalize_null(row.get(label)) is None:
            continue
        filtered.append(dict(row))
    filtered.sort(key=lambda row: (str(row.get("ts") or ""), str(row.get("symbol") or "")))
    return filtered


def _encode_rows(
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    *,
    label: str,
    requested_features: str,
) -> EncodedDataset:
    candidate_numeric, candidate_categorical = _split_requested_features(requested_features)
    used_numeric: list[str] = []
    used_categorical: list[str] = []
    dropped_features: list[str] = []

    numeric_stats: list[tuple[str, float, float]] = []
    for column in candidate_numeric:
        train_values = [_value_as_numeric(row.get(column)) for row in train_rows]
        available = [value for value in train_values if value is not None]
        if not available:
            dropped_features.append(column)
            continue
        mean = float(np.mean(available))
        std = float(np.std(available))
        if std < 1e-9:
            std = 1.0
        used_numeric.append(column)
        numeric_stats.append((column, mean, std))

    categorical_stats: list[tuple[str, list[str]]] = []
    for column in candidate_categorical:
        categories = sorted(
            {
                str(normalize_null(row.get(column)))
                for row in train_rows
                if normalize_null(row.get(column)) is not None
            }
        )
        if not categories:
            dropped_features.append(column)
            continue
        used_categorical.append(column)
        categorical_stats.append((column, categories))

    feature_names: list[str] = []
    for column, _, _ in numeric_stats:
        feature_names.append(column)
    for column, categories in categorical_stats:
        for category in categories:
            feature_names.append(f"{column}={category}")

    def _matrix(rows: list[dict[str, Any]]) -> np.ndarray:
        matrix = np.zeros((len(rows), len(feature_names)), dtype=float)
        for row_index, row in enumerate(rows):
            offset = 0
            for column, mean, std in numeric_stats:
                value = _value_as_numeric(row.get(column))
                filled = mean if value is None else value
                matrix[row_index, offset] = (filled - mean) / std
                offset += 1
            for column, categories in categorical_stats:
                current = normalize_null(row.get(column))
                current_text = None if current is None else str(current)
                for category in categories:
                    matrix[row_index, offset] = 1.0 if current_text == category else 0.0
                    offset += 1
        return matrix

    def _labels(rows: list[dict[str, Any]]) -> np.ndarray:
        labels: list[float] = []
        for row in rows:
            parsed = parse_bool(row.get(label))
            if parsed is None:
                raise ValueError(f"Encountered missing label {label} after filtering.")
            labels.append(1.0 if parsed else 0.0)
        return np.array(labels, dtype=float)

    return EncodedDataset(
        x_train=_matrix(train_rows),
        y_train=_labels(train_rows),
        x_validation=_matrix(validation_rows),
        y_validation=_labels(validation_rows),
        x_test=_matrix(test_rows),
        y_test=_labels(test_rows),
        feature_names=feature_names,
        used_numeric_features=used_numeric,
        used_categorical_features=used_categorical,
        dropped_features=sorted(set(dropped_features)),
    )


def _split_requested_features(requested: str) -> tuple[list[str], list[str]]:
    if requested == "auto":
        return list(ML_SAFE_NUMERIC_FEATURE_COLS), list(ML_SAFE_CATEGORICAL_FEATURE_COLS)
    raw_features = [item.strip() for item in requested.split(",") if item.strip()]
    categorical = [column for column in raw_features if column in ML_SAFE_CATEGORICAL_FEATURE_COLS]
    numeric = [column for column in raw_features if column not in categorical]
    return numeric, categorical


def _precision_recall_curve_area(y_true: np.ndarray, scores: np.ndarray) -> float | None:
    positives = float(np.sum(y_true))
    negatives = float(len(y_true) - positives)
    if positives <= 0 or negatives <= 0:
        return None
    order = np.argsort(-scores)
    y_sorted = y_true[order]
    tp = 0.0
    fp = 0.0
    prev_recall = 0.0
    area = 0.0
    for label in y_sorted:
        if label > 0.5:
            tp += 1.0
        else:
            fp += 1.0
        recall = tp / positives
        precision = tp / max(tp + fp, 1.0)
        area += (recall - prev_recall) * precision
        prev_recall = recall
    return float(area)


def _roc_auc_score(y_true: np.ndarray, scores: np.ndarray) -> float | None:
    positives = [score for score, target in zip(scores.tolist(), y_true.tolist()) if target > 0.5]
    negatives = [score for score, target in zip(scores.tolist(), y_true.tolist()) if target <= 0.5]
    if not positives or not negatives:
        return None
    wins = 0.0
    for pos in positives:
        for neg in negatives:
            if pos > neg:
                wins += 1.0
            elif pos == neg:
                wins += 0.5
    return float(wins / (len(positives) * len(negatives)))


def _classification_metrics(
    y_true: np.ndarray,
    scores: np.ndarray,
    *,
    threshold: float,
) -> dict[str, Any]:
    if len(y_true) == 0:
        return {
            "rows": 0,
            "positives": 0,
            "positive_rate": None,
            "predicted_positive_rate": None,
            "precision": None,
            "recall": None,
            "f1": None,
            "roc_auc": None,
            "pr_auc": None,
            "tp": 0,
            "fp": 0,
            "tn": 0,
            "fn": 0,
        }

    predictions = (scores >= threshold).astype(int)
    tp = int(np.sum((predictions == 1) & (y_true == 1)))
    fp = int(np.sum((predictions == 1) & (y_true == 0)))
    tn = int(np.sum((predictions == 0) & (y_true == 0)))
    fn = int(np.sum((predictions == 0) & (y_true == 1)))

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    positive_rate = float(np.mean(y_true)) if len(y_true) else None
    predicted_positive_rate = float(np.mean(predictions)) if len(predictions) else None
    return {
        "rows": int(len(y_true)),
        "positives": int(np.sum(y_true)),
        "positive_rate": round(positive_rate, 4) if positive_rate is not None else None,
        "predicted_positive_rate": round(predicted_positive_rate, 4) if predicted_positive_rate is not None else None,
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "roc_auc": round(float(_roc_auc_score(y_true, scores)), 4) if _roc_auc_score(y_true, scores) is not None else None,
        "pr_auc": round(float(_precision_recall_curve_area(y_true, scores)), 4) if _precision_recall_curve_area(y_true, scores) is not None else None,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }


def _choose_threshold(y_validation: np.ndarray, scores: np.ndarray) -> tuple[float, dict[str, Any]]:
    if len(y_validation) == 0:
        return 0.5, _classification_metrics(y_validation, scores, threshold=0.5)
    best_threshold = 0.5
    best_metrics = _classification_metrics(y_validation, scores, threshold=0.5)
    best_key = (
        best_metrics["f1"] or 0.0,
        best_metrics["precision"] or 0.0,
        -abs(0.5 - best_threshold),
    )
    for threshold in DEFAULT_THRESHOLD_GRID:
        metrics = _classification_metrics(y_validation, scores, threshold=threshold)
        key = (
            metrics["f1"] or 0.0,
            metrics["precision"] or 0.0,
            -abs(0.5 - threshold),
        )
        if key > best_key:
            best_threshold = threshold
            best_metrics = metrics
            best_key = key
    return best_threshold, best_metrics


def _summarize_importances(
    feature_names: list[str],
    importances: np.ndarray,
    *,
    include_sign: bool,
    top_n: int = 12,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature_name, importance in zip(feature_names, importances.tolist()):
        rows.append(
            {
                "feature": feature_name,
                "importance": round(abs(float(importance)), 6),
                "signed_weight": round(float(importance), 6) if include_sign else None,
            }
        )
    rows.sort(key=lambda item: item["importance"], reverse=True)
    return rows[:top_n]


def _aggregate_feature_summary(feature_rows: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, float]] = {}
    for rows in feature_rows:
        for row in rows:
            state = aggregate.setdefault(row["feature"], {"importance_sum": 0.0, "weight_sum": 0.0, "count": 0.0})
            state["importance_sum"] += float(row["importance"] or 0.0)
            if row.get("signed_weight") is not None:
                state["weight_sum"] += float(row["signed_weight"] or 0.0)
            state["count"] += 1.0
    result: list[dict[str, Any]] = []
    for feature, state in aggregate.items():
        result.append(
            {
                "feature": feature,
                "mean_importance": round(state["importance_sum"] / state["count"], 6),
                "mean_signed_weight": round(state["weight_sum"] / state["count"], 6) if state["weight_sum"] else None,
                "fold_count": int(state["count"]),
            }
        )
    result.sort(key=lambda item: item["mean_importance"], reverse=True)
    return result[:12]


def _mean_metric(folds: list[dict[str, Any]], name: str) -> float | None:
    values = [fold[name] for fold in folds if fold.get(name) is not None]
    if not values:
        return None
    return round(float(np.mean(values)), 4)


def _std_metric(folds: list[dict[str, Any]], name: str) -> float | None:
    values = [fold[name] for fold in folds if fold.get(name) is not None]
    if len(values) <= 1:
        return 0.0 if values else None
    return round(float(np.std(values)), 4)


def _build_model(model_name: str) -> tuple[Any, str, str]:
    if model_name == "logistic":
        if LogisticRegression is not None:
            return (
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    solver="liblinear",
                    random_state=42,
                ),
                "sklearn",
                "LogisticRegression",
            )
        return PureLogisticRegression(), "numpy", "PureLogisticRegression"
    if model_name == "random_forest":
        if RandomForestClassifier is None:
            raise RuntimeError(
                "random_forest requires scikit-learn in this environment. "
                "Use --model logistic or install scikit-learn."
            )
        return (
            RandomForestClassifier(
                n_estimators=200,
                min_samples_leaf=5,
                class_weight="balanced_subsample",
                random_state=42,
            ),
            "sklearn",
            "RandomForestClassifier",
        )
    raise ValueError(f"Unsupported model: {model_name}")


def _fit_and_score_fold(
    fold: FoldData,
    *,
    label: str,
    model_name: str,
    requested_features: str,
) -> dict[str, Any]:
    train_rows = _filter_rows_for_label(fold.train_rows, label)
    validation_rows = _filter_rows_for_label(fold.validation_rows, label)
    test_rows = _filter_rows_for_label(fold.test_rows, label)

    fold_result: dict[str, Any] = {
        "fold": fold.fold_name,
        "train_dates": fold.train_dates,
        "validation_dates": fold.validation_dates,
        "test_dates": fold.test_dates,
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "test_rows": len(test_rows),
    }

    if not train_rows or not test_rows:
        fold_result["skipped"] = True
        fold_result["skip_reason"] = "missing train/test rows after label filtering"
        return fold_result

    encoded = _encode_rows(
        train_rows,
        validation_rows,
        test_rows,
        label=label,
        requested_features=requested_features,
    )
    if encoded.x_train.shape[1] == 0:
        fold_result["skipped"] = True
        fold_result["skip_reason"] = "no usable decision-time-safe features"
        return fold_result

    train_positive_count = int(np.sum(encoded.y_train))
    train_negative_count = int(len(encoded.y_train) - train_positive_count)
    if train_positive_count == 0 or train_negative_count == 0:
        fold_result["skipped"] = True
        fold_result["skip_reason"] = "train fold contains only one class"
        return fold_result

    model, backend, backend_name = _build_model(model_name)
    model.fit(encoded.x_train, encoded.y_train)

    validation_scores = model.predict_proba(encoded.x_validation)[:, 1] if len(encoded.y_validation) else np.array([], dtype=float)
    threshold, validation_metrics = _choose_threshold(encoded.y_validation, validation_scores)
    test_scores = model.predict_proba(encoded.x_test)[:, 1]
    test_metrics = _classification_metrics(encoded.y_test, test_scores, threshold=threshold)

    importance_rows: list[dict[str, Any]]
    importance_type: str
    if hasattr(model, "coef_") and getattr(model, "coef_", None) is not None:
        coef = np.asarray(getattr(model, "coef_"))
        coef_row = coef[0] if coef.ndim > 1 else coef
        importance_rows = _summarize_importances(
            encoded.feature_names,
            coef_row,
            include_sign=True,
        )
        importance_type = "coefficient"
    elif hasattr(model, "feature_importances_"):
        importance_rows = _summarize_importances(
            encoded.feature_names,
            np.asarray(getattr(model, "feature_importances_")),
            include_sign=False,
        )
        importance_type = "feature_importance"
    else:
        importance_rows = []
        importance_type = "unavailable"

    fold_result.update(
        {
            "skipped": False,
            "model_backend": backend,
            "model_backend_name": backend_name,
            "threshold": threshold,
            "class_balance": {
                "train_positive_rate": round(float(np.mean(encoded.y_train)), 4),
                "validation_positive_rate": round(float(np.mean(encoded.y_validation)), 4) if len(encoded.y_validation) else None,
                "test_positive_rate": round(float(np.mean(encoded.y_test)), 4),
                "train_positive_count": train_positive_count,
                "validation_positive_count": int(np.sum(encoded.y_validation)),
                "test_positive_count": int(np.sum(encoded.y_test)),
            },
            "features": {
                "used_numeric": encoded.used_numeric_features,
                "used_categorical": encoded.used_categorical_features,
                "dropped": encoded.dropped_features,
                "encoded_feature_count": len(encoded.feature_names),
            },
            "validation_metrics": validation_metrics,
            "test_metrics": test_metrics,
            "feature_summary_type": importance_type,
            "top_features": importance_rows,
            "feature_names": encoded.feature_names,
        }
    )
    return fold_result


def _aggregate_results(
    fold_results: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    evaluated = [fold for fold in fold_results if not fold.get("skipped")]
    skipped = [fold for fold in fold_results if fold.get("skipped")]
    test_metrics = [fold["test_metrics"] for fold in evaluated]
    top_feature_rows = [fold.get("top_features") or [] for fold in evaluated]

    total_rows = sum(metric["rows"] for metric in test_metrics)
    total_positives = sum(metric["positives"] for metric in test_metrics)
    total_predicted_positive_rate_num = sum((metric["predicted_positive_rate"] or 0.0) * metric["rows"] for metric in test_metrics)
    macro_metrics = {
        "precision": _mean_metric(test_metrics, "precision"),
        "recall": _mean_metric(test_metrics, "recall"),
        "f1": _mean_metric(test_metrics, "f1"),
        "roc_auc": _mean_metric(test_metrics, "roc_auc"),
        "pr_auc": _mean_metric(test_metrics, "pr_auc"),
        "positive_prediction_rate": _mean_metric(test_metrics, "predicted_positive_rate"),
        "class_balance": _mean_metric(test_metrics, "positive_rate"),
    }
    stability = {
        "precision_std": _std_metric(test_metrics, "precision"),
        "recall_std": _std_metric(test_metrics, "recall"),
        "f1_std": _std_metric(test_metrics, "f1"),
        "pr_auc_std": _std_metric(test_metrics, "pr_auc"),
    }
    micro_counts = {
        "tp": sum(metric["tp"] for metric in test_metrics),
        "fp": sum(metric["fp"] for metric in test_metrics),
        "tn": sum(metric["tn"] for metric in test_metrics),
        "fn": sum(metric["fn"] for metric in test_metrics),
    }
    precision = micro_counts["tp"] / (micro_counts["tp"] + micro_counts["fp"]) if (micro_counts["tp"] + micro_counts["fp"]) else 0.0
    recall = micro_counts["tp"] / (micro_counts["tp"] + micro_counts["fn"]) if (micro_counts["tp"] + micro_counts["fn"]) else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    aggregate = {
        "evaluated_folds": len(evaluated),
        "skipped_folds": len(skipped),
        "total_test_rows": total_rows,
        "total_test_positives": total_positives,
        "class_balance": round(total_positives / total_rows, 4) if total_rows else None,
        "macro_average": macro_metrics,
        "stability": stability,
        "micro_average": {
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1": round(float(f1), 4),
            "positive_prediction_rate": round(total_predicted_positive_rate_num / total_rows, 4) if total_rows else None,
        },
        "top_features": _aggregate_feature_summary(top_feature_rows),
        "skipped_fold_reasons": [
            {
                "fold": fold["fold"],
                "reason": fold.get("skip_reason"),
            }
            for fold in skipped
        ],
    }

    class_balance = aggregate["class_balance"] or 0.0
    macro_f1 = (aggregate["macro_average"]["f1"] or 0.0)
    macro_precision = (aggregate["macro_average"]["precision"] or 0.0)
    macro_recall = (aggregate["macro_average"]["recall"] or 0.0)
    macro_pr_auc = aggregate["macro_average"]["pr_auc"]
    f1_std = aggregate["stability"]["f1_std"] or 0.0

    if aggregate["evaluated_folds"] == 0:
        research_read = "too little positive coverage"
    elif total_positives < 20 or class_balance < 0.01:
        research_read = "too little positive coverage"
    elif macro_precision >= 0.6 and macro_recall <= 0.2:
        research_read = "high precision but low recall"
    elif f1_std >= 0.12 or aggregate["skipped_folds"] > 0:
        research_read = "unstable across folds"
    elif macro_pr_auc is not None and macro_pr_auc >= class_balance + 0.05 and macro_f1 >= 0.2:
        research_read = "promising offline signal"
    else:
        research_read = "weak baseline"

    aggregate["research_read"] = research_read
    aggregate["label_definition"] = LABEL_DEFINITIONS.get(label)
    return aggregate

