#  Copyright 2008-2015 Nokia Networks
#  Copyright 2016-     Robot Framework Foundation
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from robot.utils import (  # type: ignore
    is_bytes,
    secs_to_timestr,
    timestr_to_secs,
)
from typing import Any
from .exceptions import ConfigurationException


class Configuration:
    """A simple configuration class.

    Configuration is defined with keyword arguments, in which the value must
    be an instance of :py:class:`Entry`. Different subclasses of `Entry` can
    be used to handle common types and conversions.

    Example::

        cfg = Configuration(name=StringEntry('initial'),
                            age=IntegerEntry('42'))
        assert cfg.name == initial
        assert cfg.age == 42
        cfg.update(name='John Doe')
        assert cfg.name == 'John Doe'
    """

    def __init__(self, **entries) -> None:
        self._config = entries

    def __str__(self) -> str:
        return "\n".join("%s=%s" % (k, v) for k, v in self._config.items())

    def update(self, **entries) -> None:
        """Update configuration entries.

        :param entries: entries to be updated, keyword argument names must
            match existing entry names. If any value in `**entries` is None,
            the corresponding entry is *not* updated.

        See `__init__` for an example.
        """
        for name, value in entries.items():
            if value is not None:
                self._config[name].set(value)

    def get(self, name: str) -> None:
        """Return entry corresponding to name."""
        return self._config[name]

    def __getattr__(self, name: str) -> Any:
        if name in self._config:
            return self._config[name].value
        msg = "Configuration parameter '%s' is not defined." % name
        raise ConfigurationException(msg)


class Entry:
    """A base class for values stored in :py:class:`Configuration`.

    :param:`initial` the initial value of this entry.
    """

    def __init__(self, initial: Any = None) -> None:
        self._value = self._create_value(initial)

    def __str__(self) -> str:
        return str(self._value)

    @property
    def value(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = self._parse_value(value)

    def _parse_value(self, value: Any) -> Any:
        raise NotImplementedError

    def _create_value(self, value: Any) -> Any:
        if value is None:
            return None
        return self._parse_value(value)


class StringEntry(Entry):
    """String value to be stored in :py:class:`Configuration`."""

    def _parse_value(self, value):
        return str(value)


class IntegerEntry(Entry):
    """Integer value to be stored in stored in :py:class:`Configuration`.

    Given value is converted to string using `int()`.

    """

    def _parse_value(self, value: Any) -> int:
        return int(value)


class TimeEntry(Entry):
    """Time string to be stored in :py:class:`Configuration`.

    Given time string will be converted to seconds using
    :py:func:`robot.utils.timestr_to_secs`.

    """

    def _parse_value(self, value: Any) -> float | None:
        value = str(value)
        return timestr_to_secs(value) if value else None

    def __str__(self) -> str:
        return secs_to_timestr(self._value)


class LogLevelEntry(Entry):
    """Log level to be stored in :py:class:`Configuration`.

    Given string must be one of 'TRACE', 'DEBUG', 'INFO', 'WARN' or 'NONE' case
    insensitively.
    """

    LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "NONE")

    def _parse_value(self, value: Any) -> str:
        value = str(value).upper()
        if value not in self.LEVELS:
            raise ConfigurationException("Invalid log level '%s'." % value)
        return value


class NewlineEntry(Entry):
    """New line sequence to be stored in :py:class:`Configuration`.

    Following conversion are performed on the given string:
        * 'LF' -> '\n'
        * 'CR' -> '\r'
    """

    def _parse_value(self, value: Any) -> str:
        if is_bytes(value):
            value = value.decode("ASCII")
        value = value.upper()
        return value.replace("LF", "\n").replace("CR", "\r")
