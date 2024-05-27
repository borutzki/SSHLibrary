class Shell:
    """Base class for the shell implementation.

    Classes derived from this class (i.e. :py:class:`pythonclient.Shell`
    and :py:class:`javaclient.Shell`) provide the concrete and the language
    specific implementations for reading and writing in a shell session.
    """

    def __init__(self, client, term_type, term_width, term_height):
        try:
            self._shell = client.invoke_shell(term_type, term_width, term_height)
        except AttributeError:
            raise RuntimeError(
                "Cannot open session, you need to establish a connection first."
            )

    def read(self):
        """Reads all the output from the shell.

        :returns: The read output.
        """
        data = b""
        while self._output_available():
            data += self._shell.recv(4096)
        return data

    def read_byte(self):
        """Reads a single byte from the shell.

        :returns: The read byte.
        """
        if self._output_available():
            return self._shell.recv(1)
        return b""

    def resize(self, width, height):
        self._shell.resize_pty(width=width, height=height)

    def _output_available(self):
        return self._shell.recv_ready()

    def write(self, text):
        """Writes the `text` in the current shell.

        :param str text: The text to be written. No newline characters are
            be appended automatically to the written text by this method.
        """
        self._shell.sendall(text)
