"""Fit a tiny intent classifier from labelled examples; no LLM or model download."""

import json
import math
import sys
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/api/src'))
from visualseek_api.application.query_routing import intent_features  # noqa: E402


def train(rows: list[dict]) -> dict:
    documents = [intent_features(row['query']) for row in rows]
    frequencies = Counter(key for document in documents for key in document)
    vocabulary = sorted(frequencies)
    positions = {key: i for i, key in enumerate(vocabulary)}
    idf = {key: math.log((len(rows) + 1) / (frequencies[key] + 1)) + 1
           for key in vocabulary}
    matrix = np.zeros((len(rows), len(vocabulary)), dtype=np.float64)
    for i, document in enumerate(documents):
        for key, value in document.items():
            matrix[i, positions[key]] = value * idf[key]
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    targets = np.asarray([1 if row['needs_reasoning'] else -1 for row in rows], dtype=np.float64)
    # A regularized discriminative model avoids classifying by subject frequency.
    gram = matrix @ matrix.T + 0.25 * np.eye(len(rows))
    coefficients = matrix.T @ np.linalg.solve(gram, targets)
    weights = {key: round(float(coefficients[i]), 8) for i, key in enumerate(vocabulary)}
    return {'version': 'intent-linear-v1', 'bias': 0, 'local_threshold': -0.25,
            'reasoning_threshold': 0.15, 'weights': weights, 'idf': idf,
            'training_examples': len(rows),
            'note': 'Supervised lexical intent classifier; uncertain requests escalate to LLM. '
                    'Scores are not probabilities; no guarantee on unseen paraphrases.'}


def main():
    data = ROOT / 'services/api/src/visualseek_api/application/query_router_training.jsonl'
    rows = [json.loads(line) for line in data.read_text(encoding='utf-8').splitlines()]
    training = [row for row in rows if row['split'] == 'train']
    model = train(training)
    output = ROOT / 'services/api/src/visualseek_api/application/query_router_v1.json'
    output.write_text(json.dumps(model, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    from visualseek_api.application.query_routing import IntentRouter
    router = IntentRouter()
    samples = []
    for row in rows:
        if row['split'] == 'train':
            continue
        decision = router.classify(row['query'])
        samples.append({**row, 'predicted_reasoning': decision.needs_reasoning,
                        'score': round(decision.score, 3), 'reason': decision.reason})
    errors = [row for row in samples if row['predicted_reasoning'] != row['needs_reasoning']]
    report = {'model': model['version'], 'training_examples': len(training),
              'evaluated': len(samples), 'errors': len(errors), 'samples': samples,
              'note': 'Author-labelled draft routing examples; not retrieval relevance labels.'}
    report_path = ROOT / 'data/evaluation/query-routing-report-v1.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('model', 'training_examples', 'evaluated', 'errors')}))
    for row in errors:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    main()
