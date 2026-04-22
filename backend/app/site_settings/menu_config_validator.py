from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MenuTarget(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    value: str

    @model_validator(mode="after")
    def validate_target(self):
        if self.type not in {"route", "external_url"}:
            raise ValueError("target.type must be one of: route, external_url")
        if not self.value or not self.value.strip():
            raise ValueError("target.value must be a non-empty string")
        return self


class MenuAudience(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str = "all"
    roles_any: list[str] = Field(default_factory=list)
    permissions_any: list[str] = Field(default_factory=list)
    user_ids_any: list[str] = Field(default_factory=list)
    department_ids_any: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_audience(self):
        if self.mode not in {"all", "allowlist"}:
            raise ValueError("audience.mode must be one of: all, allowlist")
        return self


class MenuItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    parent_key: str | None = None
    title: str
    order: int = 100
    visible: bool
    enabled: bool
    target: MenuTarget | None = None
    audience: MenuAudience | None = None
    unenabled_page: str | None = None
    has_studio: bool = False
    icon: str = ""

    @model_validator(mode="after")
    def validate_item(self):
        if not self.key or not self.key.strip():
            raise ValueError("menus[].key must be a non-empty string")
        if not self.title or not self.title.strip():
            raise ValueError("menus[].title must be a non-empty string")

        if self.parent_key is not None and not self.parent_key.strip():
            raise ValueError("menus[].parent_key must be a non-empty string when provided")

        if self.enabled and self.target is None:
            raise ValueError("menus[].target is required when enabled=true")

        if self.unenabled_page is not None and not self.unenabled_page.strip():
            raise ValueError("menus[].unenabled_page must be a non-empty string")
        if self.icon is None:
            raise ValueError("menus[].icon must be a string")

        return self


class MenuConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int
    menus: list[MenuItem]

    @model_validator(mode="after")
    def validate_config(self):
        if self.version != 1:
            raise ValueError("menu_config.version must be 1")

        seen_keys: set[str] = set()
        for menu in self.menus:
            if menu.key in seen_keys:
                raise ValueError(f"menus[].key duplicated: {menu.key}")
            seen_keys.add(menu.key)

        for menu in self.menus:
            if menu.parent_key is not None and menu.parent_key not in seen_keys:
                raise ValueError(
                    f"menus[].parent_key '{menu.parent_key}' in item '{menu.key}' "
                    f"references a non-existent key"
                )

        parent_keys = {m.parent_key for m in self.menus if m.parent_key}
        for menu in self.menus:
            is_group_header = menu.key in parent_keys
            if (
                menu.visible
                and not menu.enabled
                and not is_group_header
                and menu.unenabled_page is None
            ):
                raise ValueError(
                    f"menus[].unenabled_page is required for item '{menu.key}' "
                    f"because it is visible but not enabled and is not a group header"
                )

        return self


def validate_menu_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("menu_config must be an object")
    model = MenuConfig.model_validate(value)
    return model.model_dump(mode="json")
