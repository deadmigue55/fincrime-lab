"""The four candidate models of docs/methodology.md section 7.

Everything is scikit-learn so a reviewer can read it in a minute: a regularised logistic
regression as the honest simple baseline, and gradient boosting (which handles missing values and
categories natively) at three sizes. Training sees a 2% sample of the genuine events with weight
50, so predicted probabilities stay calibrated to the real prevalence.
"""

from __future__ import annotations

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from fincrime.fraud import labels

N_CAT = len(labels.CATEGORICAL)
CANDIDATES = ("logistic", "gbm_d3", "gbm_d6", "gbm_d6_big")


def _signed_log(x: np.ndarray) -> np.ndarray:
    return np.sign(x) * np.log1p(np.abs(x))


def make(name: str, seed: int = 0):
    """An unfitted model. Categorical codes are the first ``N_CAT`` columns of the design matrix."""
    if name == "logistic":
        numeric = Pipeline(
            [
                ("log", FunctionTransformer(_signed_log, feature_names_out="one-to-one")),
                ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                ("scale", StandardScaler()),
            ]
        )
        # codes are small non-negative integers (-1 = never seen), shifted so one-hot can read them
        cats = Pipeline(
            [
                (
                    "shift",
                    FunctionTransformer(
                        lambda x: np.nan_to_num(x) + 1, feature_names_out="one-to-one"
                    ),
                ),
                ("onehot", OneHotEncoder(handle_unknown="ignore")),
            ]
        )
        pre = ColumnTransformer(
            [("cat", cats, list(range(N_CAT))), ("num", numeric, slice(N_CAT, None))]
        )
        return Pipeline([("pre", pre), ("lr", LogisticRegression(C=1.0, max_iter=400))])
    settings = {
        "gbm_d3": {"max_depth": 3, "max_iter": 200},
        "gbm_d6": {"max_depth": 6, "max_iter": 200},
        "gbm_d6_big": {"max_depth": 6, "max_iter": 400},
    }[name]
    return HistGradientBoostingClassifier(
        learning_rate=0.1,
        l2_regularization=1.0,  # without it the Newton step explodes on rare weighted positives
        categorical_features=list(range(N_CAT)),
        early_stopping=False,
        random_state=seed,
        **settings,
    )


def fit(name: str, x: np.ndarray, y: np.ndarray, weight: np.ndarray, seed: int = 0):
    """Fit a candidate with sample weights (the genuine events were down-sampled)."""
    model = make(name, seed)
    if name == "logistic":
        model.fit(x, y, lr__sample_weight=weight)
    else:
        model.fit(x, y, sample_weight=weight)
    return model


def predict(model, x: np.ndarray, batch: int = 2_000_000) -> np.ndarray:
    """Probability of fraud for each row, in batches to keep memory flat."""
    return np.concatenate(
        [model.predict_proba(x[i : i + batch])[:, 1] for i in range(0, len(x), batch)]
    )
