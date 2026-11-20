# Pick model: technical report (2026-11-20)

The state of the computer player's pick model: the task, the data, what is measured, every model structure
tried so far and its results, why more data hasn't improved it, and which directions look most promising.
It is written as the input for a deeper research round on model structure.

Sources: MLflow experiments `pick-model` (id 3, 13 nightly cycles), `pick-model-experiments` (id 4, five
notebooks) and `embedding-benchmark` (id 2); code in [ml/](../../ml/); a learning-curve and diagnostics run
made for this report on snapshot `2026-11-19_0a59741e8a66` (section 7).

---

## 1. Summary

- **The task:** a player holds 10 white cards and sees a one-blank black card. Predict which card they
  play. The model gives each hand card a score, and a softmax over the hand turns the scores into probabilities.
- **The best models are no better than the popularity baseline.** Popularity means each card's
  smoothed historical play rate, with no text and no black card. On the newest snapshot (5,887 training
  picks, 1,745 test picks): popularity test MRR **0.408**, best trained model (linear e5) **0.401**. Champion
  v6 (linear e5, 2026-11-12) has not been beaten by any of the 8 nightly challengers since.
- **The models learn card quality, not the joke.** Removing the black card entirely (white-only model)
  costs nothing. A low-rank black × white interaction adds nothing measurable. Train MRR (≈0.42) is
  barely above validation (≈0.39): the models **underfit a weak signal**; they aren't overfitting.
- **More data *does* help, but only through card identity, which the pipeline's models can't see**
  (learning curve, section 7). With validation and test held fixed, from 12.5% to 100% of the training games:
  - **popularity** keeps climbing, 0.347 → 0.408, and is still rising at 100%;
  - the **pipeline model** (text only) flattens: 0.372 → 0.395 at 50% → 0.402 at 100%.

  The pipeline model scores a card only from its text embedding, so it can't learn "this particular card
  gets played a lot". It has to approximate card quality from the text, and that approximation saturates
  at ~0.40. **This is the structural problem behind "more data, no better results".**
- **Giving the model popularity as an input fixes it** (section 7.2):
  - **popularity + white text: 0.425** (+0.018 over popularity, significant), deployable as is;
  - **popularity + white + hand position: 0.443** (+0.035 over popularity, significant; top-1 0.241 vs 0.199
    for the pipeline model). Both still gain from more data.
- **Hand position is a real but UI-made signal** (section 6.3). The newest card (slot 9) is picked 13.3% of
  the time; slots 5–8 only 7.3–8.3%. The hand is an overlapping fan, and only the last card is fully visible.
  That is *presentation bias*, not humour: it improves the offline metric but doesn't make the computer player
  funnier, since the bot has no UI. Use it in training as a nuisance term and drop it at inference (section 8, H5).
- **The black card still adds (almost) nothing.** A white-only model ≈ the pipeline model, and a low-rank
  black × white interaction adds nothing measurable. **No model has found a "joke fit" signal yet.** Frozen
  sentence embeddings, compared as two separate vectors, don't encode why a card is funny on a given black
  card. The model *head* (linear vs MLP vs bilinear) isn't the bottleneck; the inputs are.
- **Most promising directions** (section 9):
  1. Quick win: popularity (+ position as a training-only term) as an input in the pipeline.
  2. Measure the ceiling with an LLM judge on a sample of test rounds (deferred: no paid API calls).
  3. A fine-tuned **cross-encoder** that reads black + white as one text, trained listwise, with popularity
     and position as side inputs.
  4. A gradient-boosted ranker over engineered features.
  5. In-game context (what this group has voted for so far).
  6. Predicting the **vote winner** instead of the pick, since that is what the computer player needs to do well.

---

## 2. The task

### 2.1 What one example is

A **human submission in a one-blank round**:

| Field | Content |
|---|---|
| black card | the round's black card (one blank, `____`) |
| hand | the player's 10 white cards **in display order**, at the moment of submitting |
| target | the position (0–9) of the card they played |

Left out: computer-player submissions (no model learns from an older model), rounds with 2–3 blanks (later),
hands of fewer than 10 cards, solo games, rounds after a game closed.

### 2.2 What the model sees, and what it doesn't

| Sees | Doesn't see |
|---|---|
| The black card's text | Who the player is: the log is anonymous, with **no player ID across games** |
| The 10 white cards' texts | The czar, or the other players and their past votes |
| (Nothing else: the model is blind to hand order) | Earlier rounds of the same game: what won, what this player played |
| | The game mode (czar vs. everyone votes), self-vote, editions |
| | Hand position as a feature, or how long a card has sat in the hand |
| | Rejected hands: 4,286 "new hand" events, i.e. 10 cards the player refused for this black card |

### 2.3 Why the label is hard

A pick isn't "the funniest card". It is a mix of:
- the player's own taste, which varies a lot between people and groups, with no ID to model it;
- strategy: in czar mode, the czar's taste; in vote mode, the group's;
- **presentation**: on a phone only ~4 of the 10 cards are visible without swiping (section 6.3);
- several good-enough cards in most hands: picking one of them is close to arbitrary.

The ceiling of top-1 accuracy is therefore far below 1, and nobody has measured where it is yet (section 9.1).

---

## 3. Data

Newest snapshot, `2026-11-19_0a59741e8a66` (gameplay logging since 2026-11-06, 14 days):

| | Count |
|---|---|
| Games (after removing 63 solo games) | 284 (150 czar mode, 134 vote mode) |
| Rounds / decisions / votes | 3,852 / 12,910 / 7,015 |
| One-blank human picks used | 9,715 (5,887 train / 2,083 val / 1,745 test) |
| Games per split | 156 train / 49 val / 49 test |
| Players per game | median 3 (IQR 3–4, p90 5) |
| Picks per game | median 38.5 |
| Distinct white cards in hands | 2,553 |
| Distinct one-blank black cards | 509 (of 517 in the catalogue) |
| Hand-change events (all "new hand", no single swaps) | 4,286 |

### 3.1 How often cards repeat

| | |
|---|---|
| A white card's appearances in training hands | median 21.5 (p10 6, p90 42.5) |
| Test hand cards seen in training | 99.6% (median 26 times) |
| Test black cards seen in training | 97.9% |
| Play rate of cards seen ≥10 times (p10 / p25 / p50 / p75 / p90) | 0.00 / 0.04 / 0.09 / 0.15 / 0.24 |
| Distinct (black, white) pairs | 90,614 |
| Pairs seen in a hand ≥3 times | 373 (0.4%) |

Two consequences:
- **Card identity is well covered.** Almost every test card has a track record, so popularity has a lot
  to work with, and its play rates differ widely between cards (0 to 24%).
- **The black × white combination almost never repeats.** Any "fit" signal must generalise from the
  texts. It can't be memorised. That is exactly what the text representation has to deliver.

### 3.2 Split

By game, in time order: the oldest 60% of games train, the next 20% validate (early stopping, model
selection), the newest 20% test. No game is in two splits. Validation and test contain only games
**without** a computer player. The split is re-made on every nightly snapshot, so **the test set changes
every cycle** and test numbers from different cycles are not comparable (section 6.1).

### 3.3 Data variants (training only)

| Variant | Training data | Status |
|---|---|---|
| A | human picks from games without a computer player | default; registered |
| B | A + human picks from games with one | identical to A (few bot games so far) |
| C | B + vote winners (the round's submissions as a "hand", the human-voted winner as target), ~1,500 extra examples | no significant gain in any cycle (section 6.4) |

---

## 4. What is measured

All metrics are over the hand of 10 ([ml/evaluate.py](../../ml/evaluate.py)):

| Metric | Meaning | Random (hand of 10) |
|---|---|---|
| top-1 | the played card is the model's first choice | 0.100 |
| top-3 | the played card is in the model's first three | 0.300 |
| **MRR** | mean of 1 / rank of the played card. **Main metric** (selection, promotion) | 0.293 |
| log loss | −log of the probability the played card got | 2.303 |

- Ties count as a fair random tie-break (expected value), so the baselines aren't helped by card order.
- **Baselines:** random, and **popularity**: each card's play rate when it was in a training hand,
  smoothed towards 1/10 with strength 10 (`(played + 1) / (shown + 10)`), normalised per hand. Unseen
  cards get the prior.
- **Selection:** the trained model with the best *validation* MRR becomes `challenger`. Test is only reported.
- **Significance:** a paired bootstrap over **games** (4,000 resamples): picks within a game share
  players and cards, so resampling picks would make the interval too narrow. Typical 95% half-width for a
  difference between two models on ~1,700 test picks: **±0.010–0.012 MRR**.
- **Promotion** (nightly, automatic): `challenger` replaces `champion` only if the 95% interval of the MRR
  difference on the newest test picks is entirely above 0.
- Not yet measured: the computer player's win rate in real votes (planned online metric, milestone 5).

---

## 5. The models

### 5.1 Card representations: frozen sentence embeddings

Each card text is embedded once by a pretrained multilingual sentence encoder, L2-normalised, and cached
([ml/features.py](../../ml/features.py)). The encoders are **never fine-tuned**.

| Key | Model | Params | Dim | Notes |
|---|---|---|---|---|
| `minilm` | sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 118M | 384 | paraphrase-trained baseline |
| `e5` | intfloat/multilingual-e5-small | 118M | 384 | contrastive retrieval model; `query: ` prefix on both sides |
| `granite` | ibm-granite/granite-embedding-97m-multilingual-r2 | 97M | 384 | ModernBERT-based, 2026 |
| experiment | intfloat/multilingual-e5-base | 278M | 768 | notebook 04 |
| experiment | huSpaCy hu_core_news_md (floret word vectors, averaged; tokens or lemmas) | n/a | 100 | Hungarian-specific; notebook 05 |

**Embedding benchmark** (first snapshot, 501 picks; "play the card most similar to the black card"):

| Encoder | top-1 | mean cosine, played card | mean cosine, not played | gap |
|---|---|---|---|---|
| minilm | 0.116 | 0.210 | 0.206 | 0.004 |
| e5 | 0.118 | 0.813 | 0.812 | 0.0004 |
| granite | 0.114 | 0.750 | 0.747 | 0.003 |

Raw semantic similarity carries almost no signal (random 0.100): the funny card isn't the most *similar*
card. e5's space is strongly anisotropic (unrelated texts at cosine ≈0.8), which is why the experiments
centre the vectors (subtract the training mean).

### 5.2 Pipeline head: `PairScorer` (linear / mlp)

[ml/train.py](../../ml/train.py). For every (black b, hand card w) pair, the features are

```
f(b, w) = [ b, w, b ⊙ w, |b − w| ]          (4 × 384 = 1,536 values)
score(b, w) = head(f(b, w))
P(card i | b, hand) = softmax_i( score(b, w_i) )   over the 10 hand cards
```

| Structure | head | Parameters | Training config |
|---|---|---|---|
| `linear` | Linear(1536 → 1) | 1,537 | Adam lr 1e-2, weight decay 1e-3, max 600 epochs |
| `mlp` | Linear(1536 → 64) → ReLU → Dropout 0.2 → Linear(64 → 1) | 98,433 | Adam lr 3e-3, weight decay 1e-4, max 300 epochs |

- Loss: cross-entropy of the played card over the hand (a **conditional logit** / listwise softmax).
- **Full-batch** Adam, one CPU thread, seeded, so cycles are reproducible bit for bit.
- Early stopping on validation loss, patience 25, best weights restored.
- Each card is scored **independently** of the other hand cards; the hand only enters through the softmax
  (independence of irrelevant alternatives).
- Note: the `b` term alone is constant across a hand, so it cancels in the softmax. The linear model
  effectively learns `w`-quality + a diagonal interaction (`b ⊙ w`, `|b − w|`).
- Grid per nightly cycle: 2 structures × 3 encoders × 3 data variants = 18 models + 2 baselines.

### 5.3 Experiment heads (notebooks, snapshot 2026-11-11)

All use the pipeline's split, loss and training loop ([ml/experiments.py](../../ml/experiments.py)).

| Notebook | Structure | Score |
|---|---|---|
| 01 bilinear | white term + low-rank interaction, rank r ∈ {0, 4, 16, 64} | `v·w + (P b)·(Q w)` |
| 02 popularity residual | popularity as input, model learns the residual | `a·log pop(w) [+ v·w] [+ (P b)·(Q w)]` |
| 03 filled sentence | the black card with the white card filled into the blank, embedded as one text | `head(E("Mi ragad? A méz."))` [+ `a·log pop`] |
| 04 other encoder | pipeline heads on e5-base (768-d) | as 5.2 |
| 05 huSpaCy | pipeline heads on Hungarian word vectors | as 5.2 |

In notebook 02 the training examples use **leave-one-out** popularity (a pick isn't counted in its own
card's rate); otherwise the model over-trusts popularity.

### 5.4 Serving

The registered model returns raw scores, (n, 11, 384) → (n, 10), in MLflow `pt2` format. The model service
([ml/serve.py](../../ml/serve.py)) embeds unseen card texts on the fly. The computer player plays the
top-scoring card. It asks for a new hand if its best score falls below the 10th percentile of best scores
in training hands, and it votes for the top-scoring submission.

---

## 6. Results

### 6.1 Nightly cycles

Test MRR per cycle (variant A). **Each row has its own, newer test set**, so compare across a row, not down a
column.

| Snapshot | Train / test picks | Popularity | linear e5 | mlp e5 | linear minilm | mlp minilm | linear granite | mlp granite | Challenger |
|---|---|---|---|---|---|---|---|---|---|
| 11-06 | 267 / 145 | 0.294 | 0.322 | 0.320 | 0.340 | **0.367** | 0.297 | 0.287 | linear granite v1 |
| 11-08 | 2,018 / 424 | **0.388** | 0.380 | 0.383 | 0.375 | 0.367 | 0.368 | 0.359 | mlp e5 v2 |
| 11-09 | 2,434 / 589 | 0.362 | **0.381** | 0.380 | 0.358 | 0.355 | 0.358 | 0.358 | mlp e5 v3 |
| 11-11a | 2,539 / 761 | 0.376 | **0.395** | 0.392 | 0.360 | 0.355 | 0.368 | 0.371 | mlp e5 v4 |
| 11-11b | 2,739 / 900 | 0.392 | **0.399** | 0.393 | 0.353 | 0.353 | 0.357 | 0.361 | linear e5 v6 (**champion**) |
| 11-12 | 3,180 / 705 | **0.411** | 0.391 | 0.384 | 0.374 | 0.355 | 0.372 | 0.359 | linear e5 v7 |
| 11-14a | 3,985 / 1,419 | **0.380** | 0.378 | 0.379 | 0.379 | 0.377 | 0.366 | 0.365 | mlp e5 v8 |
| 11-14b | 4,528 / 1,624 | **0.399** | 0.392 | 0.396 | 0.382 | 0.379 | 0.372 | 0.374 | mlp e5 v9 |
| 11-15 | 4,703 / 1,405 | **0.410** | 0.401 | 0.402 | 0.389 | 0.380 | 0.387 | 0.386 | mlp e5 v10 |
| 11-16 | 4,955 / 1,348 | **0.406** | 0.396 | 0.396 | 0.388 | 0.375 | 0.375 | 0.374 | mlp e5 v11 |
| 11-17 | 5,047 / 1,555 | 0.404 | **0.405** | 0.400 | 0.393 | 0.378 | 0.381 | 0.381 | mlp e5 v12 |
| 11-18 | 5,349 / 1,848 | **0.397** | 0.387 | 0.385 | 0.383 | 0.384 | 0.368 | 0.365 | linear e5 v13 |
| 11-19 | 5,887 / 1,745 | **0.408** | 0.401 | 0.401 | 0.387 | 0.384 | 0.373 | 0.374 | linear e5 v14 |

What the table shows:
- Since ~2,000 training picks, **popularity is best or tied in 9 of 12 cycles**. The best trained model is
  within ±0.02 of it, always inside the noise band.
- **e5 > minilm ≈ granite** consistently, by about 0.01–0.03 MRR.
- **linear ≈ mlp**: the 64× larger MLP never wins by more than noise.
- Test MRR drifts up slowly (0.38 → 0.40) for *every* row, popularity included. That is consistent with
  better card coverage, not with better models.
- The first cycle (267 picks) is the only one where text models clearly beat popularity: with little data
  the text is a better prior than a play rate that rests on one or two observations. With more data,
  popularity catches up and the text adds nothing on top.

Latest cycle in full (11-19 snapshot, 49 test games):

| Run | Train MRR | Val MRR | Test MRR | Test top-1 | Test top-3 | Test log loss | Best epoch |
|---|---|---|---|---|---|---|---|
| random | 0.293 | 0.293 | 0.293 | 0.100 | 0.300 | 2.303 | |
| **popularity** | 0.523* | **0.399** | **0.408** | **0.205** | **0.476** | **2.213** | |
| linear e5 | 0.420 | 0.391 | 0.401 | 0.199 | 0.466 | 2.223 | 311 |
| mlp e5 | 0.426 | 0.390 | 0.401 | 0.198 | 0.467 | **2.213** | 45 |
| linear minilm | 0.414 | 0.384 | 0.387 | 0.183 | 0.450 | 2.232 | 453 |
| mlp minilm | 0.469 | 0.391 | 0.384 | 0.175 | 0.457 | 2.241 | 75 |
| linear granite | 0.401 | 0.377 | 0.373 | 0.172 | 0.428 | 2.250 | 193 |
| mlp granite | 0.416 | 0.382 | 0.374 | 0.172 | 0.423 | 2.253 | 55 |

\* in-sample: the training picks are counted in their own play rates.

Log loss tells the same story: the best model is 2.21 against 2.30 for random, i.e. the played card gets
on average ~11% probability instead of 10%. **The models are barely more confident than chance.**

### 6.2 Experiment notebooks (snapshot 2026-11-11, 2,739 train / 546 val / 900 test picks)

| Model | Params | Val MRR | Test MRR | Test top-1 | Test log loss |
|---|---|---|---|---|---|
| popularity | | 0.372 | 0.392 | 0.194 | 2.244 |
| pipeline linear e5 | 1,537 | 0.384 | 0.399 | 0.204 | 2.227 |
| pipeline mlp e5 | 98,433 | 0.381 | 0.393 | 0.200 | 2.223 |
| bilinear r0 (white only) | 385 | 0.378 | 0.400 | 0.210 | 2.227 |
| bilinear r4 | 3,457 | 0.385 | 0.402 | 0.214 | 2.215 |
| bilinear r16 | 12,673 | 0.392 | 0.401 | 0.212 | 2.215 |
| bilinear r64 | 49,537 | 0.392 | 0.401 | 0.213 | 2.215 |
| popularity, learned scale | 1 | 0.372 | 0.392 | 0.194 | 2.239 |
| **popularity + white** | 386 | 0.399 | 0.411 | 0.216 | 2.204 |
| **popularity + bilinear r4** | 3,458 | **0.405** | **0.412** | 0.218 | **2.198** |
| popularity + bilinear r16 | 12,674 | 0.405 | 0.411 | 0.214 | 2.197 |
| filled sentence, linear | 385 | 0.375 | 0.384 | 0.178 | 2.259 |
| filled sentence, mlp | 24,705 | 0.383 | 0.386 | 0.180 | 2.230 |
| filled sentence + popularity | 386 | 0.395 | **0.414** | 0.219 | 2.219 |
| e5-base, linear | 3,073 | 0.390 | 0.393 | 0.184 | 2.217 |
| e5-base, mlp | 196,737 | 0.390 | 0.391 | 0.186 | 2.218 |
| huSpaCy tokens, linear / mlp | 401 / 25,729 | 0.371 / 0.383 | 0.370 / 0.373 | | |
| huSpaCy lemmas, linear / mlp | 401 / 25,729 | 0.379 / 0.383 | 0.356 / 0.352 | | |

Paired-bootstrap verdicts (vs popularity, or vs pipeline e5, over games):
- Pipeline models vs popularity: **not significant** (val +0.011, 95% CI [−0.014, +0.037]).
- **Popularity + white text: significant but small** (val +0.027, test +0.019, lower CI bound ≈ +0.002).
- **Bilinear rank > 0 vs rank 0: no measurable gain.** The black card adds nothing the embeddings can express.
- Filled sentence alone < popularity; with popularity ≈ popularity + white.
- e5-base vs e5-small: ±0. A bigger general encoder doesn't help.
- huSpaCy: −0.021 to −0.043 on test, significantly worse in 3 of 4 variants.
- With 22 games per split, the best variant on validation was sometimes the worst on test: **small
  validation differences mostly select noise**.

### 6.3 Hand position (new, from this report's diagnostics)

Share of picks per hand slot, all 9,715 one-blank picks:

| Slot | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| Picked | 10.9% | 10.7% | 10.6% | 11.2% | 12.8% | 7.3% | 7.2% | 7.9% | 8.3% | **13.3%** |

Why: after a submission the played card is removed and the new card is **appended at the end**, so slot 9 is
always the newest card. In the UI the hand is an overlapping fan (`margin-right: -40px` per card, 130 px
cards, [static/css/cards.css](../../static/css/cards.css)): only the last card is fully visible, and on a
~375 px phone about four cards fit before swiping. So slots 0–4 are visible on load, 5–8 need a swipe and are
half-covered, and slot 9 is both fully visible and new. On its own, a position prior (10 parameters) is weak
(test MRR 0.335 vs 0.293 random). Added to popularity + text, it is worth a further +0.018 MRR (section 7.2):
it explains part of the label that has nothing to do with the cards.

### 6.4 Data variants B and C

Every cycle since 11-12: B − A and C − A on test MRR are within ±0.01 with intervals spanning 0, for all
six models. Adding ~1,500 vote-winner examples (C) doesn't help in this architecture. In the first B/C cycle,
mlp granite C was significantly worse.

---

## 7. Learning curve and feature test (run for this report)

Snapshot 2026-11-19, **validation and test held fixed** (2,083 / 1,745 picks, 49 + 49 games). Training uses
random subsets of the 156 training games (3 seeds per fraction, full set once). Same loop and early stopping
as the pipeline; e5 vectors centred.

Models: popularity; position only (10 learned slot biases); pipeline linear e5 (pair features); white-only
e5 (card quality from text, no black card); popularity + white; popularity + position;
popularity + white + position.

### 7.1 Test MRR by training size (mean of 3 seeds below 100%)

| Training games | 12.5% | 25% | 50% | 75% | 100% |
|---|---|---|---|---|---|
| Training picks (mean) | 817 | 1,347 | 2,809 | 4,509 | 5,887 |
| popularity | 0.347 | 0.355 | 0.381 | 0.398 | **0.408** |
| position only | 0.345 | 0.333 | 0.342 | 0.332 | 0.335 |
| white-only e5 (card quality from text) | 0.373 | 0.386 | 0.391 | 0.395 | 0.395 |
| pipeline linear e5 (pair features) | 0.372 | 0.386 | 0.395 | 0.400 | 0.402 |
| popularity + position | 0.375 | 0.382 | 0.402 | 0.413 | 0.423 |
| popularity + white e5 | 0.381 | 0.390 | 0.409 | 0.418 | 0.425 |
| **popularity + white + position** | **0.404** | **0.415** | **0.427** | **0.432** | **0.443** |

Test top-1 at 100%: popularity 0.205, pipeline linear e5 0.199, popularity + white 0.228,
popularity + white + position **0.241**. Test log loss at 100%: 2.213 / 2.223 / 2.181 / **2.145**.

### 7.2 Paired bootstrap over the 49 test games (full training set)

| Comparison | Δ test MRR | 95% interval | Verdict |
|---|---|---|---|
| pipeline linear e5 − popularity | −0.006 | [−0.022, +0.012] | no difference |
| white-only e5 − popularity | −0.013 | [−0.028, +0.005] | no difference |
| position only − popularity | −0.072 | [−0.092, −0.054] | worse |
| popularity + position − popularity | +0.016 | [−0.001, +0.033] | borderline |
| **popularity + white − popularity** | **+0.018** | **[+0.006, +0.029]** | **better** |
| **popularity + white + position − popularity** | **+0.035** | **[+0.020, +0.051]** | **better** |
| popularity + white + position − popularity + white | +0.018 | [+0.002, +0.032] | better |

Does more data help? Full set vs a 25% subset, same model, same test picks (three subsets):

| Model | Gain 25% → 100% (per seed) |
|---|---|
| popularity | +0.043, +0.051, +0.064 (all significant) |
| pipeline linear e5 | +0.013, +0.010, +0.023 (2 of 3 significant) |
| popularity + white + position | +0.028, +0.022, +0.035 (all significant) |

### 7.3 What it means

1. **Data helps the card-level estimate, not the text model.** Popularity gains +0.06 MRR from 817 to 5,887
   picks and hasn't levelled off. The text-only pipeline model gains +0.03, almost all of it before ~2,800
   picks. From 50% to 100% it gains +0.006.
2. **The pipeline's architecture can't use what the extra data gives.** A card's score is a function of its
   text embedding only. Cards with similar embeddings get similar scores, however differently people treat
   them. Popularity has a free parameter per card that sharpens with every observation (median ~21 per card
   now). That is why popularity overtook the text models at ~2,000 picks and keeps pulling away.
3. **Text and card identity are complementary.** Popularity + white beats both alone. Text is the better
   prior for rarely seen cards (the 12.5% column), and popularity is better for well-seen ones. The model
   that combines them, plus a position term, is the best found so far, and it still improves with data.
4. **Still no black-card signal.** None of the gains above uses the black card. Section 6.2 already showed
   the black × white interaction adds nothing with frozen embeddings.

---

## 8. Diagnosis: why more data hasn't helped

| # | Hypothesis | Evidence | Verdict |
|---|---|---|---|
| H1 | **The models learn card quality, not black × white fit** | White-only ≈ pipeline model; bilinear r>0 = r0; popularity ≈ every model | **Strongly supported** |
| H2 | **Frozen embeddings don't encode the joke** | Cosine gap played vs not played ≈ 0.0004–0.004; bigger encoder (e5-base) no gain; Hungarian word vectors worse; filled sentence alone < popularity | **Supported**: the encoders were trained for semantic similarity/retrieval, and humour is incongruity, not similarity |
| H3 | **Label noise / low ceiling** | Log loss 2.21 vs 2.30 for random; top-1 ≈ 0.20; a pick mixes taste, strategy and UI; no player ID | **Likely, size unknown**: no ceiling measurement exists |
| H4 | **The architecture can't use card identity** | Text-only model plateaus at ~0.40 while popularity keeps rising with data; popularity + white beats both (section 7) | **Confirmed: the main reason more data stopped helping** |
| H4b | **Too little data for the black × white interaction** | 90,614 distinct pairs, 0.4% seen ≥3 times | **Partly**: pairs can't be memorised, so a fit signal must come from understanding the texts (H2) |
| H5 | **Missing non-text features** | Position adds +0.018 on top of popularity + white (significant) | **Supported.** But position is UI bias: use it to *debias* training, not to play |
| H6 | **Model head too weak** | linear ≈ mlp (64× params) ≈ bilinear r64 | **Rejected**: more capacity on the same inputs doesn't help |
| H7 | **Evaluation noise hides progress** | CI half-width ±0.010–0.012; test set changes nightly; validation selection picks noise at small splits | **True, and it matters**: gains below ~0.015 MRR can't be seen. Fix the test set for model research |
| H8 | **Wrong target** | The bot wants to *win votes*; the pick target includes UI bias and strategy; vote data (7,015 votes) is used only as extra examples | **Open**: worth a separate model (section 9) |

**Conclusion.** Yes, the model structure is part of the problem, though not in the head. The pipeline scores
cards **only from frozen text embeddings**. So (1) it can't learn card-specific play rates, which is where the
extra data's value goes, and (2) it can't learn a joke fit, because the two vectors are compared after the fact
by encoders trained for similarity. Bigger heads on the same inputs don't help (H6). What helps or can help:
(a) **card identity / popularity as an input**: measured, +0.018 to +0.035 MRR, and still growing with data;
(b) **position as a debiasing term**: measured; (c) a representation that reads black and white **together**
and is trained on this task (cross-encoder fine-tuning, or an LLM): untested, the only route to a real fit
signal; (d) in-game context and a clearer target (vote winners). Before (c), measure how much is
achievable at all (ceiling).

---

## 9. Candidate directions for the research round

Ordered by expected value per effort.

### 9.0 Quick win, no research needed: popularity (and position) into the pipeline

> **In progress (2026-11-20):** `popwhite` is in `ml/train.py`, the model service and `ml.promote`. The
> pipeline's own run on the 11-19 snapshot reproduces the experiment: test MRR 0.426, top-1 0.229, top-3 0.491
> (linear e5: 0.401 / 0.199 / 0.466). Scoring time is unchanged: 10 new cards ~230 ms (embedding), cached <1 ms.
Move notebook 02's structure into `ml/train.py`: `score = a·log pop_LOO(w) + v·w [+ bias(slot), training only]`.
That is +0.018 MRR deployable, significant, and it keeps improving as data grows (section 7). Open points:
popularity has to ship with the model (a card → rate table registered as an artifact, unseen cards get the
prior), and the leave-one-out rates must be used for the training examples. Every research model below
should keep this as its *base* and learn only the residual. A new structure should beat **0.425
(popularity + white)**, not 0.408.

### 9.1 Measure the ceiling first: LLM judge on a test sample

> **Deferred (decided 2026-11-20): no LLM, no paid API calls.** Kept here as an idea only. Without it, the
> ceiling stays unmeasured. The black-card shuffle test (a model must get worse when the black card is
> replaced by a random one) is the in-house substitute for "is there any fit signal": it runs on every model,
> costs nothing, and goes into `ml/evaluate.py` with step 9.0.

Ask a strong Hungarian-capable LLM, zero- or few-shot, to rank the 10 cards for 200–300 test rounds.
Score it with the same MRR and the same bootstrap.
- If it beats popularity + white (game-bootstrap interval above 0) **and** gets worse when the black card is
  swapped for a random one, the text **does** carry a fit signal and the problem is
  representation. Go to 9.2 (and distil from the LLM).
- If it doesn't, the pick label is mostly noise and taste. Invest in context, personalization and the vote target instead.

### 9.2 Cross-encoder fine-tuning (listwise)
Feed `"[black with blank filled] "` or `"[black] [SEP] [white]"` into a multilingual or Hungarian encoder
(huBERT, XLM-R base, mE5-base/-small, mDeBERTa) and **fine-tune** it end to end with the same softmax-over-hand
loss, plus `a·log popularity + position bias` as fixed side inputs (so it only learns the residual).
- Cost: 10 forward passes per example; ~6k training hands ≈ 60k pairs per epoch. Feasible on one GPU
  (the roadmap already lists GPU fine-tuning on GCP), slow on this server's CPU.
- Pretraining or auxiliary data: vote rounds (submissions as hands), rejected hands (4,286 new-hand events:
  all 10 cards negative for that black card), and possibly public English CAH data via translation.
- Risk: overfitting with 6k examples. Mitigation: freeze lower layers, LoRA, strong early stopping on a fixed test set.

### 9.3 Gradient-boosted ranker over engineered features
LightGBM/XGBoost `lambdarank` (group = hand) with: smoothed popularity (leave-one-out), times shown,
hand slot, card age in the hand (rounds since dealt), slot-9 / "new card" flag, text length, cosine
similarities (black–white under each encoder), embedding PCA components, black-card category, vote mode,
players in game, and whether the card was in a rejected hand earlier in the game. Cheap, strong on small
tabular data, and gives feature importances that explain what drives picks.

### 9.4 Card ID embeddings with text as a cold-start prior
A hybrid recommender: `score = text_head(w) + id_embedding(w) [+ interaction(b, w)]`, with the ID part
regularised towards 0 (it falls back to text for rare or new cards). It is the learned version of
"popularity + white", and can learn card×card-type affinities that popularity can't. Literature: hybrid
collaborative filtering / matrix factorization with side information.

### 9.5 In-game context (group taste)
No player IDs exist, but within a game the group's taste is observable: which cards won earlier rounds,
which the player passed over. Features or an attention layer over "this game's previous winners" (their
embeddings) let the model adapt to the table, like session-based recommendation. Most games last
~10+ rounds, so there is context to use from round 2 on.

### 9.6 Debias the label: position-aware training
Treat slot as a nuisance: train `score = humour(b, w) + bias(slot)` and use only `humour` at inference.
Literature: unbiased learning to rank (Joachims et al. 2017, position-bias estimation), PAL (Guo et al. 2019).
Alternatively fix the UI (randomise order, or show every card fully) so future data is cleaner.

### 9.7 Change the target: predict the vote winner
The computer player's goal is to win votes. Train a separate model on decided rounds: submissions as the
candidate set, human votes as the label (7,015 votes; a vote distribution is a softer, less biased label than
one player's pick). It is listwise too, and has no UI-slot bias in the same form, though shown order exists.
Compare it with the pick model on its own task, and online by win rate.

### 9.8 Set-aware scoring (lower priority)
A small transformer or Set Transformer over the 10 hand cards (+ black) so each card's score can depend on
the alternatives. Expected gain is small while H1–H3 hold, because the inputs are the problem, not the
independence of the scores.

### 9.9 Modelling taste heterogeneity without IDs (lower priority)
Latent-class or mixed logit (discrete choice models): a few latent "taste types" with a mixture over
them, inferred per game from earlier picks. It's the statistically principled version of 9.5.

---

## 10. Questions for the deep research

1. For **humour / joke-completion ranking** (Cards Against Humanity, Apples to Apples, the *New Yorker*
   caption contest, SemEval humour tasks), which model families beat a card-popularity baseline, and by how much?
   What did "Cards Against Humanity" ML studies report about popularity vs. content models?
2. Do **fine-tuned cross-encoders** beat frozen bi-encoder embeddings on humour or incongruity tasks with
   ~5–10k labelled choices? What were the best training setups (listwise loss, LoRA, layer freezing)?
3. How well do **LLMs** (zero-shot, few-shot, fine-tuned) predict human humour preferences, especially in
   **Hungarian** or other lower-resource languages? Is an LLM a usable ceiling probe, and can it be distilled
   into a small model fast enough for CPU serving (<1 s per hand)?
4. What are the best **Hungarian or multilingual encoders** (2025–2026) for fine-tuning on short informal
   texts with slang and wordplay (huBERT, XLM-R, mDeBERTa, ModernBERT-multilingual, Qwen3-Embedding, …)?
5. What are proven methods for **position/presentation bias** in implicit choice data from small datasets
   (unbiased LTR, randomisation, propensity weighting)?
6. **Hybrid recommenders** with strong item-popularity effects and content cold-start: when does
   content add value on top of item ID / popularity, and how much data does it take?
7. How to measure the **noise ceiling** of a choice task where each example is unique (no repeated
   annotators): agreement estimates from vote data, or bounds from repeated (black, white) pairs?
8. **Group/context adaptation** without user IDs: session-based models for small groups, few-shot taste
   inference from 1–10 prior choices.
9. Would predicting **vote winners** (pairwise/listwise among submissions) give a better-learnable and more
   useful target than predicting a player's own pick?

---

## Appendix: reproduce

```bash
venv-ml/bin/python -m ml.pipeline --force                 # one nightly cycle (MLflow exp "pick-model")
venv-ml/bin/python -m ml.notebooks run all                # notebooks 01–05 (exp "pick-model-experiments")
venv-ml/bin/python -m ml.promote --dry-run                # challenger vs champion, paired bootstrap
```

The learning-curve script of section 7 isn't in the repository yet. It is logged in MLflow, experiment
`pick-model-experiments`, together with the data diagnostics (section 3, 6.3) and the encoder-cost measurement:
runs `06_learning_curve 2026-11-19_0a59741e8a66` (91 nested runs, script + CSV + log as artifacts),
`data diagnostics 2026-11-19_0a59741e8a66` and `encoder cost: tokens and compute size`, all tagged
`logged_after_run`. It is the natural next notebook:
`06_learning_curve` (fixed val/test, random game subsets, the seven models above). All models in it used
the linear training config (Adam lr 1e-2, weight decay 1e-3, max 600 epochs, patience 25), e5 vectors centred,
and leave-one-out popularity for the training examples.

| Thing | Where |
|---|---|
| Task, split, metrics, bootstrap | [ml/evaluate.py](../../ml/evaluate.py) |
| Embeddings | [ml/features.py](../../ml/features.py) |
| Pipeline models and training loop | [ml/train.py](../../ml/train.py) |
| Experiment helpers | [ml/experiments.py](../../ml/experiments.py), [notebooks/](../../notebooks/README.md) |
| Data schema | [gameplay-logging.md](gameplay-logging.md) |
| Serving and computer-player rules | [computer-player.md](computer-player.md), [ml/serve.py](../../ml/serve.py) |
