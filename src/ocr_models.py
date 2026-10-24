"""Named OCR model choices shared by entry routing and model construction."""

from enum import Enum


class OcrModel(str, Enum):
    UNIVERSAL = "universal"
    OFFICIAL = "official"
    TIXCRAFT_TM = "tixcraft_tm"
