"""Optional providers — local STT/LLM/TTS and non-Gemini APIs.

Nothing in the app imports this package unless the configuration selects a
provider, so an install that uses Gemini pays nothing for its presence. See
providers/base.py for the contract and why it points the way it does.
"""
