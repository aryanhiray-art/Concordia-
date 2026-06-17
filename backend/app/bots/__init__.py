"""AI voting bots — Ollama-driven agents that vote in the daily basket.

Bots are `members` with is_bot=1: they cast weighted votes (influencing the
consensus basket) but have no Robinhood account, so the executor never trades
for them. Each bot embodies a distinct strategy; weekly it earns/loses voting
power based on how its picks actually performed (see services/bots.py).
"""
