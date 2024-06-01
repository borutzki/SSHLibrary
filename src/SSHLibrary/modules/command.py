import time

from robot.api import logger
from robot.utils import is_truthy

from .exceptions import RemoteCommandException


class RemoteCommand:
    """Base class for the remote command.

    Classes derived from this class (i.e. :py:class:`pythonclient.RemoteCommand`
    and :py:class:`javaclient.RemoteCommand`) provide the concrete and the
    language specific implementations for running the command on the remote
    host.
    """

    def __init__(self, command, encoding):
        self._command = command
        self._encoding = encoding
        self._shell = None

    def run_in(self, shell, sudo=False, sudo_password=None, invoke_subsystem=False):
        """Runs this command in the given `shell`.

        :param shell: A shell in the already open connection.

        :param sudo
         and
        :param sudo_password are used for executing commands within a sudo session.

        :param invoke_subsystem will request a subsystem on the server.
        """
        self._shell = shell
        if invoke_subsystem:
            self._invoke()
        elif not sudo:
            self._execute()
        else:
            self._execute_with_sudo(sudo_password)

    def _execute(self):
        self._shell.exec_command(self._command)

    def _invoke(self):
        self._shell.invoke_subsystem(self._command)

    def _execute_with_sudo(self, sudo_password=None):
        command = "sudo " + self._command.decode(self._encoding)
        if sudo_password is None:
            self._shell.exec_command(command)
        else:
            self._shell.exec_command(
                'echo %s | sudo --stdin --prompt "" %s' % (sudo_password, command)
            )

    def read_outputs(
        self, timeout=None, output_during_execution=False, output_if_timeout=False
    ):
        """Returns the outputs of this command.

        :returns: A 3-tuple (stdout, stderr, return_code) with values
            `stdout` and `stderr` as strings and `return_code` as an integer.
        """
        stderr, stdout = self._receive_stdout_and_stderr(
            timeout, output_during_execution, output_if_timeout
        )
        rc = self._shell.recv_exit_status()
        self._shell.close()
        return stdout, stderr, rc

    def _receive_stdout_and_stderr(
        self, timeout=None, output_during_execution=False, output_if_timeout=False
    ):
        stdout_filebuffer = self._shell.makefile("rb", -1)
        stderr_filebuffer = self._shell.makefile_stderr("rb", -1)
        stdouts = []
        stderrs = []
        while self._shell_open():
            self._flush_stdout_and_stderr(
                stderr_filebuffer,
                stderrs,
                stdout_filebuffer,
                stdouts,
                timeout,
                output_during_execution,
                output_if_timeout,
            )
            time.sleep(0.01)  # lets not be so busy
        stdout = (b"".join(stdouts) + stdout_filebuffer.read()).decode(self._encoding)
        stderr = (b"".join(stderrs) + stderr_filebuffer.read()).decode(self._encoding)
        return stderr, stdout

    def _shell_open(self):
        return not (
            self._shell.closed
            or self._shell.eof_received
            or self._shell.eof_sent
            or not self._shell.active
        )

    def _flush_stdout_and_stderr(
        self,
        stderr_filebuffer,
        stderrs,
        stdout_filebuffer,
        stdouts,
        timeout=None,
        output_during_execution=False,
        output_if_timeout=False,
    ):
        if timeout:
            end_time = time.time() + timeout
            while time.time() < end_time:
                if self._shell.status_event.wait(0):
                    break
                self._output_logging(
                    stderr_filebuffer,
                    stderrs,
                    stdout_filebuffer,
                    stdouts,
                    output_during_execution,
                )
            if not self._shell.status_event.isSet():
                if is_truthy(output_if_timeout):
                    logger.info(stdouts)
                    logger.info(stderrs)
                raise RemoteCommandException("Timed out in %s seconds" % int(timeout))
        else:
            self._output_logging(
                stderr_filebuffer,
                stderrs,
                stdout_filebuffer,
                stdouts,
                output_during_execution,
            )

    def _output_logging(
        self,
        stderr_filebuffer,
        stderrs,
        stdout_filebuffer,
        stdouts,
        output_during_execution=False,
    ):
        if self._shell.recv_ready():
            stdout_output = stdout_filebuffer.read(len(self._shell.in_buffer))
            if is_truthy(output_during_execution):
                logger.console(stdout_output)
            stdouts.append(stdout_output)
        if self._shell.recv_stderr_ready():
            stderr_output = stderr_filebuffer.read(len(self._shell.in_stderr_buffer))
            if is_truthy(output_during_execution):
                logger.console(stderr_output)
            stderrs.append(stderr_output)
