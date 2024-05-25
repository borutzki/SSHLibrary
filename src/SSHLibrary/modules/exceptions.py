class SSHLibraryException(Exception):
    """Base class for all exceptions related to SSHLibrary."""


class ConfigurationException(SSHLibraryException):
    """Raised when creating, updating or accessing a Configuration entry fails."""

    pass


class SSHClientException(SSHLibraryException):
    """Raised by SSHClient."""

    pass
