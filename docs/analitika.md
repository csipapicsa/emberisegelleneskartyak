from pathlib import Path
import textwrap

output_path = Path("/mnt/data/emberisegellenes_kartyak_learning_data_collection.md")

content = r"""
# Learning-Oriented Data Collection Plan for *Emberiség Ellenes Kártyák*

## 1. Purpose

This document specifies the **minimum and recommended gameplay data** that should be logged so that the collected data can later support:

1. black-card–white-card compatibility modelling;
2. prediction of which submitted white card will win a round;
3. an AI player that selects a white card from its hand;
4. an AI judge that ranks submitted answers;
5. optional user-specific humour modelling;
6. later contextual-bandit or reinforcement-learning experiments.

This is **not** a database schema or an implementation specification. It describes the information that is scientifically useful for learning.

The initial system does **not** need to log:

- dwell time on individual cards;
- hover duration;
- mouse movement;
- card-view order;
- scroll events;
- detailed frontend interaction traces.

The most important information is the **choice set**, the **selected card**, the **submitted alternatives**, and the **votes**.

---

# 2. Core modelling problem

The central prediction task is not simply:

> Is this white card funny?

It is:

> How well does a particular white card answer a particular black card, relative to the other available or submitted white cards?

Let:

- \(b\) denote a black card;
- \(w\) denote a white card;
- \(H_u\) denote the white cards in player \(u\)'s hand;
- \(C_r\) denote the white cards submitted in round \(r\);
- \(u\) denote a player;
- \(g\) denote the game or group context.

A general scoring function is:

\[
s(b,w)
\]

A personalized version is:

\[
s(b,w,u)
\]

A group-aware version is:

\[
s(b,w,u,g)
\]

The data should therefore preserve the relationships between:

- the black-card prompt;
- the cards available to a player;
- the card selected by that player;
- the cards submitted by all players;
- the votes received by each submitted card;
- the winning response;
- the game and round context.

---

# 3. General data-collection principles

## 3.1 Store raw card IDs and text, not only embeddings

The primary data should contain stable card identifiers and the corresponding card text.

Embeddings should be generated later as derived artifacts.

Reasons:

- embedding models may be replaced;
- separate black-card and white-card encoders may later be trained;
- embedding dimensions may change;
- task-specific query and target spaces may be learned;
- old data can be re-embedded with improved models.

Recommended conceptual separation:

```text
Source data:
card_id
card_text
card_type
card_version

Derived data:
general text embedding
task-specific black-card embedding
task-specific white-card embedding
user embedding
group embedding

Do not make a current embedding model part of the permanent definition of the dataset.

3.2 Preserve the complete decision context

A selected card is only interpretable relative to the alternatives.

For player choice modelling, preserve:

black card
cards in the player's hand
selected card

For winner prediction, preserve:

black card
all submitted cards
votes per submitted card
winning card

Without the alternative cards, it becomes difficult to distinguish:

a genuinely strong choice;
a weak choice selected from an even weaker hand;
a strong submission that lost against an exceptional alternative;
a card that is generally popular but poorly matched to the current black card.
3.3 Preserve round-level grouping

Do not store only aggregated lifetime vote counts per white card.

The same white card may perform differently depending on:

the black card;
the competing submissions;
the number of voters;
the number of players;
the game mode;
the particular group.

The training data must retain which responses competed in the same round.

4. Required card-level information
4.1 Black cards

For every black card, preserve:

black_card_id
black_card_text
card_version
number of blanks or required white cards, when applicable
whether the card is active
optional content-pack or source identifier
optional language identifier
Why it is needed

The black card defines the query, prompt, semantic context, and grammatical frame.

A black-card encoder can learn features such as:

topic;
syntactic frame;
required grammatical form;
expected semantic role;
humour type;
level of absurdity;
whether the prompt rewards semantic coherence or incongruity.
Card versioning

If the text changes, retain the version that was displayed at the time of the round.

Otherwise, the same black_card_id may incorrectly refer to different text across the training history.

4.2 White cards

For every white card, preserve:

white_card_id
white_card_text
card_version
whether the card is active
optional content-pack or source identifier
optional language identifier
Why it is needed

A white-card encoder can learn features such as:

topic;
entity type;
grammatical form;
style;
taboo or offensiveness level;
specificity;
absurdity;
cultural-reference content.

The same white card should receive different scores depending on the current black card.

5. Required game-level information

For each game, preserve at least:

game_id
game start timestamp
optional game end timestamp
game mode
number of players
identifiers of participating players
optional anonymous group or lobby identifier
relevant rule configuration

Examples of rule configuration that can affect the data-generating process:

one judge versus all-player voting;
anonymous or non-anonymous submissions;
one vote or multiple votes per player;
whether self-voting is allowed;
number of white cards in each hand;
number of white cards required by a black card;
tie-breaking rule;
whether AI players participated.
Why it is needed

Different game modes should not automatically be treated as statistically identical.

For example:

judge selection represents one person's preference;
group voting represents an aggregate preference;
anonymous and non-anonymous voting may produce different behaviour;
self-voting changes the meaning of vote counts.
6. Required round-level information

For every round, preserve:

round_id
game_id
black_card_id
black-card version used
round timestamp
number of active players
game mode in that round
eligible number of voters
judge ID, when a judge-based mode is used
IDs of all submitted white cards
vote count for every submitted card
winning white-card ID
tie information, if applicable
winning player ID
submission display order, if the interface has a stable or randomized order

A compact conceptual representation is:

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
Why each field matters
round_id

Keeps all competing submissions together.

This is required for listwise ranking and correct negative-example construction.

game_id

Allows rounds from the same group or session to be linked.

It can later support:

group adaptation;
session-level evaluation;
leakage-safe train/test splitting;
modelling changing humour preferences within a game.
black_card_id

Defines the prompt for all submissions in the round.

submitted_white_card_ids

Defines the actual candidate set that competed for votes.

These cards are not random negatives: each was selected by a human player as a potentially good answer.

vote_count_per_card

Provides more information than winner/non-winner labels.

It supports:

vote-share prediction;
ranking with graded relevance;
uncertainty estimation;
distinction between narrow and decisive wins.
winning_card_id

Provides a simple supervised target for winner classification and ranking.

number_of_players

Controls the number of possible submissions and influences win probability.

eligible_voter_count

Required to interpret raw vote counts.

Three votes out of three and three votes out of twelve are not equivalent.

timestamp

Supports:

chronological validation;
detecting changes in humour trends;
detecting card fatigue;
preventing future information from leaking into past predictions.
game_mode

The same vote or win can have different meaning under different rules.

submission display order

Optional but useful if the UI presents answers in an ordered list.

Display order may create position bias. If order is randomized, storing the realized order permits later testing and correction.

7. Required submission-level information

For every submitted answer, preserve:

round_id
player_id
white_card_id
selected white-card version
number of votes received
whether the submission won
optional submission display position
optional whether the submission was created by a human or AI player

Conceptually:

round_id
player_id
white_card_id
votes
won
display_position
player_type
Why it is needed

The submission is the primary black-card–white-card training example.

A submitted losing card is a particularly useful negative because:

it was considered plausible by a human;
it competed under the same black card;
it lost against one or more alternatives.

This is a harder and more realistic negative than a randomly selected unrelated card.

8. Required player-choice information

For every human player decision, preserve:

player_id
round_id
black_card_id
hand_white_card_ids
selected_white_card_id
optional hand order, if it is not randomized or may matter
optional whether the player submitted before a timeout
optional whether a card was automatically selected

Conceptually:

player_id
round_id
black_card_id
hand_white_card_ids
selected_white_card_id
Why the full hand is valuable

The hand identifies the alternatives that were genuinely available.

This permits the model to learn:

Given this black card and these ten cards, which card would a human choose?

The unselected hand cards are informative negatives:

available but not selected

They differ from losing submissions:

selected by a player but not preferred by voters

These two types of negative evidence represent different processes and should remain distinguishable.

Is dwell time needed?

No.

For the first generation of models, the complete hand and final selection provide much more valuable supervision than card-level dwell time.

9. Player identifiers
9.1 Recommended approach

Store a stable pseudonymous player_id when technically and legally appropriate.

Avoid storing unnecessary directly identifying information in the modelling dataset.

The initial global model can ignore the player ID, but retaining it makes later user modelling possible.

Why collect it now

The same player must be observable across multiple rounds to estimate stable preferences.

If identifiers are not retained, later personalization cannot reconstruct historical user behaviour.

A feature can be ignored later. Missing historical identity cannot be recreated later.

9.2 Distinguish player roles

A player can reveal preference through at least two behaviours:

the card they submit;
the card they vote for.

These should not automatically be treated as the same preference.

Possible representations:

u
submit
u
vote

A person may:

submit risky or strategic answers;
vote for safer answers;
adapt submissions to the group;
personally prefer a different humour style.

Therefore, preserve both submission and voting histories when the game mode provides them.

10. Voting information

When individual votes are available, preserve:

round_id
voter_player_id
voted_white_card_id
timestamp of the final vote
whether self-voting was possible
whether the voter was eligible to vote
optional vote rank if ranked voting is supported

If individual votes are not retained, at minimum preserve:

vote count per submitted card;
total eligible voters;
number of votes cast.
Why individual votes are valuable

Aggregate vote counts support a global humour model.

Individual votes additionally support:

user-specific humour profiles;
group segmentation;
personalized judges;
user-aware AI players;
analysis of preference heterogeneity.
Vote share

Raw votes should be transformed when comparing rounds with different voter counts.

vote share
r,i
	​

=
V
r
	​

v
r,i
	​

	​


where:

v
r,i
	​

 is the number of votes received by submission i in round r;
V
r
	​

 is the number of votes cast or eligible voters, depending on the game rules.

Keep the raw counts as well. Do not store only the normalized value.

11. Optional but high-value context

The following are not required for the first model, but they are lightweight and potentially useful.

11.1 Group or lobby identifier

A pseudonymous group ID can support:

s(b,w,g)

This may capture recurring group-specific humour.

11.2 Round number within the game

Useful for detecting:

adaptation to the group;
fatigue;
repeated-card effects;
changing strategies.
11.3 Current game score

Useful only for later strategic or reinforcement-learning models.

A player who is losing may make riskier choices.

11.4 Previous black and winning white cards

These can be reconstructed from the ordered game history if game_id, round_id, and timestamps are retained.

They may support a sequential model of group humour.

11.5 Card exposure frequency

This can be derived if full hands and submissions are logged.

It helps distinguish:

a card that rarely wins because it is weak;
a card that rarely wins because it is rarely dealt.
12. Data that is not initially necessary

The following can be omitted from the first implementation:

dwell time for each white card;
hover events;
mouse trajectories;
click order among cards;
scroll depth;
repeated visual exposure events within a hand;
continuous attention tracking;
detailed browser telemetry.

These features may eventually help analyse hesitation or attention, but they add substantial instrumentation and noise.

The first models will obtain stronger supervision from:

the full hand;
the selected card;
the competing submissions;
the votes;
repeated player behaviour.
13. Derived training datasets

The same raw logs can generate several distinct datasets.

13.1 Black-card–white-card pair dataset

Each submitted card creates a pair:

(b
r
	​

,w
r,i
	​

)

Possible labels:

winner/non-winner;
vote count;
vote share;
rank within the round.

Use case:

global compatibility scoring;
candidate retrieval;
card analysis.
13.2 Pairwise preference dataset

For every winner w
+
 and losing card w
−
 in the same round:

(b,w
+
,w
−
)

The model should learn:

s(b,w
+
)>s(b,w
−
)

A common pairwise loss is:

L
pair
	​

=−logσ(s(b,w
+
)−s(b,w
−
))

Advantages:

simple;
uses within-round comparisons;
less sensitive to absolute scoring scales.

Limitation:

does not use the complete candidate set jointly;
generates correlated training pairs from the same round.
13.3 Listwise winner-ranking dataset

For each round:

(b
r
	​

,C
r
	​

,w
r
∗
	​

)

where:

C
r
	​

={w
r,1
	​

,…,w
r,n
	​

} is the set of submitted cards;
w
r
∗
	​

 is the winner.

Define:

P(w
i
	​

∣b,C)=
∑
w
j
	​

∈C
	​

exp(s(b,w
j
	​

))
exp(s(b,w
i
	​

))
	​


The winner loss is:

L
winner
	​

=−logP(w
∗
∣b,C)

Advantages:

matches the actual round structure;
considers all submitted alternatives;
directly trains winner selection.
13.4 Vote-distribution dataset

When several players vote, define the observed distribution:

q
i
	​

=
∑
j
	​

v
j
	​

v
i
	​

	​


The model predicts:

p
i
	​

=
∑
j
	​

exp(s(b,w
j
	​

))
exp(s(b,w
i
	​

))
	​


A distributional loss can be:

L
vote
	​

=−
i
∑
	​

q
i
	​

logp
i
	​


This uses more information than winner-only training.

A card with 49% of votes and a card with 0% of votes are both technically losers, but they should not be treated as equally unsuccessful.

13.5 Player-choice dataset

For every player decision:

(b
r
	​

,H
r,u
	​

,w
r,u
selected
	​

)

where H
r,u
	​

 is the player's hand.

Choice probability:

P(w
i
	​

∣b,H
u
	​

)=
∑
w
j
	​

∈H
u
	​

	​

exp(s
choice
	​

(b,w
j
	​

,u))
exp(s
choice
	​

(b,w
i
	​

,u))
	​


Choice loss:

L
choice
	​

=−logP(w
selected
∣b,H
u
	​

)

This is the most direct training objective for an AI player that must choose from a hand.

13.6 Personalized voting dataset

For an individual voter:

(b
r
	​

,C
r
	​

,u,w
r,u
vote
	​

)

A personalized score can be:

s
vote
	​

(b,w,u)

The probability of voter u choosing card w
i
	​

 is:

P(w
i
	​

∣b,C,u)=
∑
w
j
	​

∈C
	​

exp(s
vote
	​

(b,w
j
	​

,u))
exp(s
vote
	​

(b,w
i
	​

,u))
	​


This can later support:

personalized AI judges;
humour-profile embeddings;
adaptation to known players.
14. Model ideas enabled by the data
14.1 Content-only baseline

Encode black and white card text using a pretrained text-embedding model.

e
b
	​

=f
b
	​

(b)
e
w
	​

=f
w
	​

(w)

A simple compatibility score is:

s(b,w)=e
b
⊤
	​

e
w
	​


This is useful as a baseline, but semantic similarity alone is unlikely to capture humour fully.

14.2 Dual-encoder compatibility model

Use separate black-card and white-card encoders or projection heads:

black card -> query encoder -> q_b
white card -> target encoder -> t_w

Score:

s(b,w)=q
b
⊤
	​

t
w
	​


Advantages:

all white cards can be pre-embedded;
fast scoring over a large card set;
suitable for candidate generation;
allows role-specific embedding spaces.

The training pairs and loss define what proximity means.

14.3 Cross-encoder ranker

Encode the black and white card together:

[BLACK] black-card text [WHITE] white-card text

The model outputs:

s
cross
	​

(b,w)

Advantages:

captures token-level interactions;
can model grammatical completion;
can model incongruity and phrase-level relationships;
likely more accurate for final ranking.

Disadvantage:

every black–white pair must be evaluated separately.

Recommended later architecture:

dual encoder -> candidate generation
cross-encoder -> reranking
14.4 AI judge

Input:

black card;
all submitted white cards.

Output:

winner;
ranking;
predicted vote distribution.

A listwise model is the most natural formulation.

14.5 General AI player

Input:

black card;
the AI player's hand.

Procedure:

score every hand card;
rank the hand;
submit the highest-scoring card or sample from the score distribution.

Deterministic choice:

w
∗
=arg
w∈H
max
	​

s(b,w)

Stochastic choice:

P(w
i
	​

)=
∑
w
j
	​

∈H
	​

exp(s(b,w
j
	​

)/T)
exp(s(b,w
i
	​

)/T)
	​


where T is a temperature parameter.

lower T: stronger, more deterministic play;
higher T: more varied and less predictable play.

The first AI player does not require reinforcement learning.

14.6 Personalized AI player

A personalized scorer can include a user embedding:

s(b,w,u)=s
global
	​

(b,w)+s
personal
	​

(b,w,u)

The user-specific component should be regularized toward the global model, especially for users with little history.

Cold-start users should use the global model.

14.7 Group-adaptive AI player

For recurring groups:

s(b,w,g)

The group representation can be learned from earlier votes within the same game or lobby.

This is a natural intermediate step between supervised learning and reinforcement learning.

15. Reinforcement learning and contextual bandits

Full reinforcement learning should not be the first modelling approach.

The first useful models are supervised:

winner ranking;
vote-distribution prediction;
player-choice prediction.

A contextual bandit becomes relevant when the system actively chooses an action and observes a reward.

Possible actions:

selecting a card from the AI hand;
choosing which black card appears next;
choosing which cards are dealt;
selecting an AI humour strategy.

Possible context:

current black card;
current AI hand;
player or group embeddings;
previous rounds;
game score;
round number.

Possible immediate reward:

R=α⋅vote share+β⋅win indicator+γ⋅diversity−λ⋅repetition

A simple reward for an AI submission could be:

R
round
	​

=
eligible voters
votes received
	​


However, optimizing only votes can create undesirable feedback loops:

repeatedly selecting universally popular cards;
overusing safe humour;
suppressing rare cards;
reducing novelty;
adapting toward increasingly extreme content.

Therefore, any later online-learning objective should consider:

vote performance;
diversity;
repetition;
card exposure;
player retention;
game balance;
safety or moderation constraints.
16. Evaluation strategy
16.1 Data splitting

Do not use only a random row split.

Recommended evaluation:

chronological train/validation/test split;
group-aware or game-aware splitting;
no round may appear in more than one split;
repeated rounds from the same game should be handled carefully;
later evaluate unseen players and unseen cards separately.

A strict chronological split tests whether the model predicts future behaviour.

16.2 Winner-ranking metrics

Useful metrics:

accuracy of predicting the winning card;
Mean Reciprocal Rank;
NDCG;
Recall@K;
log loss;
Brier score;
calibration of predicted win probabilities.

Because each round has a different number of submissions, always compare against:

random-choice performance;
popularity baselines;
card-only baselines;
black-card–white-card text baselines.
16.3 Player-choice metrics

For predicting which hand card a player submits:

choice accuracy;
Mean Reciprocal Rank of the selected card;
NDCG;
log loss;
Recall@K.

Evaluation should distinguish:

known versus unseen players;
frequent versus rare cards;
common versus rare black cards;
global versus personalized models.
16.4 Vote prediction

For vote shares:

cross-entropy or KL divergence;
mean absolute error;
mean squared error;
rank correlation;
calibration.

Vote counts should account for the number of voters.

17. Important biases and pitfalls
17.1 Submission-selection bias

Submitted cards are already filtered by human choice.

A losing submitted card is not necessarily a bad card. It may have been the best card in a weak hand.

Logging complete hands reduces this bias.

17.2 Popularity bias

Frequently dealt or widely applicable cards receive more observations and may dominate the model.

Log complete hands and card exposure so that performance can be normalized by opportunity.

17.3 Position bias

Cards shown earlier or in visually favourable positions may receive more votes.

Store submission display order when applicable.

Randomizing order is preferable.

17.4 Player and group dependence

Rounds from the same players or game are not statistically independent.

Randomly splitting individual submissions can leak player and group information across train and test sets.

17.5 Card text changes

A card whose wording changes is not the same textual stimulus.

Always preserve card versions.

17.6 Sparse user history

Most players may have too few rounds to learn stable individual embeddings.

Personalization should be evaluated only after inspecting:

number of rounds per user;
number of votes per user;
repeat-player rate;
long-tail distribution of user activity.
17.7 AI-generated data contamination

When AI players are introduced, preserve whether each submission came from a human or AI.

Otherwise, later training may recursively learn from the behaviour of older AI models rather than human preferences.

Recommended field:

player_type = human | ai
model_version = optional AI model identifier

Human-only and mixed datasets should remain separable.

18. Minimum recommended logging set
Card catalogue
black_card_id
black_card_text
black_card_version

white_card_id
white_card_text
white_card_version
Game
game_id
game_mode
game_start_timestamp
participating_player_ids
number_of_players
rule_configuration
Round
round_id
game_id
black_card_id
black_card_version
timestamp
number_of_active_players
eligible_voter_count
submitted_white_card_ids
vote_count_per_card
winning_card_id
winning_player_id
judge_id_if_applicable
submission_display_order_if_applicable
Submission
round_id
player_id
white_card_id
white_card_version
votes_received
won
player_type
AI_model_version_if_applicable
Player decision
round_id
player_id
black_card_id
hand_white_card_ids
selected_white_card_id
Individual vote, when available
round_id
voter_player_id
voted_white_card_id

This is sufficient to support the main supervised-learning tasks without logging card-level dwell time.

19. Recommended modelling sequence
Stage 1 — Descriptive analysis

Estimate:

card exposure;
submission rate;
win rate;
vote share;
black-card–white-card co-occurrence;
player activity;
group size;
hand-selection behaviour.

Do not interpret raw win rate without exposure and opponent context.

Stage 2 — Non-personalized baselines

Train:

global white-card popularity;
white-card performance per black card;
text-embedding cosine baseline;
linear or tree-based model using simple features.
Stage 3 — Global black–white ranker

Train:

dual encoder;
cross-encoder;
pairwise ranker;
listwise vote-distribution model.

Primary objective:

Predict the ranking or vote distribution of submitted cards within a round.

Stage 4 — AI player from hand

Train:

Given the black card and a ten-card hand, predict which card a human selects.

Then combine human-choice and crowd-reward information.

For example:

s
AI
	​

(b,w)=αs
choice
	​

(b,w)+βs
vote
	​

(b,w)

This separates:

what humans tend to choose;
what audiences tend to reward.
Stage 5 — User and group adaptation

Add:

submission-style user embeddings;
voting-preference user embeddings;
group embeddings;
within-game adaptation.

Compare against the global model and report cold-start performance.

Stage 6 — Contextual bandit or reinforcement learning

Only after reliable supervised models and online evaluation are available.

Use online learning to adapt:

card choice;
humour strategy;
exploration versus exploitation;
group-specific behaviour.

Do not begin with full reinforcement learning when supervised targets are already available.

20. Final recommendation

The most valuable low-cost information is:

which black card was active;
which white cards each player held;
which white card each player selected;
which white cards were submitted;
how many votes each submission received;
which card won;
which players participated and voted;
which game and round generated the observation;
when it happened;
which card and AI-model versions were active.

Detailed dwell-time and interaction telemetry are not required for the first high-quality models.

With the logging set described here, the collected data can later support:

a global humour compatibility model;
a winner-ranking model;
an AI judge;
an AI player selecting from a hand;
personalized humour profiles;
group adaptation;
contextual-bandit or reinforcement-learning extensions.
"""

output_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")

print(f"Created: {output_path}")
print(f"Size: {output_path.stat().st_size:,} bytes")