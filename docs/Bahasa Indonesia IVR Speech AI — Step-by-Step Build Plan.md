# Bahasa Indonesia IVR Speech AI — Technical Design & Build Plan

---

## 1\. Use case

Bank customers call in and speak naturally in Bahasa Indonesia. The system listens, understands the request, retrieves the relevant account data, and responds in natural spoken Indonesian — without requiring a human agent.  
Scope for PoC:

1. Balance enquiry (read)  
2. Recent transactions (read/list)  
3. Card block (write, with identity verification)  
   ---

## 2\. Tech stack, and why each piece was chosen

| Layer | Tool / Model | Rationale |
| :---- | :---- | :---- |
| STT (speech-to-text) | Qwen3-ASR-1.7B, benchmarked against Qwen3-ASR-0.6B | Open-weight (Apache 2.0), supports Indonesian, strong accuracy among open models. The 0.6B variant is benchmarked in parallel because it offers significantly higher throughput at concurrency — a real consideration once multiple calls are running simultaneously |
| Embeddings (intent routing \+ semantic cache) | LazarusNLP/all-indo-e5-small-v4 | Indonesian-tuned sentence embeddings; small enough to  run on CPU. Filters known, simple requests before they reach the larger LLM, reducing both cost and latency |
| LLM — intent/slot-filling tier | Sahabat-AI 9B | Best available tuning for colloquial and slang Indonesian, used for fast classification of simpler requests |
| LLM — dialogue/tool-calling tier | SEA-LION v4 32B or Qwen3-32B (FP8-quantized) | SEA-LION is purpose-built for Southeast Asian languages; Qwen3-32B offers stronger general tool-calling. Both are benchmarked head-to-head before locking in one |
| TTS (text-to-speech) | Cartesia (Sonic 3.5) benchmarked against Azure Speech (id-ID) | Cartesia supports Indonesian natively with sub-100ms latency, the fastest available today, and offers enterprise/dedicated deployment options. Azure is included as a benchmark comparison given its enterprise track record. No open-source Indonesian TTS is currently production-ready, so this remains a licensed component either way |
| Orchestration | Pipecat or LiveKit Agents (self-hosted) | Full control over the call pipeline, no vendor lock-in, runs identically on cloud or on-prem GPU |
| Event backbone | Solace PubSub+ | Decouples the voice pipeline from core banking systems — detailed in Section 4 |
| Backend gateway | Custom API gateway, mTLS, whitelisted tool calls only | Sole integration point between the AI system and core banking/CRM/card systems |

---

## 3\. Data residency and the self-hosted stack

Customer call audio and account data are subject to data residency requirements (Indonesia's PDP Law) — this data cannot leave the bank's approved environment during production operation. This shapes the entire model selection strategy:

* Every model in the stack is open-weight and self-hostable, deployed on infrastructure the organization controls, not a third-party managed API.  
* The same models used during the PoC are the models used in production — only the underlying hardware location changes (cloud GPU during PoC/performance testing, physical on-prem GPU for production). This avoids re-validating accuracy and latency numbers on a different stack later, since containerized, self-hosted models behave identically regardless of where they are deployed.  
* Cloud-hosted reference models (Claude, GPT, ElevenLabs) are used only as benchmark baselines during model evaluation — never in the live call path.  
  ---

## 4\. Architecture

### 4.1 IVR / telephony integration

Incoming calls reach the system as a continuous audio stream (SIP/RTP for real telephony lines, or WebSocket for browser/softphone-based testing), not as a single file delivered after the call ends. Key components at this stage:

* Voice Activity Detection (VAD) determines when the caller starts and stops speaking, enabling the system to begin processing without waiting for a fixed silence timeout.  
* Streaming STT processes audio in small chunks (roughly 100–300ms windows) as it arrives, returning partial transcripts rather than waiting for the full utterance.  
* Barge-in handling detects when a caller interrupts playb ack and immediately halts TTS output, a standard IVR requirement.

### 4.2 End-to-end flow

       Telephony line (SIP/RTP) or browser test client  
                          │  
              Load balancer / SIP gateway  
                          │  
        Media servers (stateless, autoscaled, N+1)  
                          │  
              Orchestrator (Pipecat / LiveKit)  
   ┌─────────────┬────────────────────┬──────────────┬─────────────┐  
STT pool     Embedding router \+     LLM pool       TTS pool  
(Qwen3-ASR)  semantic cache        (Sahabat-AI \+   (Cartesia /  
             (LazarusNLP, CPU)     SEA-LION/Qwen3)  Azure)  
   └─────────────┴────────────────────┴──────────────┴─────────────┘  
                          │  
                    Solace PubSub+  
                  (event backbone)  
                          │  
        Core banking / CRM / card / OTP systems

Call sequence:

1. Caller speaks; media server captures the audio stream  
2. STT transcribes in real time, streaming partial and final transcripts  
3. Embedding router checks whether the request matches a known intent or a recently cached answer; if so, the request proceeds directly to the backend without invoking the larger LLM  
4. If ambiguous, the LLM intent tier classifies the request, and the dialogue tier manages the conversation and determines the required backend action  
5. The required action is published as an event to Solace rather than called synchronously  
6. The relevant backend system (core banking, CRM, card management) consumes the event, processes it, and publishes the response back through Solace  
7. The LLM composes a reply using only the data returned from that event — it is not permitted to state information not sourced from a backend response, which is the primary safeguard against hallucination  
8. TTS converts the reply to speech and returns it to the caller

### 4.3 Role of Solace

Solace PubSub+ sits between the orchestrator and the bank's backend systems, functioning as a reliable message broker rather than a direct point-to-point connection:

* Backend requests (e.g. "retrieve balance for verified customer") are published as events rather than invoked as blocking calls.  
* If a backend system is temporarily slow or unavailable, Solace queues the request rather than causing the call to hang; the orchestrator applies a timeout and falls back to a graceful response ("an agent will follow up shortly") if no response arrives in time.  
* This decouples release cycles and failure domains — a backend outage or slowdown does not directly degrade the voice pipeline's availability.  
* Solace's guaranteed-delivery messaging ensures no request or response is lost even during transient system failures.  
  ---

## 5\. Redundancy and scalability

Redundancy:

* Each layer (STT, embedding router, LLM tiers, TTS) runs as a pool of multiple instances rather than a single point of failure; if one instance fails, the load balancer routes to a healthy instance.  
* Infrastructure is provisioned N+1 — at least one instance beyond measured peak requirement is always available.  
* Every backend call carries a timeout with a defined fallback path, so a slow or failed backend degrades the experience gracefully instead of failing the call outright.

Latency management:

* Streaming architecture at every stage (STT, LLM where supported, TTS) rather than batch processing  
* Two-tier LLM design and the embedding router minimize how often the most expensive model is invoked  
* TTS vendor selection (Cartesia) is weighted toward sub-100ms response time, benchmarked directly against Azure

Scalability:

* Each layer scales independently as its own autoscaled pool; capacity is added to whichever layer is measured as the bottleneck under load testing, rather than scaling the system uniformly  
* Stateless service design allows any available instance to handle any call, enabling elastic scaling with demand  
* At an estimated 50,000-customer base, real peak concurrency is approximately 12–17 simultaneous calls — comfortably within a 100-concurrent design target, providing significant headroom before additional scaling is required  
  ---

## 6\. Infrastructure plan

| Stage | Environment | Purpose |
| :---- | :---- | :---- |
| PoC / performance testing | Rented cloud GPU (e.g. RunPod, Lambda Labs, or a hyperscaler) | Fast to provision, no long-term commitment, identical model stack to production |
| Production | Physical on-prem GPU infrastructure | Satisfies data residency requirements for live customer data |

Minimum viable GPU configuration for PoC: one NVIDIA A100 80GB (or two L40S 48GB as an alternative) — sufficient to run the quantized 32B-parameter dialogue model, the 9B intent model, and Qwen3-ASR concurrently, with the embedding model and orchestrator running on standard CPU instances.  
---

## 6A. Component-level input/output specification

This section details the exact data format flowing between each component, starting from the raw telephony signal through to the response leaving the system.

### IVR / telephony server — raw input

* Signaling: SIP (Session Initiation Protocol) establishes and tears down the call  
* Media: RTP (Real-time Transport Protocol) over UDP carries the actual audio  
* Codec: G.711 (μ-law or A-law depending on region/carrier) — the telephony-standard codec, 8kHz sample rate, 8-bit companded, mono  
* Packetization: each RTP packet typically carries 20ms of audio (160 samples at 8kHz); packets can arrive out of order or be dropped (UDP gives no delivery guarantee), so a jitter buffer on the receiving media server reorders and smooths the stream before use  
* Media server: FreeSWITCH, Asterisk, or Kamailio (paired with RTPEngine for pure signaling/load-balancing at scale) terminates the call and exposes the live audio  
* Streaming out to the AI pipeline: FreeSWITCH's mod\_audio\_fork/mod\_audio\_stream, or Asterisk's ARI externalMedia, forks the live RTP audio to an external WebSocket/gRPC endpoint in real time — this is what makes the audio available to STT while the call is still in progress, not after it ends

### STT — Qwen3-ASR

Input:

* Format: 16-bit linear PCM, mono, resampled from 8kHz (telephony) to 16kHz — most modern STT models, including Qwen3-ASR, are trained on 16kHz audio and lose accuracy on raw 8kHz input  
* Transport: streamed as sequential byte chunks over a persistent WebSocket/gRPC connection, typically 100–300ms chunks (aggregating multiple 20ms RTP packets) — not sent as a complete file  
* Additional parameters: language hint (id for Indonesian), optional streaming/interim-results flag to request partial transcripts as audio arrives

Output:

* Structured JSON containing: transcribed text, a confidence score, word-level timestamps, an is\_final flag distinguishing partial (interim) transcripts from finalized ones, and detected language  
* Partial transcripts are emitted continuously as the caller speaks; a final transcript is emitted once VAD (Voice Activity Detection) determines the caller has finished the utterance

### LLM — Sahabat-AI 9B (intent tier) / SEA-LION v4 or Qwen3-32B (dialogue tier)

Input:

* A structured chat-style message array, not a single string — this is the standard format for tool-calling LLMs:  
  * system message: persona definition, behavioral rules (formal/casual Indonesian handling, number/currency readback rules, the hallucination guardrail), and the tool schema — each available tool defined as a JSON object with a name, description, and parameter schema (e.g. get\_balance(account\_id: string))  
  * user messages: the finalized STT transcript for the current turn, plus prior conversation turns for context  
  * tool messages: results returned from previous tool calls in the same conversation (e.g. the actual balance value fetched from the backend)  
* Generation parameters: low temperature (favoring deterministic, repeatable output over creative variation) given the banking context; a bounded max-token limit; a hard timeout

Output:

* Either a natural-language text response (streamed token-by-token to reduce time-to-first-audio at the TTS stage), or a structured tool call object — a JSON payload specifying which function to invoke and with what arguments (e.g. {"tool": "get\_balance", "arguments": {"account\_id": "..."}})  
* The intent tier's output is typically a lightweight classification (which of the known intents this matches, or "escalate to dialogue tier"); the dialogue tier's output drives the actual tool-calling and conversational response

### TTS — Cartesia (or Azure Speech, benchmark comparison)

Input:

* Text or SSML (Speech Synthesis Markup Language) — SSML is used specifically to control pronunciation of numbers, currency, and account/card digits (e.g. \<say-as interpret-as="currency"\>Rp 1.250.000\</say-as\>, or \<say-as interpret-as="digits"\> with inserted pauses for card numbers read in groups)  
* Parameters: target language/locale (id-ID), selected voice, speaking rate, and streaming flag to request progressive audio output rather than waiting for the full utterance to render

Output:

* An audio stream — typically PCM or a compressed codec (Opus/MP3) suitable for network transport — delivered progressively so playback can begin before the full response has finished synthesizing (first-audio-byte latency is the key metric here)  
* Before this audio reaches the caller, it must be transcoded back down to G.711 at 8kHz to be injected into the RTP stream and played into the live call — the reverse of the STT input pipeline  
  ---

## 6B. Solace — background and role in this system

What the company does, and its history: Solace was founded in 2001, originally building specialized hardware messaging appliances for ultra-low-latency use cases — most notably capital markets and trading systems, where microsecond-level message delivery matters. It competed in that space against established messaging platforms like TIBCO and IBM MQ. Over time, Solace evolved from purpose-built hardware into a software platform, PubSub+, while retaining its reputation for high-throughput, low-latency, guaranteed-delivery messaging — a track record that carries directly into why it's a credible choice for a live voice pipeline where delays are directly felt by a caller.  
Core product concept — the "event mesh": Solace's defining architectural idea is the event mesh — interconnected message brokers spanning cloud, on-premises, and edge environments, allowing an event published in one location to be consumed anywhere in the mesh without building point-to-point integrations between every pair of systems. This is directly relevant here: it means the voice AI pipeline (potentially cloud-hosted during PoC) and the bank's core systems (on-prem) can be connected through the mesh without a hard-wired, brittle direct connection between the two.  
Messaging patterns supported: unlike a simple message queue, Solace PubSub+ supports multiple patterns simultaneously — publish/subscribe (broadcast to any interested subscriber), point-to-point queuing (guaranteed once-only delivery to a single consumer), and request/reply (a synchronous-feeling round trip built on top of asynchronous messaging) — the last of which is the pattern used for the LLM's tool calls: the AI "requests" account data and "replies" are routed back to the specific call session waiting for it.  
Protocol support: MQTT, AMQP, REST, JMS, and native bridging to Kafka — meaning it can sit between systems built on different messaging standards without forcing the bank's existing systems to change their own protocol.  
Role in this architecture, specifically:

* Topic structure: requests and responses are organized under a hierarchical topic namespace, e.g. bank/account/{account\_id}/balance/request and the corresponding .../response topic, allowing fine-grained routing and access control per data type  
* Guaranteed delivery: if a backend system is momentarily unavailable, Solace persists the message rather than dropping it, delivering it once the consumer reconnects — this is what allows the orchestrator to apply a timeout and fall back gracefully rather than the call simply failing  
* Decoupling: the voice AI team and the core-banking integration team can evolve their respective systems independently, since neither directly calls the other — both only interact with Solace's topic contracts  
* Enterprise track record: Solace is widely deployed in banking and financial services specifically for real-time event distribution, which is a relevant reference point when justifying its selection over a simpler internal queue or direct API integration

Because the entire stack is containerized and self-hosted, migrating from the cloud PoC environment to physical production hardware is a deployment target change, not a system redesign.  
---

## 7\. Build sequence (execution order, not time-bound)

1. Provision GPU infrastructure and supporting CPU instances  
2. Define the backend API contract for required tools (balance, transactions, card block, identity verification), initially against mock data  
3. Deploy STT and embedding models  
4. Collect representative Indonesian call audio (formal, colloquial, code-switched, telephony-quality) to serve as the evaluation reference set  
5. Run the LLM and TTS bake-offs against the reference set; lock in the selected models  
6. Build the orchestrator and connect STT, embedding router, LLM pool, and TTS  
7. Implement the system prompt, identity verification step, and the hallucination guardrail (responses sourced only from tool-call data)  
8. Integrate Solace as the event backbone between the orchestrator and the mock backend  
9. Integrate the selected TTS vendor  
10. Add resilience behaviors: re-prompting on unclear input, filler responses during backend latency, fallback to a human agent  
11. Load test the complete stack and tune the embedding router's routing threshold  
12. Present results to stakeholders using the same system intended for pilot deployment  
13. Replace the mock backend with real core banking/CRM/card systems via the established API contract  
14. Run a controlled pilot, beginning with internal calls before a limited percentage of live traffic  
15. Expand scope to additional intents only once the initial set demonstrates a stable production track record  
      
    

