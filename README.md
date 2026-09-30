# 🇮🇩 Indonesia Voice AI Banking Agent & Solace Agent Mesh (SAM)

An ultra-low latency, event-driven voice and text AI banking assistant for Bahasa Indonesia. Built with an enterprise event-driven architecture over **Solace PubSub+**, powered by **Multi-Provider LLM Function Calling (Gemini 3.1 Flash Lite, NVIDIA NIM, Groq, Cloudflare Workers AI)**, **Solace Agent Mesh (SAM)**, **Whisper STT**, and **Edge TTS**.

---

## 🌟 Key Features

* **Solace Agent Mesh (SAM) Multi-Agent Architecture**:
  * Agent-to-Agent (A2A) protocol implementation (`sam/v1/`) with headers, event correlation, and domain separation.
  * Specialized Domain Agents: `AccountOperationsAgent` (balance & transactions) and `CardSecurityAgent` (card block & OTP security).
  * `SAMOrchestratorAgent` for intent routing over the Solace event backbone.
  * Direct OpenAPI (`openapi.json`) connector integration for **SAM Desktop GUI**.
* **Multi-Provider LLM Function Calling & Reasoning**:
  * **Google AI Studio** (`gemini-3.1-flash-lite`)
  * **NVIDIA NIM** (`meta/llama-3.3-70b-instruct`)
  * **Cloudflare Workers AI** (`@cf/meta/llama-3.1-8b-instruct`)
  * **Groq Cloud** (`llama-3.3-70b-versatile`)
  * **Offline Mock Mode** (`mock`)
* **Event-Driven Solace PubSub+ Backbone**:
  * Asynchronous Request-Reply messaging over Solace PubSub+ (MQTT / SMF).
  * Graceful fallback to in-memory `MockSolaceClient` when no live broker is running on `localhost:1883`.
* **Voice & Text Capabilities**:
  * **Speech-to-Text (STT)**: Whisper Large v3 (via Groq) or local SpeechRecognition engine.
  * **Text-to-Speech (TTS)**: Natural Indonesian voice synthesis via `edge-tts` (`id-ID-ArdiNeural`).
  * **Telephony Gateway**: Twilio Webhook & WebSocket 8kHz mu-law audio stream support (`/ws/telephony`).
* **Multi-Turn Session Context & Security**:
  * Multi-turn state retention across dialogue turns with a 1-hour Time-To-Live (TTL) eviction policy.
  * Multi-turn card blocking flow with 6-digit identity OTP verification and retry retention.

---

## 🏗️ System Architecture

```
                                +----------------------------------+
                                |      User (Voice / SAM Desktop)  |
                                +----------------------------------+
                                                  |
                             HTTP REST / OpenAPI / Speech Input
                                                  v
                                +----------------------------------+
                                |     LLM Reasoning Orchestrator   |
                                | (Gemini 3.1 / NIM / Groq / SAM)  |
                                +----------------------------------+
                                                  |
                                Tool Call Routing over Solace Mesh
                                                  v
                                +----------------------------------+
                                |    Solace PubSub+ Event Broker   |
                                |   (MQTT / SMF / In-Memory Mock)  |
                                +----------------------------------+
                                       /                      \
                                      v                        v
                        +---------------------------+   +---------------------------+
                        |  Account Operations Agent |   |   Card Security Agent     |
                        | (Balance & Transactions)  |   | (Block Card & OTP Retry)  |
                        +---------------------------+   +---------------------------+
                                       \                      /
                                        v                    v
                                +----------------------------------+
                                |    Core Banking Backend Service   |
                                | (FastAPI / REST / Mock Database) |
                                +----------------------------------+
```

---

## 🛠️ Prerequisites & Setup Guide

### Prerequisites

1. **Python 3.10+** (Python 3.12 recommended)
2. **Docker & Docker Compose** (Optional — for running local Solace PubSub+ Broker)
3. **API Key** for your preferred provider (**Gemini API Key**, **NVIDIA NIM Key**, or **Groq Key**)

---

### Step 1: Clone & Virtual Environment

```powershell
git clone https://github.com/your-username/Indonesia-Calling-Agent.git
cd "Indonesia Calling Agent"

python -m venv venv
.\venv\Scripts\Activate.ps1
```

---

### Step 2: Install Dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Step 3: Environment Configuration (`.env`)

Configure your provider API key in `.env`:

```ini
# LLM Provider Selection (gemini, groq, nvidia_nim, cloudflare, mock)
LLM_PROVIDER=gemini

# Google AI Studio Gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.1-flash-lite

# NVIDIA NIM (Optional)
NVIDIA_NIM_API_KEY=your_nvidia_nim_api_key_here
NVIDIA_NIM_MODEL=meta/llama-3.3-70b-instruct
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1

# Groq Cloud (Optional)
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# Solace Broker Configuration
SOLACE_HOST=localhost
SOLACE_MQTT_PORT=1883
SOLACE_SMF_PORT=55555
SOLACE_USERNAME=admin
SOLACE_PASSWORD=admin
SOLACE_VPN=default
```

---

### Step 4: Run Automated Test Suite (13/13 Passing)

Run the full pytest suite covering SAM agents, LLM tool execution, session TTL, and REST API endpoints:

```powershell
.\venv\Scripts\pytest.exe tests/test_sam_agents.py tests/test_llm_agent.py tests/test_session_context.py tests/unit/test_api.py -v
```

---

## 🚀 Running the Application

### Option A: FastAPI Backend Server (For SAM Desktop & REST APIs)

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```
* Access Swagger UI docs at `http://localhost:8000/docs`
* OpenAPI JSON spec auto-generated at `http://localhost:8000/openapi.json`

---

### Option B: Interactive Voice & Text CLI

#### 1. Interactive Text Mode (Offline or Online):
```powershell
.\venv\Scripts\python.exe app/cli/interactive_voice_solace.py --text --llm gemini
```

#### 2. Live Solace MQTT Mode:
```powershell
.\venv\Scripts\python.exe app/cli/interactive_voice_solace.py --text --mqtt --llm gemini
```

---

### Option C: Solace Agent Mesh (SAM) Desktop Integration

1. Start FastAPI server: `python -m uvicorn app.main:app --port 8000`
2. Open **SAM Desktop**.
3. Create an API Connector using `openapi.json` from the project root directory.
4. Set Base URL to `http://localhost:8000`.
5. Attach the connector to your `IndonesianBankingAgent` and start chatting!

---

## 💬 Sample Indonesian Test Queries

```text
# 1. Balance Inquiry
Cek saldo rekening 1234567890

# 2. Transaction History
Tampilkan 5 transaksi terakhir rekening 1234567890

# 3. Emergency Card Blocking
Kartu 4567890123456789 hilang, tolong diblokir OTP 123456
```

---

## 📁 Repository Structure

```
.
├── app/
│   ├── api/                     # FastAPI banking & telephony routes
│   ├── cli/
│   │   └── interactive_voice_solace.py  # Interactive CLI entry point
│   ├── core/
│   │   ├── formatters.py        # Indonesian TTS text clean-up
│   │   └── prompts.py           # System prompts & tool definitions
│   ├── services/
│   │   ├── sam/                 # Solace Agent Mesh (SAM) Domain Agents
│   │   │   ├── sam_event.py     # SAM A2A Event & Header protocol
│   │   │   ├── account_agent.py # Account Operations SAM Agent
│   │   │   ├── card_security_agent.py # Card Security SAM Agent
│   │   │   └── orchestrator_agent.py  # SAM Orchestrator Agent
│   │   ├── agent_solace.py      # Banking Agent Solace orchestrator
│   │   ├── llm_agent.py         # Multi-provider LLM Tool Executor
│   │   ├── mock_bank.py         # Mock Core Banking database & service
│   │   ├── semantic_intent.py   # SentenceTransformer fallback intent classifier
│   │   ├── session_manager.py   # Dialogue context state & 1-hour TTL manager
│   │   └── solace_client.py     # MQTT/SMF Solace PubSub+ client with graceful fallback
│   └── main.py                  # FastAPI server entry point
├── docker/
│   └── docker-compose.yml       # Solace PubSub+ Broker Docker manifest
├── tests/
│   ├── test_sam_agents.py       # SAM multi-agent serialization & routing tests
│   ├── test_llm_agent.py        # Multi-provider LLM tool execution tests
│   ├── test_session_context.py  # Multi-turn context & TTL tests
│   └── unit/test_api.py         # REST API endpoint tests
├── .env                         # Active configuration
├── openapi.json                 # OpenAPI spec for SAM Desktop integration
└── README.md                    # Documentation
```

---

## 📄 License
Distributed under the MIT License.
