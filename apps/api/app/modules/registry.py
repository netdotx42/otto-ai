from __future__ import annotations

from app.modules.base import Module
from app.modules.operations import Autopilot, Builder, Intel, Market, Simulator
from app.modules.research import Alpha, Forensic, Portfolio, Sentinel, Whale

MODULES: dict[str, Module] = {
    m.name: m
    for m in (Sentinel(), Forensic(), Whale(), Alpha(), Simulator(), Portfolio(), Autopilot(), Builder(), Intel(), Market())
}
