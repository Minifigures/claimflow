"""Temperature scaling + expected calibration error, numpy/torch only (no sklearn).

The training extras (mlflow/sklearn/matplotlib) are NOT installed in this env, so all
metrics here are plain numpy; only ``fit_temperature`` uses torch (LBFGS on NLL).
"""

from __future__ import annotations

import numpy as np


def softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    """Row-wise softmax with optional temperature (numpy, numerically stable)."""
    z = logits.astype(np.float64) / max(float(temperature), 1e-8)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def fit_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    """Fit a single softmax temperature by minimizing NLL on held-out (val) logits.

    LBFGS over log-temperature (guarantees T > 0). Returns 1.0 for empty input.
    """
    import torch  # lazy: keep numpy-only callers (e.g. ece tests) torch-free

    logits = np.asarray(logits, dtype=np.float32)
    labels = np.asarray(labels)
    if logits.ndim != 2 or labels.ndim != 1 or logits.shape[0] != labels.shape[0]:
        raise ValueError(f"bad shapes: logits {logits.shape}, labels {labels.shape}")
    if logits.shape[0] == 0:
        return 1.0

    logits_t = torch.from_numpy(logits)
    labels_t = torch.from_numpy(labels.astype(np.int64))
    log_temp = torch.zeros(1, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_temp], lr=0.1, max_iter=100)
    nll = torch.nn.CrossEntropyLoss()

    def closure() -> torch.Tensor:
        optimizer.zero_grad()
        loss = nll(logits_t / log_temp.exp(), labels_t)
        loss.backward()
        return loss

    optimizer.step(closure)  # type: ignore[arg-type]
    temperature = float(log_temp.exp().item())
    if not np.isfinite(temperature):
        return 1.0
    return float(np.clip(temperature, 0.05, 50.0))


def ece(probs: np.ndarray, labels: np.ndarray, bins: int = 15) -> float:
    """Expected calibration error (equal-width confidence bins), pure numpy.

    Perfectly confident + correct predictions give ~0.
    """
    probs = np.asarray(probs, dtype=np.float64)
    labels = np.asarray(labels)
    if probs.ndim != 2 or probs.shape[0] != labels.shape[0]:
        raise ValueError(f"bad shapes: probs {probs.shape}, labels {labels.shape}")
    if probs.shape[0] == 0:
        return 0.0
    confidence = probs.max(axis=1)
    correct = (probs.argmax(axis=1) == labels).astype(np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    n = float(len(labels))
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (confidence > lo) & (confidence <= hi) if i > 0 else (confidence <= hi)
        if mask.any():
            gap = abs(correct[mask].mean() - confidence[mask].mean())
            total += (mask.sum() / n) * gap
    return float(total)
