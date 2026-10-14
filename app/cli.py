import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.actions.intake import NaturalLanguageActionIntake
from app.actions.service import ActionError, ActionPreview, ActionService


def _print_preview(preview: ActionPreview) -> None:
    print(f"\nAction preview [{preview.action_type}] — {preview.action_id}")
    print(f"Requested change: {preview.summary}")
    print(f"Expires at: {preview.expires_at} (UTC)")
    print("Nothing has been changed yet.")
    print(f"Confirm with: /confirm {preview.action_id}")
    print(f"Decline with: /decline {preview.action_id}\n")


def main() -> None:
    # Environment-backed settings are loaded before app imports.
    from app.assistant.service import ResidentAssistant
    from app.core.config import Settings
    from app.knowledge.retrieval import KnowledgeBase
    from app.assistant.llm import GroqChatModel

    runtime = Settings()
    data_dir = Path(runtime.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path(__file__).resolve().parents[1] / data_dir
    knowledge = KnowledgeBase(data_dir)
    assistant = ResidentAssistant(knowledge, GroqChatModel())
    actions = ActionService()
    intake = NaturalLanguageActionIntake()

    # Demo identity only. In production, actor_id must come from authentication.
    actor_id = os.getenv("DEMO_RESIDENT_ID", "RES-DEMO-001")
    print("Hmlet Resident Support Assistant (type 'exit' to quit)")
    print("Natural-language maintenance and cancellation are supported.")
    print("Explicit commands: /maintenance Property | Room | Description | normal|urgent")
    print("                  /cancel BOOKING_ID | /confirm ACTION_ID | /decline ACTION_ID")
    print("This CLI uses a local demo database; it is not connected to Hmlet's live systems.\n")

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break
        if message.lower() in {"exit", "quit"}:
            print("Goodbye.")
            break
        try:
            # Explicit state-changing commands take precedence over conversational intake.
            if message.startswith("/maintenance "):
                parts = [part.strip() for part in message[len("/maintenance "):].split("|")]
                if len(parts) != 4:
                    print("Use: /maintenance Property Name | Room Number | Issue description | normal or urgent")
                    continue
                _print_preview(actions.prepare_maintenance(actor_id, *parts))
                intake.draft = None
            elif message.startswith("/cancel "):
                _print_preview(actions.prepare_cancellation(actor_id, message[len("/cancel "):].strip()))
                intake.draft = None
            elif message.startswith("/confirm "):
                result = actions.confirm(actor_id, message[len("/confirm "):].strip())
                print(f"Assistant: {result.result.get('message', 'Action completed.')}")
                print(f"Status: {result.status}; action ID: {result.action_id}")
                if result.replayed:
                    print("This was an idempotent replay; the operation was not repeated.")
            elif message.startswith("/decline "):
                actions.decline(actor_id, message[len("/decline "):].strip())
                print("Assistant: Action declined. No change was made.")
            else:
                if intake.draft is not None:
                    outcome = intake.continue_draft(message, actor_id, actions)
                    if isinstance(outcome, ActionPreview):
                        _print_preview(outcome)
                    else:
                        print(f"Assistant: {outcome}")
                    continue

                start_message = intake.begin(message)
                if start_message is not None:
                    print(f"Assistant: {start_message}")
                    continue

                response = assistant.respond(message)
                print(f"Assistant: {response.answer}")
                if response.status == "action_required":
                    print("Assistant: To start a real request, describe the issue or provide a booking ID.")
        except ActionError as exc:
            print(f"Assistant: {exc}")


if __name__ == "__main__":
    main()
