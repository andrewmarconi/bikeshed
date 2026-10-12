# Process 2 Concept
Translating a 5-person decision-making dynamic into a codified loop requires separating public actions from private evaluations and establishing routing mechanisms for back-channelling. To observe emergent behaviors like negotiation and personality clashes, the simulation architecture must operate on a tick-based state machine rather than a single linear LLM prompt.

## Phase 1: Initialization & Context Payload

Before the loop begins, the harness must populate the global state and individual agent contexts.

* **Global State:** The specific goal to be decided, environmental constraints (e.g., budget, deadline), the required decision threshold (majority vs. unanimous), and a maximum number of deliberation ticks before a forced timeout.
* **Agent Contexts:** Each of the 5 agents receives a system prompt containing their public persona, their hidden agenda (private utility function or non-negotiables), their baseline risk tolerance, and their initial affinity scores toward the other 4 agents.

## Phase 2: The Core Decision Loop

This sequence represents a single "tick" or round in the simulation. The loop repeats until the voting threshold is met or the tick limit is reached.

### Step 1: Internal Assessment & Public Posturing

* **Private Evaluation:** Each agent privately evaluates the current state of the board against their hidden agenda. The harness queries each agent silently: *"Given the current proposal, what is your stance, and what will you compromise?"*
* **Public Declaration:** Agents broadcast a short initial stance or reaction to the shared main channel. This establishes the baseline for negotiations.

### Step 2: Deliberation & Back-channelling

This step splits the harness routing into two distinct communication layers to capture political dynamics.

* **The Main Table:** Agents can post arguments, share data, or apply public pressure in the main group chat visible to all 5 participants.
* **The Side Channels (Back-channelling):** The harness allows agents to initiate private 1:1 or 1:2 messages. Agents use this space to form coalitions, offer quid-pro-quo trades, gossip, or reveal hidden constraints they are unwilling to state publicly.

### Step 3: Proposal Revision & Concessions

Based on the public debate and private back-channel deals, the parameters of the decision must shift.

* **Leadership Emergence:** The harness prompts the agent who has spoken most, or a pre-designated "chair," to draft a revised proposal that attempts to capture the center of gravity.
* **Concession Logging:** Agents update their internal state variables, logging what they conceded and what they gained.

### Step 4: The Polling Phase

* **Voting:** The harness forces a formal vote on the current revised proposal. Agents cast their vote based on their updated internal assessment.
* **Conditional Routing:**
* *If the threshold is met:* The loop terminates and proceeds to Phase 3.
* *If the threshold fails:* The loop returns to Step 1. To simulate human fatigue, the harness injects an "urgency/frustration" modifier into the agent prompts for the next tick, lowering their threshold for compromise.

## Phase 3: Resolution & Output

Once the voting threshold is cleared, the harness compiles the final state.

* **The Decision:** The final accepted proposal is logged.
* **The Autopsy:** The harness outputs a network graph of the back-channel messages, the shifting affinity scores between agents, and an analysis of which agent compromised the most from their initial hidden agenda.

## Complete Flow

1. **Initialize Environment:** Set the shared goal, constraints (budget/time), required voting threshold (e.g., majority or unanimous), and maximum deliberation ticks.
2. **Initialize Agents:** Inject each of the 5 agents with a public persona, hidden agenda, risk tolerance, and initial peer affinity matrix.
3. **Private Assessment (Loop Start):** Query each agent silently to evaluate the current proposal against their hidden constraints and determine acceptable compromises.
4. **Public Posturing:** Broadcast each agent's initial stance or reaction to the shared main channel to set the baseline for negotiation.
5. **Main Table Deliberation:** Open the public channel for shared arguments, data exchange, and public pressure visible to all agents.
6. **Private Back-channelling:** Concurrently open 1:1 and 1:2 side channels allowing agents to form secret coalitions, gossip, or offer quid-pro-quo deals.
7. **Proposal Revision:** Prompt the most active agent (or a designated chair) to draft a modified proposal attempting to capture the group's center of gravity.
8. **Internal State Update:** Prompt agents to privately log their concessions and gained advantages based on the revised proposal.
9. **Formal Vote:** Force all 5 agents to cast a formal vote on the revised proposal based on their updated internal states.
10. **Condition Check (Threshold Failed):** If the vote fails, increase the "urgency/frustration" modifier in agent prompts to lower compromise thresholds, and route back to Step 3.
11. **Resolution & Autopsy (Threshold Met):** Log the final accepted decision, output the back-channel network graph, and analyze individual agent compromises against their initial hidden agendas.