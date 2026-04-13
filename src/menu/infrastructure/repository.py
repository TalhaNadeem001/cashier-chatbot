from src.menu.infrastructure import loader


class MenuReadRepository:
    async def reload(self) -> None:
        await loader.init_menu()

    async def list_item_names(self) -> list[str]:
        return await loader.get_menu_item_names()

    async def get_item_price(self, name: str) -> float | None:
        return await loader.get_item_price(name)

    async def get_item_category(self, name: str) -> str | None:
        return await loader.get_item_category(name)

    def get_item_definition(self, name: str) -> dict | None:
        return loader.get_item_definition(name)

    def get_item_id(self, name: str) -> str | None:
        return loader.get_item_id(name)

    def resolve_mod_ids(
        self,
        item_name: str,
        selected_mods: dict[str, str | list[str]],
    ) -> list[dict]:
        return loader.resolve_mod_ids(item_name, selected_mods)

    def resolve_mod_ids_from_string(self, item_name: str, modifier_str: str) -> list[dict]:
        return loader.resolve_mod_ids_from_string(item_name, modifier_str)

    def get_menu_context(self) -> str:
        return loader.get_menu_context()

    def get_combo_catalog(self) -> list[dict]:
        return loader._combos

    def get_item_index(self) -> dict[str, dict]:
        return loader._items_by_name


menu_repository = MenuReadRepository()
