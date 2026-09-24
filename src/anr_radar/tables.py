"""Unity Catalog naming. Dev and prod write to different schema prefixes."""

from dataclasses import dataclass

LAYERS = ("bronze", "silver", "gold")


@dataclass(frozen=True)
class Tables:
    catalog: str = "workspace"
    prefix: str = "anr_"

    def schema(self, layer: str) -> str:
        assert layer in LAYERS, layer
        return f"{self.catalog}.{self.prefix}{layer}"

    def __call__(self, layer: str, name: str) -> str:
        return f"{self.schema(layer)}.{name}"
