"""Markers left in text until a business decision or trial delivery fills them."""

import re

PLACEHOLDER = re.compile(r"\[(?:تصمیم مالک|وابسته به تصمیم|از تحویل آزمایشی)[^\]\n]*\]?")
