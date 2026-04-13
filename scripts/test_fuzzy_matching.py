"""
Standalone diagnostic script for FuzzyMatcher.
Tests match_item() and match_free_modifier() across a comprehensive range of inputs.

Usage:
    cd /path/to/cashier-chatbot
    python scripts/test_fuzzy_matching.py
"""

# ── 1. Env + path setup (must precede all src imports) ──────────────────────
import os
import sys
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("FIREBASE_PROJECT_ID", "test-project")
os.environ.setdefault("FIREBASE_CLIENT_EMAIL", "test@test.iam.gserviceaccount.com")
os.environ.setdefault("FIREBASE_PRIVATE_KEY", "test-key")
os.environ.setdefault("RESTAURANT_ID", "test-restaurant")

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 2. Src imports ───────────────────────────────────────────────────────────
import asyncio
import contextlib
import io
import json
from dataclasses import dataclass
from typing import Literal
from unittest.mock import AsyncMock, patch

from src.chatbot.clarification.ai_resolver import AmbiguousMatchResolution
from src.chatbot.clarification.fuzzy_matcher import FuzzyMatcher, _FreeModifierMatch
from src.chatbot.schema import OrderItem

# ── 3. ANSI color constants ──────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ── 4. Data classes ──────────────────────────────────────────────────────────
@dataclass
class ItemTestCase:
    input_name: str
    expected_status: Literal["confirmed", "ambiguous", "not_found"]
    expected_canonical: str | None  # None = status-only check
    category: str
    note: str = ""


@dataclass
class ModifierTestCase:
    input_text: str
    allowed: list[str]
    expected_status: Literal["confirmed", "ambiguous", "not_found"]
    expected_canonical: str | None
    group_name: str

# ── 5. Load menu names ───────────────────────────────────────────────────────
_MENU_PATH = Path(__file__).parent.parent / "data" / "inventory.json"
with _MENU_PATH.open() as _f:
    _inv_data = json.load(_f)
MENU_NAMES: list[str] = [item["name"].lower() for item in _inv_data.values()]

# ── 6. Modifier option constants ─────────────────────────────────────────────
COMBO_OPTS = ["Plain Fries", "Lemon Pepper Fries", "Cajun Fries", "Nashville Seasoning Fries", "No Mods"]
WING_FLAVOR_OPTS = [
    "Naked", "Lemon Pepper Seasoning", "Nashville Seasoning", "Honey Mustard",
    "Garlic Parm", "Spicy Garlic Parm", "Hot Honey", "Sweet n Spicy",
    "BBQ", "Buffalo", "Chili Mango",
]
FRIES_SEASON_OPTS = ["Cajun", "Nashville", "Lemon Pepper", "Salt and Pepper", "Plain", "Salt"]
PATTY_OPTS = ["Single", "Double", "Triple", "Quadruple"]
ADD_ON_OPTS = ["Raw Diced Onion", "Grilled Shaved Onion", "Jalepeno", "Onion Ring", "Beef Bacon"]
WING_STYLE_OPTS = ["Mixed as is", "All flats", "All drums"]
SPICE_OPTS = ["Naked", "Mild", "Spicy", "Extra Spicy"]

# ── 7. Item test cases ───────────────────────────────────────────────────────
ITEM_TEST_CASES: list[ItemTestCase] = [
    # ── A: Exact canonical names (35) ── all hit exact case-insensitive path ─
    *[
        ItemTestCase(input_name=name, expected_status="confirmed", expected_canonical=name, category="exact")
        for name in MENU_NAMES
    ],

    # ── B: Case variants (10) ────────────────────────────────────────────────
    ItemTestCase("Chicken Sub",          "confirmed", "chicken sub",                  "case"),
    ItemTestCase("CLASSIC BURGER",       "confirmed", "classic burger",               "case"),
    ItemTestCase("HOT HONEY",            "confirmed", "hot honey",                    "case"),
    ItemTestCase("Ranch",                "confirmed", "ranch",                        "case"),
    ItemTestCase("FISH",                 "confirmed", "fish",                         "case"),
    ItemTestCase("Can Coke",             "confirmed", "can coke",                     "case"),
    ItemTestCase("ALL AMERICAN BURGER",  "confirmed", "all american burger",          "case"),
    ItemTestCase("Battered Onion Rings", "confirmed", "battered onion rings",         "case"),
    ItemTestCase("BUFFALO",              "confirmed", "buffalo",                      "case"),
    ItemTestCase("Mozzarella Sticks",    "confirmed", "mozzarella sticks",            "case"),

    # ── C: Typo variants (20) ────────────────────────────────────────────────
    ItemTestCase("chiken sub",            "confirmed",  "chicken sub",                "typo", "single char typo"),
    ItemTestCase("mozarella sticks",      "confirmed",  "mozzarella sticks",          "typo", "single-z"),
    ItemTestCase("jalapeno poppers",      "confirmed",  "jalapeno poppers",           "typo", "no accent (fuzzy)"),
    ItemTestCase("mozzarela sticks",      "confirmed",  "mozzarella sticks",          "typo", "single-l"),
    ItemTestCase("chocalate chip cookie", "confirmed",  "chocolate chip cookie",      "typo", "transposition"),
    ItemTestCase("bufalo",                "confirmed",  "buffalo",                    "typo", "single-f"),
    ItemTestCase("bufffalo",              "confirmed",  "buffalo",                    "typo", "triple-f"),
    ItemTestCase("regualar fries",        "confirmed",  "regular fries",              "typo", "extra a"),
    ItemTestCase("annimal fries",         "confirmed",  "animal fries",               "typo", "double-n"),
    ItemTestCase("hot hony burger",       "confirmed",  "hot honey burger",           "typo", "missing e"),
    ItemTestCase("clasic burger",         "confirmed",  "classic burger",             "typo", "missing s"),
    ItemTestCase("swet n spicy",          "confirmed",  "sweet n spicy",              "typo", "missing e"),
    ItemTestCase("chilli mango",          "confirmed",  "chili mango",                "typo", "double-l"),
    ItemTestCase("bonless wings",         "confirmed",  "boneless wings",             "typo", "typo resolves clearly to boneless wings"),
    ItemTestCase("chiken shawarma",       "confirmed",  "chicken shawarma",            "typo", "typo resolves clearly to chicken shawarma"),
    ItemTestCase("chiken sando",          "confirmed",  "chicken sando",              "typo", "likely clear gap"),
    ItemTestCase("hotdog",                "confirmed",  "hot dog",                    "typo", "no space"),
    ItemTestCase("cookies n cream cookie","confirmed",  "cookies 'n cream cookie",    "typo", "missing apostrophe"),
    ItemTestCase("chefs signature burger","confirmed",  "chef's signature burger",    "typo", "missing apostrophe"),
    ItemTestCase("sliced chiken breast",  "confirmed",  "sliced chicken breast",      "typo", "typo"),

    # ── D: Common customer phrasings (15) ────────────────────────────────────
    ItemTestCase("chicken sub",         "confirmed", "chicken sub",                 "phrasing", "natural phrasing"),
    ItemTestCase("chicken sando",       "confirmed", "chicken sando",               "phrasing", "shorthand"),
    ItemTestCase("classic burger",      "confirmed", "classic burger",              "phrasing", "natural"),
    ItemTestCase("all american burger", "confirmed", "all american burger",         "phrasing", "natural"),
    ItemTestCase("chef signature burger","confirmed", "chef's signature burger",    "phrasing", "missing apostrophe+s"),
    ItemTestCase("diet coke",           "confirmed", "can diet coke",               "phrasing", "without 'can'"),
    ItemTestCase("sprite zero",         "confirmed", "can sprite zero",             "phrasing", "without 'can'"),
    ItemTestCase("onion rings",         "confirmed", "battered onion rings",        "phrasing", "without 'battered'"),
    ItemTestCase("mozzarella",          "confirmed", "mozzarella sticks",           "phrasing", "without 'sticks'"),
    ItemTestCase("chicken breast",      "confirmed", "sliced chicken breast",       "phrasing", "without 'sliced'"),
    ItemTestCase("cookies n cream",     "confirmed", "cookies 'n cream cookie",     "phrasing", "shorthand"),
    ItemTestCase("sweet and spicy",     "confirmed", "sweet n spicy",               "phrasing", "'and' vs 'n'"),
    ItemTestCase("bbq sauce",           "confirmed", "bbq",                         "phrasing", "extra word"),
    ItemTestCase("buffalo sauce",       "confirmed", "buffalo",                     "phrasing", "extra word"),
    ItemTestCase("ranch dressing",      "confirmed", "ranch",                       "phrasing", "extra word"),

    # ── E: Partial / shorthand (10) ──────────────────────────────────────────
    ItemTestCase("regular",            "confirmed",  "regular fries",              "partial", "partial_ratio lifts it"),
    ItemTestCase("6 boneless",         "confirmed",  "boneless wings",             "partial", "qty + partial"),
    ItemTestCase("poppers",            "confirmed",  "jalapeno poppers",           "partial", "just 'poppers'"),
    ItemTestCase("tender",             "confirmed",  "chicken tender",             "partial", "just 'tender'"),
    ItemTestCase("sando",              "confirmed",  "chicken sando",              "partial", "just 'sando'"),
    ItemTestCase("animal style fries", "confirmed",  "animal fries",               "partial", "extra word"),
    ItemTestCase("smash way shawarma", "confirmed",  "chicken shawarma smash way", "partial", "rearranged"),
    ItemTestCase("chocolate cookie",   "ambiguous",  None,                         "partial", "chocolate chip cookie + m&m chocolate chip both match"),
    ItemTestCase("m&m cookie",         "confirmed",  "m&m chocolate chip cookie",  "partial", "m&m lifts it above chocolate chip cookie"),
    ItemTestCase("glass of coke",      "confirmed",  "glass coke",                 "partial", "extra 'of'"),

    # ── F: Confusing / adversarial (12) ──────────────────────────────────────
    ItemTestCase("hot honey",       "confirmed",  "hot honey",       "confusing", "exact match → sauce confirmed, not ambiguous"),
    ItemTestCase("wings",           "ambiguous",  None,              "confusing", "boneless + bone-in both score 90"),
    ItemTestCase("burger",          "ambiguous",  None,              "confusing", "all 4 burgers via partial"),
    ItemTestCase("coke",            "ambiguous",  None,              "confusing", "4 coke items all score 90"),
    ItemTestCase("sprite",          "ambiguous",  None,              "confusing", "3 sprite items score 90"),
    ItemTestCase("chicken",         "ambiguous",  None,              "confusing", "6 chicken items score 90"),
    ItemTestCase("fries",           "ambiguous",  None,              "confusing", "animal + regular both 90"),
    ItemTestCase("shawarma",        "ambiguous",  None,              "confusing", "both shawarma variants tie"),
    ItemTestCase("cookie",          "ambiguous",  None,              "confusing", "all 3 cookies within gap"),
    ItemTestCase("hamburger",       "ambiguous",  None,              "confusing", "all 4 burgers within gap"),
    ItemTestCase("crispy chicken",  "ambiguous",  None,              "confusing", "chicken sando + tender both match"),
    ItemTestCase("fish sandwich",   "confirmed",  "fish",            "confusing", "partial_ratio('fish sandwich','fish')=90"),

    # ── G: Not on menu (8) ───────────────────────────────────────────────────
    ItemTestCase("pizza",         "not_found", None, "not_found", "score < 65"),
    ItemTestCase("pasta",         "ambiguous", None, "not_found", "false positive: partial match with 'sliced chicken breast'"),
    ItemTestCase("salad",         "not_found", None, "not_found"),
    ItemTestCase("sushi",         "not_found", None, "not_found"),
    ItemTestCase("tacos",         "not_found", None, "not_found"),
    ItemTestCase("lemonade",      "not_found", None, "not_found"),
    ItemTestCase("soda",          "not_found", None, "not_found"),
    ItemTestCase("vanilla shake", "not_found", None, "not_found"),
]

# ── 8. Modifier test cases ───────────────────────────────────────────────────
MODIFIER_TEST_CASES: list[ModifierTestCase] = [
    # combo_fries (10)
    ModifierTestCase("Plain Fries",            COMBO_OPTS, "confirmed", "Plain Fries",              "combo_fries"),
    ModifierTestCase("Cajun Fries",            COMBO_OPTS, "confirmed", "Cajun Fries",              "combo_fries"),
    ModifierTestCase("No Mods",                COMBO_OPTS, "confirmed", "No Mods",                  "combo_fries"),
    ModifierTestCase("plain",                  COMBO_OPTS, "confirmed", "Plain Fries",              "combo_fries"),
    ModifierTestCase("cajun",                  COMBO_OPTS, "confirmed", "Cajun Fries",              "combo_fries"),
    ModifierTestCase("lemon pepper",           COMBO_OPTS, "confirmed", "Lemon Pepper Fries",       "combo_fries"),
    ModifierTestCase("nashville",              COMBO_OPTS, "confirmed", "Nashville Seasoning Fries","combo_fries"),
    ModifierTestCase("cajun freis",            COMBO_OPTS, "confirmed", "Cajun Fries",              "combo_fries"),
    ModifierTestCase("no",                     COMBO_OPTS, "confirmed", "No Mods",                  "combo_fries"),
    ModifierTestCase("cheese fries",           COMBO_OPTS, "ambiguous", None,                       "combo_fries"),

    # wing_flavor (12)
    ModifierTestCase("BBQ",                    WING_FLAVOR_OPTS, "confirmed", "BBQ",                    "wing_flavor"),
    ModifierTestCase("Buffalo",                WING_FLAVOR_OPTS, "confirmed", "Buffalo",                "wing_flavor"),
    ModifierTestCase("Naked",                  WING_FLAVOR_OPTS, "confirmed", "Naked",                  "wing_flavor"),
    ModifierTestCase("Hot Honey",              WING_FLAVOR_OPTS, "confirmed", "Hot Honey",              "wing_flavor"),
    ModifierTestCase("lemon pepper",           WING_FLAVOR_OPTS, "confirmed", "Lemon Pepper Seasoning", "wing_flavor"),
    ModifierTestCase("honey mustard",          WING_FLAVOR_OPTS, "confirmed", "Honey Mustard",          "wing_flavor"),
    ModifierTestCase("garlic parm",            WING_FLAVOR_OPTS, "confirmed", "Garlic Parm",            "wing_flavor"),
    ModifierTestCase("sweet and spicy",        WING_FLAVOR_OPTS, "confirmed", "Sweet n Spicy",          "wing_flavor"),
    ModifierTestCase("chili mango",            WING_FLAVOR_OPTS, "confirmed", "Chili Mango",            "wing_flavor"),
    ModifierTestCase("nashvile seasoning",     WING_FLAVOR_OPTS, "confirmed", "Nashville Seasoning",    "wing_flavor"),
    ModifierTestCase("garlic parmesean",       WING_FLAVOR_OPTS, "confirmed", "Garlic Parm",            "wing_flavor"),
    ModifierTestCase("ranch",                  WING_FLAVOR_OPTS, "ambiguous", None,                     "wing_flavor"),

    # fries_seasoning (8)
    ModifierTestCase("Cajun",                  FRIES_SEASON_OPTS, "confirmed", "Cajun",          "fries_seasoning"),
    ModifierTestCase("Plain",                  FRIES_SEASON_OPTS, "confirmed", "Plain",          "fries_seasoning"),
    ModifierTestCase("Salt",                   FRIES_SEASON_OPTS, "confirmed", "Salt",           "fries_seasoning"),
    ModifierTestCase("Lemon Pepper",           FRIES_SEASON_OPTS, "confirmed", "Lemon Pepper",   "fries_seasoning"),
    ModifierTestCase("Salt and Pepper",        FRIES_SEASON_OPTS, "confirmed", "Salt and Pepper","fries_seasoning"),
    ModifierTestCase("nashvile",               FRIES_SEASON_OPTS, "confirmed", "Nashville",      "fries_seasoning"),
    ModifierTestCase("spicy",                  FRIES_SEASON_OPTS, "not_found", None,             "fries_seasoning"),
    ModifierTestCase("no seasoning",           FRIES_SEASON_OPTS, "not_found", None,             "fries_seasoning"),

    # patties (8)
    ModifierTestCase("Single",                 PATTY_OPTS, "confirmed", "Single",    "patties"),
    ModifierTestCase("Double",                 PATTY_OPTS, "confirmed", "Double",    "patties"),
    ModifierTestCase("Triple",                 PATTY_OPTS, "confirmed", "Triple",    "patties"),
    ModifierTestCase("Quadruple",              PATTY_OPTS, "confirmed", "Quadruple", "patties"),
    ModifierTestCase("quad",                   PATTY_OPTS, "confirmed", "Quadruple", "patties"),
    ModifierTestCase("single patty",           PATTY_OPTS, "confirmed", "Single",    "patties"),
    ModifierTestCase("1 patty",                PATTY_OPTS, "not_found", None,        "patties"),
    ModifierTestCase("five",                   PATTY_OPTS, "not_found", None,        "patties"),

    # add_ons (9)
    ModifierTestCase("Beef Bacon",             ADD_ON_OPTS, "confirmed", "Beef Bacon",           "add_ons"),
    ModifierTestCase("Onion Ring",             ADD_ON_OPTS, "confirmed", "Onion Ring",           "add_ons"),
    ModifierTestCase("Jalepeno",               ADD_ON_OPTS, "confirmed", "Jalepeno",             "add_ons"),
    ModifierTestCase("bacon",                  ADD_ON_OPTS, "confirmed", "Beef Bacon",           "add_ons"),
    ModifierTestCase("grilled onion",          ADD_ON_OPTS, "confirmed", "Grilled Shaved Onion", "add_ons"),
    ModifierTestCase("raw onion",              ADD_ON_OPTS, "confirmed", "Raw Diced Onion",      "add_ons"),
    ModifierTestCase("jalapeno",               ADD_ON_OPTS, "confirmed", "Jalepeno",             "add_ons"),
    ModifierTestCase("cheese",                 ADD_ON_OPTS, "not_found", None,                   "add_ons"),
    ModifierTestCase("avocado",                ADD_ON_OPTS, "ambiguous", None,                   "add_ons"),

    # wing_style (5)
    ModifierTestCase("Mixed as is",            WING_STYLE_OPTS, "confirmed", "Mixed as is", "wing_style"),
    ModifierTestCase("All flats",              WING_STYLE_OPTS, "confirmed", "All flats",   "wing_style"),
    ModifierTestCase("All drums",              WING_STYLE_OPTS, "confirmed", "All drums",   "wing_style"),
    ModifierTestCase("flats",                  WING_STYLE_OPTS, "confirmed", "All flats",   "wing_style"),
    ModifierTestCase("drums",                  WING_STYLE_OPTS, "confirmed", "All drums",   "wing_style"),

    # spice_level (5)
    ModifierTestCase("Mild",                   SPICE_OPTS, "confirmed", "Mild",        "spice_level"),
    ModifierTestCase("Spicy",                  SPICE_OPTS, "confirmed", "Spicy",       "spice_level"),
    ModifierTestCase("Extra Spicy",            SPICE_OPTS, "confirmed", "Extra Spicy", "spice_level"),
    ModifierTestCase("Naked",                  SPICE_OPTS, "confirmed", "Naked",       "spice_level"),
    ModifierTestCase("hot",                    SPICE_OPTS, "not_found", None,          "spice_level"),
]

# ── 9. Evaluation helpers ────────────────────────────────────────────────────
def evaluate_item(result, tc: ItemTestCase) -> bool:
    if result.status != tc.expected_status:
        return False
    if tc.expected_canonical is not None:
        if result.canonical_name is None:
            return False
        return result.canonical_name.lower() == tc.expected_canonical.lower()
    return True


def evaluate_modifier(result: _FreeModifierMatch, tc: ModifierTestCase) -> bool:
    if result.status != tc.expected_status:
        return False
    if tc.expected_canonical is not None:
        if result.canonical is None:
            return False
        return result.canonical.lower() == tc.expected_canonical.lower()
    return True

# ── 10/11. Print helpers ─────────────────────────────────────────────────────
def _status_tag(status: str) -> str:
    if status == "confirmed":
        return "confirmed"
    if status == "ambiguous":
        return "ambiguous"
    return "not_found"


def print_item_result(tc: ItemTestCase, result, passed: bool) -> None:
    tag = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
    canonical = result.canonical_name or "-"
    status = _status_tag(result.status)
    line = f"  {tag}  {tc.input_name!r:<35} → {status:<10} → {canonical!r:<35} {DIM}[{tc.category}]{RESET}"
    if tc.note:
        line += f"  {DIM}{tc.note}{RESET}"
    print(line)
    if not passed:
        exp_canon = f"/{tc.expected_canonical!r}" if tc.expected_canonical else ""
        print(f"         {RED}expected: {tc.expected_status}{exp_canon}{RESET}")
    if result.status == "ambiguous" and result.candidates:
        print(f"         {CYAN}candidates: {', '.join(result.candidates)}{RESET}")


def print_modifier_result(tc: ModifierTestCase, result: _FreeModifierMatch, passed: bool) -> None:
    tag = f"{GREEN}[PASS]{RESET}" if passed else f"{RED}[FAIL]{RESET}"
    canonical = result.canonical or "-"
    status = _status_tag(result.status)
    line = f"  {tag}  {tc.input_text!r:<30} → {status:<10} → {canonical!r:<30} {DIM}[{tc.group_name}]{RESET}"
    print(line)
    if not passed:
        exp_canon = f"/{tc.expected_canonical!r}" if tc.expected_canonical else ""
        print(f"         {RED}expected: {tc.expected_status}{exp_canon}{RESET}")
    if result.status == "ambiguous" and result.candidates:
        print(f"         {CYAN}candidates: {', '.join(result.candidates)}{RESET}")

# ── 12. Run item tests ────────────────────────────────────────────────────────
async def run_item_tests(matcher: FuzzyMatcher) -> list[dict]:
    mock_resolution = AmbiguousMatchResolution(
        confident=False,
        canonical=None,
        clarification_message="mock",
    )
    results = []
    with patch(
        "src.chatbot.clarification.fuzzy_matcher.resolve_ambiguous_match",
        new_callable=AsyncMock,
        return_value=mock_resolution,
    ):
        for tc in ITEM_TEST_CASES:
            item = OrderItem(name=tc.input_name, quantity=1)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = await matcher.match_item(item, MENU_NAMES, None, tc.input_name)
            passed = evaluate_item(result, tc)
            results.append({"tc": tc, "result": result, "passed": passed})
    return results

# ── 13. Run modifier tests ────────────────────────────────────────────────────
def run_modifier_tests(matcher: FuzzyMatcher) -> list[dict]:
    results = []
    for tc in MODIFIER_TEST_CASES:
        result = matcher.match_free_modifier(tc.input_text, tc.allowed)
        passed = evaluate_modifier(result, tc)
        results.append({"tc": tc, "result": result, "passed": passed})
    return results

# ── 14. Summary printer ───────────────────────────────────────────────────────
def print_summary(item_results: list[dict], modifier_results: list[dict]) -> None:
    print()
    print(f"{BOLD}ITEM MATCHING ACCURACY{RESET}")
    print("─" * 55)
    print(f"  {'Category':<16} {'Pass':>5} {'Total':>7} {'Accuracy':>10}")
    print("─" * 55)

    item_categories: dict[str, list[dict]] = {}
    for r in item_results:
        cat = r["tc"].category
        item_categories.setdefault(cat, []).append(r)

    item_total_pass = item_total_all = 0
    for cat, rows in item_categories.items():
        p = sum(1 for r in rows if r["passed"])
        t = len(rows)
        item_total_pass += p
        item_total_all += t
        pct = 100.0 * p / t if t else 0
        print(f"  {cat:<16} {p:>5} {t:>7} {pct:>9.1f}%")

    print("─" * 55)
    pct = 100.0 * item_total_pass / item_total_all if item_total_all else 0
    print(f"  {'TOTAL':<16} {item_total_pass:>5} {item_total_all:>7} {pct:>9.1f}%")

    print()
    print(f"{BOLD}MODIFIER ACCURACY{RESET}")
    print("─" * 55)
    print(f"  {'Group':<20} {'Pass':>5} {'Total':>7} {'Accuracy':>10}")
    print("─" * 55)

    mod_groups: dict[str, list[dict]] = {}
    for r in modifier_results:
        grp = r["tc"].group_name
        mod_groups.setdefault(grp, []).append(r)

    mod_total_pass = mod_total_all = 0
    for grp, rows in mod_groups.items():
        p = sum(1 for r in rows if r["passed"])
        t = len(rows)
        mod_total_pass += p
        mod_total_all += t
        pct = 100.0 * p / t if t else 0
        print(f"  {grp:<20} {p:>5} {t:>7} {pct:>9.1f}%")

    print("─" * 55)
    pct = 100.0 * mod_total_pass / mod_total_all if mod_total_all else 0
    print(f"  {'TOTAL':<20} {mod_total_pass:>5} {mod_total_all:>7} {pct:>9.1f}%")

    # Overall
    total_pass = item_total_pass + mod_total_pass
    total_all = item_total_all + mod_total_all
    overall_pct = 100.0 * total_pass / total_all if total_all else 0
    print()
    print(f"{BOLD}OVERALL: {total_pass}/{total_all}  ({overall_pct:.1f}%){RESET}")

    # Failures
    failures = [r for r in item_results + modifier_results if not r["passed"]]
    if failures:
        print()
        print(f"{RED}{BOLD}FAILURES ({len(failures)}):{RESET}")
        for r in failures:
            tc = r["tc"]
            result = r["result"]
            if isinstance(tc, ItemTestCase):
                exp_canon = f"/{tc.expected_canonical!r}" if tc.expected_canonical else ""
                actual_canon = f"/{result.canonical_name!r}" if result.canonical_name else ""
                print(
                    f"  {RED}[{tc.category}]{RESET} {tc.input_name!r} → "
                    f"expected {tc.expected_status}{exp_canon}, "
                    f"got {result.status}{actual_canon}"
                )
                if result.candidates:
                    print(f"           candidates: {', '.join(result.candidates)}")
            else:
                exp_canon = f"/{tc.expected_canonical!r}" if tc.expected_canonical else ""
                actual_canon = f"/{result.canonical!r}" if result.canonical else ""
                print(
                    f"  {RED}[{tc.group_name}]{RESET} {tc.input_text!r} → "
                    f"expected {tc.expected_status}{exp_canon}, "
                    f"got {result.status}{actual_canon}"
                )
                if result.candidates:
                    print(f"           candidates: {', '.join(result.candidates)}")
    else:
        print(f"\n{GREEN}All tests passed!{RESET}")

# ── 15. Main ──────────────────────────────────────────────────────────────────
async def main() -> None:
    matcher = FuzzyMatcher()

    print(f"\n{BOLD}=== ITEM MATCHING TESTS ==={RESET}")
    print(f"  Menu has {len(MENU_NAMES)} items: {', '.join(MENU_NAMES)}\n")
    item_results = await run_item_tests(matcher)
    for r in item_results:
        print_item_result(r["tc"], r["result"], r["passed"])

    print(f"\n{BOLD}=== MODIFIER MATCHING TESTS ==={RESET}\n")
    modifier_results = run_modifier_tests(matcher)
    for r in modifier_results:
        print_modifier_result(r["tc"], r["result"], r["passed"])

    print_summary(item_results, modifier_results)


if __name__ == "__main__":
    asyncio.run(main())
