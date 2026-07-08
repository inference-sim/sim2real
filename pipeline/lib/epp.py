"""EPP image injection utilities."""


def inject_epp_image(scenario_dict: dict, registry: str, repo_name: str, tag: str, algo_name: str | None = None) -> bool:
    """Inject EPP image into all scenario entries at ``router.epp.image``.

    Registry and bare repository name are written as separate fields so the
    llm-d-router chart can render ``{registry}/{repository}:{tag}`` directly.
    Returns True if injection occurred, False if skipped (empty registry or
    no scenarios). When ``algo_name`` is provided the effective tag becomes
    ``{tag}-{algo_name}``.
    """
    if not registry:
        return False
    scenario_list = scenario_dict.get("scenario", [])
    if not scenario_list:
        return False
    effective_tag = f"{tag}-{algo_name}" if algo_name else tag
    epp_img = {
        "registry": registry,
        "repository": repo_name,
        "tag": effective_tag,
        "pullPolicy": "Always",
    }
    for entry in scenario_list:
        entry.setdefault("router", {}).setdefault("epp", {})["image"] = epp_img
    return True


def inject_image_ref(scenario_dict: dict, repository: str, tag: str) -> bool:
    """Inject a pre-built EPP image ref into all scenario entries at ``router.epp.image``.

    ``repository`` is a full image path (e.g. ``ghcr.io/org/scheduler``) which
    is split at the last ``/`` into ``registry`` and bare ``repository`` fields
    to match the chart's expected shape. Returns True if injection occurred,
    False if skipped (no scenarios).
    """
    scenario_list = scenario_dict.get("scenario", [])
    if not scenario_list:
        return False
    if "/" in repository:
        registry, bare_repo = repository.rsplit("/", 1)
    else:
        registry, bare_repo = "", repository
    img = {
        "registry": registry,
        "repository": bare_repo,
        "tag": tag,
        "pullPolicy": "Always",
    }
    for entry in scenario_list:
        entry.setdefault("router", {}).setdefault("epp", {})["image"] = img
    return True
