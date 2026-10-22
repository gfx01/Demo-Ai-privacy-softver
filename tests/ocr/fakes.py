"""Fake OCR engines, importable by the service's worker process
(OCR_ENGINE_FACTORY=tests.ocr.fakes:<name>)."""

from __future__ import annotations

import os
import time


class FakeEngine:
    name = "fake"

    def __init__(self, text: str = "Kép szövege", behaviour: str = "ok") -> None:
        self.text = text
        self.behaviour = behaviour

    def ocr(self, images, dpi):  # noqa: ARG002
        if self.behaviour == "slow":
            time.sleep(600)
        if self.behaviour == "crash":
            os._exit(3)
        if self.behaviour == "fail":
            raise ConnectionError("model server unreachable")
        return [self.text for _ in images]


def ok(settings):  # noqa: ARG001
    return FakeEngine()


def slow(settings):  # noqa: ARG001
    return FakeEngine(behaviour="slow")


def crash(settings):  # noqa: ARG001
    return FakeEngine(behaviour="crash")


def fail(settings):  # noqa: ARG001
    return FakeEngine(behaviour="fail")


def broken(settings):  # noqa: ARG001
    raise RuntimeError("language data missing")
