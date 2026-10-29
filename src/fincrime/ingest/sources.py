"""The public benchmark datasets: where they come from and what we may do with them.

Raw files are never committed and never republished. ``publish_rule`` says what may leave this
machine (results, figures and code only, for all of them). Row counts marked ``verified`` were
read in a primary source; the rest are reported as observed when converted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

AML_SIZES = ("Small", "Medium", "Large")
AML_KINDS = ("HI", "LI")  # higher / lower illicit ratio


@dataclass(frozen=True)
class Source:
    """One public dataset."""

    key: str
    kind: str  # "dataset" or "competition" on Kaggle
    ref: str
    url: str
    license: str
    publish_rule: str
    files: tuple[str, ...]
    default_files: tuple[str, ...]
    verified_rows: dict[str, int] = field(default_factory=dict)


def _aml_files(size: str) -> tuple[str, ...]:
    return tuple(
        f"{kind}-{size}_{part}"
        for kind in AML_KINDS
        for part in ("Trans.csv", "accounts.csv", "Patterns.txt")
    )


AMLWORLD = Source(
    key="amlworld",
    kind="dataset",
    ref="ealtman2019/ibm-transactions-for-anti-money-laundering-aml",
    url="https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml",
    license="CDLA-Sharing-1.0",
    publish_rule="aggregate results only; derived data would have to carry the same licence",
    files=tuple(f for size in AML_SIZES for f in _aml_files(size)),
    default_files=_aml_files("Small") + _aml_files("Medium"),
    # Large-file edge counts from the AMLworld paper's statistics table
    verified_rows={"HI-Large_Trans.csv": 179_702_229, "LI-Large_Trans.csv": 176_066_557},
)

BAF = Source(
    key="baf",
    kind="dataset",
    ref="sgpjesus/bank-account-fraud-dataset-neurips-2022",
    url="https://www.kaggle.com/datasets/sgpjesus/bank-account-fraud-dataset-neurips-2022",
    license="CC BY-NC-ND 4.0",
    publish_rule="non-commercial, no derivatives: never publish converted copies, results only",
    files=(
        "Base.csv",
        "Variant I.csv",
        "Variant II.csv",
        "Variant III.csv",
        "Variant IV.csv",
        "Variant V.csv",
    ),
    default_files=(
        "Base.csv",
        "Variant I.csv",
        "Variant II.csv",
        "Variant III.csv",
        "Variant IV.csv",
        "Variant V.csv",
    ),
    # datasheet A5: six datasets of one million instances each
    verified_rows={
        f: 1_000_000
        for f in (
            "Base.csv",
            "Variant I.csv",
            "Variant II.csv",
            "Variant III.csv",
            "Variant IV.csv",
            "Variant V.csv",
        )
    },
)

SPARKOV = Source(
    key="sparkov",
    kind="dataset",
    ref="kartik2112/fraud-detection",
    url="https://www.kaggle.com/datasets/kartik2112/fraud-detection",
    license="CC0-1.0 (Kaggle dataset metadata, checked 2026-10-29)",
    publish_rule="results only, as for the other benchmarks",
    files=("fraudTrain.csv", "fraudTest.csv"),
    default_files=("fraudTrain.csv", "fraudTest.csv"),
)

IEEE_CIS = Source(
    key="ieee_cis",
    kind="competition",
    ref="ieee-fraud-detection",
    url="https://www.kaggle.com/c/ieee-fraud-detection",
    license="Kaggle competition rules, accepted on the website",
    publish_rule="treat as not redistributable; results only",
    files=("train_transaction.csv", "train_identity.csv"),
    default_files=("train_transaction.csv", "train_identity.csv"),
    verified_rows={"train_transaction.csv": 590_540},
)

SOURCES: dict[str, Source] = {s.key: s for s in (AMLWORLD, BAF, SPARKOV, IEEE_CIS)}
