from pydantic import BaseModel, Field


class User(BaseModel):
    id: int
    is_bot: bool = False
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None


class Chat(BaseModel):
    id: int
    type: str = "private"


class Message(BaseModel):
    message_id: int
    from_field: User | None = Field(None, alias="from")
    chat: Chat | None = None
    text: str | None = None
    date: int | None = None
    entities: list[dict] | None = None

    class Config:
        populate_by_name = True


class TelegramUpdate(BaseModel):
    update_id: int
    message: Message | None = None
