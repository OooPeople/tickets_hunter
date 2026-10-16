"""Pytest plugin that writes uTiki managed JUnit from real hook reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest


def _load_context(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise pytest.UsageError("invalid uTiki reporter context")
    required = {
        "schema_version",
        "run_id",
        "attempt",
        "job_id",
        "phase",
        "target",
        "candidate_sha",
        "catalog_sha256",
        "execution_manifest_sha256",
        "fixture_digest",
        "script_digest",
        "mappings",
    }
    if set(value) != required or not isinstance(value["mappings"], list):
        raise pytest.UsageError("invalid uTiki reporter context fields")
    return value


def _properties(parent: ElementTree.Element, values: dict[str, Any]) -> None:
    element = ElementTree.SubElement(parent, "properties")
    for name, value in values.items():
        ElementTree.SubElement(
            element,
            "property",
            name=name,
            value=str(value).lower() if isinstance(value, bool) else str(value),
        )


class ManagedReporter:
    def __init__(self, context: dict[str, Any], junit: Path, result: Path) -> None:
        self.context = context
        self.junit = junit
        self.result = result
        self.items: dict[str, list[str]] = {}
        self.reports: dict[str, dict[str, pytest.TestReport]] = {}
        self.mapping: dict[str, dict[str, str]] = {}
        for value in context["mappings"]:
            if not isinstance(value, dict):
                raise pytest.UsageError("invalid uTiki reporter mapping")
            expected = {
                "case_id",
                "obligation_id",
                "variant_id",
                "node_id",
                "layer",
            }
            if set(value) != expected or any(
                not isinstance(value[name], str) or not value[name]
                for name in expected
            ):
                raise pytest.UsageError("invalid uTiki reporter mapping fields")
            node_id = value["node_id"]
            if node_id in self.mapping:
                raise pytest.UsageError("duplicate uTiki reporter node_id")
            self.mapping[node_id] = value

    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        del session, config
        self.items = {
            item.nodeid: sorted({marker.name for marker in item.iter_markers()})
            for item in items
        }

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.setdefault(report.nodeid, {})[report.when] = report

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(
        self, session: pytest.Session, exitstatus: pytest.ExitCode
    ) -> None:
        selected = set(self.items)
        missing = sorted(set(self.mapping) - selected)
        if missing and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        invalid_required: list[str] = []
        suite = ElementTree.Element(
            "testsuite",
            name=f"{self.context['job_id']}-{self.context['target']}",
            tests=str(len(self.items)),
        )
        suite_context = {
            name: self.context[name]
            for name in (
                "run_id",
                "attempt",
                "job_id",
                "phase",
                "target",
                "candidate_sha",
                "catalog_sha256",
                "execution_manifest_sha256",
                "fixture_digest",
                "script_digest",
            )
        }
        _properties(suite, suite_context)
        for node_id in sorted(self.items):
            reports = self.reports.get(node_id, {})
            testcase = ElementTree.SubElement(
                suite,
                "testcase",
                name=node_id.rsplit("::", 1)[-1],
                classname=node_id.split("::", 1)[0].replace("/", "."),
            )
            mapping = self.mapping.get(node_id)
            stages = {
                stage: reports[stage].outcome if stage in reports else "not-run"
                for stage in ("setup", "call", "teardown")
            }
            wasxfail = any(
                bool(getattr(report, "wasxfail", False))
                for report in reports.values()
            )
            passed = all(stages[stage] == "passed" for stage in stages) and not wasxfail
            if mapping is not None:
                if not passed:
                    invalid_required.append(node_id)
                _properties(
                    testcase,
                    {
                        **mapping,
                        **suite_context,
                        "outcome": "passed" if passed else "failed",
                        "wasxfail": wasxfail,
                        "setup_outcome": stages["setup"],
                        "call_outcome": stages["call"],
                        "teardown_outcome": stages["teardown"],
                        "markers": json.dumps(
                            self.items[node_id], separators=(",", ":")
                        ),
                    },
                )
            failed = next(
                (report for report in reports.values() if report.failed), None
            )
            skipped = next(
                (report for report in reports.values() if report.skipped), None
            )
            if failed is not None:
                tag = "failure" if failed.when == "call" else "error"
                ElementTree.SubElement(
                    testcase,
                    tag,
                    message=f"pytest {failed.when} {failed.outcome}",
                )
            elif skipped is not None:
                ElementTree.SubElement(testcase, "skipped", message="pytest skipped")

        if invalid_required and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

        self.junit.parent.mkdir(parents=True, exist_ok=True)
        ElementTree.ElementTree(suite).write(
            self.junit, encoding="utf-8", xml_declaration=True
        )
        payload = {
            "schema_version": 1,
            "status": "PASS"
            if session.exitstatus == pytest.ExitCode.OK and not missing
            else "FAIL",
            "initial_exit_code": int(exitstatus),
            "final_exit_code": int(session.exitstatus),
            "collected_count": int(session.testscollected),
            "selected_count": len(self.items),
            "required_count": len(self.mapping),
            "missing_node_ids": missing,
            "invalid_node_ids": invalid_required,
        }
        self.result.parent.mkdir(parents=True, exist_ok=True)
        self.result.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("utiki-evidence")
    group.addoption("--utiki-reporter-context")
    group.addoption("--utiki-managed-junit")
    group.addoption("--utiki-reporter-result")


def pytest_configure(config: pytest.Config) -> None:
    context_value = config.getoption("--utiki-reporter-context")
    junit_value = config.getoption("--utiki-managed-junit")
    result_value = config.getoption("--utiki-reporter-result")
    supplied = [context_value, junit_value, result_value]
    if not any(supplied):
        return
    if not all(supplied):
        raise pytest.UsageError("all uTiki reporter paths are required")
    plugin = ManagedReporter(
        _load_context(Path(context_value)), Path(junit_value), Path(result_value)
    )
    config.pluginmanager.register(plugin, "utiki-managed-reporter")
