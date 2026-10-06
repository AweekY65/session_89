"""Deterministic in-memory TCP-style congestion control simulator.

No real network, sockets, OS TCP state, or external services are used.
All packets, ACKs, time, and network conditions live in memory and are
driven by a virtual clock.
"""

from .sender import Sender, State
from .network import Network, NetworkConfig
from .receiver import Receiver
from .simulation import Simulation, SimConfig, LogEntry

__all__ = [
    "Sender",
    "State",
    "Network",
    "NetworkConfig",
    "Receiver",
    "Simulation",
    "SimConfig",
    "LogEntry",
]
