"""Trusted plugin discovery using Python packaging entry points."""

from importlib.metadata import entry_points

from vertical_harness.contracts import DomainPlugin, PluginManifest


PLUGIN_GROUP = "vertical_agent.plugins"


def discover_plugins(allowlist: list[str]) -> list[DomainPlugin]:
    """Load installed plugins explicitly enabled by the host.

    :param allowlist: Plugin identifiers approved for this deployment.
    :return: Plugin instances in configured order.
    :raises ValueError: If identifiers or plugin metadata are invalid.
    """
    if len(allowlist) != len(set(allowlist)):
        raise ValueError("Duplicate plugin IDs in allowlist")
    candidates = entry_points(group=PLUGIN_GROUP)
    matches = {}
    for ep in candidates:
        if ep.name in allowlist:
            if ep.name in matches:
                raise ValueError(f"Duplicate entry point: {ep.name}")
            matches[ep.name] = ep
    missing = set(allowlist) - set(matches)
    if missing:
        raise ValueError(f"Required plugins not installed: {sorted(missing)}")

    loaded = []
    for identifier in allowlist:
        factory = matches[identifier].load()
        plugin = factory()
        manifest = PluginManifest.model_validate(plugin.manifest)
        if manifest.plugin_id != identifier:
            raise ValueError(f"Entry point / manifest mismatch: {identifier}")
        if not callable(getattr(plugin, "build", None)):
            raise TypeError(f"Plugin {identifier} does not implement build()")
        loaded.append(plugin)
    return loaded
