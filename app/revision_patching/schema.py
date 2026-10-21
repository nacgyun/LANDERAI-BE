"""Versioned PATCH contract. Unknown fields and executable payloads are rejected."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


CSS_PROPERTIES = set("""color font-size font-weight font-family font-style line-height
letter-spacing word-spacing text-align text-decoration-line text-decoration-color
text-decoration-style text-decoration-thickness text-transform text-indent white-space
word-break overflow-wrap text-overflow background-color background-image background-size
background-position background-repeat width height min-width max-width min-height max-height
box-sizing aspect-ratio margin padding border-width border-style border-color border-radius
box-shadow text-shadow opacity display visibility overflow overflow-x overflow-y
flex-direction flex-wrap flex-grow flex-shrink flex-basis justify-content align-items
align-content align-self order grid-template-columns grid-template-rows grid-auto-flow
grid-auto-columns grid-auto-rows grid-column grid-row justify-items justify-self gap row-gap
column-gap position top right bottom left z-index object-fit object-position cursor
outline-color outline-style outline-width outline-offset fill stroke stroke-width filter
transform transition-property transition-duration transition-timing-function transition-delay""".split())
for side in ("top", "right", "bottom", "left"):
    CSS_PROPERTIES.update((f"padding-{side}", f"margin-{side}"))
    CSS_PROPERTIES.update(f"border-{side}-{part}" for part in ("width", "style", "color"))
CSS_PROPERTIES.update(
    f"border-{corner}-radius"
    for corner in ("top-left", "top-right", "bottom-left", "bottom-right")
)

TAGS = set("p span div button a img strong em b i small h1 h2 h3 h4 h5 h6 ul ol li section article aside br hr".split())
ATTRIBUTES = set("href target rel download src srcset sizes alt width height loading title role placeholder value name type disabled readonly required checked selected min max step maxlength minlength multiple hidden lang dir".split())
BOOLEAN_ATTRIBUTES = set("disabled readonly required checked selected multiple hidden".split())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Target(StrictModel):
    selector: str = Field(min_length=1, max_length=1000)
    expected_count: int = Field(default=1, ge=1, le=500)


class SetText(Target):
    op: Literal["set_text"]
    expected_text: str = Field(min_length=1)
    value: str


class SetAttribute(Target):
    op: Literal["set_attribute"]
    name: str
    value: Union[str, bool, None]


class SetStyle(Target):
    op: Literal["set_style"]
    property: str
    mode: Literal["set", "scale", "add"] = "set"
    value: str | None = None
    factor: float | None = Field(default=None, gt=0, le=100)
    amount: float | None = None
    state: Literal["normal", "hover", "focus", "focus-visible", "active"] = "normal"
    pseudo: Literal["", "before", "after"] = ""
    important: bool = False

    @model_validator(mode="after")
    def validate_style(self):
        if self.property not in CSS_PROPERTIES:
            raise ValueError("unsupported_css_property: " + self.property)
        fields = {"set": "value", "scale": "factor", "add": "amount"}
        selected = fields[self.mode]
        if getattr(self, selected) is None:
            raise ValueError(f"{self.mode} requires {selected}")
        if any(getattr(self, field) is not None for field in fields.values() if field != selected):
            raise ValueError("Only the selected mode value may be supplied")
        if self.mode != "set" and (self.state != "normal" or self.pseudo):
            raise ValueError("Relative operations support normal element styles only")
        return self


class Element(StrictModel):
    tag: str
    text: str = ""
    attributes: dict[str, Union[str, bool]] = Field(default_factory=dict)
    styles: dict[str, str] = Field(default_factory=dict)
    children: list["Element"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tag(self):
        if self.tag not in TAGS:
            raise ValueError("unsupported_tag: " + self.tag)
        if self.tag in ("img", "br", "hr") and (self.text or self.children):
            raise ValueError("Void elements cannot have content")
        if set(self.styles) - CSS_PROPERTIES:
            raise ValueError("unsupported_css_property")
        return self


class InsertElement(Target):
    op: Literal["insert_element"]
    position: Literal["beforebegin", "afterbegin", "beforeend", "afterend"]
    element: Element


Operation = Annotated[
    Union[SetText, SetAttribute, SetStyle, InsertElement],
    Field(discriminator="op"),
]


class PatchPlan(StrictModel):
    version: Literal["1"] = "1"
    patches: list[Operation] = Field(min_length=1, max_length=500)
