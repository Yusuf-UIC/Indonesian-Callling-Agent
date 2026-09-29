# 🇮🇩 Indonesia Voice AI Banking Agent (Solace Event-Driven)

An ultra-low latency, event-driven voice and text interactive AI banking assistant for Bahasa Indonesia. Built with an enterprise event-driven architecture over **Solace PubSub+**, powered by **Generative LLM Function Calling (Google AI Studio Gemini 2.0 Flash / Groq)**, **Whisper STT**, and **Edge TTS**.

---

## 🌟 Key Features

* **Event-Driven Solace Backbone**: Communicates asynchronously with core banking microservices over Solace PubSub+ (MQTT / SMF) using request-reply patterns.
* **LLM Function Calling & Reasoning**: Powered by **Google AI Studio (`gemini-2.0-flash`)** or **Groq (`llama-3.3-70b-versatile`)**, autonomously issuing tool calls for:
  * `check_balance`: Querying savings/checking account balances.
  * `get_transactions`: Fetching recent transaction history & mutation records.
  * `block_card`: Secure card blocking with multi-turn OTP verification.
* **Voice & Text Capabilities**:
  * **Speech-to-Text (STT)**: Whisper Large v3 (via Groq) or local `faster-whisper`.
  * **Text-to-Speech (TTS)**: Natural Indonesian voice synthesis via `edge-tts` (`id-ID-ArdiNeural` / `id-ID-GadisNeural`).
* **Multi-Turn Session Context & TTL**:
  * Maintains slot-filling state across dialogue turns.
  * Sliding 1-hour Time-To-Live (TTL) session eviction mechanism.
  * OTP retry retention (retains card number when user enters an invalid OTP).
* **Offline Fallback Architecture**: Includes a local semantic intent embedding classifier (`LazarusNLP/all-indo-e5-small-v4`) for offline execution without cloud APIs.

---

## 🏗️ Architecture Overview

```
                      +-----------------------------------+
                      |      User (Voice / Text CLI)      |
                      +-----------------------------------+
                                        |
                   STT (Whisper) / TTS (Edge-TTS) Output
                                        v
                      +-----------------------------------+
                      |   LLM Reasoning Agent (Gemini)    |
                      |   (Function Calling & Dialogue)   |
                      +-----------------------------------+
                                        |
                         Decides Solace Tool Request
                                        v
                      +-----------------------------------+
                      |    Solace PubSub+ Event Broker    |
                      |   (MQTT / SMF Request-Reply)      |
                      +-----------------------------------+
                                        |
                           Event Handler Subscriptions
                                        v
                      +-----------------------------------+
                      |    Core Banking Mock Service      |
                      |  (Accounts, Cards, Transactions)  |
                      +-----------------------------------+
```

---

## 🛠️ Step-by-Step Setup Guide for Another Machine

Follow these instructions to set up and run this project on a clean machine.

### Prerequisites

1. **Python 3.10+** (Python 3.12 recommended)
2. **Docker & Docker Compose** (for running local Solace PubSub+ Broker)
3. **Google AI Studio API Key** (for Gemini) or **Groq API Key**

---

### Step 1: Clone the Repository

```bash
git clone https://github.com/your-username/Indonesia-Calling-Agent.git
cd Indonesia-Calling-Agent
```

---

### Step 2: Create & Activate Virtual Environment

**On Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**On Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

---

### Step 3: Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

*(If `requirements.txt` is missing, install the core packages: `pip install httpx sentence-transformers torch edge-tts pytest python-dotenv paho-mqtt asyncio sounddevice`)*

---

### Step 4: Configure Environment Variables (`.env`)

Copy `.env.example` or create a `.env` file in the project root:

```ini
# LLM Configuration (Google AI Studio Gemini)
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash

# Groq Alternative (Optional)
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# Solace PubSub+ Broker Settings
SOLACE_HOST=localhost
SOLACE_MQTT_PORT=1883
SOLACE_SMF_PORT=55555
SOLACE_USERNAME=admin
SOLACE_PASSWORD=admin
SOLACE_VPN=default
```

---

### Step 5: Start Solace PubSub+ Broker in Docker

Spin up the local Solace broker container using Docker Compose:

```bash
docker compose -f docker/docker-compose.yml up solace -d
```

Verify that Solace container is running:
```bash
docker ps
```
*(Solace Web Admin Console will be accessible at `http://localhost:8080` with credentials `admin/admin`)*

---

### Step 6: Run Automated Tests to Verify Setup

Run the full pytest suite to verify intent classification, session TTL, and Solace event routing:

```bash
pytest tests/unit/test_api.py tests/test_session_context.py tests/test_llm_agent.py -v
```

---

## 🚀 Running the Voice AI Agent

### Option A: Interactive Text CLI (with Audio TTS Output)
To test via text prompt in terminal with audio voice responses via Solace MQTT:

```bash
python app/cli/interactive_voice_solace.py --text --mqtt --llm gemini
```

### Option B: Live Microphone Voice Mode
To test using real microphone speech input (Groq Whisper STT + Edge TTS output):

```bash
python app/cli/interactive_voice_solace.py --live --mqtt --llm gemini
```

### Option C: Deterministic / Offline Mock Mode (No LLM API Key required)
To run fully offline using local semantic embedding intent classification:

```bash
python app/cli/interactive_voice_solace.py --text --mqtt --llm mock
```

---

## 💬 Sample Indonesian Test Queries

Try entering these commands in the terminal CLI:

1. **Check Balance**:
   > *"Cek saldo rekening 1122334455"*
2. **Recent Transactions**:
   > *"Tampilkan 5 transaksi terakhir rekening 1122334455"*
3. **Block Card (Multi-turn OTP flow)**:
   > *"Kartu saya hilang, tolong diblokir"*
   > *(Agent asks for 16-digit card number)*
   > *"4567890123456789"*
   > *(Agent asks for 6-digit OTP code)*
   > *"123456"*

---

## 📁 Repository Structure

```
.
├── app/
│   ├── cli/
│   │   └── interactive_voice_solace.py  # Voice/Text interactive CLI entry point
│   ├── core/
│   │   ├── formatters.py                # Indonesian TTS text formatting utilities
│   │   └── prompts.py                   # System prompts & response templates
│   ├── services/
│   │   ├── agent_solace.py              # Main Banking Solace Agent orchestrator
│   │   ├── llm_agent.py                 # Gemini/Groq LLM agent & Solace Tool Executor
│   │   ├── mock_bank.py                 # Mock core banking backend service
│   │   ├── semantic_intent.py           # Local SentenceTransformers intent classifier
│   │   ├── session_manager.py           # Session context state & 1-hour TTL manager
│   │   ├── solace_client.py             # MQTT/SMF Solace PubSub+ client & event handler
│   │   └── stt_service.py               # Speech-to-text service (Whisper / Groq)
│   └── main.py                          # FastAPI web routes
├── docker/
│   └── docker-compose.yml               # Solace PubSub+ Docker compose manifest
├── tests/
│   ├── test_llm_agent.py                # LLM & Solace tool execution tests
│   ├── test_session_context.py          # Session TTL & multi-turn state tests
│   └── unit/
│       └── test_api.py                  # API endpoint tests
├── .env                                 # Environment configuration file
├── .gitignore                           # Git ignore rules
└── README.md                            # Project documentation
```

---

## 📄 License
Distributed under the MIT License.
