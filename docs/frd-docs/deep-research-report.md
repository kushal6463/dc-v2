# ThoughtWire Cognitive Brain Functional Requirements

## Purpose and scope

This document formalizes the internal ThoughtWire brainstorming into a functional requirements baseline for the product’s cognitive core: a system that combines a temporal causal graph, episodic decision capsules, approval workflows, monitoring contracts, durable wake and sleep behavior, and continuous learning. The architecture is novel as a product, but it is grounded in established ideas from causal graphical models, time-unrolled probabilistic models, externalized agent memory with reflection and planning, durable workflow execution, event histories, provenance standards, semantic metric layers, and human-overseen AI governance. citeturn14view0turn14view4turn16view0turn12view5turn13view3

The central functional thesis is that ThoughtWire should treat a **decision** rather than only a **metric** as a first-class enterprise object. OpenMetrics explicitly distinguishes metrics from singular events and notes that metrics aggregate over time, which can lose information; that distinction matters here because a business decision has a lifecycle, evidence, approvals, actions, and outcomes that cannot be represented adequately by a single time series. In parallel, modern semantic layers centralize metric definitions so that downstream tools do not drift into multiple incompatible versions of “revenue,” “CAC,” “margin,” or “conversion rate.” ThoughtWire should therefore operate on both planes at once: the metric plane and the decision plane. citeturn23view1turn23view2

The scope of this specification includes the product philosophy, conceptual architecture, agent runtime behavior, approval mechanics, monitoring, memory, learning, provenance, service boundaries, data contracts, governance expectations, and user experience principles. It does not force the company onto a single vendor stack, but where helpful it explains how Anthropic’s Agent SDK, Claude Code hooks, Managed Agents, and MCP can implement the harness semantics discussed in the brainstorming session. citeturn12view0turn12view4turn12view3turn19view0

For clarity, the document uses the following internal terms in the same sense as the brainstorming discussion. A **thoughtlet** is the smallest meaningful observed fact, such as “mobile conversion dropped 9%” or “budget pacing breached policy.” A **thought** is a coherent working hypothesis built from one or more thoughtlets. A **decision capsule** is the durable, structured packet that contains the trigger, evidence, reasoning, assumptions, expected upside and downside, confidence, required approvals, monitoring contract, and post-outcome reflections. A **monitoring contract** is the explicit promise attached to a capsule that says what to watch, for how long, and under what conditions the originating agent should wake again. A **learning projection** is the governed mechanism by which what was learned from a capsule updates the subconscious enterprise model.

## Foundational product philosophy

ThoughtWire should be designed as an enterprise-native colleague rather than as an alerting bot. In product terms, the causal graph is the **subconscious**: the stable model of how the business works. The decision capsule ledger is the **conscious episodic memory**: the store of what the system has actually seen, believed, recommended, executed, and learned. That distinction is not merely poetic; it mirrors an important engineering separation between stable structural knowledge and time-bound episodes. Research on generative agents similarly found that long-horizon coherence improves when agents keep a memory stream, retrieve relevant records, synthesize higher-level reflections, and plan using those reflections rather than relying on raw prompt history alone. citeturn16view0

This philosophy becomes especially important because an LLM’s context window is only a form of **working memory**, not durable life memory. Anthropic’s documentation explicitly describes the context window as working memory and warns that, in long-running conversations and agentic workflows, growing token count leads to degraded recall and accuracy through “context rot,” making active context management necessary. Anthropic also describes retrieval-augmented generation as runtime retrieval from an external knowledge base into context. Functionally, this means ThoughtWire must not rely on an ever-growing chat transcript as the brain. It must persist enterprise memory externally and inject only the relevant subset for the current investigation or wake event. citeturn24view2turn24view1

The user experience should therefore satisfy five product commandments. First, the system must feel like a responsible new employee who already studied the enterprise before speaking. Second, it must present a case, not just a guess. Third, it must ask for permission before acting. Fourth, it must learn quickly from human instruction and more deeply from real-world outcomes. Fifth, it must always be able to answer the question, “Why did you recommend this?” in a way that is inspectable, documented, and tied to evidence and provenance. NIST’s AI RMF emphasizes validity, reliability, accountability, transparency, explainability, and interpretability as core trustworthiness characteristics, which map directly onto those product commandments. citeturn13view3turn13view4turn13view5

This also means the product should reject several anti-patterns. It should not treat every human comment as a permanent truth that rewrites the company brain. It should not keep dormant compute processes alive for weeks just to preserve the illusion of continuity. It should not collapse causal topology, chat history, monitoring state, and approval history into one storage structure. It should not confuse raw confidence language with calibrated confidence. And it should not let action permissions depend on “best effort” memory alone when the harness can enforce deterministic behavior through hooks, policies, and durable orchestration. Claude Code’s own memory documentation explicitly says memory is treated as context rather than enforced configuration; Anthropic’s hooks system exists precisely because some controls must be enforced rather than merely suggested. citeturn20view0turn20view1

## Conceptual brain architecture

The user’s sketch is directionally correct. It captures the most important conceptual split in the entire architecture: the **X-Y plane** as the causal enterprise map and the **Z axis** as the timeline of decision capsules. The needed refinement is that decision capsules should not be inserted directly into the causal graph as if they were just more causal edges. Instead, they should live in a separate episodic ledger and project only validated learnings back into the graph, rule base, calibration tables, and retrieval memory.

![User-supplied conceptual sketch of the ThoughtWire brain model](sandbox:/mnt/data/IMG_B8A8FDEF-D44E-424D-8FDE-02A32D7CBC37.jpeg)

The sketch is especially useful because it shows three things at once. It shows stable nodes and directed influences in the enterprise graph. It shows decision capsules such as DC1 through DC4 occurring at different time intervals. And it shows projection arrows from those capsules back into the underlying knowledge structure, which is exactly how the learning loop should work. The product should preserve that geometry. The graph below the plane is where the system understands the company; the capsules above the plane are where the system understands what happened in the company.

A clean way to define the architecture is as a layered brain:

| Layer | Internal meaning | Primary role |
|---|---|---|
| Thoughtlet stream | Sensory layer | Emits observations from metrics, events, traces, policies, and user inputs |
| Temporal causal graph | Subconscious enterprise model | Stores metrics, dimensions, causal relationships, thresholds, owners, guardrails, and investigation routes |
| Decision capsule ledger | Conscious episodic memory | Stores each decision episode, approval exchange, action, and outcome |
| Approval graph | Social reasoning layer | Encodes who may review, modify, approve, or reject what |
| Monitoring contract registry | Future-awareness layer | Stores wake conditions, review dates, and post-action monitoring promises |
| Learning projection service | Consolidation layer | Promotes validated learning back into graph edges, rules, calibration, and memory |

The causal layer itself should be treated as a **temporal** causal graph rather than a naïve static DAG. Judea Pearl’s causal diagrams are explicitly defined over directed acyclic graphs, and Murphy’s dynamic Bayesian network treatment shows how sequential systems can be modeled by time slices, with dependencies inside a slice and across slices. This matters because real businesses contain feedback loops such as spend affecting revenue, revenue affecting budget, and budget affecting future spend. A static DAG would incorrectly forbid that. A time-unrolled DAG solves the problem by saying, in effect, “spend at time *t* influences revenue at time *t+1*, which influences budget at *t+2*.” citeturn14view0turn14view4

That temporal requirement has direct consequences for functional design. Every causal edge in ThoughtWire should support not just a source and target, but also a **lag model**, a confidence estimate, a strength estimate, optional directionality qualifiers, a domain scope, and evidence links. For instance, “paid search spend influences sessions” is not enough; the graph should be able to represent “paid search spend on branded campaigns in region US has a short-lag positive effect on sessions and a medium-lag positive effect on attributable revenue, unless inventory is constrained.” Without temporal structure, blast-radius traversal becomes simplistic and frequently misleading. citeturn14view4

The graph should also not be limited to metrics alone. Because ThoughtWire is a decisioning system, it must store the semantic and governance objects that make metrics usable: metric definitions, dimensions, units, derived formulas, policies, thresholds, owners, data sources, approval roles, execution tools, and documented investigation patterns. A semantic layer is useful here because it centralizes metric definitions in one place instead of letting every dashboard or workflow redefine them independently. Policy should likewise be separated from business logic using a policy engine capable of evaluating structured inputs and returning structured decisions. OPA is specifically built to decouple policy decision-making from enforcement and can return arbitrary structured data, not only allow or deny answers. citeturn23view2turn18view0

The most important architectural correction from the brainstorming session is therefore this: **the capsule is not the graph, but the capsule is attached to the graph**. A capsule references the graph subspace it touched, the evidence it used, the nodes and edges it believed were relevant, and the tools and policies it relied on. Then, when learning is promoted, it updates the graph through a governed pipeline rather than via casual direct mutation. That separation is what will allow ThoughtWire to remain explainable years later instead of degrading into an untraceable pile of ad hoc memories.

## Agent runtime and workflow lifecycle

ThoughtWire’s runtime should begin at the **thoughtlet** layer. A thoughtlet is created whenever the platform observes a material, structured fact: an anomaly, a policy breach, a threshold crossing, a data quality warning, a deployment change, a user prompt, or an external event such as a campaign launch or market shift. Because metrics aggregate and can lose event-level detail, the ingestion layer must support both numeric telemetry and event histories. OpenMetrics notes that metrics are distinct from singular events and are an intentional temporal aggregation trade-off, while OpenTelemetry standardizes the instrumentation pathway for observability data such as metrics, traces, and logs. In practice, this means ThoughtWire should ingest both “revenue is down 12%” and “campaign X was paused at 09:17 by user Y.” citeturn23view1turn2search13

Once a trigger is detected, the system shall generate a **context pack** before the agent reasons. This context pack is ThoughtWire’s implementation of the reverse temporal context injection idea from the brainstorming session. It should include the triggering thoughtlets, the relevant causal subgraph, upstream candidate causes, downstream blast radius, thresholds and policies that apply, similar prior decision capsules, prior human corrections attached to the same graph region, known guardrails, available tools, approval requirements, and active monitoring contracts touching the same subspace. The reason to force this pack is the same reason external memory exists in the first place: the LLM’s local context is limited working memory, so the platform must deliberately retrieve the right long-term enterprise memory at wake time rather than hope the model will “just remember.” Anthropic’s RAG guidance and the generative-agent architecture both point in this direction. citeturn24view2turn16view0

A simple canonical structure for the context pack is:

```json
{
  "trigger": {...},
  "relevant_subgraph": {...},
  "candidate_causes": [...],
  "blast_radius": [...],
  "policies": [...],
  "similar_capsules": [...],
  "human_corrections": [...],
  "tool_access": [...],
  "approval_requirements": [...],
  "monitoring_contracts": [...],
  "data_quality_flags": [...]
}
```

After the context pack is assembled, an investigation agent shall traverse the graph. It should inspect parent causes, sibling correlations, lagged dependencies, policies, similar cases, and potential downstream consequences. Its output is not yet an action. Its output is a **decision capsule draft**, which is the structured case file the agent will present for human review or automatic escalation depending on risk tier.

The decision capsule itself should be standardized and deterministic. A required canonical structure is:

```json
{
  "decision_capsule_id": "dc_...",
  "thought_id": "th_...",
  "created_at": "...",
  "domain": "marketing",
  "trigger": {
    "type": "policy_breach",
    "summary": "Budget pacing exceeded approved limit"
  },
  "evidence": {
    "thoughtlets": [...],
    "graph_nodes_touched": [...],
    "queries_run": [...],
    "counterfactuals_considered": [...]
  },
  "reasoning": {
    "hypothesis": "...",
    "assumptions": [...],
    "alternatives_ruled_out": [...],
    "confidence": 0.78
  },
  "recommendation": {
    "actions": [...],
    "expected_upside": {"amount": 6000, "currency": "USD"},
    "expected_downside_if_ignored": {"amount": 9000, "currency": "USD"},
    "time_horizon": "7d"
  },
  "approvals": {
    "required_roles": [...],
    "workflow_id": "wf_..."
  },
  "monitoring_contract": {...},
  "provenance": {...},
  "status": "draft"
}
```

The approval stage shall be modeled as a distinct workflow rather than as part of the causal graph itself. This is where BPMN and DMN are useful reference points. BPMN exists to provide a process notation understandable across business analysts, technical implementers, and business owners, while DMN exists to model the dependencies between related decisions, business knowledge, and input data separately from the process that surrounds them. ThoughtWire should follow that separation. The process says **who reviews what and in what order**; the decision model says **what reasoning dependencies and business knowledge are required**. citeturn22view0turn22view3

That separation is essential because the approval experience in ThoughtWire is not a generic “approve or reject” dialog. Each approver is reviewing a distinct slice of the capsule. In the marketing example discussed in the brainstorming session, the marketing lead may comment on strategy, the creative lead on tone and assets, the finance approver on budget and risk, and the execution owner on safe rollout mechanics. The human correction “you forgot the sixth dimension” is not merely a comment. It is a structured feedback event that can alter the draft capsule, produce an updated estimate, and create a candidate investigation rule for future capsules of the same type. The system shall preserve both the **before** and **after** states of the capsule so that the learning is inspectable later.

When a capsule is approved and transitions into action, the execution layer shall treat actions as orchestrated, compensable workflow steps rather than as one-shot fire-and-forget commands. Eventual consistency and rollback are normal in enterprise systems, not edge cases. Microsoft’s compensating transaction guidance states that systems should record progress, design steps as idempotent commands, and support manual intervention when a failed step cannot be automatically unwound. ThoughtWire should inherit that principle. Every execution step should therefore define what was attempted, whether it can be retried, whether it can be reversed, and what human escalation path applies if compensation fails. citeturn12view9

The monitoring stage begins immediately after action approval or execution, not after the outcome window has already elapsed. Every capsule shall generate a **monitoring contract**. This contract must state what outcome was predicted, what primary and guardrail metrics should be monitored, what duration applies, what early-warning wake conditions exist, and what final reflection time applies. That is how the system moves beyond “monitor the metric” toward “monitor the decision.” Monitoring contracts are first-class objects, not notes in a chat transcript.

The right runtime pattern for sleeping and waking is **rehydration**, not idle compute. Temporal’s workflow model is a strong reference here: workflows can run for long periods, persist event history, and use durable timers that continue across failures or downtime. Temporal explicitly supports persisted timers that can effectively sleep for months and then continue, and it reconstructs prior workflow state from an ordered event history. Anthropic’s Managed Agents documentation also describes stateful sessions with persistent event history. The ThoughtWire requirement should therefore be phrased as a user-visible continuity guarantee, not as a process-level requirement to keep a Python worker alive for a week. The user should feel like the same agent woke up, but the system should achieve that by loading capsule state, context, and event history into a fresh execution. citeturn12view5turn12view6turn12view4

## Memory, learning, and context injection

ThoughtWire needs two distinct learning loops because the brainstorming session correctly identified two different kinds of intelligence. The first is **fast corrective learning** from human review. The second is **slow reflective learning** from real-world outcomes.

Fast corrective learning happens during the approval conversation. If the agent says, “This action should recover $8,000 with 80% confidence,” and a human reviewer says, “You ignored mobile conversion,” the agent must immediately re-open the investigation, consider the missing dimension, update the estimate, and preserve the exchange as structured teaching. This learning must be available the very next time a similar capsule is formed. If it is not, the system will appear disrespectful and incompetent because it will repeat a mistake the user already corrected yesterday.

Slow reflective learning happens after the monitoring window closes or after early wake conditions fire. If the platform predicted a $6,000 recovery at 80% confidence and observed only a $4,000 recovery, the system must perform a post-outcome reflection. Classic calibration work defines the problem cleanly: predicted probabilities should correspond to true likelihoods, and miscalibration can be corrected through calibration techniques such as Platt scaling, isotonic methods, or related post-processing approaches. Later work on calibration makes the same point in modern ML terms: confidence estimates are only useful if they are representative of the true correctness likelihood. Functionally, this means ThoughtWire must track prediction-versus-actual gaps and feed them into calibration and forecast-adjustment logic rather than letting “confidence” remain purely rhetorical language. citeturn15view0turn15view2turn15view3

The core requirement is therefore not merely “store the confidence.” The core requirement is: **store the confidence, store the actual, compare them, update future confidence and expected values accordingly, and retain the update rationale**. In practical terms, ThoughtWire should support at least four learning outputs from any closed capsule.

The first output is a **causal learning candidate**. This suggests that a graph edge should be added, removed, reoriented, weakened, strengthened, or given a different temporal lag.

The second output is an **investigation rule candidate**. This says that before proposing a certain class of action, the system must check a certain class of dimension. The “sixth dimension” example from the brainstorming session belongs here.

The third output is a **calibration update candidate**. This adjusts expected value ranges, confidence intervals, or probability estimates for future recommendations.

The fourth output is an **episodic reflection**. This records the story of what happened, why the system misjudged it, and what future agents should remember when the situation resembles this one again.

These outputs must not all be promoted the same way. A one-off human preference should not instantly become a company-wide causal law. The platform therefore needs a **learning projection governor**. This governor shall classify each learning event by scope, confidence, supporting evidence, reversibility, and blast radius before promotion. The main promotion targets are the causal graph, the investigation rule library, the policy bundle store, the confidence calibration tables, and the episodic memory index. Some learnings should be promoted only locally to a team, a domain, a workflow template, or a capsule family, not globally to the whole enterprise model.

This distinction between raw memory and promoted reflection has a strong analogue in the generative-agent literature. That work does not simply store a stream of observations; it also retrieves by relevance, recency, and importance, and periodically produces higher-level reflections that become their own memory objects. ThoughtWire should behave similarly. Raw observations and approval comments belong in the memory stream. Reusable enterprise judgments belong in promoted reflection objects once validated. citeturn16view0

The harness is what makes this operational rather than aspirational. If ThoughtWire is built on Anthropic’s Agent SDK and Claude Code, the platform can use the SDK’s existing tool loop and context management, but it must not depend on default memory behavior alone. Anthropic’s docs are direct on this point: Claude Code memory is context, not enforced configuration. For deterministic enterprise behavior, the harness should inject context at session and turn boundaries and enforce action policies at tool boundaries. Anthropic’s hooks system supports exactly that, with lifecycle hooks at session, turn, and tool-call cadence, and `PreToolUse` hooks that can allow, deny, ask, defer, modify tool input, and inject additional context. citeturn12view0turn20view0turn20view2turn20view1

A good functional harness design is:

- At session start, load enterprise posture, identity, available systems, and team-level defaults.
- At user prompt submission, construct and inject the current context pack.
- At prompt expansion, attach reusable decision templates or investigation patterns.
- At pre-tool-use, enforce permissions, add environment warnings, route risky actions to approval, or block forbidden writes.
- At post-tool-use, append provenance and update the live capsule draft.
- At session end or stop, persist capsule delta, replay pointers, and monitoring state.

MCP is the cleanest interface boundary for exposing the enterprise brain to the agent. MCP explicitly separates **resources**, **tools**, and **prompts**. Resources provide contextual data such as files, schemas, or application-specific information and can support subscriptions or change notifications. Tools are model-invokable operations over external systems and, per the MCP specification, should always have a human in the loop with the ability to deny invocation. Prompts provide structured instruction templates that users can explicitly invoke. Functionally, ThoughtWire should expose the causal graph and capsule store primarily as MCP resources, investigative and execution actions as MCP tools, and standardized workflows such as “investigate anomaly,” “draft decision capsule,” or “run post-outcome reflection” as MCP prompts. Anthropic’s own MCP and Claude Code documentation explicitly position MCP as the bridge to external tools, databases, and APIs. citeturn19view1turn19view2turn19view3turn12view3

The result is that the agent need not “remember” the entire company in one context window. Instead, it can discover resources, fetch the relevant graph slice, invoke the right computation, and proceed within a constrained, auditable, deterministic runtime. That is the right operationalization of the “AI feels at home inside the enterprise” idea from the brainstorming session.

## Data and service architecture

The data architecture should follow an **event-sourced, projection-oriented** pattern. Microsoft’s event-sourcing guidance explains that append-only event ingestion can be combined with query-optimized projections, and CQRS explicitly separates write models from read models so that each can be optimized independently for scale and clarity. This is the correct shape for ThoughtWire because decisions evolve over time while the product still needs fast query surfaces for “current capsule state,” “all approvals pending with finance,” “similar past recommendations,” or “what changed in this causal region over the last month.” citeturn12view7turn12view8

Accordingly, the platform should have at least the following durable stores:

| Store | Purpose | Canonical records |
|---|---|---|
| Semantic metric registry | Single definition of metrics and dimensions | metric, dimension, unit, formula, owner, lineage, freshness |
| Temporal causal graph | Structural enterprise brain | node, edge, lag, weight, policy link, owner link |
| Capsule event ledger | Authoritative append-only history | capsule_created, evidence_added, approval_requested, approval_comment_added, approved, rejected, action_executed, monitor_created, monitor_triggered, reflection_completed |
| Capsule read models | Fast retrieval surfaces | current_capsule, approval_queue, monitor_dashboard, similar_cases index |
| Monitoring contract registry | Wake and watch layer | contract, schedule, threshold, callback, escalation rule |
| Learning candidate store | Staging for promotion | candidate_edge_change, candidate_rule, candidate_calibration, candidate_memory |
| Provenance store | Explainability and trust | entity, activity, agent links; source datasets; tool runs |
| Policy bundle store | Execution and approval constraints | spend limits, access rules, mandatory checks, recourse rules |
| Retrieval index | Reverse temporal context injection | embeddings, graph anchors, recency, importance, confidence |

The semantic metric registry is essential because the brain cannot reason over business metrics that mean different things in different places. The dbt Semantic Layer is a good reference point: it centralizes metric definitions, handles joins, and ensures downstream tools use the same definitions. ThoughtWire does not need to depend on dbt specifically, but it does need the same principle: a metric node in the graph must point to a versioned, centrally owned definition rather than a dashboard-local formula. citeturn23view2

The graph storage model should support multiple node classes. At minimum, ThoughtWire should support **Metric**, **Dimension**, **Policy**, **Threshold**, **Dataset**, **Event Type**, **Action Template**, **Owner**, **Approval Role**, **Tool**, **Decision Family**, **Learning Rule**, and **Monitor Contract** nodes. Edge types should include **causes**, **influences**, **correlates_with**, **derived_from**, **guarded_by**, **owned_by**, **approves**, **executes_with**, **investigates_with**, **monitors**, **learned_from**, **supersedes**, and **touches**. The important point is not the exact schema spelling. The important point is that every edge should be versioned, scoped, evidence-backed, and promotable or reversible.

The platform must also capture **provenance** as a first-class concern. W3C PROV defines provenance as information about entities, activities, and people involved in producing a piece of data or thing, for the purpose of assessing quality, reliability, or trustworthiness. That maps almost perfectly onto the question a user will ask about a capsule: what data influenced it, what activities were run, which people or agents approved it, and what tools touched it. ThoughtWire should use a PROV-like model for the capsule explanation layer even if its internal schema names differ. citeturn23view4

For data lineage below the decision layer, OpenLineage is a strong reference model. It supports runtime run events, design-time metadata events, dataset metadata events, and extensible facets such as schema, data quality metrics, ownership, and column-level lineage. Its column lineage facet is especially relevant because it allows the system to explain not only that a downstream metric depends on an upstream dataset, but which input columns contributed to which output columns and by what transformations. ThoughtWire should capture OpenLineage-compatible lineage wherever data derivation materially affects decision reasoning. citeturn21view0turn21view1

Policy should be stored separately from capsule text. OPA provides a clean precedent because it separates policy decision-making from enforcement and evaluates arbitrary structured input against policy bundles to return structured outputs. ThoughtWire should use the same idea: approval thresholds, mandatory review roles, spend caps, legal guardrails, safety checks, and action permissions should live as explicit policy artifacts that can be versioned, tested, and audited. A capsule can then cite which policy bundle or policy version constrained it. citeturn18view0

An example of a learning projection record is:

```json
{
  "learning_id": "lrn_...",
  "source_capsule_id": "dc_...",
  "type": "investigation_rule_candidate",
  "scope": "marketing_budget_reallocation",
  "statement": "Check mobile conversion before recommending budget reallocation",
  "evidence": {
    "human_feedback": [...],
    "observed_outcomes": [...],
    "supporting_capsules": ["dc_1", "dc_17", "dc_44"]
  },
  "promotion_status": "pending_review",
  "promotion_targets": [
    "rule_library",
    "context_pack_template"
  ]
}
```

The query layer should support at least the following derived surfaces: current capsule state, full capsule history, capsule provenance graph, active monitoring contracts, graph subspace summary, prior similar capsules by domain and graph anchors, policy applicability maps, approval bottlenecks, calibration drift dashboards, and learning candidates awaiting human or automated promotion. These are read models, not primary truth; the primary truth remains the event ledger plus versioned graph and policy stores.

## Governance, safety, and operating principles

ThoughtWire is explicitly a human-AI teaming system, which makes governance part of the product, not an afterthought. NIST’s AI RMF identifies valid and reliable, safe, secure and resilient, accountable and transparent, and explainable and interpretable behavior as core trustworthiness characteristics. It also specifies that risks related to transparency and accountability should be examined and documented, and that AI models and outputs should be explained, validated, documented, and interpreted in their context to support responsible use and governance. ThoughtWire’s decision capsule requirement aligns directly with that guidance: the capsule is the technical artifact through which explanation, validation, documentation, and context interpretation become operational. citeturn13view3turn13view4turn13view2

The NIST GAI profile is even more directly aligned with the product vision. It states that generative AI systems may warrant additional human review, tracking, documentation, and management oversight. It calls for clearly defined roles for oversight, for user feedback mechanisms with recourse, for ongoing monitoring and periodic review, for defined responsibilities around provenance and incident monitoring, and for after-action review practices. It also notes that structured public or user feedback can help evaluate whether systems are performing as intended and can calibrate and verify traditional measurement methods. ThoughtWire should adopt those principles as hard product requirements, especially because the system is intended to propose actions with business impact. citeturn28view2turn28view0turn28view1turn28view3

That governance model implies a practical approval policy. Low-risk capsules may require only one domain approver. Medium-risk capsules may require domain plus budget or operations approval. High-risk capsules may require domain, finance, legal, security, or executive review depending on the action. The decision engine shall therefore support policy-resolved approval matrices rather than hard-coded paths. A capsule should be inspectable enough that each approver can review only the slice relevant to their authority while still seeing the full causal story if needed.

The system shall also provide **recourse**. If a user says, “I disagree,” “You missed this dimension,” or “Why did you conclude this?,” the system must be able to answer with capsule evidence, graph anchors, prior similar cases, assumptions, and provenance references. W3C PROV and NIST’s focus on transparency and accountability both support the idea that the explanation should not be a fresh hallucinated summary; it should be a traceable reconstruction of entities, activities, agents, and evidence. citeturn23view4turn13view2

Tool execution should remain human-governed where material risk exists. MCP’s tools specification explicitly recommends a human in the loop with the ability to deny tool invocations, visible indicators of tool use, and confirmation prompts for operations. ThoughtWire should elevate that from recommendation to product rule for all real-world actions above a configurable risk threshold. In other words, the system may think autonomously, but action rights shall remain policy-bounded and, when appropriate, human-authorized. citeturn19view2

Operational governance also requires **workflow versioning** because decision capsules and monitoring contracts can outlive deployment cycles. Temporal’s worker versioning guidance is relevant here because it supports controlled rollout of new workflow code while protecting long-running executions, including pinned workflows that continue on the version on which they started. ThoughtWire should therefore treat the agent harness, workflow code, capsule schemas, and learning projection rules as versioned artifacts. Any capsule must record which graph version, policy version, harness version, and workflow version were active when it was created and when it later woke for reflection. citeturn17view0

Finally, the platform shall maintain retention and after-action disciplines. NIST’s GAI profile explicitly calls for document retention over test, evaluation, validation, and verification history, and for after-action review of incident response and disclosures. For ThoughtWire, that means capsule histories, approval conversations, execution events, monitoring outcomes, compensations, and learning promotions should never be silently overwritten. They should be retained according to policy, summarized where needed for retrieval efficiency, but always reconstructable for audit, debugging, and institutional learning. citeturn28view1

## Illustrative scenarios and product experience

The clearest way to understand the full product is to walk through the marketing example from the brainstorming session in operational form.

The system observes a cluster of thoughtlets: Google Ads ROAS is down, Meta spend is pacing above plan, revenue is below expected, top-SKU inventory is healthy, and mobile conversion has recently weakened. The thoughtlet service emits those facts, and the causal graph anchors them to a causal region involving spend mix, traffic quality, conversion, revenue, and inventory constraints. The agent wakes, receives the graph subspace, similar past capsules, current policies, and the active budget-approval rules. It investigates and drafts a capsule recommending a budget reallocation with an expected upside.

The first approver, perhaps the marketing lead, reviews the capsule and says that the recommendation omitted a sixth dimension: mobile conversion. The agent reopens the investigation, retrieves the relevant data, updates the impact estimate from $8,000 to $6,000, lowers or recalibrates confidence, and attaches the reviewer’s reasoning as a structured teaching event. Importantly, that teaching is not left inside free text. It becomes a candidate investigation rule with the provisional statement: “For budget reallocation recommendations, inspect mobile conversion before final recommendation.”

The updated capsule moves forward through the approval graph. Creative may alter the message plan. Finance may reduce the budget envelope or require a narrower test window. Operations may require a rollback condition. Once all required approvals complete, the capsule transitions from **approved** to **executing** and then to **monitoring**. At that time, the platform registers a monitoring contract stating that the capsule expects a specific recovery range over seven days, should wake early if CAC or margin worsens beyond guardrails, and should perform a mandatory final reflection at the end of the observation window.

Seven days later, the contract wakes the originating capsule context. The system compares predicted versus actual effect, computes the gap, and runs an outcome reflection. If actual recovery was only $4,000, the learning layer may conclude that the action direction was correct but magnitude was overstated because mobile conversion remained a limiting factor. That reflection may promote an investigation rule and a calibration update. It may also create a new composite recommendation pattern for future scenarios: “budget shift plus landing-page conversion repair,” rather than “budget shift alone.”

This flow is much closer to how a good human operator actually works than to how a conventional alerting system works. A good human does not merely notice that a metric changed. A good human asks what changed, what else it touches, what action is worth taking, whose permission is needed, what evidence supports the action, how to monitor the consequences, and what lesson to carry into the next case. The product should embody that entire loop.

The car-door analogy from the brainstorming session explains the difference between immediate learning and meta-learning especially well. The immediate lesson is local and action-specific: “check whether a hand is near the door before closing it.” The later meta-lesson is broader and relational: “my actions affect others, and harm has social consequences even after the initial event.” ThoughtWire should learn in the same layered way. A capsule may first learn a tactical rule such as “check mobile conversion.” Later, through repeated monitored outcomes, it may learn a broader principle such as “campaign budget interventions are structurally weaker when downstream conversion friction is unresolved.” The first is a local operating rule; the second is a deeper change to enterprise understanding.

The answerability requirement follows naturally from this design. If a human later asks, “Why did you recommend this?,” the system should not generate a generic explanation from scratch. It should rehydrate the capsule and answer from structured memory: the trigger, the graph nodes touched, the evidence queries run, the approvals received, the assumptions made, the policies consulted, the monitor results, and the outcome reflection. Provenance and event history make this possible; without them, the system would at best produce a plausible but untrustworthy narrative. citeturn23view4turn12view5

The ultimate product experience should therefore feel like this. ThoughtWire notices. ThoughtWire investigates. ThoughtWire presents a reasoned, structured case. Humans teach it in context. It updates the case before action. It acts only within approval and policy boundaries. It keeps watching its own decision after the action is live. It comes back when it learns something important. And over time, the enterprise brain becomes sharper, more calibrated, more explainable, and more native to the company’s way of operating.

## Final design statement

ThoughtWire’s brain should be implemented as a **temporal enterprise causal graph plus an episodic decision-memory ledger, connected by deterministic context injection and governed learning projection**. The graph is the stable structural memory of the business. The capsule ledger is the lived history of what the system believed, proposed, did, and learned. The approval graph encodes organizational judgment. The monitoring contract layer binds predictions to reality. The learning projector turns user corrections and outcomes into updated enterprise intelligence. The harness makes this usable by agents in a way that is deterministic, enforceable, and auditable rather than prompt-fragile. That architecture is consistent with causal graphical modeling, externalized retrieval and reflection for agents, durable event-history workflows, semantic metric definition, provenance tracking, and human-overseen AI governance. citeturn14view0turn14view4turn16view0turn12view7turn23view2turn23view4turn13view3

In plain product language, the system you described is not just an assistant that answers questions about business data. It is a company-native decision organism. It should perceive thoughtlets, reason over the causal body of the enterprise, produce a decision capsule, stand before humans for judgment, act only with permission, watch the world after acting, and consolidate what reality taught it back into the subconscious brain. That is the functional vision this specification captures.