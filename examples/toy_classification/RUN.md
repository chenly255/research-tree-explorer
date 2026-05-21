# How to run this example

This is the smoke test for `research-tree`. It validates the tree mechanism end-to-end on a 30-second-per-branch synthetic classification task.

## Prerequisites

- A python env with sklearn ≥ 1.0 and numpy ≥ 1.20. On Lily's machine: `/data/liying_environ/anaconda3_liying/envs/omicsclaw/bin/python`
- Claude Code with `research-tree` skill installed (`bash scripts/install.sh`)

## Recipe

```bash
cd /data3/liying/research-tree-explorer/examples/toy_classification

# 1. Regenerate the dataset if it's missing
test -f data.npz || /data/liying_environ/anaconda3_liying/envs/omicsclaw/bin/python -c "
from sklearn.datasets import make_classification
import numpy as np
X, y = make_classification(n_samples=400, n_features=10, n_informative=5,
                           n_redundant=2, n_classes=3, random_state=42)
np.savez('data.npz', X=X, y=y)
"

# 2. Open Claude Code in this directory, then invoke:
#    /research-tree init "Find the best classifier on data.npz ..."
#    /research-tree autopilot
```

## What you should see at the end

`.research-tree/FINAL_REPORT.md` should rank approximately:

| Approach | Macro-F1 (5-fold CV) |
|---|---|
| MLP (winner)    | ~0.73 |
| Random Forest   | ~0.70 |
| SVM (RBF)       | ~0.70 |
| Logistic Reg.   | ~0.62 (marked dead) |

…with a junction audit recorded under `.research-tree/audits/`, and at least one ablation of the MLP winner (e.g., wider hidden layer → small regression, confirming the default config is near-optimal at this dataset scale).

If your numbers are within ±0.02, the mechanism is working as intended.
