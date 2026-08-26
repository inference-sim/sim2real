"""Deep-merge logic for values assembly."""
import copy


# ── Tiered list merge strategy ────────────────────────────────────────────────

_LIST_KEY_CANDIDATES = ("name", "mountPath", "containerPort")


def _detect_list_key(base_list: list, overlay_list: list):
    """Return the first candidate key present in ALL dicts in both lists, or None."""
    all_items = base_list + overlay_list
    if not all_items:
        return None
    for candidate in _LIST_KEY_CANDIDATES:
        if all(candidate in d for d in all_items):
            return candidate
    return None


# ── Flag-list merge (path-scoped) ─────────────────────────────────────────────

#: Key-path suffixes whose scalar lists merge by flag name rather than being
#: replaced wholesale. Matched as a SUFFIX of the merge path, with list indices
#: elided from the path, so one entry covers both the `decode` and `prefill`
#: roles AND both merge roots in use today: `assemble_run` merges whole
#: documents (`scenario.decode.vllm.additionalFlags`) while `capacity` merges a
#: single scenario entry (`decode.vllm.additionalFlags`). An absolute path
#: anchored at the document root would match the first and silently miss the
#: second. See issue #851.
_FLAG_LIST_PATH_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("vllm", "additionalFlags"),
)


def _is_flag_list_path(path: tuple[str, ...]) -> bool:
    """Return True when `path` ends with a registered flag-list suffix."""
    return any(
        len(path) >= len(suffix) and tuple(path[-len(suffix):]) == suffix
        for suffix in _FLAG_LIST_PATH_SUFFIXES
    )


def _flag_key(entry: str) -> str:
    """Return the merge key for one CLI-flag list entry.

    `--max-num-seqs=256` keys on `--max-num-seqs`; a bare
    `--enable-chunked-prefill` is its own key. A leading `--no-` is stripped so
    a flag and its negation collapse to a single key: `--enable-prefix-caching`
    and `--no-enable-prefix-caching` express one decision, so a later layer
    stating either form must override the earlier rather than emit both.
    Emitting both would leave the outcome to vLLM's argparse ordering — the
    silent conflict issue #851 exists to avoid, and the reason it rejected
    simple list concatenation.
    """
    name = entry.split("=", 1)[0]
    if name.startswith("--no-"):
        name = "--" + name[len("--no-"):]
    return name


def _index_flags(entries: list, path: tuple[str, ...], side: str) -> dict:
    """Return ``{flag key: literal entry}`` for one flag list, preserving order.

    Raises ValueError on a non-string entry, an entry that is not a ``--`` flag,
    or two entries collapsing to the same key. Each would otherwise produce a
    silently wrong flag set, which is the failure class this tier closes.
    """
    where = ".".join(path) or "<root>"
    indexed: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(
                f"{where}: {side} flag list entry is "
                f"{type(entry).__name__}, not a string: {entry!r} — this path "
                "merges by flag name and cannot key a non-string entry"
            )
        if not entry.startswith("--"):
            raise ValueError(
                f"{where}: {side} flag list entry does not start with '--': "
                f"{entry!r} — this path merges by flag name, so a bare value "
                "(as used in space-separated arg lists like router.proxy.args) "
                "would be mis-keyed as a flag name"
            )
        key = _flag_key(entry)
        if key in indexed:
            raise ValueError(
                f"{where}: {side} flag list states '{key}' twice "
                f"({indexed[key]!r} and {entry!r}) — merging by flag name would "
                "silently drop one. State the flag once; note that '--no-X' and "
                "'--X' are the same key."
            )
        indexed[key] = entry
    return indexed


def _k8s_identity(item):
    """Return a Kubernetes identity tuple (apiVersion, kind, metadata.name), or None.

    Used to merge free-form `extraObjects:`-style manifest lists by object identity
    rather than by list position. Returns None for anything that is not a dict
    carrying all three of apiVersion, kind, and metadata.name.
    """
    if not isinstance(item, dict):
        return None
    meta = item.get("metadata")
    if not isinstance(meta, dict) or "name" not in meta:
        return None
    if "apiVersion" not in item or "kind" not in item:
        return None
    return (item["apiVersion"], item["kind"], meta["name"])


def _merge_by_keyfn(base_list: list, overlay_list: list, keyfn) -> list:
    """Merge two all-dict lists by a key extractor.

    Base entries are emitted first (deep-merged with the same-keyed overlay entry
    when present); overlay-only entries are appended in order.
    """
    overlay_by_key = {keyfn(item): item for item in overlay_list}
    result = []
    seen_keys = set()
    for bitem in base_list:
        k = keyfn(bitem)
        seen_keys.add(k)
        if k in overlay_by_key:
            result.append(deep_merge(bitem, overlay_by_key[k]))
        else:
            result.append(copy.deepcopy(bitem))
    for oitem in overlay_list:
        if keyfn(oitem) not in seen_keys:
            result.append(copy.deepcopy(oitem))
    return result


def _is_k8s_manifest(item) -> bool:
    """True if item looks like a Kubernetes manifest: a dict with apiVersion and kind."""
    return isinstance(item, dict) and "apiVersion" in item and "kind" in item


def _k8s_markers_conflict(a: dict, b: dict) -> bool:
    """True when a and b's (apiVersion, kind) markers differ and are not both absent.

    Fires whenever the two (apiVersion, kind) tuples are unequal — including the
    one-sided case where one entry carries a marker and the other carries none. Used by
    the Tier 3 positional merge to refuse folding two entries that look like distinct
    Kubernetes manifests (e.g. a malformed manifest missing apiVersion or kind that
    escaped the Tier 2a gate) rather than silently smearing unrelated objects together.

    Known limitation: two malformed manifests with *identical* markers (e.g. both
    missing apiVersion, same kind) are not a conflict here and still fold positionally.
    Malformed-manifest handling is out of scope (kubectl/Helm reject them); see #278 and
    the known-limitation tests in test_values.py.
    """
    a_markers = (a.get("apiVersion"), a.get("kind"))
    b_markers = (b.get("apiVersion"), b.get("kind"))
    if a_markers == (None, None) and b_markers == (None, None):
        return False
    return a_markers != b_markers


def _merge_k8s_objects(base_list: list, overlay_list: list) -> list:
    """Merge two lists of Kubernetes manifests without ever folding dissimilar objects.

    Manifests carrying a full identity (apiVersion, kind, metadata.name) merge by that
    identity: distinct objects are all preserved, and an overlay entry sharing an
    identity patches the matching base manifest. Manifests lacking metadata.name (e.g.
    metadata.generateName, or List kinds) cannot be keyed, so they are carried through
    untouched — base entries first, then overlay entries — rather than positionally
    folded into a dissimilar object.

    Raises ValueError on a duplicate identity within either list: duplicate
    (apiVersion, kind, metadata.name) is an invalid manifest set that would otherwise
    silently lose data.
    """
    overlay_by_key = {}
    for oitem in overlay_list:
        k = _k8s_identity(oitem)
        if k is None:
            continue
        if k in overlay_by_key:
            raise ValueError(f"duplicate Kubernetes object identity in overlay list: {k}")
        overlay_by_key[k] = oitem

    result = []
    seen_keys = set()
    for bitem in base_list:
        k = _k8s_identity(bitem)
        if k is None:
            result.append(copy.deepcopy(bitem))  # nameless: carry through, never fold
            continue
        if k in seen_keys:
            raise ValueError(f"duplicate Kubernetes object identity in base list: {k}")
        seen_keys.add(k)
        if k in overlay_by_key:
            result.append(deep_merge(bitem, overlay_by_key[k]))
        else:
            result.append(copy.deepcopy(bitem))
    for oitem in overlay_list:
        k = _k8s_identity(oitem)
        if k is None or k not in seen_keys:
            result.append(copy.deepcopy(oitem))  # overlay-only (keyed or nameless)
    return result


def _merge_lists(base_list: list, overlay_list: list) -> list:
    """Merge two lists using a tiered strategy.

    Tier 1:  either list contains non-dict items → overlay replaces base.
    Tier 2a: all entries are Kubernetes manifests → merge by (apiVersion, kind,
             metadata.name) identity; manifests without metadata.name are carried
             through, never folded. Covers free-form `extraObjects:`.
    Tier 2b: all-dict lists with a common top-level key field → merge by key.
    Tier 3:  all-dict lists without a common key → positional (index-based) merge;
             refuses (raises ValueError) to fold two entries whose (apiVersion, kind)
             markers differ and are not both absent — a malformed manifest that escaped
             Tier 2a. Entries with identical or wholly-absent markers still fold.

    Empty overlay → returns [] (explicit clear). Empty base → returns copy of overlay.
    """
    if not overlay_list:
        return []
    if not base_list:
        return copy.deepcopy(overlay_list)

    # Tier 1: any non-dict item → replace
    if not (all(isinstance(x, dict) for x in base_list)
            and all(isinstance(x, dict) for x in overlay_list)):
        return copy.deepcopy(overlay_list)

    # Tier 2a: Kubernetes manifest lists — merge by identity, never positionally fold
    if all(_is_k8s_manifest(x) for x in base_list + overlay_list):
        return _merge_k8s_objects(base_list, overlay_list)

    # Tier 2b: named-key merge
    key_field = _detect_list_key(base_list, overlay_list)
    if key_field is not None:
        return _merge_by_keyfn(base_list, overlay_list, lambda d: d[key_field])

    # Tier 3: positional merge — surplus from either side preserved
    result = []
    for i in range(max(len(base_list), len(overlay_list))):
        if i < len(base_list) and i < len(overlay_list):
            if _k8s_markers_conflict(base_list[i], overlay_list[i]):
                raise ValueError(
                    "refusing to positionally fold Kubernetes manifests with differing "
                    f"identity markers at index {i}: "
                    f"{(base_list[i].get('apiVersion'), base_list[i].get('kind'))} vs "
                    f"{(overlay_list[i].get('apiVersion'), overlay_list[i].get('kind'))} "
                    "— an entry is likely missing apiVersion or kind"
                )
            result.append(deep_merge(base_list[i], overlay_list[i]))
        elif i < len(base_list):
            result.append(copy.deepcopy(base_list[i]))
        else:
            result.append(copy.deepcopy(overlay_list[i]))
    return result


def deep_merge(base: dict, overlay: dict) -> dict:
    """Deep-merge overlay onto base. Dict keys merged recursively.

    Lists of dicts are merged by Kubernetes identity, named key, or positional index
    (see _merge_lists). Lists of scalars are replaced entirely. Returns a new dict
    (deep copy).
    """
    result = copy.deepcopy(base)
    for key, oval in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(oval, dict):
            result[key] = deep_merge(result[key], oval)
        elif key in result and isinstance(result[key], list) and isinstance(oval, list):
            result[key] = _merge_lists(result[key], oval)
        else:
            result[key] = copy.deepcopy(oval)
    return result
