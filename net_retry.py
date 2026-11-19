"""Retry a call that failed before the remote service answered."""

import time
from urllib.error import URLError

ATTEMPTS = 3
DELAYS_SECONDS = (20, 45)

_TRANSIENT_TYPES = (TimeoutError, ConnectionError, URLError)
_TRANSIENT_NAMES = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "NetworkError",
    "SSLError",
    "SSLEOFError",
    "SSLZeroReturnError",
    "SlackClientNotConnectedError",
})


def is_transient_network_error(error, seen=None):
    """True when the failure is a timeout or a connection that never completed."""
    if seen is None:
        seen = set()
    if error is None or id(error) in seen:
        return False
    seen.add(id(error))
    if isinstance(error, _TRANSIENT_TYPES) or type(error).__name__ in _TRANSIENT_NAMES:
        return True
    if is_transient_network_error(getattr(error, "__cause__", None), seen):
        return True
    reason = getattr(error, "reason", None)
    return reason is not error and is_transient_network_error(reason, seen)


def call_with_retry(operation, description, log):
    """Try a transient network call up to three times, then raise the last error."""
    for attempt in range(ATTEMPTS):
        try:
            return operation()
        except Exception as error:
            if not is_transient_network_error(error) or attempt == ATTEMPTS - 1:
                raise
            delay = DELAYS_SECONDS[attempt]
            log("{0} failed ({1}/{2}): {3}.{4}: {5}; retrying in {6}s".format(
                description,
                attempt + 1,
                ATTEMPTS,
                type(error).__module__,
                type(error).__name__,
                error,
                delay,
            ))
            time.sleep(delay)
