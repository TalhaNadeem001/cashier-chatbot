from __future__ import annotations

from typing import Optional

from firedantic import AsyncModel, AsyncSubCollection, AsyncSubModel
from pydantic import BaseModel

class CategoriesModel(BaseModel):
    name: str = ""

class ModifiersModel(BaseModel):
    name: str = ""
    price: int = 0

class ModifiergroupsModel(BaseModel):
    name: str = ""
    modifiers: list[ModifiersModel] = []

class ModifierGroupsModel(BaseModel):
    name: str = ""
    min_required: int = 0
    max_allowed: int = 0
    modifiers: list[ModifiersModel] = []

class User(AsyncModel):
    __collection__ = "Users"

class InventoryItem(AsyncSubModel):
    categories: list[CategoriesModel] = []
    pos_id: str = ""
    name: str = ""
    modifierGroups: list[ModifiergroupsModel] = []
    priceType: str = ""
    price: int = 0
    alternateName: Optional[str] = None

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "Users/{id}/Inventory"

class InventoryIdMap(AsyncSubModel):
    modifiers_by_group: dict[str, dict[str, str]] = dict()
    updated_at: Optional[object] = None
    items: dict[str, str] = dict()
    modifier_groups: dict[str, str] = dict()
    categories: dict[str, str] = dict()

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "Users/{id}/InventoryIdMaps"

class Menu(AsyncModel):
    __collection__ = "menus"
    updated_at: Optional[object] = None

class MenuCategory(AsyncSubModel):
    name: str = ""

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "menus/{id}/categories"

class MenuItem(AsyncSubModel):
    name: str = ""
    category_id: str = ""
    category_name: str = ""
    price: int = 0
    description: Optional[str] = None
    modifier_groups: list[ModifierGroupsModel] = []

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "menus/{id}/items"
