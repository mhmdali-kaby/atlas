"""One module per agent; each registers its mention handler and schedule hooks with
:data:`atlas_agents.registry.HOOKS` on import. :func:`atlas_agents.registry.discover_hooks` imports
every module here, so a new agent is a new file, not an edit to the supervisor (ATL-101)."""
