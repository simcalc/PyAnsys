"""Temporary read authentication does not waive repository/runner identity."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import public_runner_guard as guard


class RepositoryRequestTests(unittest.TestCase):
    def response(self, raw=None, status=200, url=None):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = status
        response.geturl.return_value = url or guard.PUBLIC_REPOSITORY_URL
        response.read.return_value = raw if raw is not None else json.dumps({'full_name':guard.REPOSITORY,'private':False,'visibility':'public'}).encode('ascii')
        return response

    def test_optional_read_token_is_sent_only_to_exact_nonredirecting_endpoint(self):
        opener = SimpleNamespace(open=mock.Mock(return_value=self.response()))
        for env in ({},{'PYANSYS_REPOSITORY_READ_TOKEN':'synthetic-read-only-credential'}):
            with self.subTest(env=bool(env)),mock.patch.object(guard.urllib.request,'build_opener',return_value=opener) as build:
                actual=guard.fetch_public_repository(env)
                request=opener.open.call_args.args[0]
                self.assertEqual(request.full_url,guard.PUBLIC_REPOSITORY_URL)
                self.assertEqual(request.get_method(),'GET')
                self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-read-only-credential' if env else None)
                self.assertIsInstance(build.call_args.args[0],guard.NoRepositoryRedirect)
                self.assertFalse(actual['private'])
                self.assertNotIn('credential',json.dumps(actual))

    def test_wrong_status_url_oversize_or_invalid_json_reject(self):
        for response in (self.response(status=403),self.response(url='https://elsewhere.invalid/'),
                         self.response(raw=b'x'*1_048_577),self.response(raw=b'not json')):
            with mock.patch.object(guard.urllib.request,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k:response)):
                with self.assertRaises(ValueError):guard.fetch_public_repository({})

    def test_read_auth_does_not_accept_private_identity(self):
        response=self.response(raw=b'{"full_name":"simcalc/PyAnsys","private":true}')
        with mock.patch.object(guard.urllib.request,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k:response)):
            payload=guard.fetch_public_repository({'PYANSYS_REPOSITORY_READ_TOKEN':'synthetic'})
        env={'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REPOSITORY':guard.REPOSITORY,'RUNNER_OS':'Linux','RUNNER_ENVIRONMENT':'github-hosted'}
        with self.assertRaises(ValueError):guard.validate_context(env,{'repository':payload},payload)

    def test_redirect_or_bad_header_cannot_transmit_credential(self):
        with self.assertRaises(ValueError):guard.NoRepositoryRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.invalid/')
        for token in ('space secret','line\nsecret','é','x'*1025):
            with mock.patch.object(guard.urllib.request,'build_opener') as build:
                with self.assertRaises(ValueError):guard.fetch_public_repository({'PYANSYS_REPOSITORY_READ_TOKEN':token})
                build.assert_not_called()

    def test_workflow_credential_is_step_scoped_and_not_forwarded_to_native(self):
        import run_teaching_pump_baseline as runner
        with mock.patch.dict(runner.os.environ,{'PYANSYS_REPOSITORY_READ_TOKEN':'synthetic'}):
            self.assertNotIn('PYANSYS_REPOSITORY_READ_TOKEN',runner.native.child_environment())
        for name in ('teaching-pump-free-geometry.yml','teaching-pump-free-baseline.yml'):
            raw=(Path(__file__).resolve().parents[1]/'.github/workflows'/name).read_text()
            self.assertEqual(raw.count('PYANSYS_REPOSITORY_READ_TOKEN'),1)
            self.assertIn('PYANSYS_REPOSITORY_READ_TOKEN: ${{ github.token }}',raw)
            self.assertIn('contents: read',raw)


if __name__=='__main__':unittest.main()
