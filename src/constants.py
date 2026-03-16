from enum import Enum


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


# ── Menu constants ────────────────────────────────────────────────────────────

MENU_ITEM_MAP: dict[str, dict] = {
    "Classic Beef Burger": {
        "description": "Juicy beef patty with lettuce, tomato, pickles and house sauce in a toasted brioche bun.",
        "modifiers": ["Make it double (+£2.00)", "Add jalapeños (+£0.50)", "Make it gluten-free (+£1.00)"],
        "add_ons": ["Extra cheese (£1.00)", "Bacon (£1.50)", "Fried egg (£1.00)", "Avocado (£1.50)"],
        "price": 8.50,
    },
    "BBQ Bacon Burger": {
        "description": "Beef patty topped with crispy smoked bacon, cheddar cheese and smoky BBQ sauce.",
        "modifiers": ["Make it double (+£2.00)", "Extra BBQ sauce (free)", "Make it gluten-free (+£1.00)"],
        "add_ons": ["Extra bacon (£1.50)", "Caramelised onions (£0.75)", "Extra cheese (£1.00)"],
        "price": 10.50,
    },
    "Spicy Chicken Burger": {
        "description": "Crispy fried chicken breast coated in sriracha glaze, topped with slaw and pickled chilli.",
        "modifiers": ["Mild spice (free)", "Extra hot (+£0.25)", "Make it gluten-free (+£1.00)"],
        "add_ons": ["Extra slaw (£0.75)", "Cheese (£1.00)", "Bacon (£1.50)"],
        "price": 9.50,
    },
    "Grilled Chicken Burger": {
        "description": "Marinated grilled chicken breast with rocket, sun-dried tomato and garlic aioli.",
        "modifiers": ["Make it gluten-free (+£1.00)", "No aioli (free)"],
        "add_ons": ["Extra chicken (£2.50)", "Avocado (£1.50)", "Cheese (£1.00)"],
        "price": 9.00,
    },
    "Veggie Burger": {
        "description": "Crispy chickpea and roasted red pepper patty with hummus, cucumber and fresh mint.",
        "modifiers": ["Make it vegan (free — remove feta)", "Make it gluten-free (+£1.00)"],
        "add_ons": ["Extra patty (+£3.00)", "Avocado (£1.50)", "Halloumi (£2.00)"],
        "price": 8.00,
    },
    "Double Smash Burger": {
        "description": "Two smashed beef patties with American cheese, grilled onions, mustard and ketchup.",
        "modifiers": ["Triple stack (+£2.50)", "No mustard (free)", "Make it gluten-free (+£1.00)"],
        "add_ons": ["Extra cheese (£1.00)", "Bacon (£1.50)", "Pickled jalapeños (£0.50)"],
        "price": 12.00,
    },
    "Mushroom Swiss Burger": {
        "description": "Beef patty topped with sautéed mushrooms, melted Swiss cheese and truffle mayo.",
        "modifiers": ["Make it double (+£2.00)", "No truffle mayo (free)", "Make it gluten-free (+£1.00)"],
        "add_ons": ["Extra mushrooms (£1.00)", "Bacon (£1.50)", "Extra cheese (£1.00)"],
        "price": 10.00,
    },
    "Fish Fillet Burger": {
        "description": "Crispy battered cod fillet with tartare sauce, iceberg lettuce and pickles.",
        "modifiers": ["Make it gluten-free (+£1.00)", "Extra tartare (free)"],
        "add_ons": ["Cheese (£1.00)", "Bacon (£1.50)", "Extra fillet (+£3.50)"],
        "price": 9.00,
    },
    "Club Sandwich": {
        "description": "Triple-decker with grilled chicken, bacon, egg, cheddar, lettuce and tomato on toasted sourdough.",
        "modifiers": ["Make it gluten-free (+£1.00)", "No egg (free)", "No bacon (free)"],
        "add_ons": ["Extra chicken (£2.50)", "Avocado (£1.50)", "Extra bacon (£1.50)"],
        "price": 9.50,
    },
    "Pulled Pork Sandwich": {
        "description": "Slow-cooked BBQ pulled pork with apple slaw and pickles in a toasted brioche roll.",
        "modifiers": ["Extra BBQ sauce (free)", "Make it gluten-free (+£1.00)"],
        "add_ons": ["Extra pork (+£2.50)", "Cheese (£1.00)", "Jalapeños (£0.50)"],
        "price": 9.50,
    },
    "Margherita Pizza": {
        "description": "San Marzano tomato base, fresh mozzarella and basil on a hand-stretched sourdough crust.",
        "modifiers": ["Gluten-free base (+£2.00)", "Vegan cheese (+£1.50)", "Thin crust (free)"],
        "add_ons": ["Extra mozzarella (£1.50)", "Cherry tomatoes (£0.75)", "Chilli flakes (free)"],
        "price": 10.00,
    },
    "Pepperoni Pizza": {
        "description": "Tomato base, mozzarella and generous slices of spicy pepperoni.",
        "modifiers": ["Gluten-free base (+£2.00)", "Double pepperoni (+£2.00)", "Thin crust (free)"],
        "add_ons": ["Extra mozzarella (£1.50)", "Jalapeños (£0.50)", "Red onion (£0.50)"],
        "price": 11.50,
    },
    "Chicken Caesar Salad": {
        "description": "Grilled chicken breast, romaine lettuce, parmesan shavings, croutons and classic Caesar dressing.",
        "modifiers": ["No croutons (free)", "Dressing on the side (free)", "Add anchovies (free)"],
        "add_ons": ["Extra chicken (£2.50)", "Bacon bits (£1.50)", "Soft-boiled egg (£1.00)"],
        "price": 10.50,
    },
    "Garden Salad": {
        "description": "Mixed greens, cucumber, cherry tomatoes, red onion and balsamic vinaigrette.",
        "modifiers": ["No onion (free)", "Dressing on the side (free)", "Add feta (+£1.00)"],
        "add_ons": ["Grilled chicken (£2.50)", "Halloumi (£2.00)", "Avocado (£1.50)"],
        "price": 7.00,
    },
    "Loaded Fries": {
        "description": "Thick-cut fries topped with melted cheddar, bacon bits, jalapeños and sour cream.",
        "modifiers": ["No jalapeños (free)", "No bacon (free)", "Extra cheese (+£1.00)"],
        "add_ons": ["BBQ sauce (£0.50)", "Truffle oil (+£1.00)", "Pulled pork (+£2.50)"],
        "price": 6.50,
    },
    "Sweet Potato Fries": {
        "description": "Crispy seasoned sweet potato fries served with chipotle dipping sauce.",
        "modifiers": ["Extra seasoning (free)", "Sauce on the side (free)"],
        "add_ons": ["Extra dipping sauce (£0.50)", "Cheese (£1.00)"],
        "price": 5.00,
    },
    "Onion Rings": {
        "description": "Beer-battered onion rings served with smoky chipotle mayo.",
        "modifiers": ["Extra crispy (free)", "Sauce on the side (free)"],
        "add_ons": ["Extra sauce (£0.50)"],
        "price": 4.50,
    },
    "Chicken Nuggets": {
        "description": "Eight crispy chicken nuggets served with your choice of dipping sauce.",
        "modifiers": ["Six pieces (-£1.00)", "Twelve pieces (+£2.00)"],
        "add_ons": ["BBQ sauce (free)", "Sweet chilli sauce (free)", "Honey mustard (free)"],
        "price": 6.00,
    },
    "Chocolate Milkshake": {
        "description": "Thick and creamy milkshake made with Belgian chocolate ice cream and whole milk.",
        "modifiers": ["Make it vegan (+£0.50)", "Extra thick (free)"],
        "add_ons": ["Whipped cream (£0.50)", "Chocolate flake (£0.50)"],
        "price": 5.50,
    },
    "Vanilla Milkshake": {
        "description": "Classic creamy milkshake made with Madagascan vanilla ice cream and whole milk.",
        "modifiers": ["Make it vegan (+£0.50)", "Extra thick (free)"],
        "add_ons": ["Whipped cream (£0.50)", "Caramel drizzle (£0.50)"],
        "price": 5.50,
    },
}


def _build_menu_context_string() -> str:
    lines = []
    for name, details in MENU_ITEM_MAP.items():
        lines.append(f"=== {name} ===")
        lines.append(f"Price: £{details['price']:.2f}")
        lines.append(f"Description: {details['description']}")
        lines.append(f"Modifiers: {', '.join(details['modifiers']) if details['modifiers'] else 'None'}")
        lines.append(f"Add-ons: {', '.join(details['add_ons']) if details['add_ons'] else 'None'}")
        lines.append("")
    return "\n".join(lines).strip()


MENU_CONTEXT_STRING: str = _build_menu_context_string()
