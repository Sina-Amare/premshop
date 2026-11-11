"""The bot panel's emoji: the one tracked file allowed to carry them.

A bot button holds text only, so the site's SVG icons cannot reach it, and the owner
chose emoji for the bot (2026-11-11); the website stays without them
(design-language, «Decided by the owner»).
"""

from django.utils.text import slugify

UNPAID, ACTIVE, DELIVERED, CLOSED = "⏳", "🔄", "✅", "❌"
BUY, ACCOUNT, SUPPORT, BACK, COPY = "🛒", "👤", "💬", "🔙", "📋"
OTHER_PRODUCT = "📦"

# Matched as whole words of the product's name, first match first, so a narrower name
# (apple-music) comes before a wider one (apple).
PRODUCTS = {
    "chatgpt": "🤖",
    "openai": "🤖",
    "claude": "🧠",
    "gemini": "✨",
    "google-ai": "✨",
    "perplexity": "🔎",
    "poe": "💭",
    "midjourney": "🎨",
    "runway": "🎥",
    "adobe": "🖌️",
    "cursor": "💻",
    "replit": "💻",
    "jetbrains": "💻",
    "github": "🐙",
    "docker": "🐳",
    "spotify": "🎧",
    "apple-music": "🎵",
    "suno": "🎼",
    "netflix": "🎬",
    "disney": "🏰",
    "crunchyroll": "🍥",
    "youtube": "▶️",
    "apple": "🍎",
    "google-play": "📱",
    "playstation": "🎮",
    "xbox": "🎮",
    "steam": "🎮",
    "amazon": "🛍️",
    "telegram": "⭐",
}


def for_product(name: str) -> str:
    words = f"-{slugify(name)}-"
    return next((emoji for key, emoji in PRODUCTS.items() if f"-{key}-" in words), OTHER_PRODUCT)
