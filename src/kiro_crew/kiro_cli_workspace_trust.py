"""Read kiro-cli's own "Trust this workspace?" answer for a folder. Read-only.

kiro-cli (V3) stores the answer as ``workspaceTrusted: true`` in the per-workspace
policy file under the user's ``.kiro``:
``<kiro home>/workspace-roots/<bucket>/permissions.yaml`` (or ``.json``). This
module is a port of the reader in kiro-cli's ``packages/tui-common/src/workspace``
(``workspace-home.ts``, ``workspace-hash.ts``, ``workspace-policy-file.ts``,
``workspace-trust-store.ts`` ``readWorkspaceTrust``), so the folder Crew asks about
lands in the same bucket kiro-cli wrote, Windows case rules included.

Every answer other than a readable record carrying ``workspaceTrusted: true`` in the
folder's own case mode is "not trusted": a missing, unreadable, non-UTF-8, blank or
unparsable file, a folder whose identity cannot be settled, and the user's home
(kiro-cli never reads a grant for home). Nothing here raises or writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import ntpath
import os
import posixpath
import re
import sys
from typing import Any

logger = logging.getLogger(__name__)

_WORKSPACE_ROOTS_DIRNAME = "workspace-roots"
_POLICY_FILENAMES = ("permissions.yaml", "permissions.json")
_TRUSTED_KEY = "workspaceTrusted"
_CASE_KEY = "workspaceTrustCase"
#: The separator kiro-cli normalizes every workspace path to before hashing. A string
#: rule of the hash, not a filesystem separator.
_SLASH = "/"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _collapse_posix(path: str) -> str:
    """kiro-cli's ``collapsePosix``: ``//``, ``.`` and ``..`` collapsed, trailing ``/`` kept."""
    if not path:
        return "."
    rooted = path.startswith("/")
    trailing = path.endswith("/")
    parts: list[str] = []
    for segment in path.split(_SLASH):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not rooted:
                parts.append("..")
            continue
        parts.append(segment)
    joined = _SLASH.join(parts)
    if not joined:
        if rooted:
            return "/"
        return "./" if trailing else "."
    body = f"{joined}/" if trailing else joined
    return f"/{body}" if rooted else body


def _normalize(path: str, *, fold: bool) -> str:
    """kiro-cli's ``normalizeWorkspacePathCase``."""
    absolute = path if os.path.isabs(path) else os.path.abspath(path)
    forward = absolute.replace("\\", "/")
    is_unc = _is_windows() and forward.startswith("//")
    canonical = _collapse_posix(forward)
    if is_unc and not canonical.startswith("//"):
        canonical = f"/{canonical}"
    if len(canonical) > 1:
        stripped = canonical.rstrip("/")
        if stripped and not re.fullmatch(r"[A-Za-z]:", stripped):
            canonical = stripped
    return canonical.lower() if fold else canonical


def _stat_id(path: str) -> tuple[int, int] | None:
    """``(dev, ino)``, or ``None`` when unreadable or the file id is zero."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if st.st_ino == 0:
        return None
    return st.st_dev, st.st_ino


def _windows_case_mode(physical: str) -> str | None:
    """kiro-cli's ``windowsCaseMode``: ``exact``, ``folded``, or ``None`` (unsettled)."""
    prefix = ntpath.splitdrive(physical)[0]
    rest = physical[len(prefix) :]
    if rest[:1] in ("\\", "/"):
        prefix += rest[0]
    names = [n for n in re.split(r"[\\/]", physical[len(prefix) :]) if n]
    parent = prefix
    mode = "folded"
    for name in names:
        lower = name.lower()
        other = name.upper() if lower == name else lower
        path = ntpath.join(parent, name)
        if other != name:
            first = _stat_id(path)
            if first is None:
                return None
            try:
                st = os.stat(ntpath.join(parent, other))
            except FileNotFoundError:
                mode = "exact"
            except NotADirectoryError:
                mode = "exact"
            except OSError:
                return None
            else:
                if st.st_ino == 0:
                    return None
                if (st.st_dev, st.st_ino) != first:
                    mode = "exact"
        parent = path
    return mode


def _identity(cwd: str) -> tuple[str, str] | None:
    """kiro-cli's ``resolveWorkspaceTrustIdentity`` as ``(root, case_mode)``, or ``None``."""
    try:
        physical = os.path.realpath(cwd, strict=True)
    except (OSError, ValueError):
        return None
    if _is_windows():
        if physical.startswith("\\\\?\\"):
            physical = physical[4:]
        case_mode = _windows_case_mode(physical)
        if case_mode is None:
            return None
    else:
        case_mode = "folded"
    if case_mode == "exact":
        kept = _normalize(physical, fold=False)
        drive = ntpath.splitdrive(physical)[0]
        cut = len(drive.replace("\\", "/")) + (
            1 if physical[len(drive) : len(drive) + 1] in ("\\", "/") else 0
        )
        root = kept[:cut].lower() + kept[cut:]
    else:
        root = _normalize(physical, fold=_is_windows())
    a, b = _stat_id(cwd), _stat_id(root)
    if a is None or a != b:
        return None
    return root, case_mode


def _os_home() -> str:
    """kiro-cli's ``policyOsHome``: a non-empty ``USERPROFILE``/``HOME``, else the lookup."""
    value = os.environ.get("USERPROFILE" if _is_windows() else "HOME")
    return value or os.path.expanduser("~")


def _real_prefix(paths: Any, path: str) -> str:
    tail: list[str] = []
    current = path
    while True:
        try:
            return paths.join(os.path.realpath(current, strict=True), *reversed(tail))
        except (OSError, ValueError):
            parent = paths.dirname(current)
            if parent == current:
                return path
            tail.append(paths.basename(current))
            current = parent


def _kiro_home() -> str:
    """kiro-cli's ``workspacePolicyHomeFrom``, without the test-only redirect.

    A release host never honours ``KIRO_TEST_POLICY_HOME``, so neither does Crew.
    """
    paths = ntpath if _is_windows() else posixpath
    home = _os_home()
    fallback = paths.join(home, ".kiro")
    override = (os.environ.get("KIRO_HOME") or "").strip()
    if not override or not paths.isabs(override):
        return fallback
    real = _real_prefix(paths, paths.abspath(override))
    if paths.dirname(real) == real:  # a filesystem root
        return fallback
    try:
        rel = paths.relpath(_real_prefix(paths, paths.abspath(home)), real)
    except ValueError:  # different drives on Windows
        return paths.abspath(override)
    contains_home = rel == "." or (not rel.startswith("..") and not paths.isabs(rel))
    return fallback if contains_home else paths.abspath(override)


def _bucket(root: str, case_mode: str) -> str:
    """kiro-cli's ``workspacePolicyDir`` bucket name."""
    if case_mode == "exact":
        text = f"case-exact\0{root}"
    else:
        text = _normalize(root, fold=_is_windows())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_text(path: str) -> str | None:
    """The file's UTF-8 text; ``None`` when missing. Raises on any other failure."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return None
    return data.decode("utf-8")  # strict, BOM kept, as kiro-cli reads it


def _policy_path(directory: str) -> str:
    """kiro-cli's ``workspaceTrustPath``: first non-blank file (yaml first), else yaml."""
    for name in _POLICY_FILENAMES:
        path = os.path.join(directory, name)
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            return path  # unreadable counts as populated, and reads as corrupt
        if text is not None and text.strip():
            return path
    return os.path.join(directory, _POLICY_FILENAMES[0])


def _yaml_loader() -> Any:
    """A safe loader with YAML 1.2 booleans and duplicate keys refused, as kiro-cli's parser."""
    import yaml  # type: ignore[import-untyped]

    class _Loader(yaml.SafeLoader):  # type: ignore[misc]
        pass

    bool_tag = "tag:yaml.org,2002:bool"
    _Loader.yaml_implicit_resolvers = {
        key: [(tag, rx) for tag, rx in resolvers if tag != bool_tag]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _Loader.add_implicit_resolver(
        bool_tag, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
    )

    def _mapping(loader: Any, node: Any, deep: bool = False) -> dict:
        seen: set = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, "duplicate key", key_node.start_mark
                )
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    _Loader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        lambda loader, node: _mapping(loader, node, deep=True),
    )
    return _Loader


def _parse(path: str, text: str) -> dict[str, Any]:
    if path.endswith(".json"):
        doc = json.loads(text)
    else:
        loader = _yaml_loader()(text)  # a yaml.SafeLoader subclass, driven directly
        try:
            doc = loader.get_single_data()
        finally:
            loader.dispose()
    if not isinstance(doc, dict):
        raise ValueError("policy file is not a mapping")
    return doc


def kiro_cli_trusts_folder(folder: str | os.PathLike[str] | None) -> bool:
    """Whether kiro-cli recorded "trust this workspace" for *folder*. Never raises."""
    if not folder:
        return False
    try:
        identity = _identity(os.fspath(folder))
        if identity is None:
            return False
        root, case_mode = identity
        home = _identity(_os_home())
        if home is not None and home[0] == root:
            return False  # kiro-cli never reads a grant for home
        path = _policy_path(
            os.path.join(_kiro_home(), _WORKSPACE_ROOTS_DIRNAME, _bucket(root, case_mode))
        )
        text = _read_text(path)
        if text is None or not text.strip():
            return False
        doc = _parse(path, text)
    except Exception:  # noqa: BLE001 - any failure is "not trusted", silently
        logger.debug("kiro-cli workspace trust for %s could not be read", folder, exc_info=True)
        return False
    stored_case = doc.get(_CASE_KEY)
    case_ok = stored_case == "exact" if case_mode == "exact" else _CASE_KEY not in doc
    return doc.get(_TRUSTED_KEY) is True and case_ok
