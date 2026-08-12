"""Делает модули проекта импортируемыми для pytest; демо-режим по умолчанию."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SHOPPER_DEMO_MODE", "1")
