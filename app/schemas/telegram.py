from pydantic import BaseModel, ConfigDict, Field


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
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    from_field: User | None = Field(None, alias="from")
    chat: Chat | None = None
    text: str | None = None
    date: int | None = None
    entities: list[dict] | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: Message | None = None
