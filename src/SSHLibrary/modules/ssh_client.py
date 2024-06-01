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

import fnmatch
import os
import re
import time

from robot.api import logger
from robot.utils import (  # type: ignore
    is_bytes,
    is_list_like,
    is_string,
    unicode,
)

from .command import RemoteCommand
from .config import Configuration, IntegerEntry, NewlineEntry, StringEntry, TimeEntry
from .exceptions import SSHClientException
from .scp_handlers import SCPClient, SCPTransferClient
from .sftp import SFTPClient
from .shell import Shell

try:
    import paramiko
except ImportError:
    raise ImportError(
        "Importing Paramiko library failed. " "Make sure you have Paramiko installed."
    )


# TODO: Add type hints
# TODO: Analyze usage of banner timeout
# There doesn't seem to be a simpler way to increase banner timeout
def _custom_start_client(self, *args, **kwargs):
    self.banner_timeout = 45
    self._orig_start_client(*args, **kwargs)


paramiko.transport.Transport._orig_start_client = (
    paramiko.transport.Transport.start_client
)
paramiko.transport.Transport.start_client = _custom_start_client


# See http://code.google.com/p/robotframework-sshlibrary/issues/detail?id=55
def _custom_log(self, level, msg, *args):
    def escape(s):
        return s.replace("%", "%%")

    if is_list_like(msg):
        msg = [escape(m) for m in msg]
    else:
        msg = escape(msg)
    return self._orig_log(level, msg, *args)


paramiko.sftp_client.SFTPClient._orig_log = paramiko.sftp_client.SFTPClient._log
paramiko.sftp_client.SFTPClient._log = _custom_log


class _ClientConfiguration(Configuration):
    def __init__(
        self,
        host,
        alias,
        port,
        timeout,
        newline,
        prompt,
        term_type,
        width,
        height,
        path_separator,
        encoding,
        escape_ansi,
        encoding_errors,
    ):
        super(_ClientConfiguration, self).__init__(
            index=IntegerEntry(None),
            host=StringEntry(host),
            alias=StringEntry(alias),
            port=IntegerEntry(port),
            timeout=TimeEntry(timeout),
            newline=NewlineEntry(newline),
            prompt=StringEntry(prompt),
            term_type=StringEntry(term_type),
            width=IntegerEntry(width),
            height=IntegerEntry(height),
            path_separator=StringEntry(path_separator),
            encoding=StringEntry(encoding),
            escape_ansi=StringEntry(escape_ansi),
            encoding_errors=StringEntry(encoding_errors),
        )


class SSHClient:
    """Base class for the SSH client implementation.

    This class defines the public API. Subclasses (:py:class:`pythonclient.
    SSHClient` and :py:class:`javaclient.JavaSSHClient`) provide the
    language specific concrete implementations.
    """

    tunnel = None

    def __init__(
        self,
        host,
        alias=None,
        port=22,
        timeout=3,
        newline="LF",
        prompt=None,
        term_type="vt100",
        width=80,
        height=24,
        path_separator="/",
        encoding="utf8",
        escape_ansi=False,
        encoding_errors="strict",
    ):
        self.config = _ClientConfiguration(
            host,
            alias,
            port,
            timeout,
            newline,
            prompt,
            term_type,
            width,
            height,
            path_separator,
            encoding,
            escape_ansi,
            encoding_errors,
        )
        self._sftp_client = None
        self._scp_transfer_client = None
        self._scp_all_client = None
        self._shell = None
        self._started_commands = []
        self.client = self._get_client()
        self.width = width
        self.height = height

    def _get_client(self):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return client

    @staticmethod
    def enable_logging(path):
        """Enables logging of SSH events to a file.

        :param str path: Path to the file the log is written to.

        :returns: `True`, if logging was successfully enabled. False otherwise.
        """
        paramiko.util.log_to_file(path)
        return True

    @property
    def sftp_client(self):
        """Gets the SFTP client for the connection.

        :returns: An object of the class that inherits from
            :py:class:`AbstractSFTPClient`.
        """
        if not self._sftp_client:
            self._sftp_client = self._create_sftp_client()
        return self._sftp_client

    @staticmethod
    def _read_login_ssh_config(host, username, port_number, proxy_cmd):
        ssh_config_file = os.path.expanduser("~/.ssh/config")
        if os.path.exists(ssh_config_file):
            conf = paramiko.SSHConfig()
            with open(ssh_config_file) as f:
                conf.parse(f)
            port = int(SSHClient._get_ssh_config_port(conf, host, port_number))
            user = SSHClient._get_ssh_config_user(conf, host, username)
            proxy_command = SSHClient._get_ssh_config_proxy_cmd(conf, host, proxy_cmd)
            host = SSHClient._get_ssh_config_host(conf, host)
            return host, user, port, proxy_command
        return host, username, port_number, proxy_cmd

    @staticmethod
    def _read_public_key_ssh_config(
        host, username, port_number, proxy_cmd, identity_file
    ):
        ssh_config_file = os.path.expanduser("~/.ssh/config")
        if os.path.exists(ssh_config_file):
            conf = paramiko.SSHConfig()
            with open(ssh_config_file) as f:
                conf.parse(f)
            port = int(SSHClient._get_ssh_config_port(conf, host, port_number))
            id_file = SSHClient._get_ssh_config_identity_file(conf, host, identity_file)
            user = SSHClient._get_ssh_config_user(conf, host, username)
            proxy_command = SSHClient._get_ssh_config_proxy_cmd(conf, host, proxy_cmd)
            host = SSHClient._get_ssh_config_host(conf, host)
            return host, user, port, id_file, proxy_command
        return host, username, port_number, identity_file, proxy_cmd

    @staticmethod
    def _get_ssh_config_user(conf, host, user):
        try:
            return conf.lookup(host)["user"] if not None else user
        except KeyError:
            return None

    @staticmethod
    def _get_ssh_config_proxy_cmd(conf, host, proxy_cmd):
        try:
            return conf.lookup(host)["proxycommand"] if not None else proxy_cmd
        except KeyError:
            return proxy_cmd

    @staticmethod
    def _get_ssh_config_identity_file(conf, host, id_file):
        try:
            return conf.lookup(host)["identityfile"][0] if not None else id_file
        except KeyError:
            return id_file

    @staticmethod
    def _get_ssh_config_port(conf, host, port_number):
        try:
            return conf.lookup(host)["port"] if not None else port_number
        except KeyError:
            return port_number

    @staticmethod
    def _get_ssh_config_host(conf, host):
        try:
            return conf.lookup(host)["hostname"] if not None else host
        except KeyError:
            return host

    def _get_jumphost_tunnel(self, jumphost_connection):
        dest_addr = (self.config.host, self.config.port)
        jump_addr = (jumphost_connection.config.host, jumphost_connection.config.port)
        jumphost_transport = jumphost_connection.client.get_transport()
        if not jumphost_transport:
            raise RuntimeError(
                "Could not get transport for {}:{}. Have you logged in?".format(
                    *jump_addr
                )
            )
        return jumphost_transport.open_channel("direct-tcpip", dest_addr, jump_addr)

    @property
    def scp_transfer_client(self):
        """Gets the SCP client for the file transfer.

        :returns: An object of the class that inherits from
            :py:class:`SFTPClient`.
        """
        if not self._scp_transfer_client:
            self._scp_transfer_client = self._create_scp_transfer_client()
        return self._scp_transfer_client

    @property
    def scp_all_client(self):
        """Gets the SCP client for the file transfer.

        :returns: An object of the class type
            :py:class:`SCPClient`.
        """
        if not self._scp_all_client:
            self._scp_all_client = self._create_scp_all_client()
        return self._scp_all_client

    @property
    def shell(self):
        """Gets the shell for the connection.

        :returns: An object of the class that inherits from
            :py:class:`AbstractShell`.
        """
        if not self._shell:
            self._shell = self._create_shell()
        if self.width != self.config.width or self.height != self.config.height:
            self._shell.resize(self.config.width, self.config.height)
            self.width, self.height = self.config.width, self.config.height
        return self._shell

    def _create_sftp_client(self):
        return SFTPClient(self.client, self.config.encoding)

    def _create_scp_transfer_client(self):
        return SCPTransferClient(self.client, self.config.encoding)

    def _create_scp_all_client(self):
        return SCPClient(self.client)

    def _create_shell(self):
        return Shell(
            self.client, self.config.term_type, self.config.width, self.config.height
        )

    def close(self):
        """Closes the connection."""
        if self.tunnel:
            self.tunnel.close()
        self._sftp_client = None
        self._scp_transfer_client = None
        self._scp_all_client = None
        self._shell = None
        self.client.close()
        try:
            logger.log_background_messages()
        except AttributeError:
            pass

    def login(
        self,
        username=None,
        password=None,
        allow_agent=False,
        look_for_keys=False,
        delay=None,
        proxy_cmd=None,
        read_config=False,
        jumphost_connection=None,
        keep_alive_interval="0 seconds",
    ):
        """Logs into the remote host using password authentication.

        This method reads the output from the remote host after logging in,
        thus clearing the output. If prompt is set, everything until the prompt
        is read (using :py:meth:`read_until_prompt` internally).
        Otherwise everything on the output is read with the specified `delay`
        (using :py:meth:`read` internally).

        :param keep_alive_interval: Set the transport keepalive interval.

        :param str username: Username to log in with.

        :param str password: Password for the `username`.

        :param bool allow_agent: enables the connection to the SSH agent.
            This option does not work when using Jython.

        :param bool look_for_keys: Whether the login method should look for
            available public keys for login. This will also enable ssh agent.
            This option is ignored when using Jython.

        :param str proxy_cmd: Proxy command
        :param str delay: The `delay` passed to :py:meth:`read` for reading
            the output after logging in. The delay is only effective if
            the prompt is not set.

        :param read_config: reads or ignores host entries from ``~/.ssh/config`` file. This parameter will read the hostname,
        port number, username and proxy command.

        :param SSHClient jumphost_connection : An instance of
            SSHClient that will be used as an intermediary jump-host
            for the SSH connection being attempted.

        :raises SSHClientException: If logging in failed.

        :returns: The read output from the server.
        """
        keep_alive_interval = int(TimeEntry(keep_alive_interval).value)
        username = self._encode(username)
        if not password or password == '""':
            password = None
        if password and not allow_agent:
            password = self._encode(password)
        try:
            self._login(
                username,
                password,
                allow_agent,
                look_for_keys,
                proxy_cmd,
                read_config,
                jumphost_connection,
                keep_alive_interval,
            )
        except SSHClientException:
            self.client.close()
            raise SSHClientException(
                "Authentication failed for user '%s'." % self._decode(username)
            )
        return self._read_login_output(delay)

    def _encode(self, text):
        if is_bytes(text):
            return text
        if not is_string(text):
            text = unicode(text)
        return text.encode(self.config.encoding, self.config.encoding_errors)

    def _decode(self, bytes):
        return bytes.decode(self.config.encoding, self.config.encoding_errors)

    def _login(
        self,
        username,
        password,
        allow_agent=False,
        look_for_keys=False,
        proxy_cmd=None,
        read_config=False,
        jumphost_connection=None,
        keep_alive_interval=None,
    ):
        if read_config:
            hostname = self.config.host
            self.config.host, username, self.config.port, proxy_cmd = (
                self._read_login_ssh_config(
                    hostname, username, self.config.port, proxy_cmd
                )
            )

        sock_tunnel = None

        if proxy_cmd and jumphost_connection:
            raise ValueError(
                "`proxy_cmd` and `jumphost_connection` are mutually exclusive SSH features."
            )
        elif proxy_cmd:
            sock_tunnel = paramiko.ProxyCommand(proxy_cmd)
        elif jumphost_connection:
            sock_tunnel = self._get_jumphost_tunnel(jumphost_connection)
        try:
            if not password and not allow_agent:
                # If no password is given, try login without authentication
                try:
                    self.client.connect(
                        self.config.host,
                        self.config.port,
                        username,
                        password,
                        look_for_keys=look_for_keys,
                        allow_agent=allow_agent,
                        timeout=float(self.config.timeout),
                        sock=sock_tunnel,
                    )
                except paramiko.SSHException:
                    pass
                transport = self.client.get_transport()
                transport.set_keepalive(keep_alive_interval)
                transport.auth_none(username)
            else:
                try:
                    self.client.connect(
                        self.config.host,
                        self.config.port,
                        username,
                        password,
                        look_for_keys=look_for_keys,
                        allow_agent=allow_agent,
                        timeout=float(self.config.timeout),
                        sock=sock_tunnel,
                    )
                    transport = self.client.get_transport()
                    transport.set_keepalive(keep_alive_interval)
                except paramiko.AuthenticationException:
                    try:
                        transport = self.client.get_transport()
                        transport.set_keepalive(keep_alive_interval)
                        try:
                            transport.auth_none(username)
                        except Exception:
                            pass
                        transport.auth_password(username, password)
                    except Exception:
                        raise SSHClientException
        except paramiko.AuthenticationException:
            raise SSHClientException

    def _read_login_output(self, delay):
        if not self.config.prompt:
            return self.read(delay)
        elif self.config.prompt.startswith("REGEXP:"):
            return self.read_until_regexp(self.config.prompt[7:])
        return self.read_until_prompt()

    def login_with_public_key(
        self,
        username,
        keyfile,
        password,
        allow_agent=False,
        look_for_keys=False,
        delay=None,
        proxy_cmd=None,
        jumphost_connection=None,
        read_config=False,
        keep_alive_interval="0 seconds",
    ):
        """Logs into the remote host using the public key authentication.

        This method reads the output from the remote host after logging in,
        thus clearing the output. If prompt is set, everything until the prompt
        is read (using :py:meth:`read_until_prompt` internally).
        Otherwise everything on the output is read with the specified `delay`
        (using :py:meth:`read` internally).

        :param str username: Username to log in with.

        :param str keyfile: Path to the valid OpenSSH private key file.

        :param str password: Password (if needed) for unlocking the `keyfile`.

        :param boolean allow_agent: enables the connection to the SSH agent.
            This option does not work when using Jython.

        :param boolean look_for_keys: enables the searching for discoverable
            private key files in ~/.ssh/. This option also does not work when
            using Jython.

        :param str delay: The `delay` passed to :py:meth:`read` for reading
            the output after logging in. The delay is only effective if
            the prompt is not set.

        :param str proxy_cmd : Proxy command

        :param SSHClient jumphost_connection : An instance of
            SSHClient that is will be used as an intermediary jump-host
            for the SSH connection being attempted.

        :param read_config: reads or ignores entries from ``~/.ssh/config`` file. This parameter will read the hostname,
        port number, username, identity file and proxy command.

        :raises SSHClientException: If logging in failed.

        :returns: The read output from the server.
        """
        if username:
            username = self._encode(username)
        if keyfile:
            self._verify_key_file(keyfile)
        keep_alive_interval = int(TimeEntry(keep_alive_interval).value)
        try:
            self._login_with_public_key(
                username,
                keyfile,
                password,
                allow_agent,
                look_for_keys,
                proxy_cmd,
                jumphost_connection,
                read_config,
                keep_alive_interval,
            )
        except SSHClientException:
            self.client.close()
            raise SSHClientException(
                "Login with public key failed for user \"get_banner\"'%s'."
                % self._decode(username)
            )
        return self._read_login_output(delay)

    def _verify_key_file(self, keyfile):
        if not os.path.exists(keyfile):
            raise SSHClientException("Given key file '%s' does not exist." % keyfile)
        try:
            open(keyfile).close()
        except IOError:
            raise SSHClientException("Could not read key file '%s'." % keyfile)

    def _login_with_public_key(
        self,
        username,
        key_file,
        password,
        allow_agent,
        look_for_keys,
        proxy_cmd=None,
        jumphost_connection=None,
        read_config=False,
        keep_alive_interval=None,
    ):
        if read_config:
            hostname = self.config.host
            self.config.host, username, self.config.port, key_file, proxy_cmd = (
                self._read_public_key_ssh_config(
                    hostname, username, self.config.port, proxy_cmd, key_file
                )
            )

        sock_tunnel = None
        if key_file is not None:
            if not os.path.exists(key_file):
                raise SSHClientException(
                    "Given key file '%s' does not exist." % key_file
                )
            try:
                open(key_file).close()
            except IOError:
                raise SSHClientException("Could not read key file '%s'." % key_file)
        else:
            raise RuntimeError(
                "Keyfile must be specified as keyword argument or in config file."
            )
        if proxy_cmd and jumphost_connection:
            raise ValueError(
                "`proxy_cmd` and `jumphost_connection` are mutually exclusive SSH features."
            )
        elif proxy_cmd:
            sock_tunnel = paramiko.ProxyCommand(proxy_cmd)
        elif jumphost_connection:
            sock_tunnel = self._get_jumphost_tunnel(jumphost_connection)

        try:
            self.client.connect(
                self.config.host,
                self.config.port,
                username,
                password,
                key_filename=key_file,
                allow_agent=allow_agent,
                look_for_keys=look_for_keys,
                timeout=float(self.config.timeout),
                sock=sock_tunnel,
            )
            transport = self.client.get_transport()
            transport.set_keepalive(keep_alive_interval)
        except paramiko.AuthenticationException:
            try:
                transport = self.client.get_transport()
                transport.set_keepalive(keep_alive_interval)
                try:
                    transport.auth_none(username)
                except Exception:
                    pass
                transport.auth_publickey(username, None)
            except Exception:
                raise SSHClientException

    staticmethod

    def get_banner_without_login(host, port=22):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(str(host), int(port), username="bad-username")
        except paramiko.AuthenticationException:
            return client.get_transport().get_banner()
        except Exception:
            raise SSHClientException(
                "Unable to connect to port {} on {}".format(port, host)
            )

    def get_banner(self):
        return self.client.get_transport().get_banner()

    def execute_command(
        self,
        command,
        sudo=False,
        sudo_password=None,
        timeout=None,
        output_during_execution=False,
        output_if_timeout=False,
        invoke_subsystem=False,
        forward_agent=False,
    ):
        """Executes the `command` on the remote host.

        This method waits until the output triggered by the execution of the
        `command` is available and then returns it.

        The `command` is always executed in a new shell, meaning that changes to
        the environment are not visible to the subsequent calls of this method.

        :param str command: The command to be executed on the remote host.

        :param sudo
         and
        :param sudo_password are used for executing commands within a sudo session.

        :param invoke_subsystem will request a subsystem on the server.

        :returns: A 3-tuple (stdout, stderr, return_code) with values
            `stdout` and `stderr` as strings and `return_code` as an integer.
        """
        self.start_command(
            command, sudo, sudo_password, invoke_subsystem, forward_agent
        )
        return self.read_command_output(
            timeout=timeout,
            output_during_execution=output_during_execution,
            output_if_timeout=output_if_timeout,
        )

    def start_command(
        self,
        command,
        sudo=False,
        sudo_password=None,
        invoke_subsystem=False,
        forward_agent=False,
    ):
        """Starts the execution of the `command` on the remote host.

        The started `command` is pushed into an internal stack. This stack
        always has the latest started `command` on top of it.

        The `command` is always started in a new shell, meaning that changes to
        the environment are not visible to the subsequent calls of this method.

        This method does not return anything. Use :py:meth:`read_command_output`
        to get the output of the previous started command.

        :param str command: The command to be started on the remote host.

        :param sudo
         and
        :param sudo_password are used for executing commands within a sudo session.

        :param invoke_subsystem will request a subsystem on the server.
        """
        command = self._encode(command)

        self._started_commands.append(
            self._start_command(
                command, sudo, sudo_password, invoke_subsystem, forward_agent
            )
        )

    def _start_command(
        self,
        command,
        sudo=False,
        sudo_password=None,
        invoke_subsystem=False,
        forward_agent=False,
    ):
        cmd = RemoteCommand(command, self.config.encoding)
        transport = self.client.get_transport()
        if not transport:
            raise AssertionError("Connection not open")
        new_shell = transport.open_session(timeout=float(self.config.timeout))

        if forward_agent:
            paramiko.agent.AgentRequestHandler(new_shell)

        cmd.run_in(new_shell, sudo, sudo_password, invoke_subsystem)
        return cmd

    def read_command_output(
        self, timeout=None, output_during_execution=False, output_if_timeout=False
    ):
        """Reads the output of the previous started command.

        The previous started command, started with :py:meth:`start_command`,
        is popped out of the stack and its outputs (stdout, stderr and the
        return code) are read and returned.

        :raises SSHClientException: If there are no started commands to read
            output from.

        :returns: A 3-tuple (stdout, stderr, return_code) with values
            `stdout` and `stderr` as strings and `return_code` as an integer.
        """
        if timeout:
            timeout = float(TimeEntry(timeout).value)
        try:
            return self._started_commands.pop().read_outputs(
                timeout, output_during_execution, output_if_timeout
            )
        except IndexError:
            raise SSHClientException("No started commands to read output from.")

    def write(self, text, add_newline=False):
        """Writes `text` in the current shell.

        :param str text: The text to be written.

        :param bool add_newline: If `True`, the configured newline will be
            appended to the `text` before writing it on the remote host.
            The newline is set when calling :py:meth:`open_connection`
        """
        text = self._encode(text)
        if add_newline:
            text += self._encode(self.config.newline)
        self.shell.write(text)

    def read(self, delay=None):
        """Reads all output available in the current shell.

        Reading always consumes the output, meaning that after being read,
        the read content is no longer present in the output.

        :param str delay: If given, this method reads again after the delay
            to see if there is more output is available. This wait-read cycle is
            repeated as long as further reads return more output or the
            configured timeout expires. The timeout is set when calling
            :py:meth:`open_connection`. The delay can be given as an integer
            (the number of seconds) or in Robot Framework's time format, e.g.
            `4.5s`, `3 minutes`, `2 min 3 sec`.

        :returns: The read output from the remote host.
        """
        output = self.shell.read()
        if delay:
            output += self._delayed_read(delay)
        return self._decode(output)

    def _delayed_read(self, delay):
        delay = TimeEntry(delay).value
        max_time = time.time() + self.config.get("timeout").value
        output = b""
        while time.time() < max_time:
            time.sleep(delay)
            read = self.shell.read()
            if not read:
                break
            output += read
        return output

    def read_char(self):
        """Reads a single Unicode character from the current shell.

        Reading always consumes the output, meaning that after being read,
        the read content is no longer present in the output.

        :returns: A single char read from the output.
        """
        server_output = b""
        while True:
            try:
                server_output += self.shell.read_byte()
                return self._decode(server_output)
            except UnicodeDecodeError as e:
                if e.reason == "unexpected end of data":
                    pass
                else:
                    raise

    def read_until(self, expected):
        """Reads output from the current shell until the `expected` text is
        encountered or the timeout expires.

        The timeout is set when calling :py:meth:`open_connection`.

        Reading always consumes the output, meaning that after being read,
        the read content is no longer present in the output.

        :param str expected: The text to look for in the output.

        :raises SSHClientException: If `expected` is not found in the output
            when the timeout expires.

        :returns: The read output, including the encountered `expected` text.
        """
        return self._read_until(lambda s: expected in s, expected)

    def _read_until(self, matcher, expected, timeout=None):
        output = ""
        timeout = TimeEntry(timeout) if timeout else self.config.get("timeout")
        max_time = time.time() + timeout.value
        while time.time() < max_time:
            char = self.read_char()
            if not char:
                time.sleep(0.00001)  # Release GIL so paramiko I/O thread can run
            output += char
            if matcher(output):
                return output
        raise SSHClientException(
            "No match found for '%s' in %s\nOutput:\n%s." % (expected, timeout, output)
        )

    def read_until_newline(self):
        """Reads output from the current shell until a newline character is
        encountered or the timeout expires.

        The newline character and the timeout are set when calling
        :py:meth:`open_connection`.

        Reading always consumes the output, meaning that after being read,
        the read content is no longer present in the output.

        :raises SSHClientException: If the newline character is not found in the
            output when the timeout expires.

        :returns: The read output, including the encountered newline character.
        """
        return self.read_until(self.config.newline)

    def read_until_prompt(self, strip_prompt=False):
        """Reads output from the current shell until the prompt is encountered
        or the timeout expires.

        The prompt and timeout are set when calling :py:meth:`open_connection`.

        Reading always consumes the output, meaning that after being read,
        the read content is no longer present in the output.

        :param bool strip_prompt: If 'True' then the prompt is removed from
            the resulting output

        :raises SSHClientException: If prompt is not set or is not found
            in the output when the timeout expires.

        :returns: The read output, including the encountered prompt.
        """
        if not self.config.prompt:
            raise SSHClientException("Prompt is not set.")

        if self.config.prompt.startswith("REGEXP:"):
            output = self.read_until_regexp(self.config.prompt[7:])
        else:
            output = self.read_until(self.config.prompt)
        if strip_prompt:
            output = self._strip_prompt(output)
        return output

    def _strip_prompt(self, output):
        if self.config.prompt.startswith("REGEXP:"):
            pattern = re.compile(self.config.prompt[7:])
            match = pattern.search(output)
            length = match.end() - match.start()
        else:
            length = len(self.config.prompt)
        return output[:-length]

    def read_until_regexp(self, regexp):
        """Reads output from the current shell until the `regexp` matches or
        the timeout expires.

        The timeout is set when calling :py:meth:`open_connection`.

        Reading always consumes the output, meaning that after being read,
        the read content is no longer present in the output.

        :param regexp: Either the regular expression as a string or a compiled
            Regex object.

        :raises SSHClientException: If no match against `regexp` is found when
            the timeout expires.

        :returns: The read output up and until the `regexp` matches.
        """
        if is_string(regexp):
            regexp = re.compile(regexp)
        return self._read_until(lambda s: regexp.search(s), regexp.pattern)

    def read_until_regexp_with_prefix(self, regexp, prefix):
        """
        Read and return from output until regexp matches prefix + output.

        :param regexp: a pattern or a compiled regexp object used for matching
        :raises SSHClientException: if match is not found in prefix+output when
            timeout expires.

        timeout is defined with :py:meth:`open_connection()`
        """
        if is_string(regexp):
            regexp = re.compile(regexp)
        matcher = regexp.search
        expected = regexp.pattern
        ret = ""
        timeout = self.config.get("timeout")
        start_time = time.time()
        while time.time() < float(timeout.value) + start_time:
            ret += self.read_char()
            if matcher(prefix + self._encode(ret)):
                return ret
        raise SSHClientException(
            "No match found for '%s' in %s\nOutput:\n%s" % (expected, timeout, ret)
        )

    def write_until_expected(self, text, expected, timeout, interval):
        """Writes `text` repeatedly in the current shell until the `expected`
        appears in the output or the `timeout` expires.

        :param str text: Text to be written. Uses :py:meth:`write_bare`
            internally so no newline character is appended to the written text.

        :param str expected: Text to look for in the output.

        :param int timeout: The timeout during which `expected` must appear
            in the output. Can be given as an integer (the number of seconds)
            or in Robot Framework's time format, e.g. `4.5s`, `3 minutes`,
            `2 min 3 sec`.

        :param int interval: Time to wait between the repeated writings of
            `text`.

        :raises SSHClientException: If `expected` is not found in the output
            before the `timeout` expires.

        :returns: The read output, including the encountered `expected` text.
        """
        expected = self._encode(expected)
        interval = TimeEntry(interval)
        timeout = TimeEntry(timeout)
        max_time = time.time() + timeout.value
        while time.time() < max_time:
            self.write(text)
            try:
                return self._read_until(
                    lambda s: expected in self._encode(s),
                    expected,
                    timeout=interval.value,
                )
            except SSHClientException:
                pass
        raise SSHClientException(
            "No match found for '%s' in %s." % (self._decode(expected), timeout)
        )

    def put_file(
        self,
        source,
        destination=".",
        mode="0o744",
        newline="",
        scp="OFF",
        scp_preserve_times=False,
    ):
        """Calls :py:meth:`AbstractS`FTPClient.put_file` with the given
        arguments.

        See :py:meth:`AbstractSFTPClient.put_file` for more documentation.
        """
        client = self._create_client(scp)
        return client.put_file(
            source,
            destination,
            scp_preserve_times,
            mode,
            newline,
            self.config.path_separator,
        )

    def put_directory(
        self,
        source,
        destination=".",
        mode="0o744",
        newline="",
        recursive=False,
        scp="OFF",
        scp_preserve_times=False,
    ):
        """Calls :py:meth:`AbstractSFTPClient.put_directory` with the given
        arguments and the connection specific path separator.

        The connection specific path separator is set when calling
        :py:meth:`open_connection`.

        See :py:meth:`AbstractSFTPClient.put_directory` for more documentation.
        """
        client = self._create_client(scp)
        return client.put_directory(
            source,
            destination,
            scp_preserve_times,
            mode,
            newline,
            self.config.path_separator,
            recursive,
        )

    def get_file(self, source, destination=".", scp="OFF", scp_preserve_times=False):
        """Calls :py:meth:`AbstractSFTPClient.get_file` with the given
        arguments.

        See :py:meth:`AbstractSFTPClient.get_file` for more documentation.
        """
        client = self._create_client(scp)
        if scp == "ALL":
            sources = self._get_files_for_scp_all(source)
            return client.get_file(
                sources, destination, scp_preserve_times, self.config.path_separator
            )
        return client.get_file(
            source, destination, scp_preserve_times, self.config.path_separator
        )

    def _get_files_for_scp_all(self, source):
        sources = self.execute_command('printf "%%s\\n" %s' % source)
        result = sources[0].split("\n")
        result[:] = [x for x in result if x]  # remove empty entries
        return result

    def get_directory(
        self,
        source,
        destination=".",
        recursive=False,
        scp="OFF",
        scp_preserve_times=False,
    ):
        """Calls :py:meth:`AbstractSFTPClient.get_directory` with the given
        arguments and the connection specific path separator.

        The connection specific path separator is set when calling
        :py:meth:`open_connection`.

        See :py:meth:`AbstractSFTPClient.get_directory` for more documentation.
        """
        client = self._create_client(scp)
        return client.get_directory(
            source,
            destination,
            scp_preserve_times,
            self.config.path_separator,
            recursive,
        )

    def list_dir(self, path, pattern=None, absolute=False):
        """Calls :py:meth:`.AbstractSFTPClient.list_dir` with the given
        arguments.

        See :py:meth:`AbstractSFTPClient.list_dir` for more documentation.

        :returns: A sorted list of items returned by
            :py:meth:`AbstractSFTPClient.list_dir`.
        """
        items = self.sftp_client.list_dir(path, pattern, absolute)
        return sorted(items)

    def list_files_in_dir(self, path, pattern=None, absolute=False):
        """Calls :py:meth:`AbstractSFTPClient.list_files_in_dir` with the given
        arguments.

        See :py:meth:`AbstractSFTPClient.list_files_in_dir` for more documentation.

        :returns: A sorted list of items returned by
            :py:meth:`AbstractSFTPClient.list_files_in_dir`.
        """
        files = self.sftp_client.list_files_in_dir(path, pattern, absolute)
        return sorted(files)

    def list_dirs_in_dir(self, path, pattern=None, absolute=False):
        """Calls :py:meth:`AbstractSFTPClient.list_dirs_in_dir` with the given
        arguments.

        See :py:meth:`AbstractSFTPClient.list_dirs_in_dir` for more documentation.

        :returns: A sorted list of items returned by
            :py:meth:`AbstractSFTPClient.list_dirs_in_dir`.
        """
        dirs = self.sftp_client.list_dirs_in_dir(path, pattern, absolute)
        return sorted(dirs)

    def is_dir(self, path):
        """Calls :py:meth:`AbstractSFTPClient.is_dir` with the given `path`.

        :param str path: Path to check for directory. Supports GLOB Patterns.

        :returns: Boolean indicating is the directory is present or not.

        :rtype: bool

        See :py:meth:`AbstractSFTPClient.is_dir` for more documentation.
        """
        has_glob = bool([ops for ops in "*?![" if (ops in path)])
        if has_glob:
            dir_dir = path[: (-len(path.split(self.config.path_separator)[-1]))]
            dirs = self.sftp_client.list_dirs_in_dir(dir_dir)
            for dirname in dirs:
                if fnmatch.fnmatch(dirname, path.split(self.config.path_separator)[-1]):
                    return self.sftp_client.is_dir(dir_dir + dirname)
        return self.sftp_client.is_dir(path)

    def is_file(self, path):
        """Calls :py:meth:`AbstractSFTPClient.is_file` with the given `path`.

        :param str path: Path to check for file. Supports GLOB Patterns.

        :returns: Boolean indicating is the file is present or not.

        :rtype: bool

        See :py:meth:`AbstractSFTPClient.is_file` for more documentation.
        """
        if bool([ops for ops in "*?![" if (ops in path)]):
            file_dir = path[: (-len(path.split(self.config.path_separator)[-1]))]
            if file_dir == "":
                return self.sftp_client.is_file(path)
            files = self.sftp_client.list_files_in_dir(file_dir)
            for filename in files:
                if fnmatch.fnmatch(
                    filename, path.split(self.config.path_separator)[-1]
                ):
                    return self.sftp_client.is_file(file_dir + filename)
        return self.sftp_client.is_file(path)

    def _create_client(self, scp):
        if scp.upper() == "ALL":
            return self.scp_all_client
        elif scp.upper() == "TRANSFER":
            return self.scp_transfer_client
        else:
            return self.sftp_client
