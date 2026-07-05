import logging

from app.services.memory import memory
from app.utils.telegram import tg_resp
from app.handlers.briefing import generate_briefing

logger = logging.getLogger(__name__)


async def handle_command(chat_id: int, user_id: int, text: str) -> dict[str, object]:
    cmd = text.split()[0].lower()

    if cmd == "/start":
        return tg_resp("sendMessage", chat_id, text=(
            "Hey! I'm an AI assistant.\n\n"
            "I can:\n"
            "- Chat with you using AI\n"
            "- Search the web\n"
            "- Remember facts about you\n"
            "- Generate tech briefings\n\n"
            "Commands: /clear, /note, /notes, /clearnotes, /costs, /briefing"
        ))

    if cmd == "/clear":
        await memory.clear_history(str(user_id))
        return tg_resp("sendMessage", chat_id, text="Conversation history cleared.")

    if cmd == "/note":
        note_text = text[len("/note"):].strip()
        if not note_text:
            return tg_resp("sendMessage", chat_id, text="Usage: /note <text>")
        await memory.add_note(note_text)
        return tg_resp("sendMessage", chat_id, text="Note saved.")

    if cmd == "/notes":
        notes = await memory.get_notes()
        if not notes:
            return tg_resp("sendMessage", chat_id, text="No notes saved.")
        text = "Latest notes:\n\n" + "\n".join(f"- {n['text']}" for n in notes)
        return tg_resp("sendMessage", chat_id, text=text[:4000])

    if cmd == "/clearnotes":
        await memory.clear_notes()
        return tg_resp("sendMessage", chat_id, text="All notes deleted.")

    if cmd == "/costs":
        costs = await memory.get_costs()
        input_cost = (costs.get("total_input_tokens", 0) / 1_000_000) * 3
        output_cost = (costs.get("total_output_tokens", 0) / 1_000_000) * 15
        total = input_cost + output_cost
        text = (
            f"Cost stats:\n\n"
            f"Today: {costs.get('daily_input_tokens', 0)} in / {costs.get('daily_output_tokens', 0)} out tokens\n"
            f"Total: {costs.get('total_input_tokens', 0)} in / {costs.get('total_output_tokens', 0)} out tokens\n"
            f"Estimated cost: ${total:.4f}"
        )
        return tg_resp("sendMessage", chat_id, text=text)

    if cmd == "/briefing":
        return await generate_briefing(chat_id)

    return tg_resp("sendMessage", chat_id, text="Unknown command. Try /start.")
