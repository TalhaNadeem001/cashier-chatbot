# Re-export shim — will be removed after all callers updated
from src.chatbot.cart.handlers import FoodOrderHandlerFactory

__all__ = ["FoodOrderHandlerFactory"]
