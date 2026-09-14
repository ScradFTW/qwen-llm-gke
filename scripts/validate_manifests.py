#!/usr/bin/env python3
"""Structural validation for this repo's Kubernetes manifests and cloudbuild.yaml.

This repo has no application source of ours to unit-test -- llama.cpp's
server binary is upstream, not something we wrote. What IS ours, and has
real parsing/cross-referencing logic worth protecting with tests, is the
plumbing that ties cloudbuild.yaml's substitutions to the files in k8s/:
_DOCKERFILE and _DEPLOYMENT_MANIFEST have to point at files that exist,
the active deployment manifest has to still contain the IMAGE_PLACEHOLDER
token the render-manifests step's `sed` depends on, and the apply step's
`-f` list has to actually include the file render-manifests renders. A
typo in any of those breaks the deploy at `kubectl apply` time, not at
review time -- exactly the kind of silent mismatch this project has
already hit once (see cloudbuild.yaml's own comments on real bugs found
the hard way: a *.so glob miss, a missing libgomp1, a probe-routing bug).

Run directly (`python3 scripts/validate_manifests.py`) as a fast,
dependency-light pre-deploy check -- this is what cloudbuild.yaml's
`validate` step runs -- or import its functions from tests.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
K8S_DIR = REPO_ROOT / "k8s"
CLOUDBUILD_PATH = REPO_ROOT / "cloudbuild.yaml"

REQUIRED_TOP_LEVEL_FIELDS = ("apiVersion", "kind", "metadata")

_SUBSTITUTION_RE = re.compile(r"^\$\{(_[A-Za-z0-9_]+)\}$")


def iter_yaml_documents(path: Path) -> list[Any]:
    """Parse every non-empty YAML document in a file (supports `---` multi-doc)."""
    with path.open() as f:
        return [doc for doc in yaml.safe_load_all(f) if doc is not None]


def validate_k8s_manifest(doc: Any) -> list[str]:
    """Return human-readable errors for one parsed k8s manifest document.

    Checks the fields every k8s object needs to be applyable at all:
    apiVersion, kind, and a non-empty metadata.name.
    """
    if not isinstance(doc, dict):
        return [f"document is not a mapping (got {type(doc).__name__})"]

    errors: list[str] = []
    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in doc:
            errors.append(f"missing required field '{field}'")

    metadata = doc.get("metadata")
    if "metadata" in doc and not isinstance(metadata, dict):
        errors.append("'metadata' is not a mapping")
    elif not isinstance(metadata, dict) or not metadata.get("name"):
        errors.append("missing required field 'metadata.name'")

    return errors


def validate_k8s_directory(k8s_dir: Path) -> dict[str, list[str]]:
    """Validate every *.yaml file in k8s_dir. Returns {filename: [errors]}."""
    results: dict[str, list[str]] = {}
    for path in sorted(k8s_dir.glob("*.yaml")):
        file_errors: list[str] = []
        try:
            docs = iter_yaml_documents(path)
        except yaml.YAMLError as exc:
            file_errors.append(f"invalid YAML: {exc}")
            docs = []

        if not docs and not file_errors:
            file_errors.append("file contains no YAML documents")

        for i, doc in enumerate(docs):
            prefix = "" if len(docs) == 1 else f"document {i}: "
            file_errors.extend(f"{prefix}{e}" for e in validate_k8s_manifest(doc))

        if file_errors:
            results[path.name] = file_errors
    return results


def load_cloudbuild(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def get_step(cloudbuild: dict, step_id: str) -> Optional[dict]:
    for step in cloudbuild.get("steps", []):
        if step.get("id") == step_id:
            return step
    return None


def extract_flag_values(args: list[str], flag: str) -> list[str]:
    """Return the value immediately following each occurrence of `flag` in args."""
    values = []
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            values.append(args[i + 1])
    return values


def resolve_substitution(cloudbuild: dict, value: str) -> str:
    """Resolve a single `${_FOO}`-style substitution reference to its default value.

    Returns `value` unchanged if it isn't a substitution reference.
    Raises KeyError if it references a substitution that isn't defined.
    """
    match = _SUBSTITUTION_RE.match(value)
    if not match:
        return value
    name = match.group(1)
    substitutions = cloudbuild.get("substitutions", {})
    if name not in substitutions:
        raise KeyError(f"undefined substitution '{name}'")
    return substitutions[name]


def validate_cloudbuild_wiring(repo_root: Path, cloudbuild: dict) -> list[str]:
    """Cross-check cloudbuild.yaml's substitutions and steps against the repo's files."""
    errors: list[str] = []
    substitutions = cloudbuild.get("substitutions", {})

    for sub_name in ("_DOCKERFILE", "_DEPLOYMENT_MANIFEST"):
        if sub_name not in substitutions:
            errors.append(f"substitutions.{sub_name} is not defined")
            continue
        rel_path = substitutions[sub_name]
        if not (repo_root / rel_path).is_file():
            errors.append(
                f"substitutions.{sub_name} points at '{rel_path}', which doesn't exist"
            )

    deployment_manifest = substitutions.get("_DEPLOYMENT_MANIFEST")
    if deployment_manifest and (repo_root / deployment_manifest).is_file():
        content = (repo_root / deployment_manifest).read_text()
        if "IMAGE_PLACEHOLDER" not in content:
            errors.append(
                f"'{deployment_manifest}' (substitutions._DEPLOYMENT_MANIFEST) has no "
                "IMAGE_PLACEHOLDER token for the render-manifests step's sed to replace"
            )

    render_step = get_step(cloudbuild, "render-manifests")
    rendered_output_path = None
    if render_step is None:
        errors.append("no cloudbuild step with id 'render-manifests'")
    else:
        script = "\n".join(render_step.get("args", []))
        redirect_match = re.search(r">\s*(\S+)", script)
        if redirect_match is None:
            errors.append("render-manifests step's script has no '> output' redirect")
        else:
            rendered_output_path = redirect_match.group(1)

    apply_step = get_step(cloudbuild, "apply")
    if apply_step is None:
        errors.append("no cloudbuild step with id 'apply'")
    else:
        targets = extract_flag_values(apply_step.get("args", []), "-f")
        if not targets:
            errors.append("apply step has no '-f' arguments")
        for target in targets:
            if target == rendered_output_path:
                continue  # the rendered manifest -- checked against render-manifests below
            if not (repo_root / target).is_file():
                errors.append(f"apply step references '{target}', which doesn't exist")
        if rendered_output_path is not None and rendered_output_path not in targets:
            errors.append(
                f"render-manifests writes '{rendered_output_path}' but the apply step "
                "never applies it"
            )

    return errors


def main() -> int:
    ok = True

    k8s_errors = validate_k8s_directory(K8S_DIR)
    for filename, errors in k8s_errors.items():
        ok = False
        for error in errors:
            print(f"k8s/{filename}: {error}", file=sys.stderr)

    cloudbuild = load_cloudbuild(CLOUDBUILD_PATH)
    for error in validate_cloudbuild_wiring(REPO_ROOT, cloudbuild):
        ok = False
        print(f"cloudbuild.yaml: {error}", file=sys.stderr)

    if ok:
        print("OK: all k8s manifests and cloudbuild.yaml wiring look consistent")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
