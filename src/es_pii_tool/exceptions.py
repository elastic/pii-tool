"""PII Tool Exceptions"""

import typing as t
from datetime import datetime, timedelta, timezone


class PiiToolError(Exception):  # Parent exception
    """
    Base class for all exceptions raised by the tool that are not base/native
    Exceptions
    """


class ClientError(PiiToolError):
    """
    Exception raised when the Elasticsearch client and/or connection is the source of
    the problem.

    :param message: The error message
    :param upstream: The upstream exception
    """

    def __init__(self, message: str, upstream: Exception):
        super().__init__(message)
        self.message = message
        self.upstream = upstream

    # Possibly include code here to extract any extra details, append them to message


class BadClientResult(ClientError):
    """
    Exception raised when return value from Elasticsearch API call is not or does not
    contain the expected result.

    :param message: The error message
    :param upstream: The upstream exception
    """


class MissingError(ClientError):
    """
    Exception raised when an item is expected but not found

    :param message: The error message
    :param upstream: The upstream exception
    :param missing: The missing item
    """

    def __init__(self, message: str, upstream: Exception, missing: str):
        super().__init__(message, upstream)
        #: The name of the missing item
        self.missing = missing


class MissingIndex(MissingError):
    """
    Exception raised when an index is expected but not found

    :param message: The error message
    :param upstream: The upstream exception
    :param missing: The missing index
    """


class MissingDocument(MissingError):
    """
    Exception raised when a document in an index is expected but not found

    :param message: The error message
    :param upstream: The upstream exception
    :param missing: The missing document
    """


class ConfigError(PiiToolError):
    """
    Exception raised when there is a configuration error

    :param message: The error message
    :param what: What or why the ConfigError happened
    """

    def __init__(self, message: str, what: t.Any):
        super().__init__(message)
        self.what = what


class MissingArgument(ConfigError):
    """
    Exception raised when a required argument or parameter is missing

    :param message: The error message
    :param what: What or why the ConfigError happened
    :param names: The name or names of the missing arguments. Can pass in a single
        name or a list.
    """

    def __init__(self, message: str, what: t.Any, names: t.Union[str, t.Sequence[str]]):
        super().__init__(message, what)
        if not isinstance(names, list):
            mylist = []
            mylist.append(names)
        #: The names of the missing argument
        self.names = names


class ValueMismatch(ConfigError):
    """
    Exception raised when a received value does not match what was expected.

    This is particularly used when ``expected_docs`` is specified but a different value
    is returned at query time.

    :param message: The error message
    :param what: What or why the ConfigError happened
    :param expected: What the expected value was
    """

    def __init__(self, message: str, what: t.Any, expected: t.Any):
        super().__init__(message, what)
        self.expected = expected


class PiiTimeout(PiiToolError):
    """
    Exception raised when a task has failed because the allotted time ran out

    :param message: The error message
    :param timeout: The timeout value
    :param seconds: Number of seconds
    """

    Num = t.Union[int, float]

    def __init__(
        self,
        message: str,
        seconds: t.Union[float, None] = None,
        elapsed: t.Union[float, None] = None,
        start: t.Union[datetime, None] = None,
        end: t.Union[datetime, None] = None,
    ):
        super().__init__(message)
        self.seconds = seconds
        self.elapsed = elapsed
        self.start = start
        self.end = end
        self.human = 'not calculated'
        self.parse()

    def get_human(self, value: Num) -> str:
        """
        Return human readable version of elapsed time
        Output is in days|hours|minutes|seconds.milliseconds
        """
        td = timedelta(seconds=value)
        seconds = td.seconds  # No microseconds
        h_num = seconds // 3600
        m_num = (seconds % 3600) // 60
        s_num = seconds % 60
        days = f'{td.days} days, ' if td.days else ''
        hours = f'{h_num} hours, ' if h_num > 0 else ''
        minutes = f'{m_num} minutes, ' if m_num > 0 else ''
        float_sec = float(s_num) + td.microseconds / 1000000
        return f'Elapsed time: {days}{hours}{minutes}{float_sec:.3f}'

    def parse(self) -> None:
        """Parse args and determine which value to use"""
        val = 'not calculated'
        if self.seconds:
            val = self.get_human(self.seconds)
        if self.elapsed:
            val = self.get_human(self.elapsed)
        if self.start and self.end:
            val = self.get_human((self.end - self.start).total_seconds())
        elif self.start and not self.end:
            # Going to use now as the end time
            end = datetime.now(timezone.utc)
            val = self.get_human((end - self.start).total_seconds())
        self.human = val


class FatalError(PiiToolError):
    """
    Exception raised when the program should be halted.

    :param message: The error message
    :param upstream: The upstream exception
    """

    def __init__(self, message: str, upstream: Exception):
        super().__init__(message)
        self.message = message
        self.upstream = upstream


# Possibly include more code here specific to the index and/or failure
