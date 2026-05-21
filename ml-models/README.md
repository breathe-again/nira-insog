# ml-models/

Training code, evaluation notebooks, and saved model artifacts for the ML components of Nira Insig.

Modules:

- `extraction/` — invoice / receipt field extraction (prompt templates, evaluation harness, fallback fine-tunes).
- `classification/` — document type classifier (TF-IDF + gradient boosting baseline).
- `forecasting/` — cash flow forecast (Prophet), collection probability (gradient boosting), client risk score.

Each module has a training script, an evaluation script, and a `versions/` folder for saved artifacts. Production serving happens from the backend `understanding/` and `insights/` modules, which load model artifacts at startup.

We track experiments with MLflow.
