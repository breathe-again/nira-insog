# ml-models/classification/

Document type classifier — given an extracted text + filename + a few metadata features, predict one of: `bank_statement`, `sales_invoice`, `purchase_invoice`, `receipt`, `unknown`.

Baseline: TF-IDF + LightGBM gradient boosting. Cheap, retrainable weekly, easy to debug.

Files (planned):

- `train.py` — training pipeline (data load → features → model → eval → save).
- `evaluate.py` — held-out set evaluation, confusion matrix.
- `versions/` — saved model artifacts (one per release).

The same module structure will host the expense categorization model.
