def tg_resp(method: str, chat_id: int, **kwargs: object) -> dict[str, object]:
    return {"method": method, "chat_id": chat_id, **kwargs}
