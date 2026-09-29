"""
CLI Entry Points for Indonesia IVR
===================================

Provides command-line interfaces for the IVR system.
"""

from .interactive_voice_solace import main as voice_main

__all__ = ["voice_main"]