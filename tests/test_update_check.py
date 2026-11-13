"""Tests of the check for a newer QuadSens (no Qt, no network)."""

from __future__ import annotations

import json
import unittest

from quadsens import platforms, update_check
from tests.test_win_tools import FakeNet, FakeResponse

API = "https://api.github.com/repos/freasy/quadsens/releases/latest"


def net(tag: str | None = None, status: int = 200, **more: object) -> FakeNet:
    if tag is None:
        return FakeNet({})
    body = json.dumps({"tag_name": tag, **more}).encode()
    return FakeNet({API: FakeResponse(status, body)})


class CheckTest(unittest.TestCase):
    def test_a_newer_release_is_reported_with_a_link_built_here(self) -> None:
        transport = net("v0.5.0", html_url="https://evil.example/")
        result = update_check.check("0.4.1", transport)
        self.assertEqual(result.kind, update_check.NEWER)
        self.assertEqual(result.version, "0.5.0")
        self.assertEqual(result.url,
                         "https://github.com/freasy/quadsens/releases/tag/v0.5.0")
        self.assertEqual(transport.asked, [API])       # one request, nothing else

    def test_versions_are_compared_as_numbers(self) -> None:
        self.assertEqual(update_check.check("0.9.0", net("v0.10.0")).kind,
                         update_check.NEWER)
        self.assertEqual(update_check.check("0.10.0", net("v0.9.0")).kind,
                         update_check.CURRENT)
        self.assertEqual(update_check.check("0.4", net("v0.4.0")).kind,
                         update_check.CURRENT)
        self.assertEqual(update_check.check("0.4.1", net("v0.4.1")).kind,
                         update_check.CURRENT)

    def test_a_build_ahead_of_the_release_is_current(self) -> None:
        result = update_check.check("0.6.0", net("v0.5.0"))
        self.assertEqual(result.kind, update_check.CURRENT)
        self.assertEqual(result.version, "0.5.0")

    def test_prereleases_and_drafts_are_not_offered(self) -> None:
        for flag in ("prerelease", "draft"):
            result = update_check.check("0.4.1", net("v0.5.0", **{flag: True}))
            self.assertEqual(result.kind, update_check.FAILED, flag)

    def test_failures_carry_a_reason_and_never_raise(self) -> None:
        offline = update_check.check("0.4.1", net())
        self.assertEqual(offline.kind, update_check.FAILED)
        self.assertIn("no route to host", offline.reason)
        limited = update_check.check("0.4.1", net("v0.5.0", status=403))
        self.assertEqual(limited.kind, update_check.FAILED)
        self.assertIn("403", limited.reason)
        garbage = FakeNet({API: FakeResponse(200, b"<html>")})
        self.assertEqual(update_check.check("0.4.1", garbage).kind, update_check.FAILED)
        no_numbers = update_check.check("0.4.1", net("nightly"))
        self.assertEqual(no_numbers.kind, update_check.FAILED)
        self.assertTrue(no_numbers.reason)

        def broken(url: str, timeout: float) -> object:
            raise RuntimeError("boom")
        self.assertEqual(update_check.check("0.4.1", broken).kind, update_check.FAILED)

    def test_a_redirect_to_another_host_is_not_followed(self) -> None:
        transport = FakeNet({API: FakeResponse(302, location="https://example.org/")})
        self.assertEqual(update_check.check("0.4.1", transport).kind, update_check.FAILED)
        self.assertEqual(transport.asked, [API])


class TextTest(unittest.TestCase):
    def test_headline_names_both_versions(self) -> None:
        result = update_check.Result(update_check.NEWER, "0.5.0", "u")
        self.assertEqual(update_check.headline(result, "0.4.1"),
                         "QuadSens 0.5.0 is available. You have 0.4.1.")

    def test_how_to_update_says_what_is_true_today(self) -> None:
        linux = update_check.how_to_update(platforms.LINUX)
        windows = update_check.how_to_update(platforms.WINDOWS)
        self.assertIn("release page", linux)
        self.assertIn("quadsens.exe", windows)
        for text in (linux, windows):
            # No setup and no packages exist yet: nothing may promise them.
            for word in ("package manager", "pacman", "apt", "installer", "automatically"):
                self.assertNotIn(word, text)

    def test_answer_to_a_manual_check(self) -> None:
        current = update_check.Result(update_check.CURRENT, "0.4.1")
        self.assertIn("newest version", update_check.manual_answer(current, "0.4.1"))
        failed = update_check.Result(update_check.FAILED, reason="The server did not "
                                                                 "answer in time.")
        answer = update_check.manual_answer(failed, "0.4.1")
        self.assertIn("could not", answer)
        self.assertIn("did not answer in time", answer)


if __name__ == "__main__":
    unittest.main()
