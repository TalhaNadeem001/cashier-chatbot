from src.chatbot.extraction import ai_client
from src.chatbot.schema import Message, ModifyItem, OrderItem, SwapItems


class OrderExtractor:
    async def extract_order_items(self,latest_message: str,message_history: list[Message] | None = None,) -> list[OrderItem]:
        return await ai_client.extract_order_items(
            latest_message=latest_message,
            message_history=message_history,
        )

    async def extract_add_items(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ) -> list[OrderItem]:
        return await ai_client.extract_add_items(
            latest_message=latest_message,
            order_state=order_state,
            message_history=message_history,
        )

    async def extract_modify_items(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ) -> list[ModifyItem]:
        return await ai_client.extract_modify_items(
            latest_message=latest_message,
            order_state=order_state,
            message_history=message_history,
        )

    async def extract_swap_items(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> SwapItems:
        return await ai_client.extract_swap_items(
            latest_message=latest_message,
            message_history=message_history,
        )

    async def resolve_remove_item(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> list[OrderItem]:
        return await ai_client.resolve_remove_item(
            latest_message=latest_message,
            message_history=message_history,
        )

    async def extract_pending_mod_selections(
        self,
        latest_message: str,
        item_name: str,
        missing_mod_groups_text: str,
    ) -> dict:
        return await ai_client.extract_pending_mod_selections(
            latest_message=latest_message,
            item_name=item_name,
            missing_mod_groups_text=missing_mod_groups_text,
        )

    async def resolve_confirmation(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> list[OrderItem]:
        return await ai_client.resolve_confirmation(
            latest_message=latest_message,
            message_history=message_history,
        )
