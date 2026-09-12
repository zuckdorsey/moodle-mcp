"""Tests for scripts/get_moodle_token.py.

Pure-logic coverage only — no network calls. The script is loaded by path
because ``scripts/`` is not an importable package.
"""

import base64
import importlib.util
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "get_moodle_token.py"

spec = importlib.util.spec_from_file_location("get_moodle_token", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
gt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gt)


class UrlNormalizationTests(unittest.TestCase):
    def test_base_url_is_left_alone(self):
        self.assertEqual(gt.normalize_site_url("https://moodle.example.com"), "https://moodle.example.com")

    def test_trailing_slash_is_stripped(self):
        self.assertEqual(gt.normalize_site_url("https://moodle.example.com/"), "https://moodle.example.com")

    def test_webservice_path_is_stripped(self):
        self.assertEqual(
            gt.normalize_site_url("https://learning.polibatam.ac.id/webservice/rest/server.php"),
            "https://learning.polibatam.ac.id",
        )

    def test_webservice_path_with_trailing_slash_is_stripped(self):
        self.assertEqual(
            gt.normalize_site_url("https://learning.polibatam.ac.id/webservice/rest/server.php/"),
            "https://learning.polibatam.ac.id",
        )

    def test_bare_host_gets_scheme(self):
        self.assertEqual(gt.normalize_site_url("learning.polibatam.ac.id"), "https://learning.polibatam.ac.id")

    def test_api_endpoint_is_appended(self):
        self.assertEqual(
            gt.api_endpoint("https://learning.polibatam.ac.id"),
            "https://learning.polibatam.ac.id/webservice/rest/server.php",
        )


class LaunchPayloadTests(unittest.TestCase):
    def _encode(self, payload, urlencode=False):
        raw = base64.b64encode(payload.encode()).decode()
        return urllib.parse.quote(raw, safe="") if urlencode else raw

    def test_decodes_three_part_payload(self):
        uri = "moodlemobile://token=" + self._encode("pass123:tok456:priv789")
        self.assertEqual(gt.decode_launch_uri(uri), ("tok456", "priv789"))

    def test_decodes_url_encoded_payload(self):
        uri = "moodlemobile://token=" + self._encode("pass123:tok456:priv789", urlencode=True)
        self.assertEqual(gt.decode_launch_uri(uri), ("tok456", "priv789"))

    def test_decodes_payload_without_padding(self):
        raw = base64.b64encode(b"pass123:tok456:priv789").decode().rstrip("=")
        self.assertEqual(gt.decode_launch_uri("moodlemobile://token=" + raw), ("tok456", "priv789"))

    def test_two_part_payload_is_supported(self):
        uri = "moodlemobile://token=" + self._encode("tok456:priv789")
        self.assertEqual(gt.decode_launch_uri(uri), ("tok456", "priv789"))

    def test_non_token_uri_returns_none(self):
        self.assertIsNone(gt.decode_launch_uri("https://learning.polibatam.ac.id/login/index.php"))

    def test_garbage_payload_returns_none(self):
        self.assertIsNone(gt.decode_launch_uri("moodlemobile://token=!!!not-base64!!!"))


class PassportTests(unittest.TestCase):
    def test_passport_is_32_hex_chars(self):
        passport = gt.new_passport()
        self.assertEqual(len(passport), 32)
        int(passport, 16)  # raises if not hex

    def test_passports_differ(self):
        self.assertNotEqual(gt.new_passport(), gt.new_passport())


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ".env"

    def _write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_replaces_existing_token(self):
        self._write("MOODLE_URL=https://x.test\nMOODLE_TOKEN=old\nMOODLE_MY_CLASS=Pagi C\n")
        changed = gt.update_env_token(self.path, "new")
        self.assertTrue(changed)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("MOODLE_TOKEN=new", text)
        self.assertNotIn("MOODLE_TOKEN=old", text)
        self.assertIn("MOODLE_URL=https://x.test", text)
        self.assertIn("MOODLE_MY_CLASS=Pagi C", text)

    def test_appends_when_missing(self):
        self._write("MOODLE_URL=https://x.test\n")
        gt.update_env_token(self.path, "new")
        self.assertIn("MOODLE_TOKEN=new\n", self.path.read_text(encoding="utf-8"))

    def test_appends_newline_when_file_lacks_trailing_newline(self):
        self._write("MOODLE_URL=https://x.test")
        gt.update_env_token(self.path, "new")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("MOODLE_URL=https://x.test\n", text)
        self.assertTrue(text.endswith("MOODLE_TOKEN=new\n"))

    def test_quoted_value_is_replaced_unquoted(self):
        self._write('MOODLE_TOKEN="old"\n')
        gt.update_env_token(self.path, "new")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "MOODLE_TOKEN=new\n")

    def test_duplicate_token_lines_are_collapsed(self):
        self._write("MOODLE_TOKEN=old1\nMOODLE_URL=u\nMOODLE_TOKEN=old2\n")
        gt.update_env_token(self.path, "new")
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("MOODLE_TOKEN="), 1)
        self.assertIn("MOODLE_TOKEN=new\n", text)

    def test_comments_are_preserved(self):
        self._write("# keep me\nMOODLE_TOKEN=old\n")
        gt.update_env_token(self.path, "new")
        self.assertIn("# keep me", self.path.read_text(encoding="utf-8"))

    def test_missing_file_is_created(self):
        gt.update_env_token(self.path, "new")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "MOODLE_TOKEN=new\n")

    def test_only_files_with_token_are_selected(self):
        self._write("MOODLE_TOKEN=old\n")
        other = Path(self.tmp.name) / ".env.other"
        other.write_text("SOMETHING=else\n", encoding="utf-8")
        found = gt.discover_env_files(self.tmp.name)
        self.assertEqual(found, [self.path])

    def test_token_after_other_lines_is_still_discovered(self):
        """Regression: the pattern must be MULTILINE, tokens rarely sit on line 1."""
        self._write("MOODLE_URL=https://x.test\nOBSIDIAN_VAULT_PATH=/v\nMOODLE_TOKEN=old\n")
        self.assertEqual(gt.discover_env_files(self.tmp.name), [self.path])

    def test_export_style_token_is_discovered(self):
        self._write("OTHER=1\nexport MOODLE_TOKEN=old\n")
        self.assertEqual(gt.discover_env_files(self.tmp.name), [self.path])

    def test_nested_profile_env_is_discovered(self):
        nested = Path(self.tmp.name) / "profiles" / "akademik"
        nested.mkdir(parents=True)
        env = nested / ".env"
        env.write_text("MOODLE_URL=u\nMOODLE_TOKEN=old\n", encoding="utf-8")
        self.assertEqual(gt.discover_env_files(self.tmp.name), [env])

    def test_backup_snapshots_are_skipped(self):
        snap = Path(self.tmp.name) / "state-snapshots" / "20260804-pre-update"
        snap.mkdir(parents=True)
        (snap / ".env").write_text("MOODLE_TOKEN=old\n", encoding="utf-8")
        (Path(self.tmp.name) / ".env").write_text("MOODLE_TOKEN=old\n", encoding="utf-8")
        self.assertEqual(gt.discover_env_files(self.tmp.name), [Path(self.tmp.name) / ".env"])


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_masked(self):
        fp = gt.fingerprint("a" * 32)
        self.assertIn("len=32", fp)
        self.assertIn("sha256:", fp)
        self.assertNotIn("a" * 32, fp)

    def test_empty_value(self):
        self.assertEqual(gt.fingerprint(""), "EMPTY")
        self.assertEqual(gt.fingerprint(None), "EMPTY")

    def test_same_input_same_fingerprint(self):
        self.assertEqual(gt.fingerprint("tok"), gt.fingerprint("tok"))


class LoginTokenParseTests(unittest.TestCase):
    def test_success_payload_returns_token(self):
        response = Mock()
        response.json.return_value = {"token": "abc123", "privatetoken": "priv"}
        with patch.object(gt.requests, "post", return_value=response):
            token, private, error = gt.token_via_login_token(
                "https://x.test", "user", "pass", "moodle_mobile_app", timeout=5
            )
        self.assertEqual(token, "abc123")
        self.assertEqual(private, "priv")
        self.assertIsNone(error)

    def test_error_payload_returns_message(self):
        response = Mock()
        response.json.return_value = {"error": "Invalid login, please try again", "errorcode": "invalidlogin"}
        with patch.object(gt.requests, "post", return_value=response):
            token, private, error = gt.token_via_login_token(
                "https://x.test", "user", "pass", "moodle_mobile_app", timeout=5
            )
        self.assertIsNone(token)
        self.assertEqual(error, "Invalid login, please try again (invalidlogin)")


class VerifyTokenTests(unittest.TestCase):
    def test_valid_token_reports_site_details(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"sitename": "Polibatam", "username": "ababil", "userid": 7}
        with patch.object(gt.requests, "post", return_value=response):
            ok, info = gt.verify_token("https://x.test", "good")
        self.assertTrue(ok)
        self.assertEqual(info["username"], "ababil")
        self.assertIn("Polibatam", info["message"])

    def test_invalid_token_reports_error(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "exception": "core\\exception\\moodle_exception",
            "errorcode": "invalidtoken",
            "message": "Invalid token - token not found",
        }
        with patch.object(gt.requests, "post", return_value=response):
            ok, info = gt.verify_token("https://x.test", "bad")
        self.assertFalse(ok)
        self.assertEqual(info["errorcode"], "invalidtoken")

    def test_verify_sends_browser_user_agent(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"sitename": "S", "username": "u", "userid": 1}
        with patch.object(gt.requests, "post", return_value=response) as post:
            gt.verify_token("https://x.test", "tok")
        self.assertIn("Mozilla/5.0", post.call_args.kwargs["headers"]["User-Agent"])


class LoginPageParseTests(unittest.TestCase):
    HTML = (
        '<form action="https://x.test/login/index.php" method="post">'
        '<input type="hidden" name="logintoken" value="tok123">'
        '<input name="username"><input name="password">'
        "</form>"
    )

    def test_extracts_logintoken(self):
        self.assertEqual(gt.extract_logintoken(self.HTML), "tok123")

    def test_missing_logintoken_returns_none(self):
        self.assertIsNone(gt.extract_logintoken("<html></html>"))

    def test_detects_login_error(self):
        html = '<div class="alert alert-danger">Invalid login, please try again</div>'
        self.assertTrue(gt.login_page_has_error(html))

    def test_no_error_on_clean_page(self):
        self.assertFalse(gt.login_page_has_error('<div class="alert alert-info">Welcome</div>'))

    def test_looks_like_login_page_by_title(self):
        html = '<html><head><title>Log in to the site | E-Learning</title></head></html>'
        self.assertTrue(gt.looks_like_login_page(html))

    def test_looks_like_login_page_by_form(self):
        self.assertTrue(gt.looks_like_login_page(self.HTML))

    def test_dashboard_is_not_a_login_page(self):
        self.assertTrue(gt.looks_like_login_page("<html><title>Dashboard</title></html>") is False)


class LaunchWalkTests(unittest.TestCase):
    """The redirect walk must be driven by HTTP responses, not the network."""

    def _response(self, status, location=None, text=""):
        response = Mock()
        response.status_code = status
        response.headers = {"Location": location} if location else {}
        response.text = text
        return response

    def _session(self, responses):
        session = Mock()
        session.get.side_effect = responses
        return session

    def test_follows_redirect_chain_and_decodes_token(self):
        payload = base64.b64encode(b"pp:tok456:priv789").decode()
        session = self._session([
            self._response(303, "https://x.test/admin/tool/mobile/launch.php?service=s&passport=p"),
            self._response(303, "moodlemobile://token=" + payload),
        ])
        token, private, error = gt.token_via_mobile_launch(
            "https://x.test", "moodle_mobile_app", timeout=5, session=session
        )
        self.assertEqual((token, private, error), ("tok456", "priv789", None))
        self.assertEqual(session.get.call_count, 2)

    def test_redirect_to_login_reports_session_problem(self):
        session = self._session([
            self._response(303, "https://x.test/login/index.php?redirect=%2Fadmin%2Ftool%2Fmobile%2Flaunch.php"),
        ])
        token, _, error = gt.token_via_mobile_launch(
            "https://x.test", "moodle_mobile_app", timeout=5, session=session
        )
        self.assertIsNone(token)
        self.assertIn("session", error.lower())
        self.assertNotIn("<html", error)

    def test_login_page_body_reports_session_problem_without_html_dump(self):
        session = self._session([
            self._response(200, None, '<html><head><title>Log in to the site</title></head></html>'),
        ])
        token, _, error = gt.token_via_mobile_launch(
            "https://x.test", "moodle_mobile_app", timeout=5, session=session
        )
        self.assertIsNone(token)
        self.assertIn("session", error.lower())
        self.assertNotIn("<html", error)

    def test_non_login_error_page_is_truncated_not_dumped(self):
        long_body = "<html><body>" + ("x" * 5000) + "</body></html>"
        session = self._session([self._response(500, None, long_body)])
        _, _, error = gt.token_via_mobile_launch(
            "https://x.test", "moodle_mobile_app", timeout=5, session=session
        )
        self.assertIn("HTTP 500", error)
        self.assertLess(len(error), 300)
        self.assertNotIn("<html", error)


class MaskTests(unittest.TestCase):
    def test_mask_keeps_prefix_only(self):
        self.assertEqual(gt.mask("abcdef1234567890"), "abcdef...")

    def test_mask_handles_short_values(self):
        self.assertEqual(gt.mask("ab"), "***")


if __name__ == "__main__":
    unittest.main()
