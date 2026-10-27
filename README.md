# DeeWaanAI Model Router (Layer 5)

Auditable, replayable model routing: policy as data, privacy and verifiability as hard gates, every decision a bound record. Companion to the paper "Where Should This Query Run?"

## Layout

- `spec.md` — frozen contract v1.1 (sha256 in FREEZE-v1.1.md)
- `paper/` — draft + figures + claim map (100 datapoints, semantic map, two-way verifier)
- `router/` — connector + gate + store + audit code and tests
- `experiments/` — frozen batteries, raw runs, spine
- `datasets/` — the Qafiya-layout policy/model/rule/decision stores

## Reproduce

`python3 experiments/spine.py` recomputes every headline number from frozen artifacts. `python3 paper/claim_map/verify_two_way.py` re-verifies every claim against them. Model weights (GGUF) are not included; supply your own local endpoint.

## Substrate

Qafiya spec v1.1 + hash-chained history v1.3 (github.com/DeeWaanAI/qafiya, tag v0.2.0).

## License

AGPL-3.0 — see LICENSE.
