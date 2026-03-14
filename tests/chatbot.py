import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.chatbot.constants import ConversationState

client = TestClient(app)


def post_message(latest_message: str, message_history: list | None = None):
    return client.post("/api/bot/message", json={
        "latest_message": latest_message,
        "message_history": message_history or [],
    })


# --- vague_message ---

def test_vague_single_word():
    response = post_message("hmm")
    assert response.status_code == 200
    assert response.json() == ConversationState.VAGUE_MESSAGE


def test_vague_unclear_intent():
    response = post_message("I'm not sure", [
        {"role": "assistant", "content": "Hi! How can I help you today?"},
        {"role": "user", "content": "maybe something..."},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.VAGUE_MESSAGE


# --- restaurant_question ---

def test_restaurant_opening_hours():
    response = post_message("What time do you close tonight?")
    assert response.status_code == 200
    assert response.json() == ConversationState.RESTAURANT_QUESTION


def test_restaurant_parking():
    response = post_message("Is there parking nearby?", [
        {"role": "user", "content": "Hi, quick question about the restaurant"},
        {"role": "assistant", "content": "Of course! What would you like to know?"},
        {"role": "user", "content": "Do you have outdoor seating?"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.RESTAURANT_QUESTION


# --- menu_question ---

def test_menu_vegetarian_options():
    response = post_message("Do you have anything vegetarian?")
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


def test_menu_dish_ingredients():
    response = post_message("What's in the house burger?", [
        {"role": "user", "content": "Can I see the menu?"},
        {"role": "assistant", "content": "Of course! Here's our menu..."},
        {"role": "user", "content": "Looks good, a couple of questions"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


# --- food_order ---

def test_food_order_new():
    response = post_message("I'd like a large pepperoni pizza and a Coke please")
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


def test_food_order_modify():
    response = post_message("Actually, swap the Coke for a water", [
        {"role": "user", "content": "I'd like a large pepperoni pizza and a Coke"},
        {"role": "assistant", "content": "Got it! One large pepperoni pizza and a Coke. Anything else?"},
        {"role": "user", "content": "Wait, one change"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


def test_food_order_remove():
    response = post_message("Remove the fries from my order", [
        {"role": "user", "content": "Can I get a burger and fries?"},
        {"role": "assistant", "content": "Sure! One burger and fries added."},
        {"role": "user", "content": "Actually I changed my mind"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# --- pickup_ping ---

def test_pickup_ping_eta():
    response = post_message("How long until my order is ready?", [
        {"role": "user", "content": "I'd like two cheeseburgers please"},
        {"role": "assistant", "content": "Two cheeseburgers placed! Your order is being prepared."},
        {"role": "user", "content": "Great, one more thing"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.PICKUP_PING


def test_pickup_ping_status():
    response = post_message("Is my food nearly done?")
    assert response.status_code == 200
    assert response.json() == ConversationState.PICKUP_PING


# --- misc ---

def test_misc_unrelated():
    response = post_message("What's the weather like today?")
    assert response.status_code == 200
    assert response.json() == ConversationState.MISC


def test_misc_compliment():
    response = post_message("That last meal was absolutely amazing, thank you!", [
        {"role": "user", "content": "Can I get the bill please?"},
        {"role": "assistant", "content": "Of course! Hope you enjoyed your meal."},
        {"role": "user", "content": "Just paid, wanted to leave some feedback"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.MISC


# ============================================================
# Harder tests — edge cases and intent boundaries
# ============================================================

# "What's good here?" — clear menu intent, not vague
def test_hard_whats_good_is_menu_not_vague():
    response = post_message("What's good here?")
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


# "I'll have that one" — implicit order driven by history context
def test_hard_implicit_order_from_context():
    response = post_message("I'll have that one", [
        {"role": "user", "content": "What do most people get?"},
        {"role": "assistant", "content": "The house burger is our most popular item!"},
        {"role": "user", "content": "Sounds great"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# "Make it two" — minimal message, clear order modification intent
def test_hard_make_it_two_is_order():
    response = post_message("Make it two", [
        {"role": "user", "content": "Can I get a margherita pizza?"},
        {"role": "assistant", "content": "Sure! One margherita added."},
        {"role": "user", "content": "Actually"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# "Is it spicy?" — menu question even though "it" is ambiguous
def test_hard_is_it_spicy_is_menu():
    response = post_message("Is it spicy?", [
        {"role": "user", "content": "What's the soup of the day?"},
        {"role": "assistant", "content": "Today we have a Thai coconut curry soup."},
        {"role": "user", "content": "Interesting"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


# "Still waiting..." — implicit pickup ping, no explicit question
def test_hard_still_waiting_is_pickup_ping():
    response = post_message("Still waiting...", [
        {"role": "user", "content": "How long for my order?"},
        {"role": "assistant", "content": "Should be about 10 minutes!"},
        {"role": "user", "content": "Ok that was 15 minutes ago"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.PICKUP_PING


# "Do you do gluten free?" — menu question, not restaurant question
def test_hard_gluten_free_is_menu_not_restaurant():
    response = post_message("Do you do gluten free?")
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


# "Can I change my order?" — food order intent, not vague
def test_hard_change_order_is_food_order_not_vague():
    response = post_message("Can I change my order?", [
        {"role": "user", "content": "I'd like a chicken wrap please"},
        {"role": "assistant", "content": "One chicken wrap added!"},
        {"role": "user", "content": "Wait"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# "Never mind" — vague, no recoverable intent even with history
def test_hard_never_mind_is_vague():
    response = post_message("Never mind", [
        {"role": "user", "content": "Do you have..."},
        {"role": "assistant", "content": "Sure, what were you looking for?"},
        {"role": "user", "content": "Forget it"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.VAGUE_MESSAGE


# "Are you guys open on Sundays?" — restaurant question not menu
def test_hard_open_sundays_is_restaurant_not_menu():
    response = post_message("Are you guys open on Sundays?")
    assert response.status_code == 200
    assert response.json() == ConversationState.RESTAURANT_QUESTION


# "How many calories is that?" — menu question even though it sounds misc
def test_hard_calories_is_menu_not_misc():
    response = post_message("How many calories is that?", [
        {"role": "user", "content": "What comes in the kids meal?"},
        {"role": "assistant", "content": "The kids meal has a small burger, fries, and a juice."},
        {"role": "user", "content": "Hmm I'm trying to eat healthy"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


# ============================================================
# Hardest tests — deeply ambiguous, cross-state, and adversarial
# ============================================================

# "How long does the pasta take?" — time word but asking about dish prep, not order ETA
def test_hardest_dish_prep_time_is_menu_not_pickup():
    response = post_message("How long does the pasta take to make?")
    assert response.status_code == 200
    assert response.json() == ConversationState.PICKUP_PING


# "Is the kitchen still open?" — sounds like restaurant info but in order context it's pickup_ping
def test_hardest_kitchen_open_in_order_context_is_pickup():
    response = post_message("Is the kitchen still open?", [
        {"role": "user", "content": "I placed an order 30 minutes ago"},
        {"role": "assistant", "content": "Let me check on that for you."},
        {"role": "user", "content": "It's been ages"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.PICKUP_PING


# "Is the kitchen still open?" without order context — restaurant question
def test_hardest_kitchen_open_no_context_is_restaurant():
    response = post_message("Is the kitchen still open?")
    assert response.status_code == 200
    assert response.json() == ConversationState.RESTAURANT_QUESTION


# "How long have you been open?" — time words but asking restaurant history, not order ETA
def test_hardest_how_long_open_is_restaurant_not_pickup():
    response = post_message("How long have you been open?")
    assert response.status_code == 200
    assert response.json() == ConversationState.RESTAURANT_QUESTION


# "Can I get extra sauce?" — sounds like menu question but it's an order modification
def test_hardest_extra_sauce_is_order_not_menu():
    response = post_message("Can I get extra sauce?", [
        {"role": "user", "content": "I'll have the pulled pork burger"},
        {"role": "assistant", "content": "One pulled pork burger added!"},
        {"role": "user", "content": "One thing"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# "Does it come with anything?" — menu question even though it implies ordering intent
def test_hardest_comes_with_anything_is_menu_not_order():
    response = post_message("Does it come with anything?", [
        {"role": "user", "content": "What's the steak like?"},
        {"role": "assistant", "content": "It's a 10oz sirloin, cooked to your liking."},
        {"role": "user", "content": "Sounds good"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


# "Cancel everything" — definitive food order cancellation, not vague
def test_hardest_cancel_everything_is_food_order():
    response = post_message("Cancel everything", [
        {"role": "user", "content": "Can I get two burgers, a pizza, and a large fries?"},
        {"role": "assistant", "content": "Got it! Two burgers, one pizza, large fries added."},
        {"role": "user", "content": "Actually you know what"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# "I've been waiting forever" — frustration statement with implicit pickup intent
def test_hardest_ive_been_waiting_is_pickup():
    response = post_message("I've been waiting forever", [
        {"role": "user", "content": "Can I get a chicken sandwich?"},
        {"role": "assistant", "content": "Order placed! Won't be long."},
        {"role": "user", "content": "That was 25 minutes ago"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.PICKUP_PING


# "Is the chef good with allergies?" — menu/dietary question, not a restaurant policy question
def test_hardest_chef_allergies_is_menu_not_restaurant():
    response = post_message("Is the chef good with allergies?")
    assert response.status_code == 200
    assert response.json() == ConversationState.MENU_QUESTION


# "Same again please" — implicit repeat order with no item mentioned
def test_hardest_same_again_is_food_order():
    response = post_message("Same again please", [
        {"role": "user", "content": "Can I get a flat white and a croissant?"},
        {"role": "assistant", "content": "One flat white and croissant coming up!"},
        {"role": "user", "content": "That was perfect by the way"},
    ])
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# Multi-intent: ordering AND asking a time question — food_order should dominate
def test_hardest_multi_intent_order_dominates_pickup():
    response = post_message("Can I get a margherita pizza and how long will it take?")
    assert response.status_code == 200
    assert response.json() == ConversationState.FOOD_ORDER


# "What time does lunch start?" — restaurant question, not pickup_ping despite time reference
def test_hardest_lunch_start_time_is_restaurant_not_pickup():
    response = post_message("What time does lunch start?")
    assert response.status_code == 200
    assert response.json() == ConversationState.RESTAURANT_QUESTION
