# Research Brief — Toy Classification

## Problem
On `data.npz` (400 samples × 10 features, 3 classes), find the classifier that maximizes 5-fold cross-validated macro-F1.

## Constraints
- Wall budget per branch : 30 seconds
- No external data / no internet
- Python env: `/data/liying_environ/anaconda3_liying/envs/omicsclaw/bin/python` (has sklearn 1.7.1, numpy 2.2.6)
- Each branch must produce `RESULT.md` with `METRIC=<float>` on the last line, or `DEAD.md` with a reason.

## What "good" looks like
A classifier reaching macro-F1 ≥ 0.85 on this dataset is considered successful. Below 0.6 is considered a dead branch.

## Why this exists
This is the smoke test for `research-tree-explorer`. It validates the tree-shaped exploration mechanism end-to-end without requiring GPU or slow training. Branches should differ in approach (linear, tree-based, kernel-based, neural) so the tree has measurable variety.
