"""Exercise real pinned SDK validation with only its HTTP boundary mocked."""
from unittest.mock import Mock

import pytest
from garminconnect import Garmin

from garmin_mcp import challenges


@pytest.mark.parametrize('name,expected_start', [
    ('get_available_badge_challenges', 1),
    ('get_badge_challenges', 1),
    ('get_non_completed_badge_challenges', 1),
    ('get_inprogress_virtual_challenges', 1),
    ('get_adhoc_challenges', 0),
])
async def test_challenge_defaults_pass_real_sdk_validation(monkeypatch, name, expected_start):
    sdk = Garmin()
    endpoint = Mock(return_value=[])
    monkeypatch.setattr(sdk, 'connectapi', endpoint)
    monkeypatch.setattr(challenges, 'garmin_client', sdk)
    handlers = {}

    class Registry:
        def tool(self):
            def register(fn):
                handlers[fn.__name__] = fn
                return fn
            return register

    challenges.register_tools(Registry())
    result = await handlers[name]()
    assert not result.startswith('Error')
    endpoint.assert_called_once()
    assert endpoint.call_args.kwargs['params']['start'] == str(expected_start)
