# Learning-Oriented Data Collection Plan for *Emberiség Ellenes Kártyák*

## 1. Purpose

This document defines which gameplay data should be collected to support future machine-learning models for:

1. black-card–white-card compatibility;
2. winner prediction;
3. vote prediction;
4. an AI player that chooses a white card from its hand;
5. an AI judge that ranks submitted answers;
6. optional user-specific humour modelling;
7. later contextual-bandit or reinforcement-learning experiments.

This is not a database schema and does not prescribe the technical implementation of the logging system.

The initial system does **not** need to record:

- dwell time on individual cards;
- hover duration;
- mouse movements;
- scroll events;
- the exact order in which a player inspected their cards;
- continuous frontend interaction telemetry.

The most valuable learning signal comes from:

- the active black card;
- the cards available in a player's hand;
- the card the player selected;
- the cards submitted in the round;
- votes received by each submission;
- the winning response;
- repeated player and game context.

---

## 2. Core learning problem

The central target is not simply:

> Is this white card funny?

The more useful question is:

> How well does a white card answer a particular black card relative to the other available or submitted white cards?

Let:

- \(b\) be a black card;
- \(w\) be a white card;
- \(H_u\) be player \(u\)'s hand;
- \(C_r\) be the set of submissions in round \(r\);
- \(u\) be a player;
- \(g\) be a game or group context.

A global compatibility model can learn:

\[
s(b,w)
\]

A personalized model can learn:

\[
s(b,w,u)
\]

A group-aware model can learn:

\[
s(b,w,u,g)
\]

The data should therefore preserve the relationship between:

- the black-card prompt;
- the cards available to a player;
- the player's selected card;
- all submitted cards;
- the votes received by each card;
- the winning card;
- the game, round, player, and group context.

---

## 3. General principles

### 3.1 Store raw card IDs and text, not only embeddings

The permanent source data should contain stable identifiers and card text.

Embeddings should be generated later as derived artifacts.

Reasons:

- the embedding model may later be replaced;
- black and white cards may need separate encoders;
- task-specific query and target spaces may be learned;
- embedding dimensions may change;
- all historical data can be re-embedded with improved models.

Recommended conceptual separation:

```text
Source data:
card_id
card_text
card_type
card_version

Derived data:
general text embedding
black-card query embedding
white-card target embedding
user embedding
group embedding
```

The current embedding model should not become part of the permanent definition of the dataset.

---

### 3.2 Preserve complete choice sets

A selected card is only interpretable relative to its alternatives.

For player-choice modelling, retain:

```text
black card
all white cards in the player's hand
selected white card
```

For winner prediction, retain:

```text
black card
all submitted white cards
votes per submitted card
winning card
```

Without the alternatives, it is difficult to distinguish:

- a strong selection;
- the best card from a weak hand;
- a strong submission that lost to an exceptional answer;
- a generally popular card that poorly matched the current black card.

---

### 3.3 Preserve round-level grouping

Do not keep only lifetime vote counts or global win counts per card.

The same card may behave differently depending on:

- the black card;
- the competing submissions;
- the number of players;
- the number of voters;
- the game mode;
- the group.

All submissions that competed in the same round must remain linked.

---

## 4. Card catalogue information

### 4.1 Black cards

For every black card, preserve:

- `black_card_id`
- `black_card_text`
- `black_card_version`
- number of blanks or number of required responses, when applicable
- active/inactive status
- optional content-pack identifier
- optional language identifier

#### Why collect it

The black card defines the query, semantic context, and grammatical frame.

A black-card encoder may learn:

- topic;
- syntax;
- expected semantic role;
- response type;
- humour style;
- absurdity;
- whether semantic coherence or incongruity is rewarded.

If the wording changes, retain the version used in each historical round.

---

### 4.2 White cards

For every white card, preserve:

- `white_card_id`
- `white_card_text`
- `white_card_version`
- active/inactive status
- optional content-pack identifier
- optional language identifier

#### Why collect it

A white-card encoder may learn:

- topic;
- entity type;
- grammatical form;
- style;
- taboo or offensiveness level;
- specificity;
- absurdity;
- cultural-reference content.

A white card should not receive one permanent humour score. Its usefulness depends on the black card and the competing alternatives.

---

## 5. Game-level information

For every game, preserve:

- `game_id`
- game start timestamp
- optional game end timestamp
- game mode
- number of players
- participating player IDs
- optional pseudonymous group or lobby ID
- relevant rule configuration

Rule configuration may include:

- one judge versus all-player voting;
- anonymous versus non-anonymous submissions;
- one vote versus multiple votes;
- whether self-voting is allowed;
- number of cards per hand;
- number of answers required by a black card;
- tie-breaking rule;
- whether AI players participated.

#### Why collect it

Different game modes create different labels.

For example:

- a judge decision represents one person's preference;
- group voting represents an aggregate preference;
- self-voting changes the meaning of the vote count;
- anonymous and visible submissions may produce different behaviour.

---

## 6. Round-level information

For every round, preserve:

- `round_id`
- `game_id`
- `black_card_id`
- `black_card_version`
- round timestamp
- round number within the game
- number of active players
- game mode
- number of eligible voters
- number of votes actually cast
- judge ID, when applicable
- all submitted white-card IDs
- vote count for every submitted card
- winning white-card ID
- winning player ID
- tie information, when applicable
- submission display order, when applicable

Minimum conceptual set:

```text
round_id
game_id
black_card_id
submitted_white_card_ids
vote_count_per_card
winning_card_id
number_of_players
eligible_voter_count
timestamp
game_mode
```

### Why each field matters

#### `round_id`

Keeps all competing submissions together.

This is required for pairwise and listwise ranking.

#### `game_id`

Links rounds from the same session.

This supports:

- group adaptation;
- session-aware evaluation;
- leakage-safe data splitting;
- modelling preference changes during a game.

#### `black_card_id`

Defines the prompt for every submission in the round.

#### `submitted_white_card_ids`

Defines the actual candidate set that competed for votes.

These are valuable hard negatives because every submitted card was considered plausible by a human.

#### `vote_count_per_card`

Contains more information than winner/non-winner labels.

It supports:

- vote-share prediction;
- graded relevance;
- uncertainty estimation;
- distinguishing narrow and decisive wins.

#### `winning_card_id`

Provides a simple target for winner prediction.

#### `number_of_players`

Influences the number of submissions and baseline win probability.

#### `eligible_voter_count`

Required to interpret raw vote counts.

Three votes out of three and three votes out of twelve are not equivalent.

#### `timestamp`

Supports:

- chronological validation;
- detection of humour drift;
- card-fatigue analysis;
- avoidance of future-to-past information leakage.

#### `submission_display_order`

Useful for detecting position bias.

Randomizing the order is preferable, but the realized order should still be preserved when possible.

---

## 7. Submission-level information

For every submitted answer, preserve:

- `round_id`
- `player_id`
- `white_card_id`
- `white_card_version`
- votes received
- whether it won
- optional display position
- whether the player was human or AI
- optional AI model version

Conceptually:

```text
round_id
player_id
white_card_id
votes_received
won
display_position
player_type
AI_model_version
```

#### Why collect it

Each submission creates one black-card–white-card training example.

A losing submitted card is a useful negative because:

- a human considered it plausible;
- it competed under the same black card;
- it lost against specific alternatives.

This is more informative than pairing the black card with a random unrelated white card.

---

## 8. Player-choice information

For every player decision, preserve:

- `player_id`
- `round_id`
- `black_card_id`
- `hand_white_card_ids`
- `selected_white_card_id`
- optional hand order
- optional timeout or automatic-selection indicator

Minimum conceptual set:

```text
player_id
round_id
black_card_id
hand_white_card_ids
selected_white_card_id
```

### Why the complete hand matters

The hand identifies the alternatives that were genuinely available.

This allows the model to learn:

> Given this black card and these ten available cards, which one would a human select?

Unselected hand cards are informative negatives:

```text
available but not selected
```

These differ from losing submissions:

```text
selected by a player but not preferred by voters
```

The two processes should remain distinguishable.

### Is card-level dwell time needed?

No.

The complete hand and final selection provide substantially more useful supervision than dwell time for the first generation of models.

---

## 9. Player identifiers

A stable pseudonymous `player_id` is recommended when technically and legally appropriate.

The modelling dataset should avoid unnecessary direct identifiers.

The first global model may ignore player identity, but retaining it enables later personalization.

### Why collect it now

Historical identity cannot be reconstructed later.

User identity can always be ignored during modelling, but missing repeated-player information cannot be recovered.

Possible future player representations:

\[
u^{\text{submit}}
\]

and

\[
u^{\text{vote}}
\]

These roles should remain distinct because a player may:

- submit risky answers;
- vote for safer answers;
- adapt submissions to the group;
- prefer a different humour style personally.

---

## 10. Voting information

When individual votes are available, preserve:

- `round_id`
- `voter_player_id`
- `voted_white_card_id`
- final vote timestamp
- whether self-voting was possible
- whether the voter was eligible
- optional vote rank if ranked voting is supported

Minimum aggregate fallback:

- vote count for every submission;
- total eligible voters;
- number of votes cast.

### Vote share

A normalized outcome is:

\[
\text{vote share}_{r,i}
=
\frac{v_{r,i}}{V_r}
\]

where:

- \(v_{r,i}\) is the vote count for submission \(i\) in round \(r\);
- \(V_r\) is the number of votes cast.

Keep raw vote counts as well. Do not store only normalized values.

### Why individual votes are valuable

Individual voting supports:

- user-specific humour profiles;
- personalized AI judges;
- group segmentation;
- preference heterogeneity;
- user-aware AI players.

Aggregate vote counts are still sufficient for a strong global model.

---

## 11. Optional but lightweight context

Useful optional fields include:

- pseudonymous group or lobby ID;
- round number within the game;
- current player scores;
- whether a card has appeared earlier in the game;
- timeout indicators;
- card-dealing method;
- randomized hand order;
- randomized submission order.

These can support:

- group-specific humour;
- within-game adaptation;
- fatigue analysis;
- risk-taking strategy;
- repeated-card effects.

---

## 12. Data that is not initially necessary

The first implementation can omit:

- dwell time for each card;
- hover events;
- mouse trajectories;
- card inspection order;
- scroll depth;
- continuous attention tracking;
- detailed browser telemetry.

The strongest early supervision comes from:

- complete hands;
- selected cards;
- competing submissions;
- votes;
- repeated player behaviour.

---

## 13. Derived learning datasets

The same raw gameplay logs can support several distinct training tasks.

### 13.1 Black-card–white-card pair data

Each submitted card creates:

\[
(b_r,w_{r,i})
\]

Possible labels:

- winner/non-winner;
- vote count;
- vote share;
- rank in the round.

Possible use:

- global compatibility scoring;
- candidate retrieval;
- card analysis.

---

### 13.2 Pairwise preference data

For a winner \(w^+\) and a losing card \(w^-\) from the same round:

\[
(b,w^+,w^-)
\]

The desired ordering is:

\[
s(b,w^+) > s(b,w^-)
\]

A common loss is:

\[
\mathcal{L}_{\text{pair}}
=
-\log \sigma
\left(
s(b,w^+) - s(b,w^-)
\right)
\]

Advantages:

- simple;
- uses within-round comparisons;
- less dependent on absolute score calibration.

Limitation:

- does not model the full candidate set jointly;
- creates correlated pairs from the same round.

---

### 13.3 Listwise winner-ranking data

For each round:

\[
(b_r,C_r,w_r^*)
\]

where:

- \(C_r\) is the set of submitted white cards;
- \(w_r^*\) is the winner.

Define:

\[
P(w_i \mid b,C)
=
\frac{\exp(s(b,w_i))}
{\sum_{w_j \in C}\exp(s(b,w_j))}
\]

The winner loss is:

\[
\mathcal{L}_{\text{winner}}
=
-\log P(w^* \mid b,C)
\]

This formulation most directly matches the actual round structure.

---

### 13.4 Vote-distribution data

Observed vote distribution:

\[
q_i
=
\frac{v_i}
{\sum_j v_j}
\]

Predicted distribution:

\[
p_i
=
\frac{\exp(s(b,w_i))}
{\sum_j\exp(s(b,w_j))}
\]

A distributional loss is:

\[
\mathcal{L}_{\text{vote}}
=
-\sum_i q_i \log p_i
\]

This retains more information than a winner-only label.

A card with 49% of votes and a card with 0% are both losers, but they should not receive identical supervision.

---

### 13.5 Player-choice data

For every player:

\[
(b_r,H_{r,u},w_{r,u}^{\text{selected}})
\]

Choice probability:

\[
P(w_i \mid b,H_u)
=
\frac{\exp(s_{\text{choice}}(b,w_i,u))}
{\sum_{w_j \in H_u}
\exp(s_{\text{choice}}(b,w_j,u))}
\]

Choice loss:

\[
\mathcal{L}_{\text{choice}}
=
-\log P(w^{\text{selected}} \mid b,H_u)
\]

This is the most direct objective for training an AI player that selects from a hand.

---

### 13.6 Personalized vote data

For voter \(u\):

\[
(b_r,C_r,u,w_{r,u}^{\text{vote}})
\]

Personalized scoring:

\[
s_{\text{vote}}(b,w,u)
\]

Voting probability:

\[
P(w_i \mid b,C,u)
=
\frac{\exp(s_{\text{vote}}(b,w_i,u))}
{\sum_{w_j \in C}
\exp(s_{\text{vote}}(b,w_j,u))}
\]

This can support personalized AI judges and user humour embeddings.

---

## 14. Model families enabled by the data

### 14.1 Content-only embedding baseline

Encode black and white card text:

\[
e_b=f_b(b)
\]

\[
e_w=f_w(w)
\]

Simple score:

\[
s(b,w)=e_b^\top e_w
\]

This is useful as a baseline, but semantic similarity alone is unlikely to capture humour adequately.

---

### 14.2 Dual-encoder compatibility model

Use separate black-card and white-card encoders or projection heads:

```text
black card -> query encoder -> q_b
white card -> target encoder -> t_w
```

Score:

\[
s(b,w)=q_b^\top t_w
\]

Advantages:

- all white cards can be pre-embedded;
- fast retrieval;
- role-specific embedding spaces;
- suitable for large candidate sets.

---

### 14.3 Cross-encoder ranker

Encode the black and white card together:

```text
[BLACK] black-card text [WHITE] white-card text
```

The model outputs:

\[
s_{\text{cross}}(b,w)
\]

Advantages:

- token-level interaction;
- grammatical compatibility;
- phrase-level incongruity;
- potentially higher ranking accuracy.

Disadvantage:

- every black–white pair must be evaluated separately.

Recommended later architecture:

```text
dual encoder -> candidate generation
cross-encoder -> reranking
```

---

### 14.4 AI judge

Input:

- black card;
- all submitted white cards.

Output:

- predicted winner;
- full ranking;
- predicted vote distribution.

A listwise model is the most natural formulation.

---

### 14.5 General AI player

Input:

- black card;
- AI player's hand.

Deterministic action:

\[
w^*
=
\arg\max_{w \in H}
s(b,w)
\]

Stochastic action:

\[
P(w_i)
=
\frac{\exp(s(b,w_i)/T)}
{\sum_{w_j \in H}\exp(s(b,w_j)/T)}
\]

where \(T\) is a temperature parameter.

- low \(T\): stronger and more deterministic;
- higher \(T\): more varied and less predictable.

The first AI player does not require reinforcement learning.

---

### 14.6 Personalized AI player

A personalized score can be:

\[
s(b,w,u)
=
s_{\text{global}}(b,w)
+
s_{\text{personal}}(b,w,u)
\]

The user-specific component should be regularized toward the global model.

Cold-start users should use the global model.

---

### 14.7 Group-adaptive model

For recurring groups:

\[
s(b,w,g)
\]

The group representation may be learned from earlier rounds and votes in the same game.

This is a natural intermediate step before reinforcement learning.

---

## 15. Reinforcement learning and contextual bandits

Full reinforcement learning should not be the first modelling approach.

The first useful models are supervised:

- winner ranking;
- vote-distribution prediction;
- player-choice prediction.

A contextual bandit becomes relevant when the system selects an action and receives a reward.

Possible actions:

- choosing a card from an AI hand;
- selecting the next black card;
- choosing cards to deal;
- selecting an AI humour strategy.

Possible context:

- active black card;
- AI hand;
- player or group embeddings;
- previous rounds;
- current score;
- round number.

Simple round reward:

\[
R_{\text{round}}
=
\frac{\text{votes received}}
{\text{eligible voters}}
\]

A richer reward may be:

\[
R
=
\alpha \cdot \text{vote share}
+
\beta \cdot \text{win indicator}
+
\gamma \cdot \text{diversity}
-
\lambda \cdot \text{repetition}
\]

Optimizing only votes can create feedback loops:

- repeated use of safe cards;
- suppression of rare cards;
- reduced novelty;
- overuse of extreme content.

Later online objectives should consider:

- vote performance;
- diversity;
- repetition;
- card exposure;
- retention;
- game balance;
- moderation constraints.

---

## 16. Evaluation strategy

### 16.1 Data splitting

Do not rely only on random row splitting.

Recommended:

- chronological train/validation/test split;
- game-aware splitting;
- no round in multiple splits;
- separate evaluation for unseen users;
- separate evaluation for rare or unseen cards.

A chronological split tests future prediction and reduces leakage.

---

### 16.2 Winner-ranking metrics

Useful metrics:

- winner accuracy;
- Mean Reciprocal Rank;
- NDCG;
- Recall@K;
- log loss;
- Brier score;
- calibration.

Always compare with:

- random choice;
- global popularity;
- white-card-only popularity;
- black-card–white-card frequency;
- pretrained embedding baselines.

---

### 16.3 Player-choice metrics

For predicting which card a player submits:

- choice accuracy;
- Mean Reciprocal Rank;
- NDCG;
- Recall@K;
- log loss.

Report separately for:

- known and unseen players;
- frequent and rare cards;
- common and rare black cards;
- global and personalized models.

---

### 16.4 Vote prediction

Useful metrics:

- cross-entropy;
- KL divergence;
- mean absolute error;
- mean squared error;
- rank correlation;
- calibration.

Always account for the number of voters.

---

## 17. Biases and methodological pitfalls

### 17.1 Submission-selection bias

Submitted cards were already filtered by human choice.

A losing submission is not necessarily bad. It may have been the best card in a weak hand.

Logging complete hands reduces this bias.

---

### 17.2 Popularity bias

Frequently dealt or broadly applicable cards receive more observations.

Exposure must be considered when interpreting win rate.

---

### 17.3 Position bias

Cards displayed earlier or more prominently may receive more votes.

Store display order when possible and randomize order in the interface.

---

### 17.4 Player and group dependence

Rounds from the same players and games are not independent.

Randomly splitting submissions may leak player or group information across train and test sets.

---

### 17.5 Card text changes

A changed card is a changed textual stimulus.

Always preserve card versions.

---

### 17.6 Sparse user histories

Many players may have too few observations for reliable individual embeddings.

Before personalization, inspect:

- rounds per player;
- votes per player;
- repeat-player rate;
- long-tail user distribution.

---

### 17.7 AI-generated data contamination

Once AI players are introduced, preserve:

```text
player_type = human | ai
AI_model_version
```

Human-only and mixed datasets must remain separable.

Otherwise, future models may recursively learn from earlier AI behaviour instead of human preferences.

---

## 18. Minimum recommended logging set

### Card catalogue

```text
black_card_id
black_card_text
black_card_version

white_card_id
white_card_text
white_card_version
```

### Game

```text
game_id
game_mode
game_start_timestamp
participating_player_ids
number_of_players
rule_configuration
```

### Round

```text
round_id
game_id
black_card_id
black_card_version
timestamp
round_number
number_of_active_players
eligible_voter_count
number_of_votes_cast
submitted_white_card_ids
vote_count_per_card
winning_card_id
winning_player_id
judge_id_if_applicable
submission_display_order_if_applicable
```

### Submission

```text
round_id
player_id
white_card_id
white_card_version
votes_received
won
player_type
AI_model_version_if_applicable
```

### Player decision

```text
round_id
player_id
black_card_id
hand_white_card_ids
selected_white_card_id
```

### Individual vote, when available

```text
round_id
voter_player_id
voted_white_card_id
```

This is sufficient for the principal supervised-learning tasks without card-level dwell-time logging.

---

## 19. Recommended modelling sequence

### Stage 1 — Descriptive analysis

Estimate:

- card exposure;
- submission rate;
- win rate;
- vote share;
- black-card–white-card co-occurrence;
- hand-selection behaviour;
- player activity;
- group size.

Do not interpret raw win rate without exposure and opponent context.

---

### Stage 2 — Baselines

Train:

1. global white-card popularity;
2. white-card performance per black card;
3. pretrained text-embedding cosine baseline;
4. simple linear or tree-based models.

---

### Stage 3 — Global black–white ranker

Train:

- dual encoder;
- cross-encoder;
- pairwise ranker;
- listwise vote-distribution model.

Primary objective:

> Predict the ranking or vote distribution of submitted cards within a round.

---

### Stage 4 — AI player selecting from a hand

Train:

> Given the black card and a ten-card hand, predict which card a human selects.

Then combine choice and audience reward:

\[
s_{\text{AI}}(b,w)
=
\alpha s_{\text{choice}}(b,w)
+
\beta s_{\text{vote}}(b,w)
\]

This separates:

- what people tend to submit;
- what audiences tend to reward.

---

### Stage 5 — User and group adaptation

Add:

- submission-style user embeddings;
- voting-preference user embeddings;
- group embeddings;
- within-game adaptation.

Compare every personalized model against the global model.

---

### Stage 6 — Contextual bandit or reinforcement learning

Only after reliable supervised models and online evaluation exist.

Potential goals:

- adaptive card choice;
- humour-strategy selection;
- exploration versus exploitation;
- group-specific behaviour.

---

## 20. Final recommendation

The most valuable low-cost information is:

1. which black card was active;
2. which white cards each player held;
3. which white card each player selected;
4. which white cards were submitted;
5. how many votes each submission received;
6. which card won;
7. which players participated and voted;
8. which game and round generated the observation;
9. when it happened;
10. which card and AI-model versions were active.

Detailed dwell-time and frontend telemetry are not required for the first high-quality models.

This logging set can later support:

- a global humour-compatibility model;
- winner ranking;
- vote-distribution prediction;
- an AI judge;
- an AI player selecting from a hand;
- personalized humour profiles;
- group adaptation;
- contextual-bandit or reinforcement-learning extensions.


+ 1 interactino: If the user selected "give 10 more new cards" since it is relevant as well if the user think that the availablw white cards are not good enough.