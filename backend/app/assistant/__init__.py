"""In-app AI assistant: turns plain requests into PiTun API calls.

Design: roadmap/07-ai-assistant.md (private). The model gets a few generic
tools (search the API, read, propose a plan) plus fuzzy node/device lookup;
every change is a plan the user approves before it runs.
"""
