"""Briefing delivery layer.

Per-channel adapters that take a generated briefing and ship it
somewhere. Each adapter exposes a single ``send_*`` function that
degrades to ``status="skipped"`` when its credentials aren't present
and never raises — failures land in the returned status dict so the
orchestrator can record them without aborting the run.
"""
