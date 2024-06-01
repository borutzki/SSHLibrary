from paramiko import SSHClient, Channel, SSHException


class Shell:
    """Shell implementation for provided SSHClient session."""

    def __init__(
        self, client: SSHClient, term_type: str, term_width: int, term_height: int
    ) -> None:
        try:
            self._shell: Channel = client.invoke_shell(
                term_type, term_width, term_height
            )
        except (AttributeError, SSHException):
            raise RuntimeError(
                "Cannot open session, you need to establish a connection first."
            )

    @property
    def output_available(self) -> bool:
        """Return `True` if any non-empty output is available to read from current shell."""
        return self._shell.recv_ready()

    def read(self) -> bytes:
        """Reads all the output from the shell.

        :returns: The read output.
        """
        data = b""
        while self.output_available:
            data += self._shell.recv(4096)
        return data

    def read_byte(self) -> bytes:
        """Reads a single byte from the shell.

        :returns: The read byte.
        """
        if self.output_available:
            return self._shell.recv(1)
        return b""

    def resize(self, width: int, height: int) -> None:
        self._shell.resize_pty(width=width, height=height)

    def write(self, text: bytes) -> None:
        """Writes the `text` in the current shell.

        :param str text: The text to be written. No newline characters are
            be appended automatically to the written text by this method.
        """
        self._shell.sendall(text)
