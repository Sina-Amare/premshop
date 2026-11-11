"""The five status tones: each one's words read on its own tint, on the site and in
the panel's light and dark themes, and the "with us" tone stays apart from cobalt,
which marks what can be pressed. Every value is read from the stylesheets."""

import colorsys
import re
from pathlib import Path

import pytest

from apps.core.tests.test_accessibility_browser import contrast

CSS = Path(__file__).resolve().parents[3] / "static" / "css"
# Attention, with us, done, closed, problem: the site keeps the badge names it had.
SITE_TONES = ("time", "progress", "ok", "neutral", "danger")
PANEL_TONES = ("attention", "progress", "done", "closed", "problem")


def hex_tokens(text: str) -> dict[str, str]:
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", text))


def to_oklab(colour: str) -> tuple[float, float, float]:
    srgb = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)
    long_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    medium = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    short = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
        1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
        0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short,
    )


def from_oklab(lightness: float, a: float, b: float) -> str:
    long_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    medium = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    short = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * long_ - 3.3077115913 * medium + 0.2309699292 * short,
        -1.2684380046 * long_ + 2.6097574011 * medium - 0.3413193965 * short,
        -0.0041960863 * long_ - 0.7034186147 * medium + 1.7076147010 * short,
    )
    srgb = (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055 for c in linear)
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in srgb)


def mixed(colour: str, ground: str, share: float) -> str:
    """CSS `color-mix(in oklab, colour share%, ground)`."""
    one, two = to_oklab(colour), to_oklab(ground)
    return from_oklab(*(x * share + y * (1 - share) for x, y in zip(one, two, strict=True)))


def rgb(colour: str) -> str:
    return "rgb({}, {}, {})".format(*(int(colour[i : i + 2], 16) for i in (1, 3, 5)))


def hue(colour: str) -> float:
    return colorsys.rgb_to_hls(*(int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)))[0] * 360


def resolve(value: str, tokens: dict[str, str]) -> str:
    value = value.strip()
    mix = re.fullmatch(
        r"color-mix\(in oklab, var\((--[a-z0-9-]+)\) (\d+)%, var\((--[a-z0-9-]+)\)\)", value
    )
    if mix:
        return mixed(tokens[mix[1]], tokens[mix[3]], int(mix[2]) / 100)
    plain = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    assert plain, value
    return tokens[plain[1]]


def site_tokens() -> dict[str, str]:
    """input.css, then storefront.css's remapping, as every customer page loads them."""
    tokens = hex_tokens((CSS / "input.css").read_text(encoding="utf-8"))
    store = (CSS / "storefront.css").read_text(encoding="utf-8")
    tokens.update(hex_tokens(store))
    for name, ref in re.findall(r"(--color-[a-z0-9-]+)\s*:\s*var\((--[a-z0-9-]+)\)", store):
        if ref in tokens:
            tokens[name] = tokens[ref]
    return tokens


def site_badges() -> dict[str, tuple[str, str]]:
    source = (CSS / "input.css").read_text(encoding="utf-8")
    tokens = site_tokens()
    badges = {}
    for tone in SITE_TONES:
        rule = re.search(rf"\.badge--{tone}\s*\{{([^}}]*)\}}", source)
        assert rule, f".badge--{tone} is missing from input.css"
        fields = dict(
            (key.strip(), val)
            for key, val in (part.split(":", 1) for part in rule[1].split(";") if ":" in part)
        )
        badges[tone] = (resolve(fields["color"], tokens), resolve(fields["background"], tokens))
    return badges


def panel_tones(theme: str) -> dict[str, tuple[str, str]]:
    source = (CSS / "admin-experience.css").read_text(encoding="utf-8")
    block = re.search(r"html\.dark\{([^}]*)\}" if theme == "dark" else r":root\{([^}]*)\}", source)
    assert block, theme
    tokens = hex_tokens(block[1])
    return {
        tone: (tokens[f"--panel-{tone}-text"], tokens[f"--panel-{tone}-bg"]) for tone in PANEL_TONES
    }


@pytest.mark.parametrize("tone", SITE_TONES)
def test_each_site_tone_reads_on_its_own_tint(tone):
    text, tint = site_badges()[tone]
    assert contrast(rgb(text), rgb(tint)) >= 4.5, (tone, text, tint)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tone", PANEL_TONES)
def test_each_panel_tone_reads_on_its_own_tint(theme, tone):
    text, tint = panel_tones(theme)[tone]
    assert contrast(rgb(text), rgb(tint)) >= 4.5, (theme, tone, text, tint)


def test_the_with_us_tone_is_not_the_colour_of_what_can_be_pressed():
    cobalt = hex_tokens((CSS / "storefront.css").read_text(encoding="utf-8"))["--v-accent"]
    panel = (CSS / "admin-experience.css").read_text(encoding="utf-8")
    dark = re.search(r"html\.dark\{([^}]*)\}", panel)
    assert dark
    dark_accent = hex_tokens(dark[1])["--panel-accent"]
    site = hex_tokens((CSS / "input.css").read_text(encoding="utf-8"))
    light = re.search(r":root\{([^}]*)\}", panel)
    assert light
    cyans = [site["--color-progress"], site["--color-progress-text"]]
    cyans += [
        hex_tokens(theme[1])[f"--panel-progress-{part}"]
        for theme in (light, dark)
        for part in ("bg", "text")
    ]
    for colour in cyans:
        for accent in (cobalt, dark_accent):
            distance = abs(hue(colour) - hue(accent))
            assert min(distance, 360 - distance) >= 25, (colour, accent)
