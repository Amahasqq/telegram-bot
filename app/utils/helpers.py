from app.constants import MAX_TEXT_LENGTH


def build_system_prompt(base_prompt: str, user_facts: list[str]) -> str:
    facts_text = "\n".join(f"- {f}" for f in user_facts) if user_facts else "No known facts about the user."
    return base_prompt.replace("{user_facts}", facts_text)


def build_messages(
    sys_prompt: str,
    history: list[dict],
    user_text: str,
    search_results: list[dict] | None = None,
) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": sys_prompt}]
    msgs.extend(history)
    if search_results:
        ctx = "\n\n".join(
            f"{r.get('title', '')}\n{r.get('content', '')}\n{r.get('url', '')}"
            for r in search_results
        )
        user_text = f"{user_text}\n\n[Search results:]\n{ctx}"
    msgs.append({"role": "user", "content": user_text})
    return msgs


def truncate(text: str, max_len: int = MAX_TEXT_LENGTH) -> str:
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    boundary = cut.rfind(" ")
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip() + "…"
