# Long-text evidence registry

A working extraction and review application: SQLite task leases, exact source spans, persistent entity identity, bounded CLI workers and a React review interface. The registry, worker and interface are exported from the same implementation, not re-created as a mockup.

## Run the offline example

Python 3.11+ is sufficient for the backend and all offline tests.

```sh
python -m unittest discover -s experiments/book_registry -p "test_*.py"
python demo.py
cd experiments/book_registry/ui
npm install
npm run build
cd ../../..
python experiments/book_registry/review_server.py --db "data/demo/<database printed by demo>.sqlite3"
```

The server prints its loopback URL. The original fixture text is synthetic, not an excerpt from a published book. Each demo run creates a fresh database and leaves previous runs intact.

## Implementation

- `registry.py`: short SQLite transactions, leased work, incremental idempotent batches, immutable source binding, explicit review decisions and record visibility.
- `chapter_runner.py`: finite worker lifecycle, lease heartbeat, structured response compilation, retry records and checkpoint-aware continuation. It requires an explicitly configured local CLI; the offline demo never calls it.
- `entity_preflight.py`: lookup proof and revision binding before merging references. A changed identity lookup must be revisited rather than silently accepted.
- `compact_wire.py`, `alignment.py`, `context_reader.py`: compact model output expands to a validated evidence contract; context can be extended without confusing a quote with proof of identity.
- `review_server.py` and `ui/src/`: searchable entities and relations, source inspection and stable review handles. The browser is read-only; explicit decisions are applied through `apply_review.py` with revision checks and saved feedback.
- `admission_scores.py`, `score_admission.py`: append-only prioritization scores and provenance. A score is advisory; it neither deletes facts nor establishes calibrated confidence.

## Boundary and safety

The public package includes the complete registry-to-review workflow and its offline tests. Corpus-specific importers, production databases, model outputs, hard-coded feedback batches and unrelated exploratory notebooks are deliberately outside this package. To import another corpus, use `Registry.create` with your own unit/paragraph identities, UTF-8 hashes and offsets; `demo.py` shows the full input contract.

Worker execution is opt-in and can consume a separately installed CLI's allowance. Inspect its command and permissions for your environment before running. Do not expose the review server to the public Internet; this is a local workbench, not a multi-tenant service. Scores are not a fine-tuned language model, SFT or preference-alignment claim.
