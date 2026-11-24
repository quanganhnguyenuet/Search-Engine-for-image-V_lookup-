# hops-v2-holdout

A held-out set for multi-hop and multi-intent search: 20 `multi_hop` (v01-v20) and 20
`multi_intent` (n01-n20) queries in the format of `eval/hops-v1`.

hops-v1 became a dev set: the pipeline was fixed query by query against it, so its scores
now overstate how well the fixes generalise. This set checks that, and only at checkpoints.

## How the queries were made

- Brands and generic categories come from `scripts/sample_hops_holdout.py --seed 2026`:
  random brands with at least 3 products and 2 product types that no hops-v1 query names,
  and categories that hold none of those brands' products.
- Phrasings deliberately differ from hops-v1: no diacritics (v05, v20, n20), other
  connectors ("kèm", "cùng", "hay", "với cả", "+", "lẫn", "cũng như", "thêm cả", a bare
  comma), other exclusions ("không tính", "ngoại trừ"), and question forms.
- The pipeline was not run on these queries while they were written.

## Rules

1. Do not use this set while changing the pipeline: do not search these queries, read their
   traces, or tune settings on them. Develop on hops-v1, silver and the unit tests.
2. Run it only at a checkpoint, after the change is finished and committed:

   ```
   python scripts/build_eval_hops.py --set hops-v2-holdout
   python scripts/hops_judge_server.py --set hops-v2-holdout   # grade the new cards
   python scripts/evaluate_hops.py --set hops-v2-holdout
   ```

3. Report the aggregate numbers (P@10, wrong@10, all_parts@10, CIs) next to hops-v1. A gap
   between the two sets is the overfitting signal.
4. Do not fix the pipeline from individual holdout failures. If a failure has to be studied,
   move that query to hops-v1 (dev) and treat this set as used up: draw a new holdout with
   another seed (`--seed`) and add the brands of this set to the exclusion list.
5. A rebuilt pool adds unjudged cards, and the evaluator scores them as wrong (grade 0).
   Grade the new cards before reading the scores; grades of earlier cards are kept.
