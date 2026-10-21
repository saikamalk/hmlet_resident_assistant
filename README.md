# Hmlet Resident Assistant

A resident-support assistant prototype demonstrating four guardrails: scope control, grounded answers, prompt-injection
resistance, and explicit action confirmation.

**Core:** approved-data retrieval, safe fallback, input checks, and confirmation-gated actions.  
**Extras:** FastAPI hardening, Streamlit UI, OIDC integration, Redis rate limiting, and a local booking demo adapter.

**Key design decision:** the model never authorizes or executes a state-changing action. Private support tickets are
excluded from the retrieval index.

## Guardrail map

| Guardrail                             | Implementation                                                  | Tests / evaluation                                                                                      |
|---------------------------------------|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| Scope control                         | `app/assistant/guardrails.py`, `app/assistant/service.py`                         | `tests/test_guardrails_adversarial.py`, `tests/test_assistant.py`; `python eval/run_security_eval.py`   |
| Grounded answers                      | `app/knowledge/retrieval.py`, `app/assistant/grounding.py`, `app/assistant/service.py`      | `tests/test_assistant.py`, `tests/test_claim_grounding.py`; `python eval/run_retrieval_eval.py`         |
| Injection and private-data resistance | `app/assistant/guardrails.py`; private ticket file excluded from indexing | `tests/test_guardrails_adversarial.py`; security evaluation                                             |
| Explicit action confirmation          | `app/actions/intake.py`, `app/actions/service.py`, `app/api.py`          | `tests/test_actions.py`, `tests/test_action_intake.py`, `tests/test_api.py`, `tests/test_end_to_end.py` |

Read [GUARDRAILS.md](GUARDRAILS.md) for behavior and limitations, then [ARCHITECTURE.md](ARCHITECTURE.md) for component
interactions. Use [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the short walkthrough.

## Quick start (local development, no OIDC)

This mode disables API authentication and uses the development actor. Run only on your own machine with synthetic data.
Never use it in a shared or production environment.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set your own `GROQ_API_KEY`, plus:

```dotenv
APP_ENV=development
AUTH_DISABLED=true
```

Start the API:

```bash
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

Start the UI in a second terminal:

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open `http://localhost:8501`. Use `http://127.0.0.1:8000` as the API URL. In local bypass mode, leave the bearer-token
field empty.

The project uses the Groq client configured by `GROQ_MODEL` (default: `llama-3.3-70b-versatile`). Without
a working Groq key/model, the project does not provide a full mock conversational mode; model-backed answers cannot be
demonstrated offline.

## Tests and Evaluation

The project includes unit tests and offline evaluation scripts for retrieval, guardrails, and answer routing.

The following results were obtained on the reviewed version on October 14, 2026:

- **Unit tests:** 91 passed
- **Security tests:** 10/10 passed
- **Retrieval and guardrail evaluation:** 18/18 passed
- **Answer routing:** 18/18 cases matched the expected outcomes
- **Required evidence retrieval:** 6/6 cases passed
- **False-positive retrievals for unknown questions:** 0

These evaluations use the project's local test data and do not require live LLM responses. Results may differ if the code or test data changes.

### Run the tests

```bash
python -m unittest discover -s tests -v
python eval/run_security_eval.py
python eval/run_retrieval_eval.py
python eval/run_answer_quality_eval.py
```

The evaluation scripts run locally and help check that changes to retrieval, routing, and guardrails do not break expected behavior.

## API overview

| Method | Route                                                   | Purpose                                |
|--------|---------------------------------------------------------|----------------------------------------|
| `GET`  | `/health`                                               | Liveness                               |
| `GET`  | `/ready`                                                | Readiness                              |
| `POST` | `/v1/sessions`                                          | Create actor-bound session             |
| `GET`  | `/v1/availability`                                      | Search local sample listings           |
| `GET`  | `/v1/bookings`                                          | List local-demo bookings for the actor |
| `POST` | `/v1/sessions/{session_id}/messages`                    | Submit resident message                |
| `POST` | `/v1/sessions/{session_id}/actions/{action_id}/confirm` | Confirm pending actor-owned action     |
| `POST` | `/v1/sessions/{session_id}/actions/{action_id}/decline` | Decline pending action                 |

The booking adapter uses local sample data and does not update Hmlet's live systems.

## Project map

```text
app/       assistant, guardrails, retrieval, grounding, auth, API, actions, booking
data/      sample listings and policies; private tickets are not indexed
tests/     unit and integration regression tests
eval/      offline security, retrieval, and answer-quality checks
streamlit_app.py
README.md
GUARDRAILS.md
ARCHITECTURE.md
DEMO_SCRIPT.md
```

## Limitations and next steps

See the canonical limitations section in [GUARDRAILS.md](GUARDRAILS.md). In brief: input checks are deterministic
patterns and can miss semantic paraphrases; citation validation does not prove entailment; SQLite is for
local/single-instance use; and bookings are local demos. Production work would integrate a real identity provider and
booking API, use shared transactional storage, and run broader adversarial and live-model evaluations.

## Repository hygiene

Keep `.env`, API keys, tokens, SQLite files, and Python caches out of version control. The submission archive should be
generated from the cleaned source tree, not from a working directory containing local configuration or runtime
databases.
