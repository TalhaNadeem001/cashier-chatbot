from pydantic import BaseModel, RootModel


class InventoryModifierSchema(BaseModel):
    id: str
    name: str
    price: int = 0


class InventoryModifierGroupSchema(BaseModel):
    id: str
    name: str
    modifiers: list[InventoryModifierSchema] = []


class InventoryCategoryRefSchema(BaseModel):
    id: str
    name: str


class InventoryItemSchema(BaseModel):
    id: str
    name: str
    price: int = 0
    priceType: str = "FIXED"
    pos_id: str = ""
    categories: list[InventoryCategoryRefSchema] = []
    modifierGroups: list[InventoryModifierGroupSchema] = []
    alternateName: str | None = None


class InventoryIngestRequest(RootModel[dict[str, InventoryItemSchema]]):
    pass


class MenuIngestResponse(BaseModel):
    success: bool
    items_synced: int = 0
    categories_synced: int = 0
    combos_synced: int = 0
