#!/usr/bin/env python3
"""Tests for validate_manifests.py.

Run with: python3 -m unittest discover -s scripts -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import validate_manifests as vm


class ValidateK8sManifestTests(unittest.TestCase):
    """Unit tests for the per-document field checker."""

    def test_valid_manifest_has_no_errors(self):
        doc = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "llm"},
        }
        self.assertEqual(vm.validate_k8s_manifest(doc), [])

    def test_missing_api_version_is_reported(self):
        doc = {"kind": "Namespace", "metadata": {"name": "llm"}}
        errors = vm.validate_k8s_manifest(doc)
        self.assertIn("missing required field 'apiVersion'", errors)

    def test_missing_kind_is_reported(self):
        doc = {"apiVersion": "v1", "metadata": {"name": "llm"}}
        errors = vm.validate_k8s_manifest(doc)
        self.assertIn("missing required field 'kind'", errors)

    def test_missing_metadata_block_is_reported(self):
        doc = {"apiVersion": "v1", "kind": "Namespace"}
        errors = vm.validate_k8s_manifest(doc)
        self.assertIn("missing required field 'metadata'", errors)
        self.assertIn("missing required field 'metadata.name'", errors)

    def test_missing_metadata_name_is_reported(self):
        doc = {"apiVersion": "v1", "kind": "Namespace", "metadata": {}}
        errors = vm.validate_k8s_manifest(doc)
        self.assertEqual(errors, ["missing required field 'metadata.name'"])

    def test_blank_metadata_name_is_reported(self):
        doc = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ""}}
        errors = vm.validate_k8s_manifest(doc)
        self.assertEqual(errors, ["missing required field 'metadata.name'"])

    def test_metadata_not_a_mapping_is_reported(self):
        doc = {"apiVersion": "v1", "kind": "Namespace", "metadata": "llm"}
        errors = vm.validate_k8s_manifest(doc)
        self.assertEqual(errors, ["'metadata' is not a mapping"])

    def test_non_mapping_document_is_reported(self):
        errors = vm.validate_k8s_manifest(["not", "a", "mapping"])
        self.assertEqual(len(errors), 1)
        self.assertIn("not a mapping", errors[0])

    def test_none_document_is_reported(self):
        errors = vm.validate_k8s_manifest(None)
        self.assertEqual(len(errors), 1)
        self.assertIn("not a mapping", errors[0])


class IterYamlDocumentsTests(unittest.TestCase):
    """Unit tests for the multi-document YAML loader."""

    def test_single_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.yaml"
            path.write_text("apiVersion: v1\nkind: Namespace\n")
            docs = vm.iter_yaml_documents(path)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["kind"], "Namespace")

    def test_multi_document_separated_by_dashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "multi.yaml"
            path.write_text(
                "apiVersion: v1\nkind: Namespace\n---\napiVersion: v1\nkind: Service\n"
            )
            docs = vm.iter_yaml_documents(path)
            self.assertEqual(len(docs), 2)
            self.assertEqual([d["kind"] for d in docs], ["Namespace", "Service"])

    def test_blank_documents_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trailing.yaml"
            path.write_text("apiVersion: v1\nkind: Namespace\n---\n")
            docs = vm.iter_yaml_documents(path)
            self.assertEqual(len(docs), 1)

    def test_invalid_yaml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.yaml"
            path.write_text("apiVersion: v1\nkind: [unterminated\n")
            with self.assertRaises(vm.yaml.YAMLError):
                vm.iter_yaml_documents(path)


class ValidateK8sDirectoryTests(unittest.TestCase):
    """Tests against synthetic fixture directories (not just the real repo)."""

    def test_all_valid_files_produce_no_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            k8s_dir = Path(tmp)
            (k8s_dir / "ns.yaml").write_text(
                "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: llm\n"
            )
            (k8s_dir / "svc.yaml").write_text(
                "apiVersion: v1\nkind: Service\nmetadata:\n  name: qwen-llm\n"
            )
            self.assertEqual(vm.validate_k8s_directory(k8s_dir), {})

    def test_broken_yaml_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            k8s_dir = Path(tmp)
            (k8s_dir / "broken.yaml").write_text("kind: [unterminated\n")
            results = vm.validate_k8s_directory(k8s_dir)
            self.assertIn("broken.yaml", results)
            self.assertTrue(any("invalid YAML" in e for e in results["broken.yaml"]))

    def test_missing_field_file_is_flagged_and_valid_file_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            k8s_dir = Path(tmp)
            (k8s_dir / "good.yaml").write_text(
                "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: llm\n"
            )
            (k8s_dir / "bad.yaml").write_text("kind: Namespace\nmetadata:\n  name: llm\n")
            results = vm.validate_k8s_directory(k8s_dir)
            self.assertNotIn("good.yaml", results)
            self.assertIn("bad.yaml", results)
            self.assertIn("missing required field 'apiVersion'", results["bad.yaml"])

    def test_empty_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            k8s_dir = Path(tmp)
            (k8s_dir / "empty.yaml").write_text("")
            results = vm.validate_k8s_directory(k8s_dir)
            self.assertIn("empty.yaml", results)
            self.assertIn("file contains no YAML documents", results["empty.yaml"])

    def test_real_repo_k8s_directory_is_valid(self):
        """Integration check: the actual k8s/ manifests in this repo must pass."""
        self.assertEqual(vm.validate_k8s_directory(vm.K8S_DIR), {})


class ExtractFlagValuesTests(unittest.TestCase):
    def test_extracts_each_value_after_flag(self):
        args = ["apply", "-f", "a.yaml", "-f", "b.yaml"]
        self.assertEqual(vm.extract_flag_values(args, "-f"), ["a.yaml", "b.yaml"])

    def test_no_matches_returns_empty_list(self):
        self.assertEqual(vm.extract_flag_values(["apply"], "-f"), [])

    def test_flag_as_last_arg_is_ignored(self):
        self.assertEqual(vm.extract_flag_values(["apply", "-f"], "-f"), [])

    def test_only_matches_exact_flag(self):
        args = ["apply", "--file", "a.yaml", "-f", "b.yaml"]
        self.assertEqual(vm.extract_flag_values(args, "-f"), ["b.yaml"])


class ResolveSubstitutionTests(unittest.TestCase):
    def test_resolves_defined_substitution(self):
        cloudbuild = {"substitutions": {"_DOCKERFILE": "Dockerfile.cpu"}}
        self.assertEqual(
            vm.resolve_substitution(cloudbuild, "${_DOCKERFILE}"), "Dockerfile.cpu"
        )

    def test_literal_value_passes_through_unchanged(self):
        cloudbuild = {"substitutions": {}}
        self.assertEqual(vm.resolve_substitution(cloudbuild, "Dockerfile"), "Dockerfile")

    def test_undefined_substitution_raises_key_error(self):
        cloudbuild = {"substitutions": {}}
        with self.assertRaises(KeyError):
            vm.resolve_substitution(cloudbuild, "${_MISSING}")

    def test_partial_braces_are_not_treated_as_substitution(self):
        cloudbuild = {"substitutions": {}}
        self.assertEqual(vm.resolve_substitution(cloudbuild, "$_DOCKERFILE"), "$_DOCKERFILE")


def _minimal_cloudbuild(**overrides) -> dict:
    """A minimal but structurally complete cloudbuild dict, for fixture tests."""
    cloudbuild = {
        "substitutions": {
            "_DOCKERFILE": "Dockerfile.cpu",
            "_DEPLOYMENT_MANIFEST": "k8s/deployment-cpu.yaml",
        },
        "steps": [
            {
                "id": "render-manifests",
                "args": ["-c", "sed ... k8s/deployment-cpu.yaml > /workspace/out.yaml"],
            },
            {
                "id": "apply",
                "args": [
                    "apply",
                    "-f",
                    "k8s/namespace.yaml",
                    "-f",
                    "/workspace/out.yaml",
                ],
            },
        ],
    }
    cloudbuild.update(overrides)
    return cloudbuild


class ValidateCloudbuildWiringTests(unittest.TestCase):
    """Fixture-driven tests: these build a throwaway repo tree + cloudbuild dict
    so each failure branch is actually exercised, not just the current
    (already-correct) state of the real repo.
    """

    def _make_repo(self, tmp: str) -> Path:
        repo_root = Path(tmp)
        (repo_root / "k8s").mkdir()
        (repo_root / "Dockerfile.cpu").write_text("FROM scratch\n")
        (repo_root / "k8s" / "namespace.yaml").write_text(
            "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: llm\n"
        )
        (repo_root / "k8s" / "deployment-cpu.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: qwen-llm\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            "        - image: IMAGE_PLACEHOLDER\n"
        )
        return repo_root

    def test_well_formed_wiring_has_no_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            errors = vm.validate_cloudbuild_wiring(repo_root, _minimal_cloudbuild())
            self.assertEqual(errors, [])

    def test_missing_dockerfile_substitution_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            cloudbuild = _minimal_cloudbuild()
            del cloudbuild["substitutions"]["_DOCKERFILE"]
            errors = vm.validate_cloudbuild_wiring(repo_root, cloudbuild)
            self.assertIn("substitutions._DOCKERFILE is not defined", errors)

    def test_dockerfile_substitution_pointing_nowhere_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            cloudbuild = _minimal_cloudbuild()
            cloudbuild["substitutions"]["_DOCKERFILE"] = "Dockerfile.does-not-exist"
            errors = vm.validate_cloudbuild_wiring(repo_root, cloudbuild)
            self.assertTrue(
                any("Dockerfile.does-not-exist" in e for e in errors),
                errors,
            )

    def test_deployment_manifest_missing_placeholder_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            (repo_root / "k8s" / "deployment-cpu.yaml").write_text(
                "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: qwen-llm\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "        - image: already-hardcoded:latest\n"
            )
            errors = vm.validate_cloudbuild_wiring(repo_root, _minimal_cloudbuild())
            self.assertTrue(any("IMAGE_PLACEHOLDER" in e for e in errors), errors)

    def test_apply_step_missing_rendered_manifest_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            cloudbuild = _minimal_cloudbuild()
            # apply step forgets to apply the file render-manifests actually renders
            cloudbuild["steps"][1]["args"] = ["apply", "-f", "k8s/namespace.yaml"]
            errors = vm.validate_cloudbuild_wiring(repo_root, cloudbuild)
            self.assertTrue(
                any("never applies it" in e for e in errors),
                errors,
            )

    def test_apply_step_referencing_nonexistent_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            cloudbuild = _minimal_cloudbuild()
            cloudbuild["steps"][1]["args"] = [
                "apply",
                "-f",
                "k8s/typo-ed-name.yaml",
                "-f",
                "/workspace/out.yaml",
            ]
            errors = vm.validate_cloudbuild_wiring(repo_root, cloudbuild)
            self.assertTrue(
                any("k8s/typo-ed-name.yaml" in e for e in errors),
                errors,
            )

    def test_missing_apply_step_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            cloudbuild = _minimal_cloudbuild()
            cloudbuild["steps"] = [cloudbuild["steps"][0]]  # drop the apply step
            errors = vm.validate_cloudbuild_wiring(repo_root, cloudbuild)
            self.assertIn("no cloudbuild step with id 'apply'", errors)

    def test_missing_render_manifests_step_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self._make_repo(tmp)
            cloudbuild = _minimal_cloudbuild()
            cloudbuild["steps"] = [cloudbuild["steps"][1]]  # drop render-manifests
            errors = vm.validate_cloudbuild_wiring(repo_root, cloudbuild)
            self.assertIn("no cloudbuild step with id 'render-manifests'", errors)

    def test_real_repo_cloudbuild_wiring_is_valid(self):
        """Integration check: this repo's actual cloudbuild.yaml must pass today."""
        cloudbuild = vm.load_cloudbuild(vm.CLOUDBUILD_PATH)
        errors = vm.validate_cloudbuild_wiring(vm.REPO_ROOT, cloudbuild)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
